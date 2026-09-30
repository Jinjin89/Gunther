"""The transcription providers someone has set up, their models, and which model does which job.

A *provider* is one connection (SenseVoice, Qwen, OpenAI, or any server with an
OpenAI-style ``/audio/transcriptions``): a name, an address, a key, and the
models chosen from it. A *job* picks one model, and whether it works live:

- recording: writes the words while a lecture is being recorded, in segments;
- dictation: writes the words when you speak into Ask.

Live suits a recording; a whole take sent at once reads better in Ask, so
dictation starts with live off.

This lives in the service settings file beside the language models (see
model_registry). Until someone saves providers, they are made from the older
``STT_*``, ``SENSEVOICE_*`` and ``QWEN_STT_*`` settings, so an existing setup
keeps working unchanged.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from gunther.config import Settings


@dataclass(frozen=True)
class Preset:
    name: str
    base_url: str
    models: tuple[str, ...] = ()
    key_optional: bool = False
    note: str = ""


PRESETS: dict[str, Preset] = {
    "sensevoice": Preset(
        "SenseVoice",
        "http://127.0.0.1:8765",
        ("sensevoice-small",),
        key_optional=True,
        note="Private: audio stays on this computer or your network.",
    ),
    "qwen": Preset(
        "Qwen",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ("qwen3-asr-flash",),
        note="Strong on Chinese. Outside China use dashscope-intl.aliyuncs.com.",
    ),
    "openai": Preset(
        "OpenAI",
        "https://api.openai.com/v1",
        ("whisper-1", "gpt-4o-mini-transcribe"),
    ),
    "compatible": Preset(
        "Other server",
        "http://127.0.0.1:8000/v1",
        key_optional=True,
        note="Groq, a Whisper you host, or any server with /audio/transcriptions.",
    ),
}


@dataclass(frozen=True)
class Role:
    id: str
    label: str
    description: str
    stream: bool


ROLES: tuple[Role, ...] = (
    Role(
        "recording",
        "Recording",
        "Writes the words while you record. Live is best here.",
        True,
    ),
    Role(
        "dictation",
        "Ask dictation",
        "Writes the words when you speak into Ask. Sending the whole take reads better.",
        False,
    ),
)
ROLE_BY_ID = {role.id: role for role in ROLES}
# Settings of the single transcription service that came before providers.
LEGACY_KEYS = (
    "stt_provider",
    "stt_base_url",
    "stt_api_key",
    "stt_model",
    "stt_language",
    "sensevoice_url",
    "sensevoice_segment_seconds",
    "qwen_stt_base_url",
    "qwen_stt_api_key",
    "qwen_stt_model",
)


class SpeechError(ValueError):
    """A provider or job that cannot be saved; the message says why."""


# Checking what is saved ---------------------------------------------------------


def _url(value: Any, what: str) -> str:
    text = str(value or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise SpeechError(f"{what}: use a full address starting with http:// or https://.")
    return text.rstrip("/")


def clean_key(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text or len(text) > 500 or re.search(r"\s", text):
        raise SpeechError("API key: paste the key on its own, without spaces.")
    return text


def clean_model(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        item = {"id": item}
    if not isinstance(item, dict):
        raise SpeechError("Each model needs an id.")
    model_id = str(item.get("id") or "").strip()
    if not model_id or len(model_id) > 200 or re.search(r"\s", model_id):
        raise SpeechError("Model: use the model's id, without spaces.")
    label = str(item.get("label") or "").strip()[:60] or model_id
    return {"id": model_id, "label": label}


def new_provider_id(kind: str, taken: set[str]) -> str:
    if kind not in taken:
        return kind
    while True:
        candidate = f"{kind}-{secrets.token_hex(2)}"
        if candidate not in taken:
            return candidate


def clean_provider(raw: dict[str, Any], existing_ids: set[str] = frozenset()) -> dict[str, Any]:
    kind = raw.get("kind")
    if kind not in PRESETS:
        raise SpeechError("Choose a kind of provider.")
    preset = PRESETS[kind]
    provider_id = str(raw.get("id") or "").strip() or new_provider_id(kind, existing_ids)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", provider_id):
        raise SpeechError("A provider's id must be short, lowercase letters and digits.")
    models = [clean_model(item) for item in raw.get("models") or []]
    ids = [model["id"] for model in models]
    if len(set(ids)) != len(ids):
        raise SpeechError("A model is listed twice.")
    if len(models) > 100:
        raise SpeechError("Choose at most 100 models per provider.")
    return {
        "id": provider_id,
        "name": str(raw.get("name") or "").strip()[:60] or preset.name,
        "kind": kind,
        "baseUrl": _url(raw.get("baseUrl") or preset.base_url, "Address"),
        "apiKey": clean_key(raw.get("apiKey")),
        "models": models,
    }


def clean_language(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if text and not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", text):
        raise SpeechError(f"{label}: use a language code like en or zh, or leave it empty.")
    return text


def clean_roles(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    roles: dict[str, dict[str, Any]] = {}
    for role_id, choice in raw.items():
        role = ROLE_BY_ID.get(role_id)
        if role is None:
            raise SpeechError(f"Unknown job: {role_id}")
        if not isinstance(choice, dict):
            raise SpeechError(f"{role.label}: choose a model.")
        model = choice.get("model")
        if model is not None and (not isinstance(model, str) or "/" not in model):
            raise SpeechError(f"{role.label}: choose a model.")
        stream = choice.get("stream", role.stream)
        if not isinstance(stream, bool):
            raise SpeechError(f"{role.label}: live must be on or off.")
        roles[role_id] = {
            "model": model,
            "stream": stream,
            "language": clean_language(choice.get("language"), role.label),
        }
    return roles


# What is in use ----------------------------------------------------------------


@dataclass(frozen=True)
class SharedKey:
    """A key saved elsewhere for the same company (Qwen's, under Models or Read aloud)."""

    api_key: str
    # Where that key was set up; DashScope keys belong to one region's address.
    base_url: str | None = None


def providers_from_environment(
    settings: Settings, shared: dict[str, SharedKey] | None = None
) -> list[dict[str, Any]]:
    """The providers the older single transcription service describes.

    A Qwen key already saved for its language models also brings in Qwen
    transcription, ready to be chosen for a job; nothing is sent there until it is.
    """

    shared_qwen = (shared or {}).get("qwen")
    providers = [
        {
            "id": "sensevoice",
            "name": PRESETS["sensevoice"].name,
            "kind": "sensevoice",
            "baseUrl": settings.sensevoice_url.rstrip("/"),
            "apiKey": None,
            "models": [clean_model("sensevoice-small")],
        }
    ]
    if settings.qwen_stt_api_key:
        providers.append(
            {
                "id": "qwen",
                "name": PRESETS["qwen"].name,
                "kind": "qwen",
                "baseUrl": settings.qwen_stt_base_url.rstrip("/"),
                "apiKey": settings.qwen_stt_api_key,
                "models": [clean_model(settings.qwen_stt_model)],
            }
        )
    elif shared_qwen:
        providers.append(
            {
                "id": "qwen",
                "name": PRESETS["qwen"].name,
                "kind": "qwen",
                "baseUrl": (shared_qwen.base_url or settings.qwen_stt_base_url).rstrip("/"),
                "apiKey": None,
                "models": [clean_model(settings.qwen_stt_model)],
            }
        )
    if settings.stt_base_url:
        providers.append(
            {
                "id": "compatible",
                "name": PRESETS["compatible"].name,
                "kind": "compatible",
                "baseUrl": settings.stt_base_url.rstrip("/"),
                "apiKey": settings.stt_api_key,
                "models": [clean_model(settings.stt_model)],
            }
        )
    return providers


def default_roles(providers: list[dict[str, Any]], settings: Settings) -> dict[str, dict[str, Any]]:
    """Both jobs on the provider the older setting chose; recording live, dictation whole."""

    by_kind = {provider["kind"]: provider for provider in providers if provider["models"]}
    wanted = settings.stt_provider
    if wanted == "auto":
        wanted = "compatible" if "compatible" in by_kind else "sensevoice"
    provider = by_kind.get(wanted)
    ref = f"{provider['id']}/{provider['models'][0]['id']}" if provider else None
    return {
        role.id: {"model": ref, "stream": role.stream, "language": settings.stt_language}
        for role in ROLES
    }


@dataclass(frozen=True)
class Registry:
    providers: list[dict[str, Any]]
    roles: dict[str, dict[str, Any]]
    # Providers come from STT_* settings, not yet from the app.
    from_environment: bool
    # By kind: keys saved elsewhere that a provider without its own key uses.
    shared: dict[str, SharedKey] = field(default_factory=dict)


def effective(
    saved_providers: list | None,
    saved_roles: dict | None,
    settings: Settings,
    shared: dict[str, SharedKey] | None = None,
) -> Registry:
    """What is in use: saved providers, or the environment's; saved jobs over defaults."""

    shared = dict(shared or {})
    from_environment = saved_providers is None
    providers = (
        providers_from_environment(settings, shared) if from_environment else saved_providers
    )
    roles = default_roles(providers, settings)
    for role_id, choice in (saved_roles or {}).items():
        if role_id in roles:
            roles[role_id] = dict(choice)
    return Registry(providers, roles, from_environment, shared)


@dataclass(frozen=True)
class Choice:
    """The model a job uses now, and everything needed to call it."""

    kind: str
    provider: str
    base_url: str
    api_key: str | None
    model: str
    label: str
    stream: bool
    language: str


def model_ref(provider: dict[str, Any], model: dict[str, Any]) -> str:
    return f"{provider['id']}/{model['id']}"


def key_for(
    provider: dict[str, Any], shared: dict[str, SharedKey] | None = None
) -> tuple[str | None, bool]:
    """The key a provider calls with, and whether it is one saved elsewhere."""

    if provider.get("apiKey"):
        return provider["apiKey"], False
    found = (shared or {}).get(provider["kind"])
    return (found.api_key, True) if found else (None, False)


def problem_with(
    provider: dict[str, Any], shared: dict[str, SharedKey] | None = None
) -> str | None:
    if key_for(provider, shared)[0] or PRESETS[provider["kind"]].key_optional:
        return None
    return f"{provider['name']} needs an API key."


def resolve(registry: Registry, role_id: str) -> tuple[Choice | None, str | None]:
    """The model a job uses, or why there is none."""

    choice = registry.roles.get(role_id) or {}
    ref = choice.get("model")
    if not ref:
        return None, "Choose a model."
    for provider in registry.providers:
        for model in provider["models"]:
            if model_ref(provider, model) != ref:
                continue
            problem = problem_with(provider, registry.shared)
            if problem:
                return None, problem
            return (
                Choice(
                    kind=provider["kind"],
                    provider=provider["name"],
                    base_url=provider["baseUrl"],
                    api_key=key_for(provider, registry.shared)[0],
                    model=model["id"],
                    label=model["label"],
                    stream=bool(choice.get("stream", ROLE_BY_ID[role_id].stream)),
                    language=str(choice.get("language") or ""),
                ),
                None,
            )
    return None, "Its model was removed. Choose another."
