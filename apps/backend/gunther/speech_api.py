"""Transcription providers and jobs, set up in Settings → Transcription (see speech_registry).

The desktop owner sets them up. Keys never leave the backend.
"""

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from gunther import speech_registry
from gunther.provider_settings import (
    edit_provider,
    find_provider,
    provider_fingerprint,
    unique_provider_name,
)
from gunther.realtime import try_qwen_live
from gunther.schemas import ApiModel
from gunther.service_settings import list_models, secret_hint
from gunther.settings_api_common import require_desktop_owner, service_settings
from gunther.speech_registry import PRESETS, ROLES, Registry, SpeechError
from gunther.transcription import qwen_root, sensevoice_health, try_qwen_model

OWNER_REQUIRED = "Transcription can only be set up on the desktop"
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


def _registry(request: Request) -> Registry:
    return request.app.state.speech_registry


def _provider_out(provider: dict[str, Any], request: Request) -> dict[str, Any]:
    registry = _registry(request)
    preset = PRESETS[provider["kind"]]
    key = provider.get("apiKey")
    effective_key, shared = speech_registry.key_for(provider, registry.shared)
    check = service_settings(request).provider_check(
        "speech", provider["id"], provider_fingerprint(provider, effective_key)
    )
    problem = speech_registry.problem_with(provider, registry.shared)
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
        # No key of its own: the one saved for the same company under Models is used.
        "keyShared": shared,
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
            "check": check,
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
                **(
                    {"checkAgain": speech_registry.check_again(registry)}
                    if role.id == "recording"
                    else {}
                ),
            }
        )
    return out


def speech_overview(request: Request) -> dict[str, Any]:
    registry = _registry(request)
    return {
        "persisted": service_settings(request).persisted,
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
        service_settings(request).save_speech(providers=providers, roles=roles)
    except OSError as error:
        raise HTTPException(503, "The settings could not be saved to disk") from error


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
    require_desktop_owner(request, OWNER_REQUIRED)
    return speech_overview(request)


@router.post("/settings/speech/providers", status_code=201)
def create_provider(payload: ProviderIn, request: Request) -> dict[str, Any]:
    require_desktop_owner(request, OWNER_REQUIRED)
    preset = PRESETS.get(payload.kind or "")
    if preset is None:
        raise HTTPException(422, "Choose a kind of provider.")
    registry = _registry(request)
    providers = [dict(provider) for provider in registry.providers]
    try:
        provider = speech_registry.clean_provider(
            {
                "kind": payload.kind,
                "name": unique_provider_name(payload.name or preset.name, providers),
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
    require_desktop_owner(request, OWNER_REQUIRED)
    providers, index = find_provider(_registry(request).providers, provider_id)
    current = providers[index]
    changes = payload.model_dump(exclude_unset=True)
    draft = edit_provider(current, changes)
    try:
        providers[index] = speech_registry.clean_provider(draft)
    except SpeechError as error:
        raise HTTPException(422, str(error)) from error
    _save(request, providers=providers, roles=_roles_after(request, providers))
    return speech_overview(request)


@router.delete("/settings/speech/providers/{provider_id}")
def delete_provider(provider_id: str, request: Request) -> dict[str, Any]:
    require_desktop_owner(request, OWNER_REQUIRED)
    providers, index = find_provider(_registry(request).providers, provider_id)
    del providers[index]
    _save(request, providers=providers, roles=_roles_after(request, providers))
    return speech_overview(request)


@router.put("/settings/speech/roles")
def save_roles(payload: RolesIn, request: Request) -> dict[str, Any]:
    require_desktop_owner(request, OWNER_REQUIRED)
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
    for role_id, choice in changes.items():
        # A save that leaves the switch out keeps what was set.
        previous = (registry.roles.get(role_id) or {}).get("check_again")
        if role_id == "recording" and "check_again" not in choice and previous is not None:
            choice["check_again"] = previous
    roles = {**registry.roles, **changes}
    # Saving jobs keeps the providers in use, even ones that came from the environment.
    _save(request, providers=[dict(p) for p in registry.providers], roles=roles)
    return speech_overview(request)


async def _test(provider: dict[str, Any], api_key: str | None) -> dict[str, Any]:
    if provider["kind"] == "sensevoice":
        status = await sensevoice_health(provider["baseUrl"], timeout=4.0)
        if status is None:
            return {
                "ok": False,
                "message": f"SenseVoice is not answering at {provider['baseUrl']}. Is it running?",
                "warning": None,
            }
        model = str(status.get("model", "sensevoice-small"))
        return {
            "ok": True,
            "message": f"SenseVoice is running ({model}).",
            "warning": None,
        }
    preset = PRESETS[provider["kind"]]
    # Qwen lists a key's models on its OpenAI-compatible address, apart from where it transcribes.
    address = (
        f"{qwen_root(provider['baseUrl'])}/compatible-mode/v1"
        if provider["kind"] == "qwen"
        else provider["baseUrl"]
    )
    result, listed = await list_models(
        address, api_key, provider["name"], key_optional=preset.key_optional
    )
    chosen = [model["id"] for model in provider["models"]]
    warning = result.warning
    if result.ok and provider["kind"] == "qwen" and chosen and api_key:
        # Its list leaves out the Qwen-Audio recognisers: ask each model instead. A model
        # is a pair (upload and live ids): the upload half must work; the live half
        # only decides whether recording can write live sentences.
        problems: list[str | None] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for model in chosen:
                problem = await try_qwen_model(client, provider["baseUrl"], api_key, model)
                if problem is None and await try_qwen_live(provider["baseUrl"], api_key, model):
                    problem = (
                        f"{model}: live text isn't available for this model; "
                        "recording will send short pieces instead."
                    )
                problems.append(problem)
        found = [problem for problem in problems if problem]
        if found:
            warning = " ".join(dict.fromkeys(found))
        return {"ok": result.ok, "message": result.message, "warning": warning}
    missing = [model for model in chosen if listed and model not in listed]
    if result.ok and missing:
        warning = f"{', '.join(missing)} is not in this account's model list."
    return {"ok": result.ok, "message": result.message, "warning": warning}


@router.post("/settings/speech/providers/{provider_id}/test")
async def test_provider(provider_id: str, payload: ProviderIn, request: Request) -> dict[str, Any]:
    """Try a provider with the values on screen; a saved one's result is kept."""

    require_desktop_owner(request, OWNER_REQUIRED)
    providers, index = find_provider(_registry(request).providers, provider_id)
    current = providers[index]
    changes = payload.model_dump(exclude_unset=True)
    draft = edit_provider(current, changes)
    try:
        draft = speech_registry.clean_provider(draft)
    except SpeechError as error:
        raise HTTPException(422, str(error)) from error
    effective_key = speech_registry.key_for(draft, _registry(request).shared)[0]
    outcome = await _test(draft, effective_key)
    entry = {k: outcome[k] for k in ("ok", "message", "warning")}
    service_settings(request).record_provider_check(
        "speech", provider_id, {**entry, "fingerprint": provider_fingerprint(draft, effective_key)}
    )
    return {**outcome, "overview": speech_overview(request)}
