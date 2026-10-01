"""Read-aloud providers, jobs and audio (see tts_service). The desktop owner only.

Laid out like transcription (see speech_api): providers are added, edited and
removed on their own, and each job picks a model and its voice. Keys never
leave the backend.
"""

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from gunther import tts_providers
from gunther.device_auth import AuthPrincipal
from gunther.schemas import ApiModel
from gunther.service_settings import ServiceSettingsStore, secret_hint
from gunther.tts_providers import PROVIDERS, TtsError, repair_wav
from gunther.tts_service import (
    ROLES,
    Clip,
    SpeechNotFound,
    TtsConfigError,
    TtsService,
    clean_config,
    clean_provider,
    clean_role,
    find_model,
    key_for,
    model_ref,
    problem_with,
    providers_in_use,
    resolve,
    resolve_model,
)

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


class SampleIn(ApiModel):
    """A short line spoken with the values on screen, saved or not."""

    # A provider being edited, with its unsaved address and key; the Answers job when left out.
    provider_id: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    options: dict[str, str] | None = None


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


def _config(request: Request) -> dict[str, Any]:
    try:
        return clean_config(_store(request).tts())
    except TtsConfigError:
        # Hand-edited settings Gunther cannot read: start over rather than break Settings.
        return clean_config(None)


def _providers(request: Request) -> list[dict[str, Any]]:
    return [dict(p) for p in providers_in_use(_config(request), *_registries(request))]


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


def _fingerprint(provider: dict[str, Any], registries: tuple[Any, Any]) -> str:
    """Address and key a check was made with: changing either makes the result stale."""

    key = key_for(provider, *registries)[0] or ""
    return hashlib.sha256(f"{provider['baseUrl']}\n{key}".encode()).hexdigest()


def _record(request: Request, provider: dict[str, Any], error: TtsError | None) -> None:
    """Remember that a provider spoke (or why it did not), so Settings can say Connected."""

    entry = {"ok": error is None, "message": str(error) if error else "It spoke.", "warning": None}
    _store(request).record_tts_check(
        provider["id"], {**entry, "fingerprint": _fingerprint(provider, _registries(request))}
    )


def _provider_out(
    provider: dict[str, Any], registries: tuple[Any, Any], check: dict[str, Any] | None
) -> dict[str, Any]:
    supplier = PROVIDERS[provider["kind"]]
    key, shared = key_for(provider, *registries)
    problem = problem_with(provider, *registries)
    count = len(provider["models"])
    if check and check.get("fingerprint") != _fingerprint(provider, registries):
        check = None
    if problem:
        status = {"state": "not_configured", "summary": "Needs an API key."}
    elif check and not check["ok"]:
        status = {"state": "error", "summary": check["message"]}
    elif count == 0:
        status = {"state": "not_configured", "summary": "Add a model to use it."}
    else:
        status = {"state": "configured", "summary": f"{count} model{'s' if count != 1 else ''}"}
    own_key = provider.get("apiKey")
    return {
        "id": provider["id"],
        "name": provider["name"],
        "kind": provider["kind"],
        "baseUrl": provider["baseUrl"],
        "keySet": bool(own_key),
        "keyHint": secret_hint(own_key),
        "keyShared": shared and bool(key),
        "keyOptional": supplier.key_optional,
        "readsStructure": supplier.reads_structure,
        "note": supplier.note,
        "models": [
            {"id": model["id"], "ref": model_ref(provider, model), "label": model["label"]}
            for model in provider["models"]
        ],
        "status": {
            **status,
            "check": check and {k: check[k] for k in ("ok", "message", "warning", "checkedAt")},
        },
    }


def overview(request: Request) -> dict[str, Any]:
    config = _config(request)
    registries = _registries(request)
    providers = providers_in_use(config, *registries)
    roles = []
    for role in ROLES:
        choice = config["roles"].get(role.id) or {}
        _, problem = resolve(config, *registries, role=role.id)
        found = find_model(providers, choice.get("model"))
        roles.append(
            {
                "id": role.id,
                "label": role.label,
                "description": role.description,
                "model": choice.get("model"),
                # The voice and language of the chosen model's supplier.
                "kind": found[0]["kind"] if found else None,
                "options": choice.get("options") or {},
                "autoRead": bool(choice.get("autoRead")),
                "problem": problem if choice.get("model") else None,
            }
        )
    answers = next(role for role in roles if role["id"] == "answers")
    _, answers_problem = resolve(config, *registries)
    return {
        "persisted": _store(request).persisted,
        # For the Listen button: read by itself, and why it cannot read (null when it can).
        "autoRead": answers["autoRead"],
        "problem": answers_problem,
        "providers": [
            _provider_out(provider, registries, _store(request).tts_check(provider["id"]))
            for provider in providers
        ],
        "roles": roles,
        "presets": [tts_providers.describe(supplier) for supplier in PROVIDERS.values()],
        "cache": _service(request).stats(),
    }


def _save(
    request: Request, providers: list[dict[str, Any]], roles: dict[str, Any] | None = None
) -> None:
    config = _config(request)
    try:
        cleaned = clean_config(
            {"providers": providers, "roles": config["roles"] if roles is None else roles}
        )
    except TtsConfigError as error:
        raise HTTPException(422, str(error)) from error
    try:
        _store(request).save_tts(cleaned)
    except OSError as error:
        raise HTTPException(503, "The settings could not be saved to disk") from error


def _find(request: Request, provider_id: str) -> tuple[list[dict[str, Any]], int]:
    providers = _providers(request)
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
    """A job whose model went away moves to the first model left, or is turned off.

    Its voice is kept when the new model comes from the same supplier.
    """

    roles = {}
    for role_id, choice in _config(request)["roles"].items():
        before = find_model(_providers(request), choice.get("model"))
        if choice.get("model") and find_model(providers, choice["model"]) is None:
            refs = [(p, model_ref(p, m)) for p in providers for m in p["models"]]
            if refs:
                provider, ref = refs[0]
                same_supplier = before is not None and before[0]["kind"] == provider["kind"]
                choice = {
                    **choice,
                    "model": ref,
                    "options": choice.get("options") if same_supplier else {},
                }
            else:
                choice = {**choice, "model": None, "options": {}}
        roles[role_id] = choice
    return roles


# Routes ------------------------------------------------------------------------


@router.get("/settings/tts")
def get_tts(request: Request) -> dict[str, Any]:
    _owner_only(request)
    return overview(request)


@router.post("/settings/tts/providers", status_code=201)
def create_provider(payload: ProviderIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    supplier = PROVIDERS.get(payload.kind or "")
    if supplier is None:
        raise HTTPException(422, "Choose a kind of provider.")
    providers = _providers(request)
    try:
        provider = clean_provider(
            {
                "kind": payload.kind,
                "name": _unique_name(payload.name or supplier.name, providers),
                "baseUrl": payload.base_url or supplier.base_url,
                "apiKey": payload.api_key,
                "models": payload.models,
            },
            {provider["id"] for provider in providers},
        )
    except TtsConfigError as error:
        raise HTTPException(422, str(error)) from error
    providers.append(provider)
    _save(request, providers, _roles_after(request, providers))
    return overview(request)


@router.put("/settings/tts/providers/{provider_id}")
def update_provider(provider_id: str, payload: ProviderIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    providers, index = _find(request, provider_id)
    changes = payload.model_dump(exclude_unset=True)
    draft = {**providers[index]}
    for field, key in (
        ("name", "name"),
        ("base_url", "baseUrl"),
        ("api_key", "apiKey"),
        ("models", "models"),
    ):
        if field in changes:
            draft[key] = changes[field]
    try:
        providers[index] = clean_provider(draft)
    except TtsConfigError as error:
        raise HTTPException(422, str(error)) from error
    _save(request, providers, _roles_after(request, providers))
    return overview(request)


@router.delete("/settings/tts/providers/{provider_id}")
def delete_provider(provider_id: str, request: Request) -> dict[str, Any]:
    _owner_only(request)
    providers, index = _find(request, provider_id)
    del providers[index]
    _save(request, providers, _roles_after(request, providers))
    return overview(request)


@router.put("/settings/tts/roles")
def save_roles(payload: RolesIn, request: Request) -> dict[str, Any]:
    _owner_only(request)
    config = _config(request)
    providers = _providers(request)
    roles = dict(config["roles"])
    for role_id, change in payload.roles.items():
        current = roles.get(role_id) or {}
        merged = {**current, **change}
        if "model" in change and change["model"] != current.get("model"):
            before = find_model(providers, current.get("model"))
            after = find_model(providers, change["model"])
            if "options" not in change and not (
                before and after and before[0]["kind"] == after[0]["kind"]
            ):
                # Another supplier has other voices: start from its defaults.
                merged["options"] = {}
        try:
            roles[role_id] = clean_role(role_id, merged, providers)
        except TtsConfigError as error:
            raise HTTPException(422, str(error)) from error
    # Saving a job also keeps the providers it was chosen from.
    _save(request, providers, roles)
    return overview(request)


@router.post("/settings/tts/sample")
async def sample(payload: SampleIn, request: Request) -> Response:
    """Hear a voice before keeping it: a provider being edited, or the Answers job."""

    _owner_only(request)
    registries = _registries(request)
    config = _config(request)
    providers = providers_in_use(config, *registries)
    answers = config["roles"].get("answers") or {}
    chosen = find_model(providers, answers.get("model"))
    if payload.provider_id:
        provider = next((p for p in providers if p["id"] == payload.provider_id), None)
        if provider is None:
            raise HTTPException(404, "There is no such provider")
        draft = {**provider}
        if payload.base_url:
            draft["baseUrl"] = payload.base_url
        if payload.api_key:
            draft["apiKey"] = payload.api_key
        try:
            provider = clean_provider(draft)
        except TtsConfigError as error:
            raise HTTPException(422, str(error)) from error
        model = payload.model or (provider["models"][0]["id"] if provider["models"] else None)
        same = chosen is not None and chosen[0]["kind"] == provider["kind"]
        options = payload.options or (answers.get("options") if same else None)
    elif chosen is not None:
        provider, found = chosen
        model = payload.model or found["id"]
        options = {**(answers.get("options") or {}), **(payload.options or {})}
    else:
        raise HTTPException(422, "Choose a voice in Settings → Read aloud.")
    if not model:
        raise HTTPException(422, f"{provider['name']} has no model yet. Add one first.")
    try:
        resolved, problem = resolve_model(provider, model, options, *registries)
    except TtsConfigError as error:
        raise HTTPException(422, str(error)) from error
    if resolved is None:
        raise HTTPException(422, problem)
    try:
        audio = await _service(request).sample(resolved)
    except TtsError as error:
        _record(request, provider, error)
        raise HTTPException(502, str(error)) from error
    _record(request, provider, None)
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
    resolved, problem = resolve(_config(request), *_registries(request))
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


@router.post("/sessions/{session_id}/messages/{message_id}/speech/begin")
async def begin_speech(
    session_id: str, message_id: str, request: Request, fresh: bool = False
) -> dict[str, Any]:
    """Like speak_message, but a first reading is handed over in parts to play as they arrive."""

    _owner_only(request)
    resolved, problem = resolve(_config(request), *_registries(request))
    if resolved is None:
        raise HTTPException(409, problem)
    try:
        begun = await _service(request).begin(
            session_id, message_id, resolved, request.app.state.models, fresh
        )
    except SpeechNotFound as error:
        raise HTTPException(404, str(error)) from error
    except TtsError as error:
        raise HTTPException(502, str(error)) from error
    if begun.clip is not None:
        return {"clip": _clip_out(begun.clip), "jobId": None, "parts": 1}
    assert begun.job is not None
    return {"clip": None, "jobId": begun.job.id, "parts": len(begun.job.pieces)}


@router.get("/speech/jobs/{job_id}/parts/{index}")
async def job_part(job_id: str, index: int, request: Request) -> Response:
    """One part of an answer being made; waits until it exists."""

    _owner_only(request)
    try:
        audio = await _service(request).job_part(job_id, index)
    except SpeechNotFound as error:
        raise HTTPException(404, str(error)) from error
    except TtsError as error:
        raise HTTPException(502, str(error)) from error
    return Response(audio, media_type="audio/wav")


@router.get("/speech/jobs/{job_id}/script")
def job_script(job_id: str, request: Request) -> dict[str, str]:
    """What is being spoken, while the answer is still being made."""

    _owner_only(request)
    try:
        return {"script": _service(request).job_script(job_id)}
    except SpeechNotFound as error:
        raise HTTPException(404, str(error)) from error


@router.get("/speech/clips/{clip_id}/audio")
def clip_audio(clip_id: str, request: Request) -> Response:
    _owner_only(request)
    try:
        path = _service(request).audio_path(clip_id)
    except SpeechNotFound as error:
        raise HTTPException(404, str(error)) from error
    # Clips kept before Gunther repaired Qwen's sizes are repaired as they are sent.
    return Response(repair_wav(path.read_bytes()), media_type="audio/wav")


@router.get("/speech/clips/{clip_id}/script")
def clip_script(clip_id: str, request: Request) -> dict[str, str]:
    """What was spoken, for people who want to check how a table was described."""

    _owner_only(request)
    try:
        return {"script": _service(request).script_of(clip_id)}
    except SpeechNotFound as error:
        raise HTTPException(404, str(error)) from error
