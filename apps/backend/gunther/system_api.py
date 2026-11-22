"""Service health, workspace bootstrap, mobile gateway and device pairing routes."""

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from gunther import speech_registry
from gunther.api_common import _service
from gunther.device_auth import (
    WORKSPACE_PROTOCOL_VERSION,
    AuthPrincipal,
    PairedDeviceNotFoundError,
    PairedDeviceView,
    PairingAlreadyUsedError,
    PairingCodeInvalidError,
    PairingExpiredError,
    PairingNotFoundError,
)
from gunther.pairing_exchange_guard import PairingExchangeRejected
from gunther.schemas import (
    CreateDevicePairingInput,
    DeviceCredentialOut,
    DevicePairingSessionOut,
    ExchangeDevicePairingInput,
    HealthOut,
    MobileGatewayStatusOut,
    PairedDeviceOut,
    WorkspaceBootstrapOut,
)
from gunther.transcription import sensevoice_health

router = APIRouter()


PAIRING_EXCHANGE_MAX_BYTES = 16 * 1024


PAIRING_EXCHANGE_READ_TIMEOUT_SECONDS = 5.0


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
    return [_paired_device_out(device) for device in request.app.state.device_auth.list_devices()]


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
