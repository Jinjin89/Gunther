"""The backend's recent log lines, kept in memory for Settings → Developer → Logs.

Nothing configured logging before, so Gunther's warnings reached backend.log only
through Python's last-resort handler and its INFO lines (a planning step that
failed, say) were dropped. This keeps the last few hundred lines from Gunther's
own loggers, INFO and up, and still writes warnings to stderr as before.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

KEPT_LINES = 400
_FORMAT = logging.Formatter()


class RecentLog(logging.Handler):
    def __init__(self, capacity: int = KEPT_LINES) -> None:
        super().__init__(level=logging.INFO)
        self._lines: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message = f"{message}\n{_FORMAT.formatException(record.exc_info)}"
            line = {
                "at": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": message[:4000],
            }
        except Exception:  # noqa: BLE001 - a log line must never break the caller
            return
        with self._guard:
            self._lines.append(line)

    def lines(self, limit: int = 200, level: str = "info") -> list[dict[str, Any]]:
        floor = logging.getLevelName(level.upper())
        floor = floor if isinstance(floor, int) else logging.INFO
        with self._guard:
            kept = [
                line
                for line in self._lines
                if logging.getLevelName(line["level"].upper()) >= floor
            ]
        return kept[-limit:][::-1]


_installed: RecentLog | None = None


def install() -> RecentLog:
    """Keep Gunther's recent log lines; safe to call more than once."""

    global _installed
    if _installed is not None:
        return _installed
    recent = RecentLog()
    logger = logging.getLogger("gunther")
    logger.setLevel(logging.INFO)
    logger.addHandler(recent)
    # With a handler of its own, Python's last resort no longer prints Gunther's
    # warnings; this keeps them in stderr (backend.log in the desktop app).
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(logging.WARNING)
    stderr.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(stderr)
    _installed = recent
    return recent
