"""Answers being written, kept apart from the pages that watch them.

Leaving a conversation must not stop its answer: the page only stops listening.
Each answer runs on its own thread and keeps what it has reported (steps and
text so far), so a page that comes back is given everything it missed and then
follows along live. Only an explicit stop ends an answer early.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

# Events that end an answer's stream: the saved turn, an error, or a stop.
FINAL = frozenset({"done", "error", "stopped"})


class Stopped(Exception):
    """Someone pressed Stop; the answer is dropped and the question kept, marked."""


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
    _watchers: list[_Watcher] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, name: str, data: dict[str, Any]) -> None:
        with self._lock:
            self.events.append((name, data))
            if name in FINAL:
                self.finished = True
            watchers = list(self._watchers)
        for watcher in watchers:
            watcher.loop.call_soon_threadsafe(watcher.queue.put_nowait, (name, data))

    def _replay(self) -> list[tuple[str, dict[str, Any]]]:
        """What happened so far, with the text joined into one piece."""

        out: list[tuple[str, dict[str, Any]]] = []
        for name, data in self.events:
            if name == "text" and out and out[-1][0] == "text":
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
    ) -> AnswerRun | None:
        """Begin an answer on its own thread; None when one is already being written."""

        with self._lock:
            if session_id in self._runs:
                return None
            run = AnswerRun(session_id, question)
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
        if run is None:
            return False
        run.stop_requested.set()
        return True
