"""Bounded admission control for the unauthenticated pairing exchange."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PairingExchangeRejected(RuntimeError):
    detail: str
    retry_after_seconds: int


class PairingExchangeGuard:
    """Apply bounded per-source and process-wide admission limits."""

    def __init__(
        self,
        *,
        max_attempts: int = 12,
        window_seconds: float = 10.0,
        max_concurrent: int = 4,
        max_sources: int = 1024,
    ) -> None:
        if min(max_attempts, max_concurrent, max_sources) < 1 or window_seconds <= 0:
            raise ValueError("Pairing exchange limits must be positive")
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._max_sources = max_sources
        self._lock = threading.Lock()
        self._attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self._concurrency = threading.BoundedSemaphore(max_concurrent)

    @contextmanager
    def acquire(self, source: str) -> Iterator[None]:
        now = time.monotonic()
        retry_after = self._record_attempt(source or "unknown", now)
        if retry_after is not None:
            raise PairingExchangeRejected(
                "Too many pairing attempts from this device",
                retry_after,
            )
        if not self._concurrency.acquire(blocking=False):
            raise PairingExchangeRejected(
                "Too many pairing exchanges are in progress",
                1,
            )
        try:
            yield
        finally:
            self._concurrency.release()

    def _record_attempt(self, source: str, now: float) -> int | None:
        cutoff = now - self._window_seconds
        with self._lock:
            attempts = self._attempts.get(source)
            if attempts is None:
                if len(self._attempts) >= self._max_sources:
                    self._attempts.popitem(last=False)
                attempts = deque()
                self._attempts[source] = attempts
            else:
                self._attempts.move_to_end(source)
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self._max_attempts:
                return max(1, math.ceil(attempts[0] + self._window_seconds - now))
            attempts.append(now)
            return None
