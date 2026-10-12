"""What Gunther is working on in the background, for the top bar.

Every job (Transcribe again, an output being built, an answer being written) keeps one
entry here while it works and for a while after. Many entries can run at once. Jobs run
on threads and on the event loop, so one lock guards the entries and a version number
that grows with every change; the desktop waits on the version instead of polling.
The list lives in memory only.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

ACTIVE = ("waiting", "running")
KEEP_FINISHED_FOR = timedelta(minutes=60)
KEEP_FINISHED_COUNT = 20
_UNSET: Any = object()


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


@dataclass
class _Entry:
    id: str
    kind: str
    title: str
    about: str
    open: dict[str, Any] | None
    stop: Callable[[], object] | None
    started_at: datetime
    state: str = "running"
    detail: str = ""
    progress: float | None = None
    error: str | None = None
    ended_at: datetime | None = None
    token: object = field(default_factory=object)

    def out(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "about": self.about,
            "state": self.state,
            "progress": self.progress,
            "detail": self.detail,
            "error": self.error,
            "startedAt": _iso(self.started_at),
            "endedAt": _iso(self.ended_at) if self.ended_at else None,
            "open": self.open,
            "canStop": self.stop is not None and self.state in ACTIVE,
        }


class Tracked:
    """One job's entry. Safe to call from any thread; it does nothing once replaced."""

    def __init__(self, activity: Activity, entry: _Entry) -> None:
        self._activity = activity
        self._entry = entry
        self._token = entry.token

    def update(
        self,
        *,
        state: str | None = None,
        title: str | None = None,
        detail: str | None = None,
        progress: float | None = _UNSET,
    ) -> None:
        self._activity._change(
            self._entry, self._token, state=state, title=title, detail=detail, progress=progress
        )

    def done(self, title: str, detail: str = "") -> None:
        self._activity._change(
            self._entry, self._token, state="done", title=title, detail=detail, error=None
        )

    def failed(self, title: str, error: str) -> None:
        self._activity._change(
            self._entry, self._token, state="failed", title=title, detail="", error=error
        )

    def stopped(self) -> None:
        """The person stopped it: the entry goes at once."""

        self._activity._remove(self._entry, self._token)

    def __enter__(self) -> Tracked:
        return self

    def __exit__(self, kind: object, error: object, trace: object) -> None:
        # Leaving by an error must not leave a spinner behind.
        if error is not None and self._entry.state in ACTIVE:
            self.failed(self._entry.title, "Something went wrong.")


class Activity:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._version = 0
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []

    def track(
        self,
        id: str,
        kind: str,
        title: str,
        about: str,
        *,
        open: dict[str, Any] | None,
        stop: Callable[[], object] | None,
        state: str = "running",
        detail: str = "",
    ) -> Tracked:
        """Start an entry; one with the same id is replaced."""

        entry = _Entry(id, kind, title, about, open, stop, self._clock(), state, detail)
        with self._lock:
            self._entries.pop(id, None)
            self._entries[id] = entry
            waiters = self._bump()
        self._wake(waiters)
        return Tracked(self, entry)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            waiters = self._prune()
            result = self._list()
        self._wake(waiters)
        return result

    async def wait_for_change(self, after: int, timeout: float) -> dict[str, Any]:
        """The list now if it differs from version ``after``, else when it changes or on timeout."""

        waiter = (asyncio.get_running_loop(), asyncio.Event())
        with self._lock:
            if self._version != after:
                return self._list()
            self._waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter[1].wait(), timeout)
        except TimeoutError:
            pass
        finally:
            with self._lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
        return self.snapshot()

    def stop(self, id: str) -> bool | None:
        """None: nothing running has this id. False: it has no stop, or its stop said no
        (returned False). True: stop was asked."""

        with self._lock:
            entry = self._entries.get(id)
            if entry is None or entry.state not in ACTIVE:
                return None
            stop = entry.stop
        if stop is None:
            return False
        return stop() is not False

    def dismiss(self, id: str) -> bool | None:
        """None: no such entry. False: it is still running. True: it is gone."""

        with self._lock:
            entry = self._entries.get(id)
            if entry is None:
                return None
            if entry.state in ACTIVE:
                return False
            del self._entries[id]
            waiters = self._bump()
        self._wake(waiters)
        return True

    def clear(self) -> None:
        with self._lock:
            finished = [key for key, entry in self._entries.items() if entry.state not in ACTIVE]
            for key in finished:
                del self._entries[key]
            waiters = self._bump() if finished else []
        self._wake(waiters)

    # Inside --------------------------------------------------------------------------------

    def _list(self) -> dict[str, Any]:
        return {
            "version": self._version,
            "items": [entry.out() for entry in self._entries.values()],
        }

    def _bump(self) -> list[tuple[asyncio.AbstractEventLoop, asyncio.Event]]:
        """Call with the lock held; the caller wakes the returned waiters after releasing it."""

        self._version += 1
        waiters, self._waiters = self._waiters, []
        return waiters

    @staticmethod
    def _wake(waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]]) -> None:
        for loop, event in waiters:
            with contextlib.suppress(RuntimeError):  # that loop has closed
                loop.call_soon_threadsafe(event.set)

    def _prune(self) -> list[tuple[asyncio.AbstractEventLoop, asyncio.Event]]:
        """Drop finished entries that are old, or beyond the newest 20. Lock held."""

        now = self._clock()
        finished = sorted(
            (entry for entry in self._entries.values() if entry.ended_at is not None),
            key=lambda entry: entry.ended_at,
            reverse=True,
        )
        drop = [
            entry
            for number, entry in enumerate(finished)
            if number >= KEEP_FINISHED_COUNT or now - entry.ended_at > KEEP_FINISHED_FOR
        ]
        for entry in drop:
            del self._entries[entry.id]
        return self._bump() if drop else []

    def _change(self, entry: _Entry, token: object, **fields: Any) -> None:
        with self._lock:
            if self._entries.get(entry.id) is not entry or entry.token is not token:
                return
            if entry.state not in ACTIVE:
                return
            changed = False
            for name, value in fields.items():
                if value is _UNSET or (value is None and name != "progress" and name != "error"):
                    continue
                if name == "progress" and value is not None:
                    value = round(max(0.0, min(1.0, value)), 2)
                if getattr(entry, name) != value:
                    setattr(entry, name, value)
                    changed = True
            if changed and entry.state not in ACTIVE:
                entry.ended_at = self._clock()
            waiters = self._bump() if changed else []
        self._wake(waiters)

    def _remove(self, entry: _Entry, token: object) -> None:
        with self._lock:
            if self._entries.get(entry.id) is not entry or entry.token is not token:
                return
            del self._entries[entry.id]
            waiters = self._bump()
        self._wake(waiters)
