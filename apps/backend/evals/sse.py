"""Read Gunther's JSON event streams in repo tools and test fixtures."""

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from httpx import Client, Response

_LINE_ENDING = re.compile(r"\r\n?|\n")


def _sse_lines(chunks: Iterable[str]) -> Iterator[str]:
    """Yield complete SSE lines while preserving Unicode separators in their text."""
    parts: list[str] = []
    skip_lf = False
    for chunk in chunks:
        if not chunk:
            continue
        if skip_lf:
            # The CR in a split CRLF already ended its line.
            chunk = chunk.removeprefix("\n")
        skip_lf = chunk.endswith("\r")
        start = 0
        for ending in _LINE_ENDING.finditer(chunk):
            parts.append(chunk[start:ending.start()])
            yield "".join(parts)
            parts.clear()
            start = ending.end()
        parts.append(chunk[start:])


def iter_events(response: Response) -> Iterator[tuple[str, dict[str, Any]]]:
    """Decode complete SSE events, including JSON spread across data lines."""
    name = "message"
    lines: list[str] = []
    for line in _sse_lines(response.iter_text()):
        if line:
            field, _, value = line.partition(":")
            if field == "event":
                name = value.removeprefix(" ")
            elif field == "data":
                lines.append(value.removeprefix(" "))
            continue
        if lines:
            yield name, json.loads("\n".join(lines))
        name = "message"
        lines.clear()


def read_events(response: Response) -> list[tuple[str, dict[str, Any]]]:
    return list(iter_events(response))


class AnswerStreamError(Exception):
    def __init__(self, detail: str, status: int | None = None) -> None:
        super().__init__(detail)
        self.status = status


def ask_turn(
    client: Client,
    session_id: str,
    payload: dict[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Post a question and return its saved turn. HTTP rejections raise HTTPStatusError."""
    url = f"/api/sessions/{session_id}/messages/stream"
    with client.stream("POST", url, headers=headers, json=payload) as response:
        if not response.is_success:
            response.read()
            response.raise_for_status()
        for name, data in iter_events(response):
            if name == "done":
                return data
            if name == "error":
                raise AnswerStreamError(data["detail"], data["status"])
            if name == "stopped":
                raise AnswerStreamError("The answer was stopped.")
    raise AnswerStreamError("The answer stream ended without a terminal event.")
