from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

from starlette.types import Message, Receive, Scope, Send

DEFAULT_REQUEST_BODY_BYTES = 8 * 1024 * 1024
ASSET_REQUEST_BODY_BYTES = 512 * 1024 * 1024
DIRECT_RECORDING_BODY_BYTES = 200 * 1024 * 1024
RECORDING_CHUNK_BODY_BYTES = 16 * 1024 * 1024
PAIRING_EXCHANGE_BODY_BYTES = 16 * 1024

_RECORDING_CHUNK_PATH = re.compile(r"/recordings/rec_[a-f0-9]{24}/chunks$")


class _BodyLimitExceeded(Exception):
    pass


def request_body_limit(path: str, method: str) -> int:
    """Return the hard transport limit before a framework parser allocates memory."""

    if method == "POST" and path.endswith("/captures/assets"):
        return ASSET_REQUEST_BODY_BYTES
    if method == "POST" and path.endswith("/pairing/exchange"):
        return PAIRING_EXCHANGE_BODY_BYTES
    if method == "POST" and path.endswith("/recordings"):
        return DIRECT_RECORDING_BODY_BYTES
    if method == "PUT" and _RECORDING_CHUNK_PATH.search(path):
        return RECORDING_CHUNK_BODY_BYTES
    return DEFAULT_REQUEST_BODY_BYTES


def request_body_limit_detail(path: str, limit: int) -> str:
    """Keep route-specific public errors stable while enforcing transport caps."""

    if path.endswith("/pairing/exchange"):
        return "Pairing request is too large"
    return f"Request body exceeds the {limit}-byte limit"


class RequestBodyLimitMiddleware:
    """Bound declared and chunked HTTP bodies before FastAPI/Pydantic reads them.

    Asset and recording routes retain their larger, route-specific streaming
    limits. Every other write is capped at 8 MiB, which is above Gunther's
    largest accepted JSON document but prevents an authenticated device from
    forcing an unbounded allocation before schema validation.
    """

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {
            "POST",
            "PUT",
            "PATCH",
        }:
            await self.app(scope, receive, send)
            return

        method = str(scope["method"])
        path = str(scope.get("path", ""))
        limit = request_body_limit(path, method)
        limit_detail = request_body_limit_detail(path, limit)
        headers = {
            key.lower(): value
            for key, value in scope.get("headers", [])
        }
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                declared_size = int(declared.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                await self._reject(send, 400, "Content-Length must be a non-negative integer")
                return
            if declared_size < 0:
                await self._reject(send, 400, "Content-Length must be a non-negative integer")
                return
            if declared_size > limit:
                await self._reject(send, 413, limit_detail)
                return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyLimitExceeded
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _BodyLimitExceeded:
            if response_started:
                raise
            await self._reject(send, 413, limit_detail)

    @staticmethod
    async def _reject(send: Send, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
