import asyncio
import json
import logging
import re
import secrets
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, WebSocket, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from gunther import speech_registry
from gunther.answer_runs import AnswerRun, AnswerRuns, Stopped
from gunther.asset_service import (
    AssetNotFoundError,
    AssetService,
    AssetTooLargeError,
    AssetValidationError,
)
from gunther.brief import Brief
from gunther.device_auth import (
    WORKSPACE_PROTOCOL_VERSION,
    AuthPrincipal,
    DeviceAuthError,
    PairedDeviceNotFoundError,
    PairedDeviceView,
    PairingAlreadyUsedError,
    PairingCodeInvalidError,
    PairingExpiredError,
    PairingNotFoundError,
    parse_bearer_authorization,
)
from gunther.lecture import LectureSummaryError
from gunther.llm import ModelError
from gunther.pairing_exchange_guard import PairingExchangeRejected
from gunther.realtime import proxy_realtime_transcription, sensevoice_health
from gunther.recording_service import (
    MAX_CHUNK_BYTES,
    MAX_DIRECT_UPLOAD_BYTES,
    RecordingConflictError,
    RecordingNotFoundError,
    RecordingService,
    RecordingTooLargeError,
    RecordingValidationError,
    recording_media_type,
)
from gunther.schemas import (
    ArtifactOut,
    ArtifactSummaryOut,
    AskSkillOut,
    AssertionOut,
    AssetCaptureOut,
    BriefOut,
    BuildOutputInput,
    ConversationTurnOut,
    CreateDevicePairingInput,
    CreateKnowledgeBaseInput,
    CreateKnowledgeProposalInput,
    CreateKnowledgeSessionInput,
    CreateLectureSummaryInput,
    CreateNotebookNoteInput,
    CreateSessionMessageInput,
    CreateSourceInput,
    DeviceCredentialOut,
    DevicePairingSessionOut,
    EditOutputInput,
    ExchangeDevicePairingInput,
    FileNotebookNoteInput,
    FileNotebookNoteOut,
    FileSessionInput,
    FileSourceInput,
    FileSourceOut,
    HealthOut,
    ImportResultOut,
    InboxItemOut,
    KnowledgeBaseOut,
    KnowledgeGraphOut,
    KnowledgeProposalOut,
    KnowledgeSearchResultOut,
    KnowledgeSessionOut,
    KnowledgeSessionSummaryOut,
    KnowledgeUnitOut,
    LectureSummaryOut,
    MobileGatewayStatusOut,
    NotebookNoteOut,
    OverviewOut,
    PairedDeviceOut,
    RecordingCheckpointInput,
    RecordingSessionOut,
    ReviseOutputInput,
    SourceDetailOut,
    SourceKind,
    SourceSummaryOut,
    UpdateAssertionStatusInput,
    UpdateKnowledgeBaseInput,
    UpdateKnowledgeProposalInput,
    UpdateKnowledgeSessionInput,
    UpdateNotebookNoteInput,
    WebCaptureInput,
    WebCaptureOut,
    WebSearchOut,
    WorkspaceBootstrapOut,
)
from gunther.service import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    KnowledgeService,
)
from gunther.skillbook import ask_skills
from gunther.storage_budget import StorageBudgetError, StorageReservation
from gunther.web_capture import (
    WebCaptureConflictError,
    WebCaptureFetchError,
    WebCaptureService,
    WebCaptureTimeoutError,
    WebCaptureTooLargeError,
    WebCaptureValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter()
PAIRING_EXCHANGE_MAX_BYTES = 16 * 1024
PAIRING_EXCHANGE_READ_TIMEOUT_SECONDS = 5.0


async def _read_limited_body(
    request: Request,
    max_bytes: int,
    *,
    reservation: StorageReservation | None = None,
) -> bytes:
    """Read a request without allowing an unbounded in-memory allocation."""

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as error:
            raise RecordingValidationError("Content-Length must be an integer.") from error
        if declared_size < 0:
            raise RecordingValidationError("Content-Length cannot be negative.")
        if declared_size > max_bytes:
            raise RecordingTooLargeError(
                f"The upload exceeds the {max_bytes}-byte request limit."
            )

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise RecordingTooLargeError(
                f"The upload exceeds the {max_bytes}-byte request limit."
            )
        if reservation is not None and chunk:
            # Hold the full received body until the service materializes it on
            # disk. Unknown-length streams therefore remain concurrency-safe.
            reservation.prepare_write(len(body) + len(chunk))
        body.extend(chunk)
    return bytes(body)


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


async def _read_pairing_exchange_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from error
        if declared_size < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_size > PAIRING_EXCHANGE_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Pairing request is too large")

    body = bytearray()
    try:
        async with asyncio.timeout(PAIRING_EXCHANGE_READ_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                if len(body) + len(chunk) > PAIRING_EXCHANGE_MAX_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Pairing request is too large",
                    )
                body.extend(chunk)
    except TimeoutError as error:
        raise HTTPException(status_code=408, detail="Pairing request timed out") from error
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
            secrets.compare_digest(candidate, expected)
            for candidate in sidecar_candidates
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


def _service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service


def _assets(request: Request) -> AssetService:
    service = _service(request)
    return AssetService(
        service.sessions,
        request.app.state.settings.assets_dir,
        service,
        request.app.state.ocr_provider,
        request.app.state.storage_budget,
    )


def _web_captures(request: Request) -> WebCaptureService:
    return WebCaptureService(
        _assets(request),
        _service(request),
        request.app.state.web_capture_resolver,
        request.app.state.web_capture_fetcher,
    )


def _iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _paired_device_out(device: PairedDeviceView) -> PairedDeviceOut:
    return PairedDeviceOut(
        id=device.id,
        workspace_id=device.workspace_id,
        name=device.name,
        platform=device.platform,
        scopes=list(device.scopes),
        created_at=_iso_datetime(device.created_at) or "",
        last_used_at=_iso_datetime(device.last_used_at),
        revoked_at=_iso_datetime(device.revoked_at),
    )


def _principal(request: Request) -> AuthPrincipal:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, AuthPrincipal):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return principal


def _require_sidecar_admin(request: Request) -> None:
    if _principal(request).kind != "sidecar":
        raise HTTPException(status_code=403, detail="Sidecar administrator required")


@router.get("/mobile-gateway/status", response_model=MobileGatewayStatusOut)
def mobile_gateway_status(request: Request) -> MobileGatewayStatusOut:
    _require_sidecar_admin(request)
    snapshot = request.app.state.mobile_gateway.snapshot()
    return MobileGatewayStatusOut(
        enabled=snapshot.enabled,
        running=snapshot.running,
        address=snapshot.address,
        ca_fingerprint=snapshot.ca_fingerprint,
        ca_certificate_pem=snapshot.ca_certificate_pem,
        protocol_version=snapshot.protocol_version,
        error=snapshot.error,
    )


@router.post(
    "/pairing/sessions",
    response_model=DevicePairingSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_device_pairing_session(
    payload: CreateDevicePairingInput,
    request: Request,
) -> DevicePairingSessionOut:
    _require_sidecar_admin(request)
    pairing = request.app.state.device_auth.create_pairing_session(
        tuple(payload.scopes), payload.expires_in_seconds
    )
    return DevicePairingSessionOut(
        pairing_id=pairing.pairing_id,
        pairing_code=pairing.pairing_code,
        workspace_id=pairing.workspace.workspace_id,
        workspace_name=pairing.workspace.display_name,
        protocol_version=WORKSPACE_PROTOCOL_VERSION,
        scopes=list(pairing.scopes),
        created_at=_iso_datetime(pairing.created_at) or "",
        expires_at=_iso_datetime(pairing.expires_at) or "",
    )


@router.post(
    "/pairing/exchange",
    response_model=DeviceCredentialOut,
    status_code=status.HTTP_201_CREATED,
)
async def exchange_device_pairing(request: Request) -> DeviceCredentialOut:
    source = request.client.host if request.client is not None else "unknown"
    try:
        with request.app.state.pairing_exchange_guard.acquire(source):
            body = await _read_pairing_exchange_body(request)
            try:
                payload = ExchangeDevicePairingInput.model_validate_json(body)
            except ValidationError as error:
                raise HTTPException(status_code=422, detail="Invalid pairing request") from error
            try:
                credential = await run_in_threadpool(
                    request.app.state.device_auth.exchange_pairing_code,
                    payload.pairing_id,
                    payload.pairing_code,
                    payload.device_name,
                    payload.platform,
                )
            except PairingNotFoundError as error:
                raise HTTPException(status_code=404, detail="Pairing session not found") from error
            except PairingCodeInvalidError as error:
                raise HTTPException(status_code=401, detail="Pairing code is invalid") from error
            except PairingExpiredError as error:
                raise HTTPException(status_code=410, detail="Pairing code has expired") from error
            except PairingAlreadyUsedError as error:
                raise HTTPException(
                    status_code=409,
                    detail="Pairing code was already used",
                ) from error
    except PairingExchangeRejected as error:
        raise HTTPException(
            status_code=429,
            detail=error.detail,
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from error

    return DeviceCredentialOut(
        access_token=credential.access_token,
        protocol_version=WORKSPACE_PROTOCOL_VERSION,
        workspace_id=credential.workspace.workspace_id,
        workspace_name=credential.workspace.display_name,
        device=_paired_device_out(credential.device),
    )


@router.get("/devices", response_model=list[PairedDeviceOut])
def paired_devices(request: Request) -> list[PairedDeviceOut]:
    _require_sidecar_admin(request)
    return [
        _paired_device_out(device)
        for device in request.app.state.device_auth.list_devices()
    ]


@router.post("/devices/{device_id}/revoke", response_model=PairedDeviceOut)
def revoke_paired_device(device_id: str, request: Request) -> PairedDeviceOut:
    _require_sidecar_admin(request)
    try:
        device = request.app.state.device_auth.revoke_device(device_id)
    except PairedDeviceNotFoundError as error:
        raise HTTPException(status_code=404, detail="Paired device not found") from error
    return _paired_device_out(device)


@router.get("/workspace/bootstrap", response_model=WorkspaceBootstrapOut)
def workspace_bootstrap(request: Request) -> WorkspaceBootstrapOut:
    principal = _principal(request)
    workspace = request.app.state.device_auth.workspace()
    return WorkspaceBootstrapOut(
        workspace_id=workspace.workspace_id,
        workspace_name=workspace.display_name,
        protocol_version=WORKSPACE_PROTOCOL_VERSION,
        minimum_protocol_version=WORKSPACE_PROTOCOL_VERSION,
        auth_kind=principal.kind,
        device_id=principal.device_id,
        scopes=list(principal.scopes),
        capabilities=[
            "capture",
            "recording-upload",
            "live-transcription",
        ],
    )


@router.get("/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    service = _service(request)
    recording, _ = speech_registry.resolve(request.app.state.speech_registry, "recording")
    transcription_mode = "not_configured"
    transcription_provider = "none"
    transcription_model = ""
    if recording is not None:
        transcription_model = recording.model
        if recording.kind == "sensevoice":
            running = await sensevoice_health(recording.base_url)
            transcription_mode = "sensevoice_local" if running else "not_configured"
            transcription_provider = "sensevoice" if running else "none"
            if running:
                transcription_model = str(running.get("model", recording.model))
        elif recording.kind == "qwen":
            transcription_mode, transcription_provider = "qwen", "qwen"
        else:
            transcription_mode, transcription_provider = "compatible", "compatible"
    ocr = request.app.state.ocr_provider
    ocr_provider_name = getattr(ocr, "active_name", None) or ocr.name
    analysis = request.app.state.models.for_role("analysis")
    ask = request.app.state.models.for_role("ask")
    return HealthOut(
        extraction_mode=service.extraction_mode,
        web_search_mode=request.app.state.online_search.mode,
        transcription_mode=transcription_mode,
        transcription_provider=transcription_provider,
        transcription_model=transcription_model,
        summary_mode=(
            request.app.state.lecture_summarizer.model.display
            if request.app.state.lecture_summarizer
            else "off"
        ),
        digest_mode=(
            analysis[0].display
            if analysis and request.app.state.knowledge_service.index.digest_method
            else "off"
        ),
        analysis_model=analysis[0].display if analysis else None,
        ask_model=ask[0].display if ask else None,
        digest_images=request.app.state.knowledge_service.index.digest_vision,
        ocr_mode="local" if ocr.available else "not_configured",
        ocr_provider=ocr_provider_name if ocr.available else "none",
    )


@router.get("/search", response_model=list[KnowledgeSearchResultOut])
def search(
    request: Request,
    query: Annotated[str, Query(alias="q", min_length=1, max_length=500)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    knowledge_base_ids: Annotated[list[str] | None, Query(alias="knowledgeBaseId")] = None,
) -> list[KnowledgeSearchResultOut]:
    scope = list(dict.fromkeys(item.strip() for item in knowledge_base_ids or [] if item.strip()))
    if len(scope) > 20 or any(len(item) > 160 for item in scope):
        raise HTTPException(
            status_code=422,
            detail="Search at most 20 knowledge bases, each identified by at most 160 characters",
        )
    return _service(request).search_knowledge(
        query, limit=limit, knowledge_base_ids=scope or None
    )


@router.get("/search/web", response_model=WebSearchOut)
def web_search(
    request: Request,
    query: Annotated[str, Query(alias="q", min_length=1, max_length=500)],
) -> WebSearchOut:
    # Home shows a short answer above the pages; Ask's agent reads the pages itself.
    return request.app.state.online_search.search(query, answer=True)


@router.post(
    "/captures/assets",
    response_model=AssetCaptureOut,
    status_code=status.HTTP_201_CREATED,
)
async def capture_asset(
    request: Request,
    title: Annotated[str, Query(min_length=1, max_length=160)],
    file_name: Annotated[str, Query(alias="fileName", min_length=1, max_length=255)],
    kind: Annotated[SourceKind, Query()] = "file",
    knowledge_base_id: Annotated[
        str | None, Query(alias="knowledgeBaseId", max_length=160)
    ] = None,
    notes: Annotated[str, Query(max_length=5_000)] = "",
    defer_processing: Annotated[bool, Query(alias="deferProcessing")] = False,
) -> AssetCaptureOut:
    try:
        return await _assets(request).capture(
            request.stream(),
            title=title,
            kind=kind,
            file_name=file_name,
            media_type=request.headers.get("content-type", "application/octet-stream"),
            knowledge_base_id=knowledge_base_id,
            notes=notes,
            expected_size=_declared_body_size(request),
            defer_processing=defer_processing,
        )
    except AssetTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except AssetValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except StorageBudgetError as error:
        raise HTTPException(status_code=507, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/captures/web",
    response_model=WebCaptureOut,
    status_code=status.HTTP_201_CREATED,
)
async def capture_web(payload: WebCaptureInput, request: Request) -> WebCaptureOut:
    try:
        return await _web_captures(request).capture(payload)
    except WebCaptureTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except WebCaptureValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except StorageBudgetError as error:
        raise HTTPException(status_code=507, detail=str(error)) from error
    except WebCaptureConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except WebCaptureTimeoutError as error:
        raise HTTPException(status_code=504, detail=str(error)) from error
    except WebCaptureFetchError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/assets/{asset_id}", response_class=FileResponse)
def download_asset(asset_id: str, request: Request) -> FileResponse:
    try:
        path, media_type, file_name = _assets(request).download(asset_id)
        return FileResponse(path, media_type=media_type, filename=file_name)
    except AssetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/notes", response_model=list[NotebookNoteOut])
def notebook_notes(
    request: Request,
    note_status: Annotated[
        str | None, Query(alias="status", pattern="^(inbox|filed|archived)$")
    ] = None,
    query: Annotated[str | None, Query(alias="q", max_length=500)] = None,
) -> list[NotebookNoteOut]:
    return _service(request).list_notebook_notes(status=note_status, query=query)


@router.post(
    "/notes",
    response_model=NotebookNoteOut,
    status_code=status.HTTP_201_CREATED,
)
def create_notebook_note(
    payload: CreateNotebookNoteInput,
    request: Request,
) -> NotebookNoteOut:
    return _service(request).create_notebook_note(payload)


@router.patch("/notes/{note_id}", response_model=NotebookNoteOut)
def update_notebook_note(
    note_id: str,
    payload: UpdateNotebookNoteInput,
    request: Request,
) -> NotebookNoteOut:
    try:
        return _service(request).update_notebook_note(note_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/notes/{note_id}/file", response_model=FileNotebookNoteOut)
def file_notebook_note(
    note_id: str,
    payload: FileNotebookNoteInput,
    request: Request,
) -> FileNotebookNoteOut:
    try:
        return _service(request).file_notebook_note(note_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


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
    response: Response,
    sequence: Annotated[int | None, Query(ge=0)] = None,
    checksum: Annotated[str | None, Header(alias="X-Chunk-SHA256")] = None,
) -> RecordingSessionOut:
    """Append one chunk.

    New clients provide ``sequence`` and ``X-Chunk-SHA256`` for safe retries. The
    legacy request without either value remains supported for the current desktop.
    """
    try:
        data = await _read_limited_body(request, MAX_CHUNK_BYTES)
        if sequence is None:
            response.headers["Deprecation"] = "true"
            response.headers["Link"] = (
                '</api/recordings/{id}/chunks?sequence={n}>; rel="successor-version"'
            )
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


@router.post(
    "/recordings",
    response_model=RecordingSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def save_recording(
    request: Request,
    title: Annotated[str, Query(min_length=1, max_length=160)],
    content_type: Annotated[str | None, Header()] = None,
) -> RecordingSessionOut:
    try:
        expected_size = _declared_body_size(request)
        if expected_size is not None and expected_size > MAX_DIRECT_UPLOAD_BYTES:
            raise RecordingTooLargeError(
                f"The upload exceeds the {MAX_DIRECT_UPLOAD_BYTES}-byte request limit."
            )
        recordings = _recordings(request)
        with request.app.state.storage_budget.reserve(
            request.app.state.settings.recordings_dir,
            expected_bytes=expected_size or 0,
        ) as reservation:
            data = await _read_limited_body(
                request,
                MAX_DIRECT_UPLOAD_BYTES,
                reservation=reservation,
            )
            return recordings.save(
                title,
                content_type or "audio/webm",
                data,
                reservation=reservation,
            )
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
            if path.is_file() and not path.name.endswith(".part")
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
) -> None:
    if not await _authorize_websocket(websocket):
        return
    state = websocket.app.state
    choice, problem = speech_registry.resolve(
        state.speech_registry, "dictation" if purpose == "dictation" else "recording"
    )
    await proxy_realtime_transcription(
        websocket,
        choice=choice,
        problem=problem or "",
        segment_seconds=state.settings.sensevoice_segment_seconds,
        context=context,
    )


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseOut])
def knowledge_bases(request: Request) -> list[KnowledgeBaseOut]:
    return _service(request).list_knowledge_bases()


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseOut,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_base(payload: CreateKnowledgeBaseInput, request: Request) -> KnowledgeBaseOut:
    return _service(request).create_knowledge_base(payload)


@router.patch("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseOut)
def update_knowledge_base(
    knowledge_base_id: str,
    payload: UpdateKnowledgeBaseInput,
    request: Request,
) -> KnowledgeBaseOut:
    try:
        return _service(request).update_knowledge_base(knowledge_base_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/overview", response_model=OverviewOut)
def overview(request: Request) -> OverviewOut:
    return _service(request).overview()


@router.get("/graph", response_model=KnowledgeGraphOut)
def graph(request: Request) -> KnowledgeGraphOut:
    return _service(request).get_graph()


@router.get("/inbox", response_model=list[InboxItemOut])
def inbox(
    request: Request,
    inbox_state: Annotated[
        str | None, Query(alias="state", pattern="^(unfiled|needs_review)$")
    ] = None,
    item_type: Annotated[
        str | None,
        Query(alias="itemType", pattern="^(source|quick_note)$"),
    ] = None,
) -> list[InboxItemOut]:
    return _service(request).list_inbox(state=inbox_state, item_type=item_type)


@router.get("/sources", response_model=list[SourceSummaryOut])
def sources(request: Request) -> list[SourceSummaryOut]:
    return _service(request).list_sources()


@router.get("/sources/{source_id}", response_model=SourceDetailOut)
def source(source_id: str, request: Request) -> SourceDetailOut:
    try:
        return _service(request).get_source(source_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sources/{source_id}/file", response_model=FileSourceOut)
def file_source(
    source_id: str,
    payload: FileSourceInput,
    request: Request,
) -> FileSourceOut:
    try:
        return _service(request).file_source(source_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/knowledge-bases/{knowledge_base_id}/sources",
    response_model=list[SourceSummaryOut],
)
def knowledge_base_sources(knowledge_base_id: str, request: Request) -> list[SourceSummaryOut]:
    return _service(request).list_knowledge_base_sources(knowledge_base_id)


@router.post(
    "/sources",
    response_model=ImportResultOut,
    status_code=status.HTTP_201_CREATED,
)
def create_source(payload: CreateSourceInput, request: Request) -> ImportResultOut:
    try:
        return _service(request).import_source(payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/assertions", response_model=list[AssertionOut])
def assertions(
    request: Request,
    assertion_status: Annotated[
        str | None, Query(alias="status", pattern="^(provisional|verified|disputed)$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AssertionOut]:
    return _service(request).list_assertions(status=assertion_status, limit=limit)


@router.patch("/sources/{source_id}/assertions/status", response_model=list[AssertionOut])
def update_source_assertion_statuses(
    source_id: str,
    payload: UpdateAssertionStatusInput,
    request: Request,
) -> list[AssertionOut]:
    try:
        return _service(request).update_source_assertion_statuses(source_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/assertions/{assertion_id}/status", response_model=AssertionOut)
def update_assertion_status(
    assertion_id: str,
    payload: UpdateAssertionStatusInput,
    request: Request,
) -> AssertionOut:
    try:
        return _service(request).update_assertion_status(assertion_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/knowledge-bases/{knowledge_base_id}/sessions",
    response_model=list[KnowledgeSessionSummaryOut],
)
def knowledge_sessions(
    knowledge_base_id: str,
    request: Request,
    include_archived: Annotated[bool, Query(alias="includeArchived")] = False,
) -> list[KnowledgeSessionSummaryOut]:
    return _service(request).list_knowledge_sessions(
        knowledge_base_id, include_archived=include_archived
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/sessions",
    response_model=KnowledgeSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_session(
    knowledge_base_id: str,
    payload: CreateKnowledgeSessionInput,
    request: Request,
) -> KnowledgeSessionOut:
    try:
        return _service(request).create_knowledge_session(knowledge_base_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/sessions/{session_id}", response_model=KnowledgeSessionOut)
def knowledge_session(session_id: str, request: Request) -> KnowledgeSessionOut:
    try:
        return _service(request).get_knowledge_session(session_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/sessions/{session_id}/messages/{message_id}/branch",
    response_model=KnowledgeSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def branch_knowledge_session(
    session_id: str, message_id: str, request: Request
) -> KnowledgeSessionOut:
    try:
        return _service(request).branch_knowledge_session(session_id, message_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/sessions/{session_id}", response_model=KnowledgeSessionSummaryOut)
def update_knowledge_session(
    session_id: str,
    payload: UpdateKnowledgeSessionInput,
    request: Request,
) -> KnowledgeSessionSummaryOut:
    try:
        return _service(request).update_knowledge_session(session_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/sessions/{session_id}/messages", response_model=ConversationTurnOut)
def create_session_message(
    session_id: str,
    payload: CreateSessionMessageInput,
    request: Request,
) -> ConversationTurnOut:
    try:
        return _service(request).create_session_turn(session_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/ask/skills", response_model=list[AskSkillOut])
def list_ask_skills() -> list[AskSkillOut]:
    """The skills the composer's / menu offers."""

    return [
        AskSkillOut(
            command=skill.command,
            title=skill.title,
            description=skill.description,
            budgets=list(skill.budgets),
        )
        for skill in ask_skills().values()
    ]


@router.post("/sessions/{session_id}/brief/refresh", response_model=BriefOut)
def refresh_session_brief(session_id: str, request: Request) -> BriefOut:
    """Bring the conversation's brief up to date with its last answer."""

    try:
        return _service(request).refresh_brief(session_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/sessions/{session_id}/brief", response_model=BriefOut)
def edit_session_brief(session_id: str, payload: Brief, request: Request) -> BriefOut:
    try:
        return _service(request).edit_brief(session_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/sessions/{session_id}/file", response_model=KnowledgeSessionSummaryOut)
def file_home_session(
    session_id: str, payload: FileSessionInput, request: Request
) -> KnowledgeSessionSummaryOut:
    try:
        return _service(request).file_home_session(session_id, payload.knowledge_base_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error




def _answer_runs(request: Request) -> AnswerRuns:
    return request.app.state.answer_runs


def _sse(run: AnswerRun, *, resumed: bool = False):
    async def events():
        async for name, data in run.follow(resumed=resumed):
            yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/sessions/{session_id}/messages/stream")
async def stream_session_message(
    session_id: str,
    payload: CreateSessionMessageInput,
    request: Request,
) -> StreamingResponse:
    """Like posting a message, but as server-sent events while it is answered.

    ``step`` and ``text`` events report the agent at work; ``done`` carries
    the saved turn, ``error`` says why there is none, and ``stopped`` follows
    a stop. Closing the connection only stops listening: the answer is still
    written and saved, and ``GET .../answer/stream`` follows it again. Only
    ``POST .../answer/stop`` ends it early, keeping the question marked stopped.
    """

    service = _service(request)
    researching = (payload.skill or "").strip().lstrip("/") == "research"

    def work(run: AnswerRun) -> None:
        writing = False

        def hear(event: dict) -> None:
            nonlocal writing
            # Research keeps what it found when stopped before it writes (see ``stopping``);
            # every other answer is dropped.
            if run.stop_requested.is_set():
                if writing or not researching:
                    raise Stopped
                if event["type"] == "text":
                    # Too late to end gathering: the answer is already being written.
                    run.stop_requested.clear()
            if event["type"] == "text":
                writing = True
            if event["type"] != "saving":
                run.publish(event["type"], event)

        def stopping() -> bool:
            """Research: the first Stop ends gathering. It is used up, so a second Stop,
            while the answer is being written, drops the answer as for any other."""
            if writing or not run.stop_requested.is_set():
                return False
            run.stop_requested.clear()
            return True

        try:
            turn = (
                service.create_session_turn(session_id, payload, hear, stopping)
                if researching
                else service.create_session_turn(session_id, payload, hear)
            )
            run.publish("done", turn.model_dump(mode="json", by_alias=True))
        except Stopped:
            service.record_interrupted_question(session_id, payload.content, "stopped")
            run.publish("stopped", {"type": "stopped"})
        except LookupError as error:
            run.publish("error", {"status": 404, "detail": str(error)})
        except ValueError as error:
            run.publish("error", {"status": 400, "detail": str(error)})
        except Exception:
            logger.exception("Answering a question failed")
            service.record_interrupted_question(session_id, payload.content, "failed")
            run.publish("error", {"status": 500, "detail": "The answer could not be written."})

    run = _answer_runs(request).start(session_id, payload.content, work)
    if run is None:
        raise HTTPException(
            status_code=409,
            detail="Gunther is still answering the last question here. Wait, or stop it first.",
        )
    return _sse(run)


@router.get("/sessions/{session_id}/answer/stream", response_model=None)
async def follow_session_answer(session_id: str, request: Request) -> Response:
    """Follow an answer still being written: what it did so far, then live. 204 when none is."""

    run = _answer_runs(request).get(session_id)
    if run is None or run.finished:
        return Response(status_code=204)
    return _sse(run, resumed=True)


@router.post("/sessions/{session_id}/answer/stop", status_code=204)
def stop_session_answer(session_id: str, request: Request) -> Response:
    """Stop the answer being written; the question stays in the conversation, marked."""

    _answer_runs(request).stop(session_id)
    return Response(status_code=204)


@router.post(
    "/sessions/{session_id}/messages/{message_id}/proposal",
    response_model=KnowledgeProposalOut,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_proposal(
    session_id: str,
    message_id: str,
    payload: CreateKnowledgeProposalInput,
    request: Request,
) -> KnowledgeProposalOut:
    try:
        return _service(request).create_knowledge_proposal(session_id, message_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get(
    "/knowledge-bases/{knowledge_base_id}/proposals",
    response_model=list[KnowledgeProposalOut],
)
def knowledge_proposals(
    knowledge_base_id: str,
    request: Request,
    proposal_status: Annotated[
        str | None, Query(alias="status", pattern="^(pending|accepted|held|rejected)$")
    ] = None,
) -> list[KnowledgeProposalOut]:
    return _service(request).list_knowledge_proposals(knowledge_base_id, status=proposal_status)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/units",
    response_model=list[KnowledgeUnitOut],
)
def knowledge_units(knowledge_base_id: str, request: Request) -> list[KnowledgeUnitOut]:
    return _service(request).list_knowledge_units(knowledge_base_id)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/artifacts",
    response_model=list[ArtifactSummaryOut],
)
def artifacts(
    knowledge_base_id: str,
    request: Request,
    workspace_id: Annotated[
        str,
        Header(
            alias="X-Gunther-Workspace-Id",
            min_length=1,
            max_length=40,
        ),
    ],
) -> list[ArtifactSummaryOut]:
    try:
        return _service(request).list_artifacts(knowledge_base_id, workspace_id)
    except ArtifactIntegrityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}",
    response_model=ArtifactOut,
)
def artifact(
    knowledge_base_id: str,
    artifact_id: str,
    request: Request,
    workspace_id: Annotated[
        str,
        Header(
            alias="X-Gunther-Workspace-Id",
            min_length=1,
            max_length=40,
        ),
    ],
) -> ArtifactOut:
    try:
        return _service(request).get_artifact(
            knowledge_base_id,
            artifact_id,
            workspace_id,
        )
    except ArtifactIntegrityError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


WorkspaceHeader = Annotated[
    str, Header(alias="X-Gunther-Workspace-Id", min_length=1, max_length=40)
]


def _outputs_key(knowledge_base_id: str) -> str:
    """One output at a time is built in a library."""

    return f"outputs:{knowledge_base_id}"


def _output_run(request: Request, knowledge_base_id: str, label: str, call) -> StreamingResponse:
    """Start building (or revising) an output and stream how it goes.

    ``call`` does the work, given the function that hears each step. The events are the
    agents' progress (``outline``, ``section``, ``step``, ``text``), then ``done`` with
    the saved version, ``error`` saying why there is none, or ``stopped``. Closing the
    connection only stops listening; ``POST .../outputs/build/stop`` ends the work, and
    then nothing is saved.
    """

    def work(run: AnswerRun) -> None:
        def hear(event: dict) -> None:
            if run.stop_requested.is_set():
                raise Stopped
            if event["type"] != "saving":
                run.publish(event["type"], event)

        try:
            artifact = call(hear)
            run.publish("done", artifact.model_dump(mode="json", by_alias=True))
        except Stopped:
            run.publish("stopped", {"type": "stopped"})
        except (ArtifactConflictError, ArtifactIntegrityError) as error:
            run.publish("error", {"status": 409, "detail": str(error)})
        except LookupError as error:
            run.publish("error", {"status": 404, "detail": str(error)})
        except ModelError as error:
            run.publish("error", {"status": 502, "detail": str(error)})
        except ValueError as error:
            run.publish("error", {"status": 400, "detail": str(error)})
        except Exception:
            logger.exception("Building an output failed")
            run.publish("error", {"status": 500, "detail": "The output could not be built."})

    run = _answer_runs(request).start(_outputs_key(knowledge_base_id), label, work)
    if run is None:
        raise HTTPException(
            status_code=409, detail="Gunther is still building an output here."
        )
    return _sse(run)


@router.post("/knowledge-bases/{knowledge_base_id}/outputs/build/stream")
async def build_output_stream(
    knowledge_base_id: str,
    payload: BuildOutputInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> StreamingResponse:
    """Build a report or slides by agents from the library or what was picked."""

    service = _service(request)
    return _output_run(
        request,
        knowledge_base_id,
        payload.brief,
        lambda hear: service.build_output(knowledge_base_id, workspace_id, payload, hear),
    )


@router.post("/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/revise/stream")
async def revise_output_stream(
    knowledge_base_id: str,
    artifact_id: str,
    payload: ReviseOutputInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> StreamingResponse:
    """Change a section (or all) of the latest version as instructed: the next version."""

    service = _service(request)
    return _output_run(
        request,
        knowledge_base_id,
        payload.instruction,
        lambda hear: service.revise_output(
            knowledge_base_id, workspace_id, artifact_id, payload, hear
        ),
    )


@router.get("/knowledge-bases/{knowledge_base_id}/outputs/build/stream", response_model=None)
async def follow_output_build(
    knowledge_base_id: str, request: Request, workspace_id: WorkspaceHeader
) -> Response:
    """Follow an output being built: what it did so far, then live. 204 when none is."""

    run = _answer_runs(request).get(_outputs_key(knowledge_base_id))
    if run is None or run.finished:
        return Response(status_code=204)
    return _sse(run, resumed=True)


@router.post("/knowledge-bases/{knowledge_base_id}/outputs/build/stop", status_code=204)
def stop_output_build(
    knowledge_base_id: str, request: Request, workspace_id: WorkspaceHeader
) -> Response:
    """Stop the output being built. Nothing is saved."""

    _answer_runs(request).stop(_outputs_key(knowledge_base_id))
    return Response(status_code=204)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/edits",
    response_model=ArtifactOut,
    status_code=status.HTTP_201_CREATED,
)
def edit_output(
    knowledge_base_id: str,
    artifact_id: str,
    payload: EditOutputInput,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> ArtifactOut:
    """Save typed text as the next version, exactly as typed."""

    try:
        return _service(request).edit_output(knowledge_base_id, workspace_id, artifact_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ArtifactConflictError, ArtifactIntegrityError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post(
    "/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}/check",
    response_model=ArtifactOut,
)
def check_output(
    knowledge_base_id: str,
    artifact_id: str,
    request: Request,
    workspace_id: WorkspaceHeader,
) -> ArtifactOut:
    """Have the Checker look at the sections it has not checked as they read now."""

    try:
        return _service(request).check_output(knowledge_base_id, workspace_id, artifact_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ArtifactConflictError, ArtifactIntegrityError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ModelError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.patch("/proposals/{proposal_id}", response_model=KnowledgeProposalOut)
def update_knowledge_proposal(
    proposal_id: str,
    payload: UpdateKnowledgeProposalInput,
    request: Request,
) -> KnowledgeProposalOut:
    try:
        return _service(request).update_knowledge_proposal(proposal_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
