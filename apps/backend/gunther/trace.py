"""How an answer was made, step by step, kept only while Settings → Developer asks for it.

A :class:`Trace` is started for one answer and made current with :func:`tracing`.
The agent marks its steps with :func:`step` (plan, search, write, check, cite), and
the model gateway records every call it makes into the step that is open, with the
exact prompts, the reply, its reasoning and how long it took. Nothing is recorded
when no trace is current, so answering costs the same with the switch off.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# One very long prompt must not make a trace huge; the rest of it is still readable.
MAX_TEXT = 20_000
MAX_ITEMS = 60

_current: ContextVar[Trace | None] = ContextVar("gunther_trace", default=None)


def clip(value: Any) -> Any:
    """Long text cut with a note, long lists shortened, nested values the same."""

    if isinstance(value, str):
        if len(value) <= MAX_TEXT:
            return value
        return f"{value[:MAX_TEXT]}\n… {len(value) - MAX_TEXT:,} more characters not kept"
    if isinstance(value, list | tuple):
        items = [clip(item) for item in value[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            items.append(f"… {len(value) - MAX_ITEMS} more")
        return items
    if isinstance(value, dict):
        return {str(key): clip(item) for key, item in value.items()}
    return value


@dataclass
class Trace:
    steps: list[dict[str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)
    _open: list[dict[str, Any]] = field(default_factory=list)

    def _now(self) -> int:
        return round((time.perf_counter() - self.started) * 1000)

    def add(self, kind: str, label: str, **detail: Any) -> dict[str, Any]:
        entry = {"kind": kind, "label": label, "atMs": self._now(), **clip(detail)}
        # Inside an open step (a model call while searching, say): kept under it.
        (self._open[-1].setdefault("children", []) if self._open else self.steps).append(entry)
        return entry

    @contextmanager
    def timed(self, kind: str, label: str, **detail: Any) -> Iterator[dict[str, Any]]:
        entry = self.add(kind, label, **detail)
        self._open.append(entry)
        try:
            yield entry
        except BaseException as error:
            entry["error"] = clip(str(error) or type(error).__name__)
            raise
        finally:
            self._open.pop()
            entry["ms"] = self._now() - entry["atMs"]

    def out(self) -> dict[str, Any]:
        return {"version": 1, "totalMs": self._now(), "steps": self.steps}


@contextmanager
def tracing(trace: Trace | None) -> Iterator[Trace | None]:
    """Make ``trace`` current for the work inside; None records nothing."""

    token = _current.set(trace)
    try:
        yield trace
    finally:
        _current.reset(token)


def current() -> Trace | None:
    return _current.get()


def note(kind: str, label: str, **detail: Any) -> dict[str, Any] | None:
    """Record something that happened, if a trace is being kept."""

    trace = _current.get()
    return trace.add(kind, label, **detail) if trace else None


@contextmanager
def step(kind: str, label: str, **detail: Any) -> Iterator[dict[str, Any]]:
    """A step that takes time; add what it found to the yielded dict with :func:`update`."""

    trace = _current.get()
    if trace is None:
        yield {}
        return
    with trace.timed(kind, label, **detail) as entry:
        yield entry


def update(entry: dict[str, Any], **detail: Any) -> None:
    """Add details to a step, clipped like the rest; nothing happens when no trace is kept."""

    if entry is not None and _current.get() is not None:
        entry.update(clip(detail))
