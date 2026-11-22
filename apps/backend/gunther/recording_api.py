"""Recording upload, playback, live transcription and reading tools."""

import asyncio
import re
import secrets
from dataclasses import replace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, WebSocket, status
from fastapi.responses import FileResponse
from pydantic import Field

from gunther import listen_copy, speech_registry, usage
from gunther.api_common import _declared_body_size, _service
from gunther.database import session_scope
from gunther.device_auth import AuthPrincipal, DeviceAuthError, parse_bearer_authorization
from gunther.lecture import LectureSummaryError
from gunther.paragraphs import ParagraphsError, group_paragraphs
from gunther.realtime import proxy_realtime_transcription
from gunther.recording_service import (
    MAX_CHUNK_BYTES,
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingService,
    RecordingTooLargeError,
    RecordingValidationError,
    recording_media_type,
)
from gunther.retranscribe import RecordingNotFound, RetranscribeError
from gunther.schemas import (
    ApiModel,
    CreateLectureSummaryInput,
    LectureSummaryOut,
    RecordingCheckpointInput,
    RecordingSessionOut,
)
from gunther.storage_budget import StorageBudgetError
from gunther.transcription import RELISTEN_PAD_MS, ListenError, relisten_audio

router = APIRouter()


async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    """Read a request without allowing an unbounded in-memory allocation."""

    declared_size = _declared_body_size(request)
    if declared_size is not None and declared_size > max_bytes:
        raise RecordingTooLargeError(f"The upload exceeds the {max_bytes}-byte request limit.")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise RecordingTooLargeError(f"The upload exceeds the {max_bytes}-byte request limit.")
        body.extend(chunk)
    return bytes(body)


async def _authorize_websocket(websocket: WebSocket) -> bool:
    """Authenticate sidecar or device without accepting device tokens in URLs."""

    settings = websocket.app.state.settings
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        await websocket.close(code=1008)
        return False

    authorization = websocket.headers.get("authorization")
    sidecar_candidates = [
        value
        for value in (
            websocket.headers.get("x-gunther-token"),
            websocket.query_params.get("token"),
        )
        if value
    ]
    if authorization is not None:
        if sidecar_candidates:
            await websocket.close(code=1008)
            return False
        bearer = parse_bearer_authorization(authorization)
        if bearer is None:
            await websocket.close(code=1008)
            return False
        try:
            principal = websocket.app.state.device_auth.authenticate_device(bearer)
        except DeviceAuthError:
            principal = None
        if principal is None or not principal.has_scope("transcription:stream"):
            await websocket.close(code=1008)
            return False
        websocket.state.auth_principal = principal
        return True

    if not websocket.app.state.allow_sidecar_auth:
        await websocket.close(code=1008)
        return False

    expected = settings.auth_token
    if sidecar_candidates and expected:
        authorized = any(
            secrets.compare_digest(candidate, expected) for candidate in sidecar_candidates
        )
        if not authorized:
            await websocket.close(code=1008)
            return False
        websocket.state.auth_principal = AuthPrincipal(kind="sidecar")
    elif sidecar_candidates or expected:
        await websocket.close(code=1008)
        return False
    else:
        websocket.state.auth_principal = AuthPrincipal(kind="development")
    return True


@router.post("/lectures/summarize", response_model=LectureSummaryOut)
def summarize_lecture(
    payload: CreateLectureSummaryInput,
    request: Request,
) -> LectureSummaryOut:
    summarizer = request.app.state.lecture_summarizer
    if summarizer is None:
        raise HTTPException(
            status_code=503,
            detail="Summaries need a model with an API key. Set one up in Settings, under Models.",
        )
    try:
        return summarizer.summarize(payload.title, payload.transcript, payload.duration_seconds)
    except LectureSummaryError as error:
        raise HTTPException(
            status_code=502,
            detail=f"The model could not write the summary ({error}). Try again.",
        ) from error


def _recordings(request: Request) -> RecordingService:
    return RecordingService(
        _service(request).sessions,
        request.app.state.settings.recordings_dir,
        request.app.state.storage_budget,
    )


def _raise_recording_error(error: Exception) -> None:
    if isinstance(error, StorageBudgetError):
        raise HTTPException(status_code=507, detail=str(error)) from error
    if isinstance(error, RecordingNotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, RecordingConflictError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, RecordingTooLargeError):
        raise HTTPException(status_code=413, detail=str(error)) from error
    if isinstance(error, RecordingValidationError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error


@router.get("/recordings", response_model=list[RecordingSessionOut])
def recordings(request: Request) -> list[RecordingSessionOut]:
    try:
        return _recordings(request).list()
    except Exception as error:
        _raise_recording_error(error)
        raise AssertionError("unreachable") from error


@router.post(
    "/recordings/sessions",
    response_model=RecordingSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def start_recording_session(
    request: Request,
    title: Annotated[str, Query(min_length=1, max_length=160)],
    content_type: Annotated[str | None, Header()] = None,
) -> RecordingSessionOut:
    """Create the destination before capture so long recordings persist incrementally."""
    try:
        return _recordings(request).start(title, content_type or "audio/webm")
    except Exception as error:
        _raise_recording_error(error)
        raise AssertionError("unreachable") from error


@router.put("/recordings/{recording_id}/chunks", response_model=RecordingSessionOut)
async def append_recording_chunk(
    recording_id: str,
    request: Request,
    sequence: Annotated[int, Query(ge=0)],
    checksum: Annotated[str, Header(alias="X-Chunk-SHA256")],
) -> RecordingSessionOut:
    """Append one sequenced, checksummed chunk for safe retries."""
    try:
        data = await _read_limited_body(request, MAX_CHUNK_BYTES)
        return _recordings(request).append(
            recording_id,
            data,
            sequence=sequence,
            checksum=checksum,
        )
    except Exception as error:
        _raise_recording_error(error)
        raise AssertionError("unreachable") from error


@router.patch("/recordings/{recording_id}/checkpoint", response_model=RecordingSessionOut)
def checkpoint_recording_session(
    recording_id: str,
    payload: RecordingCheckpointInput,
    request: Request,
) -> RecordingSessionOut:
    try:
        return _recordings(request).checkpoint(recording_id, payload)
    except Exception as error:
        _raise_recording_error(error)
        raise AssertionError("unreachable") from error


@router.post("/recordings/{recording_id}/complete", response_model=RecordingSessionOut)
def complete_recording_session(
    recording_id: str,
    request: Request,
) -> RecordingSessionOut:
    try:
        return _recordings(request).complete(recording_id)
    except Exception as error:
        _raise_recording_error(error)
        raise AssertionError("unreachable") from error


@router.get("/recordings/{recording_id}/metadata", response_model=RecordingSessionOut)
def recording_metadata(recording_id: str, request: Request) -> RecordingSessionOut:
    try:
        return _recordings(request).metadata(recording_id)
    except Exception as error:
        _raise_recording_error(error)
        raise AssertionError("unreachable") from error


@router.get("/recordings/{recording_id}", response_class=FileResponse)
def get_recording(recording_id: str, request: Request) -> FileResponse:
    try:
        path, media_type, file_name = _recordings(request).download(recording_id)
        return FileResponse(path, media_type=media_type, filename=file_name)
    except StorageBudgetError as error:
        raise HTTPException(status_code=507, detail=str(error)) from error
    except RecordingNotFoundError as error:
        # Existing installations may have files created before recording sessions
        # became database entities. Keep only genuinely untracked assets retrievable
        # during upgrade; a known session marked failed must never fall through and
        # expose bytes that failed the database/file integrity check.
        if not re.fullmatch(r"rec_[a-f0-9]{24}", recording_id):
            raise HTTPException(status_code=404, detail="Recording not found") from error
        try:
            _recordings(request).metadata(recording_id)
        except RecordingNotFoundError:
            pass
        else:
            raise HTTPException(status_code=404, detail="Recording not found") from error
        candidates = [
            path
            for path in request.app.state.settings.recordings_dir.glob(f"{recording_id}.*")
            if path.is_file() and not path.name.endswith(".part") and not listen_copy.is_copy(path)
        ]
        if len(candidates) != 1:
            raise HTTPException(status_code=404, detail="Recording not found") from error
        path = candidates[0]
        return FileResponse(path, media_type=recording_media_type(path), filename=path.name)


@router.websocket("/recordings/live")
async def live_recording(
    websocket: WebSocket,
    context: str = "",
    purpose: Literal["recording", "dictation"] = "recording",
    protocol: Annotated[int, Query(ge=2, le=2)] = 2,
    stream: bool = False,
    sample_rate: Annotated[int, Query(ge=8_000, le=48_000)] = 24_000,
    offset_ms: Annotated[int, Query(ge=0)] = 0,
    recording_id: Annotated[str | None, Query(max_length=64, pattern="^[A-Za-z0-9_-]+$")] = None,
) -> None:
    """The live transcript socket emits sentence pieces on the recording clock.

    ``offset_ms`` is where this connection's audio starts. ``recording_id``, when it names a
    recording still being captured, makes the backend keep a 16 kHz copy of the audio so
    it can be listened to again (``POST /recordings/{id}/relisten``).
    Desktop dictation requests ``stream=true`` without changing saved jobs.
    """

    if not await _authorize_websocket(websocket):
        return
    state = websocket.app.state
    role = "dictation" if purpose == "dictation" else "recording"
    choice, problem = speech_registry.resolve(state.speech_registry, role)
    # Desktop text fields request live dictation explicitly for this socket.
    if purpose == "dictation" and stream and choice is not None:
        choice = replace(choice, stream=True)
    listen = None
    if recording_id and purpose == "recording":
        # Only a recording still being captured gets an audio copy.
        with session_scope(state.knowledge_service.sessions) as session:
            capturing = listen_copy.recording_state(session, recording_id) == "capturing"
        if capturing:
            listen = listen_copy.ListenCopy(state.settings.recordings_dir, recording_id)
    await proxy_realtime_transcription(
        websocket,
        choice=choice,
        problem=problem or "",
        segment_seconds=state.settings.sensevoice_segment_seconds,
        context=context,
        sample_rate=sample_rate,
        offset_ms=offset_ms,
        listen=listen,
        check_again=bool(
            listen is not None
            and choice is not None
            # SenseVoice can't use what was said before: hearing it again gains nothing.
            and choice.kind != "sensevoice"
            and speech_registry.check_again(state.speech_registry)
        ),
        group_paragraphs=state.models.for_role("analysis") is not None,
    )


class RelistenContext(ApiModel):
    terms: list[str] = Field(default_factory=list, max_length=60)
    before: str = Field(default="", max_length=4_000)
    after: str = Field(default="", max_length=4_000)


class RelistenInput(ApiModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    pass_: Literal["minute", "retry", "final"] = Field(default="minute", alias="pass")
    context: RelistenContext = Field(default_factory=RelistenContext)


MAX_RELISTEN_MS = 180_000


@router.post("/recordings/{recording_id}/relisten")
async def relisten_recording(
    recording_id: str, payload: RelistenInput, request: Request
) -> dict[str, Any]:
    """Listen again to a stretch of a recording with the Recording job's model.

    The stretch comes from the audio copy the live socket keeps. Parts the copy does not
    hold are filled with silence and listed in ``missing``; with none of it, 409.
    """

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", recording_id):
        raise HTTPException(status_code=404, detail="Recording not found")
    start, end = payload.start_ms, payload.end_ms
    if end <= start or end - start > MAX_RELISTEN_MS:
        raise HTTPException(
            status_code=422,
            detail=f"Choose a stretch of up to {MAX_RELISTEN_MS // 1000} seconds.",
        )
    choice, _problem = speech_registry.resolve(request.app.state.speech_registry, "recording")
    if choice is None:
        raise HTTPException(
            status_code=424, detail="Choose a Recording model in Settings → Transcription."
        )
    directory = request.app.state.settings.recordings_dir
    padded_start = max(0, start - RELISTEN_PAD_MS)
    pcm, held = await asyncio.to_thread(
        listen_copy.read_range, directory, recording_id, padded_start, end + RELISTEN_PAD_MS
    )
    if not listen_copy.touches(held, start, end):
        raise HTTPException(
            status_code=409,
            detail=f"No audio was kept for {start / 1000:.1f}\u2013{end / 1000:.1f} s.",
        )
    try:
        heard = await relisten_audio(
            choice,
            pcm,
            audio_start_ms=padded_start,
            start_ms=start,
            end_ms=end,
            terms=payload.context.terms,
            before=payload.context.before,
            after=payload.context.after,
        )
    except ListenError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {
        "text": heard["text"],
        "sentences": heard["sentences"],
        "model": choice.model,
        "provider": choice.provider,
        "missing": [list(span) for span in listen_copy.missing_in(held, start, end)],
    }


@router.get("/recordings/{recording_id}/transcribe-again")
def recording_transcribe_again(recording_id: str, request: Request) -> dict[str, Any]:
    """How transcribing this recording again stands: ``idle``, ``waiting``, ``running``
    (``heardMs`` of ``totalMs``), ``done`` or ``failed`` (with ``error``)."""

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", recording_id):
        raise HTTPException(status_code=404, detail="Recording not found")
    return request.app.state.retranscriber.status(recording_id)


@router.post("/recordings/{recording_id}/transcribe-again", status_code=202)
async def transcribe_recording_again(recording_id: str, request: Request) -> dict[str, Any]:
    """Hear the saved audio again with the Recording model and replace the transcript."""

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", recording_id):
        raise HTTPException(status_code=404, detail="Recording not found")
    try:
        return await request.app.state.retranscriber.start(recording_id)
    except RecordingNotFound as error:
        raise HTTPException(status_code=404, detail="Recording not found") from error
    except RetranscribeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


class ParagraphsInput(ApiModel):
    sentences: list[Annotated[str, Field(max_length=2_000)]] = Field(min_length=1, max_length=300)
    before: str = Field(default="", max_length=4_000)
    pass_: Literal["live", "regroup"] = Field(default="live", alias="pass")


@router.post("/recordings/{recording_id}/paragraphs")
async def group_recording_paragraphs(
    recording_id: str, payload: ParagraphsInput, request: Request
) -> dict[str, Any]:
    """Where paragraphs start among these sentences, by content (the Analysis model).

    Only sentence numbers come back; the model never returns or changes text.
    """

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", recording_id):
        raise HTTPException(status_code=404, detail="Recording not found")
    gateway = request.app.state.models
    chosen = gateway.for_role("analysis")
    if chosen is None:
        raise HTTPException(
            status_code=424,
            detail="Paragraphs by topic need a model. Set one up under Settings → Models.",
        )
    model, effort = chosen
    try:
        with usage.job("analysis", "paragraphs", ref=recording_id):
            starts = await asyncio.to_thread(
                group_paragraphs,
                gateway,
                model,
                effort,
                payload.sentences,
                payload.before[-400:],
            )
    except ParagraphsError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"starts": starts, "model": model.display}
