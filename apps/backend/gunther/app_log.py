"""The desktop service's log files (the app's side is apps/desktop/src-tauri/src/app_log.rs).

The app passes a folder with ``--log-dir``; lines go to one file per day there,
``2026-09-30.backend.log``, and a day's file past ``MAX_FILE_BYTES`` goes on in
``2026-09-30.backend.2.log``. The app deletes old days. Each line starts with the
local time in the app's format, so the app's and the service's files of one day
read as one story. Secrets never reach a file: tokens, keys and bearer
credentials are masked in every line.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

MAX_FILE_BYTES = 20 * 1024 * 1024
NAME = "backend"

_LEVELS = {"WARNING": "WARN", "CRITICAL": "FATAL"}
_SECRETS = (
    (re.compile(r"(?i)\b(token|api[_-]?key|secret|password|signature)=([^&\s\"']+)"), r"\1=…"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer …"),
    (
        re.compile(r"(?i)(\"(?:apiKey|api_key|authToken|auth_token|token|password|secret)\"\s*:\s*)\"[^\"]*\""),
        r'\1"…"',
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "sk-…"),
)


def redact(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def file_name(date: str, part: int, name: str = NAME) -> str:
    return f"{date}.{name}.log" if part <= 1 else f"{date}.{name}.{part}.log"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _source(logger_name: str) -> str:
    if logger_name in {"gunther", "__main__"}:
        return NAME
    if logger_name.startswith("gunther."):
        return f"{NAME}/{logger_name.removeprefix('gunther.')}"
    if logger_name.startswith("uvicorn"):
        return "uvicorn"
    return logger_name


class LineFormatter(logging.Formatter):
    """``2026-09-30 14:03:22.481+08:00 INFO  backend/tts_service message``, secrets masked."""

    def format(self, record: logging.LogRecord) -> str:
        when = datetime.fromtimestamp(record.created).astimezone()
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            message = f"{message}\n{self.formatStack(record.stack_info)}"
        level = _LEVELS.get(record.levelname, record.levelname)
        stamp = when.isoformat(sep=" ", timespec="milliseconds")
        return f"{stamp} {level:<5} {_source(record.name)} {redact(message)}"


class DailyFileHandler(logging.Handler):
    """Append to today's file in ``folder``, moving on each day and when a file is full."""

    def __init__(
        self,
        folder: Path,
        *,
        name: str = NAME,
        max_bytes: int = MAX_FILE_BYTES,
        today: Callable[[], str] = _today,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.name_in_files = name
        self.max_bytes = max_bytes
        self._today = today
        self._stream: TextIO | None = None
        self._date: str | None = None
        self._size = 0
        self.path: Path | None = None

    def _current(self) -> TextIO:
        date = self._today()
        if self._stream is not None and self._date == date and self._size < self.max_bytes:
            return self._stream
        self._close_stream()
        self.folder.mkdir(parents=True, exist_ok=True)
        part = 1
        while True:
            path = self.folder / file_name(date, part, self.name_in_files)
            try:
                size = path.lstat().st_size
            except FileNotFoundError:
                size = 0
            if size < self.max_bytes:
                break
            part += 1
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags, 0o600)
        self._stream = os.fdopen(descriptor, "a", encoding="utf-8")
        self._date, self._size, self.path = date, size, path
        return self._stream

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            finally:
                self._stream = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = f"{self.format(record)}\n"
            stream = self._current()
            stream.write(line)
            stream.flush()
            self._size += len(line.encode("utf-8"))
        except Exception:  # noqa: BLE001 - logging must never break the caller
            self.handleError(record)

    def close(self) -> None:
        with self.lock:
            self._close_stream()
        super().close()


_configured = False


def configure(folder: Path | None, *, level: int = logging.INFO) -> None:
    """Send every logger's lines to today's file in ``folder`` (or stderr without one).

    Also logs what would otherwise vanish: unhandled errors in any thread,
    Python warnings, and a crash inside native code (to stderr, which the app
    points at the same file).
    """

    global _configured
    if _configured:
        return
    _configured = True
    handler: logging.Handler = (
        DailyFileHandler(folder) if folder is not None else logging.StreamHandler(sys.stderr)
    )
    handler.setFormatter(LineFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    # httpx names each call to a model or supplier (useful); httpcore repeats it in detail.
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)
    logging.captureWarnings(True)

    unhandled = logging.getLogger("gunther.unhandled")

    def log_unhandled(kind: type[BaseException], error: BaseException, trace: Any) -> None:
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, error, trace)
            return
        unhandled.critical("Unhandled error", exc_info=(kind, error, trace))

    def log_thread(arguments: threading.ExceptHookArgs) -> None:
        if arguments.exc_type is SystemExit or arguments.exc_value is None:
            return
        thread = arguments.thread.name if arguments.thread else "a thread"
        unhandled.error(
            "Unhandled error in %s",
            thread,
            exc_info=(arguments.exc_type, arguments.exc_value, arguments.exc_traceback),
        )

    sys.excepthook = log_unhandled
    threading.excepthook = log_thread
    stderr = sys.stderr
    if stderr is not None:
        try:
            stderr.fileno()
        except (OSError, ValueError, AttributeError):
            return
        faulthandler.enable(stderr)


class RequestLog:
    """One line per request: method, path, status and time; never the query, which holds the token.

    Changes (anything but GET), slow reads, WebSockets and failures are logged at
    INFO and up; a quick read or the web view's preflight check, only at DEBUG.
    """

    def __init__(self, app: Any, *, slow_ms: float = 1500.0) -> None:
        self.app = app
        self.slow_ms = slow_ms
        # Not under "gunther": these lines go to the files, not Settings → Developer → Logs.
        self.logger = logging.getLogger("api")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        kind = scope.get("type")
        if kind not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "WS") if kind == "http" else "WS"
        path = scope.get("path", "")
        started = time.perf_counter()
        status: int | None = None

        async def capture(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            elif message["type"] == "websocket.accept":
                status = 101
                self.logger.info("WS %s opened", path)
            elif message["type"] == "websocket.close" and status is None:
                status = int(message.get("code", 1000))
            await send(message)

        failed = False
        try:
            await self.app(scope, receive, capture)
        except BaseException:
            failed = True
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            shown = 500 if failed and status is None else status
            level = self._level(method, shown, elapsed, failed)
            if self.logger.isEnabledFor(level):
                self.logger.log(level, "%s %s %s %.0f ms", method, path, shown or "-", elapsed)

    def _level(self, method: str, status: int | None, elapsed: float, failed: bool) -> int:
        if failed or (status is not None and 500 <= status < 600):
            return logging.ERROR
        if status is not None and 400 <= status < 500:
            return logging.WARNING
        # OPTIONS is the web view asking before each call, not a call of its own.
        if method not in {"GET", "HEAD", "OPTIONS"} or elapsed >= self.slow_ms:
            return logging.INFO
        return logging.DEBUG
