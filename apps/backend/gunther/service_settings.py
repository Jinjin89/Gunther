"""The outside services Gunther uses, and the settings people change for them in the app.

Each service is declared once here: its fields (which ``Settings`` values they
edit, how to show them, how to check them), how to tell whether it is ready,
and how to test a connection. The Settings page draws itself from these
declarations, so a new service is a new entry in ``SERVICES``, not new UI.

Values saved from the app live in one JSON file beside the database and win
over ``.env`` and the environment. Secrets are written there but never sent back
to the app: it sees only whether one is set, and its last four characters.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import TypeAdapter, ValidationError

from gunther import speech_registry
from gunther.config import Settings
from gunther.model_registry import LEGACY_KEYS, RegistryError, clean_provider, clean_roles
from gunther.online_search import check_key as check_tavily_key

logger = logging.getLogger(__name__)

FieldKind = Literal["text", "secret", "url", "select", "toggle", "number"]
StatusState = Literal["configured", "not_configured", "error", "off"]

CHECK_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    description: str = ""
    # Values other fields take when this option is picked, e.g. a provider's URL.
    presets: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceField:
    key: str  # a Settings field
    label: str
    kind: FieldKind
    help: str = ""
    placeholder: str = ""
    options: tuple[Option, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    # Shown only while another field has one of these values.
    shown_when: tuple[str, tuple[str, ...]] | None = None
    required: bool = False
    pattern: str | None = None
    pattern_message: str = ""


@dataclass(frozen=True)
class Status:
    state: StatusState
    summary: str


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str
    warning: str | None = None


@dataclass(frozen=True)
class Service:
    id: str
    title: str
    description: str
    fields: tuple[ServiceField, ...]
    # The settings in use, and the models (a ModelGateway, or None) for services that write.
    status: Callable[[Settings, Any], Status]
    check: Callable[[Settings], Awaitable[CheckResult]] | None = None
    # Settings of other services this one relies on (a check covers them too).
    depends_on: tuple[str, ...] = ()
    note: str = ""

    def keys(self) -> tuple[str, ...]:
        return tuple(item.key for item in self.fields)


# Checks ------------------------------------------------------------------------


def _host(url: str) -> str:
    return urlsplit(url).netloc or url


async def list_models(
    base_url: str, api_key: str | None, service_name: str, *, key_optional: bool = False
) -> tuple[CheckResult, list[str]]:
    """Ask an OpenAI-style API for its models: proves the address and the key."""

    if not api_key and not key_optional:
        return CheckResult(False, "Add an API key first."), []
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
    except httpx.TimeoutException:
        return CheckResult(False, f"{_host(base_url)} did not answer in time."), []
    except httpx.HTTPError:
        return CheckResult(False, f"Could not reach {_host(base_url)}. Check the address."), []
    if response.status_code in (401, 403):
        return CheckResult(False, f"{service_name} did not accept this API key."), []
    if response.status_code == 404:
        return CheckResult(
            False, "No API answered at this address. Check the base URL (many end in /v1)."
        ), []
    if response.status_code >= 400:
        return CheckResult(
            False, f"{service_name} answered with an error ({response.status_code})."
        ), []
    try:
        listed = [str(item["id"]) for item in response.json().get("data", [])]
    except (ValueError, TypeError, KeyError, AttributeError):
        listed = []
    return CheckResult(True, "Connected, and the key works."), sorted(set(listed))


async def _check_web_search(settings: Settings) -> CheckResult:
    if not settings.tavily_api_key:
        return CheckResult(False, "Add a Tavily API key first.")
    ok, message = await check_tavily_key(settings.tavily_api_key)
    return CheckResult(ok, message)


# Status ------------------------------------------------------------------------


def _web_search_status(settings: Settings, _models: Any) -> Status:
    if not settings.tavily_api_key:
        return Status("not_configured", "No key yet. Ask cannot search the web.")
    return Status("configured", f"Tavily · {settings.web_search_depth} search")


def _summaries_status(settings: Settings, models: Any) -> Status:
    if settings.ai_summaries == "off":
        return Status("off", "Turned off. Captures are kept without a summary.")
    analysis = models.for_role("analysis") if models else None
    if analysis is None:
        return Status("not_configured", "Needs a model: set one up under Models.")
    summary = f"Written by {analysis[0].display}"
    photos = models.for_role("photos")
    if settings.ai_summary_images and photos and photos[0].vision and photos[0] != analysis[0]:
        summary += f"; photos by {photos[0].display}"
    return Status("configured", summary)


# The services ------------------------------------------------------------------

MODEL_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}"
MODEL_MESSAGE = "Use the model's id, without spaces."

SERVICES: tuple[Service, ...] = (
    Service(
        id="web_search",
        title="Web search",
        description="Lets Ask look things up online and cite the pages it read.",
        note=(
            "By Tavily (tavily.com), which has a free monthly allowance. Ask searches "
            "the web only when its Web toggle is on."
        ),
        fields=(
            ServiceField("tavily_api_key", "Tavily API key", "secret", placeholder="tvly-…"),
            ServiceField(
                "web_search_depth",
                "Depth",
                "select",
                help="Advanced reads pages more closely and costs more of your allowance.",
                options=(
                    Option("basic", "Basic", "Fast, one credit per search."),
                    Option("advanced", "Advanced", "Slower and more thorough, two credits."),
                ),
            ),
            ServiceField(
                "web_search_max_results",
                "Pages per search",
                "number",
                minimum=1,
                maximum=10,
                step=1,
            ),
        ),
        status=_web_search_status,
        check=_check_web_search,
    ),
    Service(
        id="summaries",
        title="Summaries",
        description="A summary of everything you capture, citing the passages it came from.",
        note="Written by the Analysis model; photos by the Photos model (see Models).",
        fields=(
            ServiceField(
                "ai_summaries",
                "Summaries",
                "select",
                options=(Option("auto", "On"), Option("off", "Off")),
            ),
            ServiceField(
                "ai_summary_images",
                "Show photos to the model",
                "toggle",
                help="Otherwise the model reads only the text found in them.",
                shown_when=("ai_summaries", ("auto",)),
            ),
        ),
        status=_summaries_status,
    ),
)

SERVICE_BY_ID = {service.id: service for service in SERVICES}
FIELD_BY_KEY = {item.key: item for service in SERVICES for item in service.fields}


# Values ------------------------------------------------------------------------


class ServiceSettingsError(ValueError):
    """A value someone entered cannot be used. ``field`` names it."""

    def __init__(self, key: str, message: str) -> None:
        super().__init__(f"{FIELD_BY_KEY[key].label}: {message}")
        self.field = key
        self.reason = message


def validate_value(key: str, value: Any) -> Any:
    """The value as Settings holds it, or ServiceSettingsError saying what is wrong."""

    spec = FIELD_BY_KEY[key]
    if isinstance(value, str):
        value = value.strip()
    if spec.kind == "secret":
        if value in (None, ""):
            return None
        if not isinstance(value, str) or len(value) > 500 or re.search(r"\s", value):
            raise ServiceSettingsError(key, "Paste the key on its own, without spaces.")
        return value
    if spec.required and value in (None, ""):
        raise ServiceSettingsError(key, "This cannot be empty.")
    if spec.kind == "url":
        if value in (None, ""):
            return ""
        parts = urlsplit(str(value))
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ServiceSettingsError(key, "Use a full address starting with http:// or https://.")
        return str(value).rstrip("/")
    if spec.kind == "select" and value not in {option.value for option in spec.options}:
        raise ServiceSettingsError(key, "Choose one of the options.")
    if spec.pattern and not re.fullmatch(spec.pattern, str(value)):
        raise ServiceSettingsError(key, spec.pattern_message)
    if spec.kind == "number":
        if isinstance(value, bool):
            raise ServiceSettingsError(key, "Enter a number.")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ServiceSettingsError(key, "Enter a number.") from error
        if (spec.minimum is not None and number < spec.minimum) or (
            spec.maximum is not None and number > spec.maximum
        ):
            raise ServiceSettingsError(
                key, f"Use a number from {spec.minimum:g} to {spec.maximum:g}."
            )
        value = number
    if spec.kind == "toggle" and not isinstance(value, bool):
        raise ServiceSettingsError(key, "Turn it on or off.")
    try:
        return TypeAdapter(Settings.model_fields[key].annotation).validate_python(value)
    except ValidationError as error:
        raise ServiceSettingsError(key, "This value is not supported.") from error


def secret_hint(value: str | None) -> str | None:
    return value[-4:] if value and len(value) >= 12 else None


def fingerprint(service: Service, settings: Settings) -> str:
    """Identifies the values a check ran against, so a later change retires it."""

    values = {key: getattr(settings, key) for key in (*service.keys(), *service.depends_on)}
    encoded = json.dumps(values, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class ServiceSettingsStore:
    """Values saved from the app, and the result of the last check of each service.

    Every app serving this workspace (the desktop's and the phone gateway's)
    shares one store and hears about each change, so a save applies everywhere
    at once, without a restart.
    """

    # 2: language models moved from one set of llm_* values to providers and roles.
    # 3: transcription moved from one service to providers and jobs.
    VERSION = 3

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._values: dict[str, Any] = {}
        self._checks: dict[str, dict[str, Any]] = {}
        # None until someone saves them: the environment's model is used meanwhile.
        self._providers: list[dict[str, Any]] | None = None
        self._roles: dict[str, dict[str, Any]] | None = None
        # Transcription providers and jobs, the same way.
        self._speech_providers: list[dict[str, Any]] | None = None
        self._speech_roles: dict[str, dict[str, Any]] | None = None
        # Read-aloud settings (see tts_service); None until someone saves them.
        self._tts: dict[str, Any] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._load()

    @property
    def persisted(self) -> bool:
        return self.path is not None

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Saved service settings could not be read; using defaults")
            return
        for key, value in dict(payload.get("values") or {}).items():
            if key in LEGACY_KEYS or key in speech_registry.LEGACY_KEYS:
                # A language model saved by version 1, or the one transcription service
                # before providers: each stands in until providers are saved (see
                # model_registry and speech_registry, providers_from_environment).
                try:
                    annotation = Settings.model_fields[key].annotation
                    self._values[key] = TypeAdapter(annotation).validate_python(value)
                except ValidationError:
                    logger.warning("Ignoring a saved setting that is no longer valid: %s", key)
                continue
            if key not in FIELD_BY_KEY:
                continue
            try:
                self._values[key] = validate_value(key, value)
            except ServiceSettingsError:
                logger.warning("Ignoring a saved setting that is no longer valid: %s", key)
        if isinstance(payload.get("tts"), dict):
            self._tts = payload["tts"]
        checks = payload.get("checks") or {}
        self._checks = {
            key: dict(value) for key, value in checks.items() if isinstance(value, dict)
        }
        try:
            if payload.get("providers") is not None:
                taken: set[str] = set()
                providers = []
                for raw in payload["providers"]:
                    provider = clean_provider(raw, taken)
                    taken.add(provider["id"])
                    providers.append(provider)
                self._providers = providers
            if payload.get("roles") is not None:
                self._roles = clean_roles(payload["roles"])
        except (RegistryError, TypeError, AttributeError):
            logger.warning("Saved model providers could not be read; using the defaults")
        try:
            if payload.get("speechProviders") is not None:
                taken = set()
                speech = []
                for raw in payload["speechProviders"]:
                    provider = speech_registry.clean_provider(raw, taken)
                    taken.add(provider["id"])
                    speech.append(provider)
                self._speech_providers = speech
            if payload.get("speechRoles") is not None:
                self._speech_roles = speech_registry.clean_roles(payload["speechRoles"])
        except (speech_registry.SpeechError, TypeError, AttributeError):
            logger.warning("Saved transcription providers could not be read; using the defaults")

    def _write(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "values": self._values,
            "checks": self._checks,
            "providers": self._providers,
            "roles": self._roles,
            "speechProviders": self._speech_providers,
            "speechRoles": self._speech_roles,
            "tts": self._tts,
        }
        descriptor, temporary = tempfile.mkstemp(
            prefix=".service-settings.", suffix=".json", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def values(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def apply(self, base: Settings) -> Settings:
        """The base settings with every saved value on top."""

        return base.model_copy(update=self.values())

    def update(self, changes: dict[str, Any]) -> None:
        """Save changes: a value to keep, or ``None`` to go back to the default."""

        validated = {
            key: validate_value(key, value) for key, value in changes.items() if value is not None
        }
        with self._lock:
            for key, value in changes.items():
                if value is None:
                    self._values.pop(key, None)
                else:
                    self._values[key] = validated[key]
            self._write()
            listeners = list(self._listeners)
        for listener in listeners:
            listener()

    def models(self) -> tuple[list[dict[str, Any]] | None, dict[str, dict[str, Any]] | None]:
        """Saved providers and roles, or None for each not saved yet."""

        with self._lock:
            return copy.deepcopy(self._providers), copy.deepcopy(self._roles)

    def save_models(
        self,
        providers: list[dict[str, Any]] | None = None,
        roles: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Save providers and/or roles, already checked (see model_registry)."""

        with self._lock:
            if providers is not None:
                self._providers = copy.deepcopy(providers)
                # Providers replace the one language model of version 1.
                for key in LEGACY_KEYS:
                    self._values.pop(key, None)
            if roles is not None:
                self._roles = copy.deepcopy(roles)
            self._write()
            listeners = list(self._listeners)
        for listener in listeners:
            listener()

    def speech(self) -> tuple[list[dict[str, Any]] | None, dict[str, dict[str, Any]] | None]:
        """Saved transcription providers and jobs, or None for each not saved yet."""

        with self._lock:
            return copy.deepcopy(self._speech_providers), copy.deepcopy(self._speech_roles)

    def save_speech(
        self,
        providers: list[dict[str, Any]] | None = None,
        roles: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Save transcription providers and/or jobs, already checked (see speech_registry)."""

        with self._lock:
            if providers is not None:
                self._speech_providers = copy.deepcopy(providers)
                # Providers replace the single service of before.
                for key in speech_registry.LEGACY_KEYS:
                    self._values.pop(key, None)
            if roles is not None:
                self._speech_roles = copy.deepcopy(roles)
            self._write()
            listeners = list(self._listeners)
        for listener in listeners:
            listener()

    def tts(self) -> dict[str, Any] | None:
        """Saved read-aloud settings, or None when never saved."""

        with self._lock:
            return copy.deepcopy(self._tts)

    def save_tts(self, config: dict[str, Any]) -> None:
        """Save read-aloud settings, already checked (see tts_service)."""

        with self._lock:
            self._tts = copy.deepcopy(config)
            self._write()
            listeners = list(self._listeners)
        for listener in listeners:
            listener()

    def record_speech_check(self, provider_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            self._checks[f"speech:{provider_id}"] = {**entry, "checkedAt": _now()}
            try:
                self._write()
            except OSError:
                logger.warning("The result of a connection test could not be saved", exc_info=True)

    def speech_check(self, provider_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._checks.get(f"speech:{provider_id}")
            return dict(entry) if entry else None

    def record_model_check(self, provider_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            self._checks[f"provider:{provider_id}"] = {**entry, "checkedAt": _now()}
            try:
                self._write()
            except OSError:
                logger.warning("The result of a connection test could not be saved", exc_info=True)

    def model_check(self, provider_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._checks.get(f"provider:{provider_id}")
            return dict(entry) if entry else None

    def subscribe(self, listener: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def record_check(
        self, service: Service, settings: Settings, result: CheckResult
    ) -> dict[str, Any]:
        entry = {
            "ok": result.ok,
            "message": result.message,
            "warning": result.warning,
            "checkedAt": _now(),
            "fingerprint": fingerprint(service, settings),
        }
        with self._lock:
            self._checks[service.id] = entry
            try:
                self._write()
            except OSError:
                logger.warning("The result of a connection test could not be saved", exc_info=True)
        return entry

    def last_check(self, service: Service, settings: Settings) -> dict[str, Any] | None:
        """The last check, if it ran against the values in use now."""

        with self._lock:
            entry = self._checks.get(service.id)
        if entry and entry.get("fingerprint") == fingerprint(service, settings):
            return entry
        return None


# What the app sees -------------------------------------------------------------


def value_source(key: str, store: ServiceSettingsStore, base: Settings) -> str:
    if key in store.values():
        return "saved"
    # Set in the environment, to something other than the default (an empty
    # DEEPSEEK_API_KEY= in .env changes nothing).
    if key in base.model_fields_set and getattr(base, key) != Settings.model_fields[key].default:
        return "environment"
    return "default"


def describe(
    service: Service, store: ServiceSettingsStore, base: Settings, models: Any = None
) -> dict[str, Any]:
    """A service as the Settings page draws it. Secrets are never included."""

    settings = store.apply(base)
    status = service.status(settings, models)
    check = store.last_check(service, settings) if service.check else None
    state: StatusState = status.state
    summary = status.summary
    if check and not check["ok"] and state == "configured":
        state, summary = "error", check["message"]
    fields = []
    for item in service.fields:
        value = getattr(settings, item.key)
        default = Settings.model_fields[item.key].default
        fields.append(
            {
                "key": item.key,
                "label": item.label,
                "kind": item.kind,
                "help": item.help,
                "placeholder": item.placeholder,
                "options": [
                    {
                        "value": option.value,
                        "label": option.label,
                        "description": option.description,
                        "presets": option.presets,
                    }
                    for option in item.options
                ],
                "min": item.minimum,
                "max": item.maximum,
                "step": item.step,
                "required": item.required,
                "pattern": item.pattern,
                "patternMessage": item.pattern_message,
                "shownWhen": (
                    {"key": item.shown_when[0], "values": list(item.shown_when[1])}
                    if item.shown_when
                    else None
                ),
                "value": None if item.kind == "secret" else value,
                "default": None if item.kind == "secret" else default,
                "isSet": bool(value),
                "hint": secret_hint(value) if item.kind == "secret" else None,
                "source": value_source(item.key, store, base),
                "envVar": item.key.upper(),
            }
        )
    return {
        "id": service.id,
        "title": service.title,
        "description": service.description,
        "note": service.note,
        "fields": fields,
        "canTest": service.check is not None,
        "status": {
            "state": state,
            "summary": summary,
            "checkedAt": check["checkedAt"] if check else None,
            "check": (
                {"ok": check["ok"], "message": check["message"], "warning": check["warning"]}
                if check
                else None
            ),
        },
    }


def draft_settings(
    service: Service, store: ServiceSettingsStore, base: Settings, draft: dict[str, Any]
) -> Settings:
    """Settings in use now, with a service's unsaved values on top (for a test)."""

    settings = store.apply(base)
    update: dict[str, Any] = {}
    for key, value in draft.items():
        if key not in service.keys():  # noqa: SIM118 (a tuple)
            raise KeyError(key)
        if value is None:
            update[key] = getattr(base, key)
        else:
            update[key] = validate_value(key, value)
    return settings.model_copy(update=update)
