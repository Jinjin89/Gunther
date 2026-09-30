"""Transcription providers and jobs, set up in Settings → Transcription (see speech_registry).

The desktop owner sets them up. Keys never leave the backend.
"""

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from gunther import speech_registry
from gunther.device_auth import AuthPrincipal
from gunther.realtime import sensevoice_health
from gunther.schemas import ApiModel
from gunther.service_settings import ServiceSettingsStore, list_models, secret_hint
from gunther.speech_registry import PRESETS, ROLES, Registry, SpeechError

router = APIRouter()


class ProviderIn(ApiModel):
    kind: str | None = None
    name: str | None = None
    base_url: str | None = None
    # Left out: keep the saved key. Empty: remove it.
    api_key: str | None = None
    # Each an id, or {id, label}.
    models: list[Any] | None = None


class RolesIn(ApiModel):
    roles: dict[str, dict[str, Any]]


def _owner_only(request: Request) -> None:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, AuthPrincipal) or principal.kind == "device":
        raise HTTPException(403, "Transcription can only be set up on the desktop")


def _store(request: Request) -> ServiceSettingsStore:
    return request.app.state.service_settings


def _registry(request: Request) -> Registry:
    return request.app.state.speech_registry


def _fingerprint(provider: dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{provider['baseUrl']}\n{provider.get('apiKey') or ''}".encode()
    ).hexdigest()


def _provider_out(provider: dict[str, Any], request: Request) -> dict[str, Any]:
    registry = _registry(request)
    preset = PRESETS[provider["kind"]]
    key = provider.get("apiKey")
    check = _store(request).speech_check(provider["id"])
    if check and check.get("fingerprint") != _fingerprint(provider):
        check = None
    problem = speech_registry.problem_with(provider)
    if problem:
        status = {"state": "not_configured", "summary": "Needs an API key."}
    elif check and not check["ok"]:
        status = {"state": "error", "summary": check["message"]}
    else:
        count = len(provider["models"])
        status = {
            "state": "configured" if count else "not_configured",
            "summary": f"{count} model{'s' if count != 1 else ''}"
            if count
            else "Add a model to use it.",
        }
    return {
        "id": provider["id"],
        "name": provider["name"],
        "kind": provider["kind"],
        "baseUrl": provider["baseUrl"],
        "keySet": bool(key),
        "keyHint": secret_hint(key),
        "keySource": "environment" if registry.from_environment and key else "saved",
        "keyOptional": preset.key_optional,
        "note": preset.note,
        "models": [
            {
                "id": model["id"],
                "ref": speech_registry.model_ref(provider, model),
                "label": model["label"],
            }
            for model in provider["models"]
        ],
        "status": {
            **status,
            "check": check and {k: check[k] for k in ("ok", "message", "warning", "checkedAt")},
        },
    }


def _roles_out(request: Request) -> list[dict[str, Any]]:
    registry = _registry(request)
    out = []
    for role in ROLES:
        choice = registry.roles.get(role.id) or {}
        resolved, problem = speech_registry.resolve(registry, role.id)
        out.append(
            {
                "id": role.id,
                "label": role.label,
                "description": role.description,
                "model": choice.get("model"),
                "stream": bool(choice.get("stream", role.stream)),
                "language": choice.get("language") or "",
                "problem": None if resolved else problem,
            }
        )
    return out


def speech_overview(request: Request) -> dict[str, Any]:
    registry = _registry(request)
    return {
        "persisted": _store(request).persisted,
        "fromEnvironment": registry.from_environment,
        "providers": [_provider_out(provider, request) for provider in registry.providers],
        "roles": _roles_out(request),
        "presets": [
            {
                "kind": kind,
                "name": preset.name,
                "baseUrl": preset.base_url,
                "models": list(preset.models),
                "keyOptional": preset.key_optional,
                "note": preset.note,
            }
            for kind, preset in PRESETS.items()
        ],
    }


def _save(request: Request, providers: list[dict[str, Any]] | None = None, roles=None) -> None:
    try:
        _store(request).save_speech(providers=providers, roles=roles)
    except OSError as error:
        raise HTTPException(503, "The settings could not be saved to disk") from error


def _find(request: Request, provider_id: str) -> tuple[list[dict[str, Any]], int]:
    providers = [dict(provider) for provider in _registry(request).providers]
    for index, provider in enumerate(providers):
        if provider["id"] == provider_id:
            return providers, index
    raise HTTPException(404, "There is no such provider")


def _unique_name(name: str, providers: list[dict[str, Any]]) -> str:
    taken = {provider["name"] for provider in providers}
    if name not in taken:
        return name
    number = 2
    while f"{name} {number}" in taken:
        number += 1
    return f"{name} {number}"


def _roles_after(request: Request, providers: list[dict[str, Any]]) -> dict[str, Any]:
    """Jobs whose model went away move to the first model left."""

    refs = [speech_registry.model_ref(p, m) for p in providers for m in p["models"]]
    roles = {}
    for role_id, choice in _registry(request).roles.items():
        if choice.get("model") not in refs:
            choice = {**choice, "model": refs[0] if refs else None}
        roles[role_id] = choice
    return roles


# Routes ------------------------------------------------------------------------


@router.get("/settings/speech")
def get_speech(request: Request) -> dict[str, Any]:
    _owner_only(request)
    return speech_overview(request)


@router.post("/settings/speech/providers", status_code=201)
def create_provider(payload: ProviderIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    preset = PRESETS.get(payload.kind or "")
    if preset is None:
        raise HTTPException(422, "Choose a kind of provider.")
    registry = _registry(request)
    providers = [dict(provider) for provider in registry.providers]
    try:
        provider = speech_registry.clean_provider(
            {
                "kind": payload.kind,
                "name": _unique_name(payload.name or preset.name, providers),
                "baseUrl": payload.base_url or preset.base_url,
                "apiKey": payload.api_key,
                "models": payload.models if payload.models is not None else list(preset.models),
            },
            {provider["id"] for provider in providers},
        )
    except SpeechError as error:
        raise HTTPException(422, str(error)) from error
    providers.append(provider)
    roles = _roles_after(request, providers)
    _save(request, providers=providers, roles=roles)
    return speech_overview(request)


@router.put("/settings/speech/providers/{provider_id}")
def update_provider(provider_id: str, payload: ProviderIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    providers, index = _find(request, provider_id)
    current = providers[index]
    changes = payload.model_dump(exclude_unset=True)
    draft = {**current}
    if "name" in changes:
        draft["name"] = changes["name"]
    if "base_url" in changes:
        draft["baseUrl"] = changes["base_url"]
    if "api_key" in changes:
        draft["apiKey"] = changes["api_key"]
    if "models" in changes:
        draft["models"] = changes["models"]
    try:
        providers[index] = speech_registry.clean_provider(draft)
    except SpeechError as error:
        raise HTTPException(422, str(error)) from error
    _save(request, providers=providers, roles=_roles_after(request, providers))
    return speech_overview(request)


@router.delete("/settings/speech/providers/{provider_id}")
def delete_provider(provider_id: str, request: Request) -> dict[str, Any]:
    _owner_only(request)
    providers, index = _find(request, provider_id)
    del providers[index]
    _save(request, providers=providers, roles=_roles_after(request, providers))
    return speech_overview(request)


@router.put("/settings/speech/roles")
def save_roles(payload: RolesIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    try:
        changes = speech_registry.clean_roles(payload.roles)
    except SpeechError as error:
        raise HTTPException(422, str(error)) from error
    registry = _registry(request)
    refs = {speech_registry.model_ref(p, m) for p in registry.providers for m in p["models"]}
    for role_id, choice in changes.items():
        if choice["model"] is not None and choice["model"] not in refs:
            raise HTTPException(
                422, f"{speech_registry.ROLE_BY_ID[role_id].label}: that model is not set up."
            )
    roles = {**registry.roles, **changes}
    # Saving jobs keeps the providers in use, even ones that came from the environment.
    _save(request, providers=[dict(p) for p in registry.providers], roles=roles)
    return speech_overview(request)


async def _test(provider: dict[str, Any]) -> dict[str, Any]:
    if provider["kind"] == "sensevoice":
        status = await sensevoice_health(provider["baseUrl"], timeout=4.0)
        if status is None:
            return {
                "ok": False,
                "message": f"SenseVoice is not answering at {provider['baseUrl']}. Is it running?",
                "warning": None,
                "available": [],
            }
        model = str(status.get("model", "sensevoice-small"))
        return {
            "ok": True,
            "message": f"SenseVoice is running ({model}).",
            "warning": None,
            "available": [model],
        }
    optional = PRESETS[provider["kind"]].key_optional
    result, available = await list_models(
        provider["baseUrl"], provider.get("apiKey"), provider["name"], key_optional=optional
    )
    chosen = [model["id"] for model in provider["models"]]
    missing = [model for model in chosen if available and model not in available]
    warning = result.warning
    if result.ok and missing:
        warning = f"{', '.join(missing)} is not in this account's model list."
    return {"ok": result.ok, "message": result.message, "warning": warning, "available": available}


@router.post("/settings/speech/providers/{provider_id}/test")
async def test_provider(provider_id: str, payload: ProviderIn, request: Request) -> dict[str, Any]:
    """Try a provider with the values on screen; a saved one's result is kept."""

    _owner_only(request)
    providers, index = _find(request, provider_id)
    current = providers[index]
    changes = payload.model_dump(exclude_unset=True)
    draft = {
        **current,
        "baseUrl": changes.get("base_url") or current["baseUrl"],
        "apiKey": changes["api_key"] if changes.get("api_key") else current.get("apiKey"),
    }
    try:
        draft = speech_registry.clean_provider(draft)
    except SpeechError as error:
        raise HTTPException(422, str(error)) from error
    outcome = await _test(draft)
    entry = {k: outcome[k] for k in ("ok", "message", "warning")}
    _store(request).record_speech_check(
        provider_id, {**entry, "fingerprint": _fingerprint(draft)}
    )
    return {**outcome, "overview": speech_overview(request)}
