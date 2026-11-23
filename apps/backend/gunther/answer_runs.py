"""Answers being written, kept apart from the pages that watch them.

Leaving a conversation must not stop its answer: the page only stops listening.
Each answer runs on its own thread and keeps what it has reported (steps and
text so far), so a page that comes back is given everything it missed and then
follows along live. Only an explicit stop ends an answer early.

Outputs use the same registry, with a key for each Artifacts conversation or
unattached lineage.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from gunther.activity import Tracked

# Events that end an answer's stream: the saved turn, an error, or a stop.
FINAL = frozenset({"done", "error", "stopped"})
_stop_requested: ContextVar[threading.Event | None] = ContextVar("gunther_stop", default=None)


class Stopped(Exception):
    """Someone pressed Stop; the answer is dropped and the question kept, marked."""


@contextmanager
def stopping_on(requested: threading.Event) -> Iterator[None]:
    token = _stop_requested.set(requested)
    try:
        yield
    finally:
        _stop_requested.reset(token)


def check_stop() -> None:
    if (requested := _stop_requested.get()) is not None and requested.is_set():
        raise Stopped


@dataclass
class _Watcher:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[tuple[str, dict[str, Any]]]


@dataclass
class AnswerRun:
    session_id: str
    question: str
    started_at: float = field(default_factory=time.time)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    stop_requested: threading.Event = field(default_factory=threading.Event)
    finished: bool = False
    scope: tuple[str, str] | None = None  # Workspace and library of a scoped run.
    _activity: tuple[Tracked, str, str] | None = field(default=None, init=False)
    _watchers: list[_Watcher] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def track_activity(self, tracked: Tracked, *, done: str, failed: str) -> Tracked:
        self._activity = (tracked, done, failed)
        return tracked

    def stop(self) -> bool:
        with self._lock:
            if self.finished:
                return False
            self.stop_requested.set()
            return True

    def publish(
        self, name: str, data: dict[str, Any], *, open: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            if name in FINAL:
                # The worker has saved its result. Finish Activity before readers
                # can follow the terminal event or see that the run has ended.
                if self._activity is not None:
                    tracked, done, failed = self._activity
                    if name == "done":
                        tracked.done(done, open=open)
                    elif name == "stopped":
                        tracked.stopped()
                    else:
                        tracked.failed(failed, data["detail"])
                self.finished = True
            self.events.append((name, data))
            watchers = list(self._watchers)
        for watcher in watchers:
            watcher.loop.call_soon_threadsafe(watcher.queue.put_nowait, (name, data))

    def _replay(self) -> list[tuple[str, dict[str, Any]]]:
        """What happened so far, with the text joined into one piece."""

        out: list[tuple[str, dict[str, Any]]] = []
        for name, data in self.events:
            # Text joins only within one section (an output's sections are written in turn).
            if (
                name == "text"
                and out
                and out[-1][0] == "text"
                and out[-1][1].get("section") == data.get("section")
            ):
                out[-1] = ("text", {**data, "text": out[-1][1]["text"] + data["text"]})
            else:
                out.append((name, data))
        return out

    async def follow(self, *, resumed: bool = False) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Everything so far, then each new event, until the answer ends or the reader leaves."""

        watcher = _Watcher(asyncio.get_running_loop(), asyncio.Queue())
        with self._lock:
            past = self._replay()
            done = self.finished
            if not done:
                self._watchers.append(watcher)
        try:
            if resumed:
                yield "resumed", {
                    "type": "resumed",
                    "question": self.question,
                    "startedAt": self.started_at,
                }
            for event in past:
                yield event
                if event[0] in FINAL:
                    return
            if done:
                return
            while True:
                event = await watcher.queue.get()
                yield event
                if event[0] in FINAL:
                    return
        finally:
            # A reader leaving only stops listening; the answer goes on.
            with self._lock:
                if watcher in self._watchers:
                    self._watchers.remove(watcher)


class AnswerRuns:
    """At most one answer per conversation at a time."""

    def __init__(self) -> None:
        self._runs: dict[str, AnswerRun] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> AnswerRun | None:
        with self._lock:
            return self._runs.get(session_id)

    def start(
        self,
        session_id: str,
        question: str,
        work: Callable[[AnswerRun], None],
        *,
        scope: tuple[str, str] | None = None,
    ) -> AnswerRun | None:
        """Begin an answer on its own thread; None when one is already being written."""

        with self._lock:
            current = self._runs.get(session_id)
            if current is not None and not current.finished:
                return None
            run = AnswerRun(session_id, question, scope=scope)
            self._runs[session_id] = run

        def target() -> None:
            try:
                work(run)
            finally:
                if not run.finished:
                    run.publish(
                        "error", {"status": 500, "detail": "The answer could not be written."}
                    )
                with self._lock:
                    if self._runs.get(session_id) is run:
                        del self._runs[session_id]

        threading.Thread(target=target, name=f"answer-{session_id}", daemon=True).start()
        return run

    def stop(self, session_id: str) -> bool:
        run = self.get(session_id)
        return run is not None and run.stop()
