"""The providers someone has set up, their models, and which model does which job.

A *provider* is one connection (DeepSeek, Kimi, GLM, Qwen, OpenAI, or a
self-hosted server): a name, an address, a key, and the models chosen from it.
A *role* is a job, with the model and effort it uses:

- analysis: reading captures, their summaries, papers, and topic overviews;
- ask: answering in Ask (each conversation can pick another model);
- photos: looking at photos, which needs a model that can see images.

This lives in the service settings file (see service_settings) beside the
other services. Until someone saves providers, one is made from the
environment's ``LLM_*`` settings (or the older ``DEEPSEEK_*`` names), so an
existing setup keeps working unchanged.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from gunther.config import Settings
from gunther.llm import ModelGateway, ModelInfo
from gunther.model_profiles import DIALECTS, EFFORTS, Effort, profile_for


@dataclass(frozen=True)
class Preset:
    name: str
    base_url: str
    models: tuple[str, ...] = ()
    key_optional: bool = False
    note: str = ""


PRESETS: dict[str, Preset] = {
    "deepseek": Preset(
        "DeepSeek", "https://api.deepseek.com", ("deepseek-flash", "deepseek-v4-pro")
    ),
    "kimi": Preset(
        "Kimi",
        "https://api.moonshot.ai/v1",
        ("kimi-k3",),
        note="In mainland China use https://api.moonshot.cn/v1.",
    ),
    "glm": Preset("GLM", "https://open.bigmodel.cn/api/paas/v4", ("glm-5.3-flash",)),
    "qwen": Preset(
        "Qwen",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        note="Use your Model Studio workspace address if it gives you one.",
    ),
    "openai": Preset(
        "OpenAI",
        "https://api.openai.com/v1",
        note="Leave the key empty to use the one under OpenAI services.",
    ),
    "compatible": Preset(
        "Self-hosted",
        "http://127.0.0.1:8000/v1",
        key_optional=True,
        note="vLLM, Ollama (http://127.0.0.1:11434/v1), LM Studio, or any OpenAI-compatible API.",
    ),
}

MODEL_LABELS = {
    "deepseek-flash": "Flash",
    "deepseek-v4-flash": "V4 Flash",
    "deepseek-v4-pro": "V4 Pro",
    "kimi-k3": "K3",
    "glm-5.3-flash": "5.3 Flash",
}


@dataclass(frozen=True)
class Role:
    id: str
    label: str
    description: str
    effort: Effort
    needs_vision: bool = False


ROLES: tuple[Role, ...] = (
    Role(
        "analysis",
        "Analysis",
        "Reads what you capture and writes summaries, paper notes and topic overviews.",
        "low",
    ),
    Role("ask", "Ask", "Answers questions. Each conversation can switch model.", "high"),
    Role("photos", "Photos", "Looks at photos you capture.", "low", needs_vision=True),
)
ROLE_BY_ID = {role.id: role for role in ROLES}
LEGACY_KEYS = ("llm_provider", "llm_api_key", "llm_model", "llm_base_url")


class RegistryError(ValueError):
    """A provider or role that cannot be saved; the message says why."""


# Checking what is saved ---------------------------------------------------------


def _url(value: Any, what: str) -> str:
    text = str(value or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise RegistryError(f"{what}: use a full address starting with http:// or https://.")
    return text.rstrip("/")


def clean_key(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text or len(text) > 500 or re.search(r"\s", text):
        raise RegistryError("API key: paste the key on its own, without spaces.")
    return text


def clean_model(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        item = {"id": item}
    if not isinstance(item, dict):
        raise RegistryError("Each model needs an id.")
    model_id = str(item.get("id") or "").strip()
    if not model_id or len(model_id) > 200 or re.search(r"\s", model_id):
        raise RegistryError("Model: use the model's id, without spaces.")
    label = str(item.get("label") or "").strip()[:60] or MODEL_LABELS.get(model_id, model_id)
    vision = item.get("vision")
    if vision is not None and not isinstance(vision, bool):
        raise RegistryError(f"{model_id}: images must be on or off.")
    dialect = item.get("dialect") or None
    if dialect is not None and dialect not in DIALECTS:
        raise RegistryError(f"{model_id}: unknown thinking setting.")
    return {"id": model_id, "label": label, "vision": vision, "dialect": dialect}


def clean_provider(raw: dict[str, Any], existing_ids: set[str] = frozenset()) -> dict[str, Any]:
    kind = raw.get("kind")
    if kind not in PRESETS:
        raise RegistryError("Choose a kind of provider.")
    preset = PRESETS[kind]
    provider_id = str(raw.get("id") or "").strip() or new_provider_id(kind, existing_ids)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", provider_id):
        raise RegistryError("A provider's id must be short, lowercase letters and digits.")
    name = str(raw.get("name") or "").strip()[:60] or preset.name
    models = [clean_model(item) for item in raw.get("models") or []]
    ids = [model["id"] for model in models]
    if len(set(ids)) != len(ids):
        raise RegistryError("A model is listed twice.")
    if len(models) > 200:
        raise RegistryError("Choose at most 200 models per provider.")
    return {
        "id": provider_id,
        "name": name,
        "kind": kind,
        "baseUrl": _url(raw.get("baseUrl") or preset.base_url, "Base URL"),
        "apiKey": clean_key(raw.get("apiKey")),
        "models": models,
    }


def new_provider_id(kind: str, taken: set[str]) -> str:
    if kind not in taken:
        return kind
    while True:
        candidate = f"{kind}-{secrets.token_hex(2)}"
        if candidate not in taken:
            return candidate


def clean_roles(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    roles: dict[str, dict[str, Any]] = {}
    for role_id, choice in raw.items():
        if role_id not in ROLE_BY_ID:
            raise RegistryError(f"Unknown job: {role_id}")
        if not isinstance(choice, dict):
            raise RegistryError(f"{ROLE_BY_ID[role_id].label}: choose a model.")
        model = choice.get("model")
        if model is not None and (not isinstance(model, str) or "/" not in model):
            raise RegistryError(f"{ROLE_BY_ID[role_id].label}: choose a model.")
        effort = choice.get("effort") or ROLE_BY_ID[role_id].effort
        if effort not in EFFORTS:
            raise RegistryError(f"{ROLE_BY_ID[role_id].label}: effort must be one of {EFFORTS}.")
        roles[role_id] = {"model": model, "effort": effort}
    return roles


# What is in use ----------------------------------------------------------------


def providers_from_environment(settings: Settings) -> list[dict[str, Any]]:
    """The one provider an older setup describes with LLM_* (or DEEPSEEK_*) settings."""

    kind = settings.llm_provider
    preset = PRESETS[kind]
    models = [settings.llm_model, *(m for m in preset.models if m != settings.llm_model)]
    return [
        {
            "id": kind,
            "name": preset.name,
            "kind": kind,
            "baseUrl": settings.llm_base_url.rstrip("/"),
            "apiKey": settings.llm_api_key,
            "models": [clean_model(model) for model in models],
        }
    ]


def default_roles(providers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every job on the first provider's first model; photos on one that sees."""

    infos = [(provider, model) for provider in providers for model in provider["models"]]
    if not infos:
        return {role.id: {"model": None, "effort": role.effort} for role in ROLES}
    first = f"{infos[0][0]['id']}/{infos[0][1]['id']}"
    seeing = next(
        (
            f"{provider['id']}/{model['id']}"
            for provider, model in infos
            if profile_for(provider["kind"], model["id"], vision=model["vision"]).vision
        ),
        first,
    )
    return {
        role.id: {"model": seeing if role.needs_vision else first, "effort": role.effort}
        for role in ROLES
    }


@dataclass(frozen=True)
class Registry:
    providers: list[dict[str, Any]]
    roles: dict[str, dict[str, Any]]
    # Providers come from LLM_* settings, not yet from the app.
    from_environment: bool


def effective(
    saved_providers: list | None, saved_roles: dict | None, settings: Settings
) -> Registry:
    """What is in use: saved providers, or the environment's; saved roles over defaults."""

    from_environment = saved_providers is None
    providers = providers_from_environment(settings) if from_environment else saved_providers
    roles = default_roles(providers)
    for role_id, choice in (saved_roles or {}).items():
        if role_id in roles:
            roles[role_id] = dict(choice)
    return Registry(providers, roles, from_environment)


def provider_key(provider: dict[str, Any], settings: Settings) -> tuple[str | None, str]:
    """The key a provider uses, and where it comes from: saved, openai, or none."""

    if provider.get("apiKey"):
        return provider["apiKey"], "saved"
    if provider["kind"] == "openai" and settings.openai_api_key:
        return settings.openai_api_key, "openai"
    return None, "none"


def model_infos(registry: Registry, settings: Settings) -> list[tuple[ModelInfo, str | None]]:
    """Every chosen model, with why it cannot be used yet (None when it can)."""

    infos = []
    for provider in registry.providers:
        key, _ = provider_key(provider, settings)
        problem = (
            None
            if key or PRESETS[provider["kind"]].key_optional
            else f"{provider['name']} needs an API key."
        )
        for model in provider["models"]:
            profile = profile_for(
                provider["kind"], model["id"], dialect=model["dialect"], vision=model["vision"]
            )
            info = ModelInfo(
                provider_id=provider["id"],
                provider_name=provider["name"],
                provider_kind=provider["kind"],
                model_id=model["id"],
                label=model["label"],
                base_url=provider["baseUrl"],
                api_key=key,
                profile=profile,
            )
            infos.append((info, problem))
    return infos


def build_gateway(registry: Registry, settings: Settings, **options: Any) -> ModelGateway:
    ready = [info for info, problem in model_infos(registry, settings) if problem is None]
    refs = {info.ref for info in ready}
    roles = {
        role_id: (choice["model"], choice["effort"])
        for role_id, choice in registry.roles.items()
        if choice.get("model") in refs
    }
    return ModelGateway(ready, roles, **options)
