"""Read-aloud settings and audio (see tts_service). The desktop owner only."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from gunther import tts_providers
from gunther.device_auth import AuthPrincipal
from gunther.schemas import ApiModel
from gunther.service_settings import ServiceSettingsStore, secret_hint
from gunther.tts_providers import PROVIDERS, TtsError
from gunther.tts_service import (
    Clip,
    SpeechNotFound,
    TtsConfigError,
    TtsService,
    clean_config,
    clean_provider_entry,
    default_config,
    resolve,
)

router = APIRouter()


class ProviderIn(ApiModel):
    # Left out: keep the saved key. Empty: remove it.
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    options: dict[str, str] | None = None


class TtsIn(ApiModel):
    active: str | None = None
    auto_read: bool | None = None
    # Which supplier `provider` edits; the active one when left out.
    kind: str | None = None
    provider: ProviderIn | None = None


def _owner_only(request: Request) -> None:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, AuthPrincipal) or principal.kind == "device":
        raise HTTPException(403, "Reading aloud can only be set up on the desktop")


def _store(request: Request) -> ServiceSettingsStore:
    return request.app.state.service_settings


def _service(request: Request) -> TtsService:
    return request.app.state.tts


def _registries(request: Request) -> tuple[Any, Any]:
    state = request.app.state
    return state.model_registry, state.speech_registry


def _saved(request: Request) -> dict[str, Any]:
    return _store(request).tts() or default_config()


def _clip_out(clip: Clip) -> dict[str, Any]:
    return {
        "id": clip.id,
        "messageId": clip.message_id,
        "durationSeconds": clip.duration_seconds,
        "bytes": clip.audio_bytes,
        "cached": clip.cached,
        "describedBy": clip.described_by,
        "provider": clip.provider,
        "voice": clip.voice,
    }


def overview(request: Request) -> dict[str, Any]:
    saved = _saved(request)
    registries = _registries(request)
    providers = []
    for kind, provider in PROVIDERS.items():
        entry = saved["providers"].get(kind) or clean_provider_entry(provider, {})
        key = entry.get("apiKey")
        resolved, problem = resolve(saved, *registries, kind=kind)
        providers.append(
            {
                **tts_providers.describe(provider),
                "baseUrl": entry["baseUrl"],
                "model": entry["model"],
                "values": entry["options"],
                "keySet": bool(key),
                "keyHint": secret_hint(key),
                "keyShared": bool(resolved and resolved.key_shared),
                "problem": problem,
            }
        )
    active = saved.get("active")
    _, active_problem = resolve(saved, *registries)
    return {
        "persisted": _store(request).persisted,
        "active": active,
        "autoRead": bool(saved.get("autoRead")),
        # Null: ready to read. Otherwise the reason, for the speaker button to show.
        "problem": active_problem,
        "providers": providers,
        "cache": _service(request).stats(),
    }


@router.get("/settings/tts")
def get_tts(request: Request) -> dict[str, Any]:
    _owner_only(request)
    return overview(request)


def _merged(request: Request, payload: TtsIn) -> dict[str, Any]:
    saved = _saved(request)
    config = {**saved, "providers": {k: dict(v) for k, v in saved["providers"].items()}}
    if "active" in payload.model_fields_set:
        config["active"] = payload.active
    if payload.auto_read is not None:
        config["autoRead"] = payload.auto_read
    if payload.provider is not None:
        kind = payload.kind or config.get("active")
        if kind not in PROVIDERS:
            raise HTTPException(422, "Choose a supplier first.")
        changes = payload.provider.model_dump(exclude_unset=True)
        entry = dict(config["providers"].get(kind) or clean_provider_entry(PROVIDERS[kind], {}))
        if "api_key" in changes:
            entry["apiKey"] = changes["api_key"]
        if changes.get("base_url"):
            entry["baseUrl"] = changes["base_url"]
        if changes.get("model"):
            entry["model"] = changes["model"]
        if changes.get("options") is not None:
            entry["options"] = {**entry.get("options", {}), **changes["options"]}
        config["providers"][kind] = entry
    return config


@router.put("/settings/tts")
def save_tts(payload: TtsIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    try:
        config = clean_config(_merged(request, payload))
    except TtsConfigError as error:
        raise HTTPException(422, str(error)) from error
    try:
        _store(request).save_tts(config)
    except OSError as error:
        raise HTTPException(503, "The settings could not be saved to disk") from error
    return overview(request)


@router.post("/settings/tts/sample")
async def sample(payload: TtsIn, request: Request) -> Response:
    """A short line spoken with the settings on screen, saved or not: hear before you keep."""

    _owner_only(request)
    try:
        config = clean_config(_merged(request, payload))
    except TtsConfigError as error:
        raise HTTPException(422, str(error)) from error
    kind = payload.kind or config["active"]
    resolved, problem = resolve(config, *_registries(request), kind=kind)
    if resolved is None:
        raise HTTPException(422, problem)
    try:
        audio = await _service(request).sample(resolved)
    except TtsError as error:
        raise HTTPException(502, str(error)) from error
    return Response(audio, media_type="audio/wav")


@router.delete("/settings/tts/cache")
def clear_cache(request: Request) -> dict[str, Any]:
    _owner_only(request)
    _service(request).clear()
    return overview(request)


@router.post("/sessions/{session_id}/messages/{message_id}/speech")
async def speak_message(session_id: str, message_id: str, request: Request) -> dict[str, Any]:
    """The audio of an answer: made the first time, found after that."""

    _owner_only(request)
    resolved, problem = resolve(_saved(request), *_registries(request))
    if resolved is None:
        raise HTTPException(409, problem)
    try:
        clip = await _service(request).speak(
            session_id, message_id, resolved, request.app.state.models
        )
    except SpeechNotFound as error:
        raise HTTPException(404, str(error)) from error
    except TtsError as error:
        raise HTTPException(502, str(error)) from error
    return _clip_out(clip)


@router.get("/speech/clips/{clip_id}/audio")
def clip_audio(clip_id: str, request: Request) -> FileResponse:
    _owner_only(request)
    try:
        path = _service(request).audio_path(clip_id)
    except SpeechNotFound as error:
        raise HTTPException(404, str(error)) from error
    return FileResponse(path, media_type="audio/wav")


@router.get("/speech/clips/{clip_id}/script")
def clip_script(clip_id: str, request: Request) -> dict[str, str]:
    """What was spoken, for people who want to check how a table was described."""

    _owner_only(request)
    try:
        return {"script": _service(request).script_of(clip_id)}
    except SpeechNotFound as error:
        raise HTTPException(404, str(error)) from error
