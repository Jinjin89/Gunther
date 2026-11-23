"""Request-scoped service access, body limits and shared SSE transport."""

import json
from collections.abc import AsyncIterable

from fastapi import Request
from fastapi.responses import StreamingResponse

from gunther.answer_runs import AnswerRun, AnswerRuns
from gunther.recording_service import RecordingValidationError
from gunther.service import KnowledgeService


def _declared_body_size(request: Request) -> int | None:
    content_length = request.headers.get("content-length")
    if content_length is None:
        return None
    try:
        declared_size = int(content_length)
    except ValueError as error:
        raise RecordingValidationError("Content-Length must be an integer.") from error
    if declared_size < 0:
        raise RecordingValidationError("Content-Length cannot be negative.")
    return declared_size


def _service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service


def _answer_runs(request: Request) -> AnswerRuns:
    return request.app.state.answer_runs


def _sse(run: AnswerRun, *, resumed: bool = False):
    return _sse_events(run.follow(resumed=resumed))


def _sse_events(events: AsyncIterable[tuple[str, dict]]) -> StreamingResponse:
    async def encode():
        async for name, data in events:
            yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        encode(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
