"""Language model providers and jobs, set up in Settings → Models (see model_registry).

The desktop owner sets them up; any signed-in device may list the models that
are ready, to pick one in Ask. Keys never leave the backend.
"""

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from gunther import model_registry
from gunther.device_auth import AuthPrincipal
from gunther.model_profiles import DIALECTS, EFFORT_LABELS, EFFORTS, profile_for
from gunther.model_registry import PRESETS, ROLES, Registry, RegistryError
from gunther.schemas import ApiModel
from gunther.service_settings import ServiceSettingsStore, list_models, secret_hint

router = APIRouter()


class ProviderIn(ApiModel):
    kind: str | None = None
    name: str | None = None
    base_url: str | None = None
    # Left out: keep the saved key. Empty: remove it.
    api_key: str | None = None
    # Each an id, or {id, label, vision, dialect}.
    models: list[Any] | None = None


class RolesIn(ApiModel):
    roles: dict[str, dict[str, Any]]


def _owner_only(request: Request) -> None:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, AuthPrincipal) or principal.kind == "device":
        raise HTTPException(403, "Models can only be set up on the desktop")


def _store(request: Request) -> ServiceSettingsStore:
    return request.app.state.service_settings


def _registry(request: Request) -> Registry:
    return request.app.state.model_registry


def _settings(request: Request):
    return request.app.state.settings


def _levels(dialect_id: str) -> list[dict[str, str]]:
    dialect = DIALECTS[dialect_id]
    return [{"id": level, "label": dialect.name(level)} for level in dialect.levels]


def _key_fingerprint(provider: dict[str, Any], key: str | None) -> str:
    return hashlib.sha256(f"{provider['baseUrl']}\n{key or ''}".encode()).hexdigest()


def _provider_out(provider: dict[str, Any], request: Request) -> dict[str, Any]:
    registry, settings = _registry(request), _settings(request)
    preset = PRESETS[provider["kind"]]
    key, source = model_registry.provider_key(provider, settings)
    if registry.from_environment and source == "saved":
        source = "environment"
    ready = bool(key) or preset.key_optional
    check = _store(request).model_check(provider["id"])
    if check and check.get("fingerprint") != _key_fingerprint(provider, key):
        check = None
    if not ready:
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
    models = []
    for model in provider["models"]:
        built_in = profile_for(provider["kind"], model["id"])
        profile = profile_for(
            provider["kind"], model["id"], dialect=model["dialect"], vision=model["vision"]
        )
        models.append(
            {
                "id": model["id"],
                "ref": f"{provider['id']}/{model['id']}",
                "label": model["label"],
                "vision": profile.vision,
                "visionBuiltIn": built_in.vision,
                "dialect": profile.dialect.id,
                "dialectBuiltIn": built_in.dialect.id,
                "levels": _levels(profile.dialect.id),
            }
        )
    return {
        "id": provider["id"],
        "name": provider["name"],
        "kind": provider["kind"],
        "baseUrl": provider["baseUrl"],
        "keySet": bool(key),
        "keyHint": secret_hint(key),
        "keySource": source,
        "keyOptional": preset.key_optional,
        "note": preset.note,
        "models": models,
        "status": {
            **status,
            "check": check and {k: check[k] for k in ("ok", "message", "warning", "checkedAt")},
        },
    }


def _roles_out(request: Request) -> list[dict[str, Any]]:
    registry, settings = _registry(request), _settings(request)
    infos = {
        info.ref: (info, problem)
        for info, problem in model_registry.model_infos(registry, settings)
    }
    out = []
    for role in ROLES:
        choice = registry.roles.get(role.id, {"model": None, "effort": role.effort})
        found = infos.get(choice["model"] or "")
        if choice["model"] is None:
            problem = "Choose a model."
        elif found is None:
            problem = "Its model was removed. Choose another."
        else:
            problem = found[1]
            if problem is None and role.needs_vision and not found[0].vision:
                problem = f"{found[0].label} cannot see images; photos will be read as text."
        out.append(
            {
                "id": role.id,
                "label": role.label,
                "description": role.description,
                "needsVision": role.needs_vision,
                "model": choice["model"],
                "effort": choice["effort"],
                "problem": problem,
            }
        )
    return out


def models_overview(request: Request) -> dict[str, Any]:
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
        "dialects": [
            {"id": dialect.id, "label": dialect.label, "levels": _levels(dialect.id)}
            for dialect in DIALECTS.values()
        ],
        "efforts": [{"id": effort, "label": EFFORT_LABELS[effort]} for effort in EFFORTS],
    }


def _save(request: Request, providers: list[dict[str, Any]] | None = None, roles=None) -> None:
    try:
        _store(request).save_models(providers=providers, roles=roles)
    except OSError as error:
        raise HTTPException(503, "The settings could not be saved to disk") from error


def _find(request: Request, provider_id: str) -> tuple[list[dict[str, Any]], int]:
    providers = [dict(provider) for provider in _registry(request).providers]
    for index, provider in enumerate(providers):
        if provider["id"] == provider_id:
            return providers, index
    raise HTTPException(404, "There is no such provider")


# Routes ------------------------------------------------------------------------


@router.get("/models")
def list_ready_models(request: Request) -> dict[str, Any]:
    """The models Ask can use now, for the model menu. No keys, no addresses."""

    models = request.app.state.models
    ask = models.for_role("ask")
    return {
        "models": [
            {
                "ref": info.ref,
                "provider": info.provider_name,
                "label": info.label,
                "display": info.display,
                "vision": info.vision,
                "levels": _levels(info.profile.dialect.id),
            }
            for info in models.models
        ],
        "default": {"model": ask[0].ref, "effort": ask[1]} if ask else None,
        "efforts": [{"id": effort, "label": EFFORT_LABELS[effort]} for effort in EFFORTS],
    }


@router.get("/settings/models")
def get_models(request: Request) -> dict[str, Any]:
    _owner_only(request)
    return models_overview(request)


@router.post("/settings/providers", status_code=201)
def create_provider(payload: ProviderIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    providers = [dict(provider) for provider in _registry(request).providers]
    preset = PRESETS.get(payload.kind or "")
    if preset is None:
        raise HTTPException(422, "Choose a kind of provider.")
    raw = {
        "kind": payload.kind,
        "name": payload.name or _unique_name(preset.name, providers),
        "baseUrl": payload.base_url or preset.base_url,
        "apiKey": payload.api_key,
        "models": payload.models if payload.models is not None else list(preset.models),
    }
    try:
        provider = model_registry.clean_provider(raw, {p["id"] for p in providers})
    except RegistryError as error:
        raise HTTPException(422, str(error)) from error
    providers.append(provider)
    _save(request, providers=providers, roles=_roles_after(request, providers))
    return models_overview(request)


def _unique_name(name: str, providers: list[dict[str, Any]]) -> str:
    names = {provider["name"] for provider in providers}
    if name not in names:
        return name
    number = 2
    while f"{name} {number}" in names:
        number += 1
    return f"{name} {number}"


def _roles_after(request: Request, providers: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep every job's choice; a job without a usable model takes the first one."""

    current = _registry(request).roles
    fallback = model_registry.default_roles(providers)
    refs = {f"{p['id']}/{m['id']}" for p in providers for m in p["models"]}
    return {
        role.id: current.get(role.id)
        if (current.get(role.id) or {}).get("model") in refs
        else {**fallback[role.id], "effort": (current.get(role.id) or fallback[role.id])["effort"]}
        for role in ROLES
    }


@router.put("/settings/providers/{provider_id}")
def update_provider(provider_id: str, payload: ProviderIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    providers, index = _find(request, provider_id)
    current = providers[index]
    changes = payload.model_dump(exclude_unset=True, by_alias=False)
    raw = {
        **current,
        "name": changes.get("name", current["name"]),
        "baseUrl": changes.get("base_url", current["baseUrl"]),
        "apiKey": current["apiKey"] if "api_key" not in changes else changes["api_key"],
        "models": changes["models"] if changes.get("models") is not None else current["models"],
    }
    try:
        providers[index] = model_registry.clean_provider(raw)
    except RegistryError as error:
        raise HTTPException(422, str(error)) from error
    _save(request, providers=providers, roles=_roles_after(request, providers))
    return models_overview(request)


@router.delete("/settings/providers/{provider_id}")
def delete_provider(provider_id: str, request: Request) -> dict[str, Any]:
    _owner_only(request)
    providers, index = _find(request, provider_id)
    del providers[index]
    _save(request, providers=providers, roles=_roles_after(request, providers))
    return models_overview(request)


@router.put("/settings/model-roles")
def save_roles(payload: RolesIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    try:
        changes = model_registry.clean_roles(payload.roles)
    except RegistryError as error:
        raise HTTPException(422, str(error)) from error
    registry = _registry(request)
    refs = {f"{p['id']}/{m['id']}" for p in registry.providers for m in p["models"]}
    for role_id, choice in changes.items():
        if choice["model"] is not None and choice["model"] not in refs:
            raise HTTPException(
                422, f"{model_registry.ROLE_BY_ID[role_id].label}: that model is not set up."
            )
    roles = {**registry.roles, **changes}
    # Saving jobs keeps the providers in use, even ones that came from the environment.
    _save(request, providers=[dict(p) for p in registry.providers], roles=roles)
    return models_overview(request)


async def _test(request: Request, provider: dict[str, Any]) -> dict[str, Any]:
    key, _ = model_registry.provider_key(provider, _settings(request))
    optional = PRESETS[provider["kind"]].key_optional
    result, available = await list_models(
        provider["baseUrl"], key, provider["name"], key_optional=optional
    )
    chosen = [model["id"] for model in provider["models"]]
    missing = [model for model in chosen if available and model not in available]
    warning = result.warning
    if result.ok and missing:
        warning = f"{', '.join(missing)} is not in this account's model list."
    return {
        "ok": result.ok,
        "message": result.message,
        "warning": warning,
        "available": available,
        "fingerprint": _key_fingerprint(provider, key),
    }


@router.post("/settings/providers/test")
async def test_new_provider(payload: ProviderIn, request: Request) -> dict[str, Any]:
    """Try a provider before adding it; lists the models it offers."""

    _owner_only(request)
    preset = PRESETS.get(payload.kind or "")
    if preset is None:
        raise HTTPException(422, "Choose a kind of provider.")
    try:
        provider = model_registry.clean_provider(
            {
                "kind": payload.kind,
                "name": payload.name or preset.name,
                "baseUrl": payload.base_url or preset.base_url,
                "apiKey": payload.api_key,
                "models": payload.models or [],
            }
        )
    except RegistryError as error:
        raise HTTPException(422, str(error)) from error
    outcome = await _test(request, provider)
    outcome.pop("fingerprint")
    return outcome


@router.post("/settings/providers/{provider_id}/test")
async def test_provider(provider_id: str, payload: ProviderIn, request: Request) -> dict[str, Any]:
    """Try a provider with the values on screen; a saved one's result is kept."""

    _owner_only(request)
    providers, index = _find(request, provider_id)
    current = providers[index]
    changes = payload.model_dump(exclude_unset=True)
    draft = {
        **current,
        "baseUrl": changes.get("base_url") or current["baseUrl"],
        "apiKey": changes["api_key"] if changes.get("api_key") else current["apiKey"],
    }
    try:
        draft = model_registry.clean_provider(draft)
    except RegistryError as error:
        raise HTTPException(422, str(error)) from error
    outcome = await _test(request, draft)
    _store(request).record_model_check(
        provider_id, {k: outcome[k] for k in ("ok", "message", "warning", "fingerprint")}
    )
    outcome.pop("fingerprint")
    return {**outcome, "overview": models_overview(request)}
