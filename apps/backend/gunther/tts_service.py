"""Reading answers aloud: settings, the pipeline, and the clips kept on disk.

Saying an answer is: message text → script (tts_narration) → pieces short
enough for the supplier → audio → one WAV file kept under ``speech_dir`` and
listed in ``speech_clips``. Asking again for the same text, supplier, model and
options finds the clip and plays it; nothing is synthesized twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from gunther.database import session_scope
from gunther.llm import ModelGateway
from gunther.models import SessionMessage, SpeechClip
from gunther.tts_narration import NARRATION_VERSION, narrate
from gunther.tts_providers import (
    PROVIDERS,
    ProviderConfig,
    TtsError,
    TtsProvider,
    describe_wav,
    join_wav,
    split_for_listening,
    split_for_speech,
    wav_seconds,
)

logger = logging.getLogger(__name__)

SAMPLE_TEXT = "Hello, this is how I will read your answers. 你好，这就是我朗读回答的声音。"


class TtsConfigError(ValueError):
    """Settings that cannot be saved; the message says why."""


# Settings ---------------------------------------------------------------------------
#
# Shaped like transcription (see speech_registry): *providers* are connections you
# set up (a supplier, an address, a key, the models chosen from it), and *jobs*
# pick one model and the voice it speaks with. Reading answers is the only job
# today; a new one is a new entry in ROLES.


@dataclass(frozen=True)
class Role:
    id: str
    label: str
    description: str


ROLES: tuple[Role, ...] = (
    Role(
        "answers",
        "Answers",
        "Speaks an answer when you press Listen, or by itself if you turn that on.",
    ),
)
ROLE_BY_ID = {role.id: role for role in ROLES}


def default_config() -> dict[str, Any]:
    # No providers saved yet: one of each supplier is offered (see providers_in_use).
    return {"providers": None, "roles": {}}


def _clean_url(value: Any) -> str:
    text = str(value or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise TtsConfigError("Address: use a full address starting with http:// or https://.")
    return text.rstrip("/")


def _clean_key(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text or len(text) > 500 or re.search(r"\s", text):
        raise TtsConfigError("API key: paste the key on its own, without spaces.")
    return text


def _clean_model(item: Any) -> dict[str, str]:
    if isinstance(item, str):
        item = {"id": item}
    if not isinstance(item, dict):
        raise TtsConfigError("Each model needs an id.")
    model_id = str(item.get("id") or "").strip()
    if not model_id or len(model_id) > 200 or re.search(r"\s", model_id):
        raise TtsConfigError("Model: use the model's id, without spaces.")
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
    if kind not in PROVIDERS:
        raise TtsConfigError("Choose a kind of provider.")
    supplier = PROVIDERS[kind]
    provider_id = str(raw.get("id") or "").strip() or new_provider_id(kind, set(existing_ids))
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", provider_id):
        raise TtsConfigError("A provider's id must be short, lowercase letters and digits.")
    given = raw.get("models")
    models = [_clean_model(item) for item in (supplier.models if given is None else given)]
    ids = [model["id"] for model in models]
    if len(set(ids)) != len(ids):
        raise TtsConfigError("A model is listed twice.")
    if len(models) > 50:
        raise TtsConfigError("Choose at most 50 models per provider.")
    return {
        "id": provider_id,
        "name": str(raw.get("name") or "").strip()[:60] or supplier.name,
        "kind": kind,
        "baseUrl": _clean_url(raw.get("baseUrl") or supplier.base_url),
        "apiKey": _clean_key(raw.get("apiKey")),
        "models": models,
    }


def clean_options(supplier: TtsProvider, given: Any) -> dict[str, str]:
    """A supplier's options (voice, language...), each checked against its choices."""

    if given is None:
        given = {}
    if not isinstance(given, dict):
        raise TtsConfigError("Options are not in a shape Gunther understands.")
    options: dict[str, str] = {}
    for option in supplier.options:
        value = str(given.get(option.key) or option.default).strip()
        allowed = {choice.value for choice in option.choices}
        if not value or len(value) > 80:
            raise TtsConfigError(f"{option.label}: choose one.")
        if allowed and value not in allowed and not option.allow_custom:
            raise TtsConfigError(f"{option.label}: {value} is not one of this supplier's choices.")
        options[option.key] = value
    return options


def model_ref(provider: dict[str, Any], model: dict[str, Any]) -> str:
    return f"{provider['id']}/{model['id']}"


def find_model(
    providers: list[dict[str, Any]], ref: str | None
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for provider in providers:
        for model in provider["models"]:
            if model_ref(provider, model) == ref:
                return provider, model
    return None


def clean_role(role_id: str, raw: Any, providers: list[dict[str, Any]]) -> dict[str, Any]:
    role = ROLE_BY_ID.get(role_id)
    if role is None:
        raise TtsConfigError(f"Unknown job: {role_id}")
    if not isinstance(raw, dict):
        raise TtsConfigError(f"{role.label}: choose a model.")
    ref = raw.get("model")
    if ref is not None and not isinstance(ref, str):
        raise TtsConfigError(f"{role.label}: choose a model.")
    found = find_model(providers, ref) if ref else None
    if ref and found is None:
        raise TtsConfigError(f"{role.label}: that model is not set up.")
    auto_read = raw.get("autoRead", False)
    if not isinstance(auto_read, bool):
        raise TtsConfigError("Reading automatically must be on or off.")
    options = clean_options(PROVIDERS[found[0]["kind"]], raw.get("options")) if found else {}
    return {"model": ref or None, "options": options, "autoRead": auto_read}


def upgrade_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Bring settings saved before providers to the current shape.

    ``{"active", "autoRead", "providers": {kind: {apiKey, baseUrl, model, options}}}``
    becomes a provider per saved supplier and the Answers job on the active one.
    """

    if not raw:
        return default_config()
    if "roles" in raw or not isinstance(raw.get("providers"), dict):
        return {"providers": raw.get("providers"), "roles": dict(raw.get("roles") or {})}
    providers: list[dict[str, Any]] = []
    roles: dict[str, Any] = {}
    active = raw.get("active")
    for kind, entry in raw["providers"].items():
        if kind not in PROVIDERS or not isinstance(entry, dict):
            continue
        model = str(entry.get("model") or PROVIDERS[kind].models[0])
        providers.append(
            {
                "id": kind,
                "kind": kind,
                "baseUrl": entry.get("baseUrl"),
                "apiKey": entry.get("apiKey"),
                "models": list(dict.fromkeys([*PROVIDERS[kind].models, model])),
            }
        )
        if kind == active:
            roles["answers"] = {
                "model": f"{kind}/{model}",
                "options": entry.get("options") or {},
                "autoRead": bool(raw.get("autoRead")),
            }
    if active in PROVIDERS and active not in raw["providers"]:
        # Chosen but never edited: the supplier as it comes.
        providers.append({"id": active, "kind": active})
        roles["answers"] = {
            "model": f"{active}/{PROVIDERS[active].models[0]}",
            "autoRead": bool(raw.get("autoRead")),
        }
    return {"providers": providers or None, "roles": roles}


def clean_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    config = upgrade_config(raw)
    providers: list[dict[str, Any]] | None = None
    if config["providers"] is not None:
        providers = []
        for item in config["providers"]:
            if not isinstance(item, dict):
                raise TtsConfigError("A provider is not in a shape Gunther understands.")
            if item.get("id") in {provider["id"] for provider in providers}:
                raise TtsConfigError("Two providers have the same id.")
            providers.append(clean_provider(item, {provider["id"] for provider in providers}))
    roles = {
        role_id: clean_role(role_id, choice, providers or [])
        for role_id, choice in (config["roles"] or {}).items()
    }
    return {"providers": providers, "roles": roles}


def shared_key(kind: str, *registries: Any) -> tuple[str, str | None] | None:
    """A key already saved for the same company elsewhere (Qwen's, for its models).

    Returned with the address it was saved with: DashScope keys belong to one region.
    """

    for registry in registries:
        for provider in getattr(registry, "providers", []) or []:
            if provider.get("kind") == kind and provider.get("apiKey"):
                return provider["apiKey"], provider.get("baseUrl")
    return None


def _region_root(supplier: TtsProvider, base_url: str | None) -> str:
    """Where a supplier lives in the region a shared key was set up for."""

    host = urlsplit(base_url or "").hostname or ""
    if supplier.kind == "qwen" and host.startswith("dashscope-intl."):
        return "https://dashscope-intl.aliyuncs.com"
    return supplier.base_url


def providers_in_use(config: dict[str, Any], *registries: Any) -> list[dict[str, Any]]:
    """Saved providers, or, before any are saved, one of each supplier to start from."""

    if config.get("providers") is not None:
        return config["providers"]
    providers = []
    for kind, supplier in PROVIDERS.items():
        shared = shared_key(kind, *registries)
        base_url = _region_root(supplier, shared[1] if shared else None)
        providers.append(clean_provider({"id": kind, "kind": kind, "baseUrl": base_url}))
    return providers


def key_for(provider: dict[str, Any], *registries: Any) -> tuple[str | None, bool]:
    """The key a provider calls with, and whether it is one saved elsewhere."""

    if provider.get("apiKey"):
        return provider["apiKey"], False
    shared = shared_key(provider["kind"], *registries)
    return (shared[0], True) if shared else (None, False)


def problem_with(provider: dict[str, Any], *registries: Any) -> str | None:
    if key_for(provider, *registries)[0] or PROVIDERS[provider["kind"]].key_optional:
        return None
    return f"{provider['name']} needs an API key."


@dataclass(frozen=True)
class Resolved:
    provider: TtsProvider
    config: ProviderConfig
    key_shared: bool


def resolve_model(
    provider: dict[str, Any], model_id: str, options: dict[str, str] | None, *registries: Any
) -> tuple[Resolved | None, str | None]:
    """How to call one provider's model, or why it cannot be called."""

    supplier = PROVIDERS.get(provider["kind"])
    if supplier is None:
        return None, "That supplier is no longer available."
    problem = problem_with(provider, *registries)
    if problem:
        return None, problem
    key, shared = key_for(provider, *registries)
    config = ProviderConfig(provider["baseUrl"], key, model_id, clean_options(supplier, options))
    return Resolved(supplier, config, shared), None


def resolve(
    saved: dict[str, Any] | None, *registries: Any, role: str = "answers"
) -> tuple[Resolved | None, str | None]:
    """The model a job speaks with and how to call it, or why there is none."""

    config = clean_config(saved)
    choice = config["roles"].get(role) or {}
    if not choice.get("model"):
        return None, "Choose a voice in Settings → Read aloud."
    found = find_model(providers_in_use(config, *registries), choice["model"])
    if found is None:
        return None, "Its model was removed. Choose another in Settings → Read aloud."
    provider, model = found
    return resolve_model(provider, model["id"], choice.get("options"), *registries)


# Clips ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Clip:
    id: str
    message_id: str
    duration_seconds: float
    audio_bytes: int
    cached: bool
    described_by: str | None
    provider: str
    voice: str | None


def _clip_of(row: SpeechClip, cached: bool) -> Clip:
    options = json.loads(row.options_json or "{}")
    return Clip(
        row.id,
        row.message_id,
        row.duration_seconds,
        row.audio_bytes,
        cached,
        row.described_by,
        row.provider,
        options.get("voice"),
    )


def cache_key(text: str, resolved: Resolved) -> str:
    payload = json.dumps(
        [
            NARRATION_VERSION,
            resolved.provider.kind,
            resolved.config.model,
            sorted(resolved.config.options.items()),
            text,
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class SpeechNotFound(LookupError):
    pass


class Job:
    """An answer being made part by part, so the first part can play before the last exists."""

    def __init__(self, job_id: str, pieces: list[str]) -> None:
        self.id = job_id
        self.pieces = pieces
        self.audio: list[bytes | None] = [None] * len(pieces)
        self.error: TtsError | None = None
        self.changed = asyncio.Event()
        self.cancelled = False

    def publish(self) -> None:
        self.changed.set()
        self.changed = asyncio.Event()

    async def part(self, index: int) -> bytes:
        while True:
            audio = self.audio[index]
            if audio is not None:
                return audio
            if self.error is not None:
                raise self.error
            await self.changed.wait()


@dataclass(frozen=True)
class Begun:
    """Either the finished clip, or a job whose parts are fetched one by one."""

    clip: Clip | None
    job: Job | None


MAX_JOBS = 6


async def _made(resolved: Resolved, piece: str, label: str) -> bytes:
    """One piece from the supplier, logged with how long it took and what came back."""

    started = time.perf_counter()
    audio = await resolved.provider.synthesize(resolved.config, piece)
    described, sound = describe_wav(audio)
    logger.log(
        logging.INFO if sound else logging.WARNING,
        "Speech %s: %d chars from %s in %.0f ms → %s",
        label,
        len(piece),
        resolved.provider.kind,
        (time.perf_counter() - started) * 1000,
        described,
    )
    return audio


class TtsService:
    def __init__(self, sessions: sessionmaker[Session], directory: Path) -> None:
        self.sessions = sessions
        self.directory = directory
        self._locks: dict[str, asyncio.Lock] = {}
        self._running: dict[str, Job] = {}
        self._jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    # Making speech ---------------------------------------------------------------

    async def synthesize(self, resolved: Resolved, script: str) -> bytes:
        pieces = split_for_speech(script, resolved.provider.max_chars)
        if not pieces:
            raise TtsError("There is nothing in this answer to read aloud.")
        parts: list[bytes] = []
        # In order and one at a time: suppliers rate-limit, and a failure stops early.
        for index, piece in enumerate(pieces):
            parts.append(await _made(resolved, piece, f"part {index + 1}/{len(pieces)}"))
        return join_wav(parts)

    async def sample(self, resolved: Resolved) -> bytes:
        return await self.synthesize(resolved, SAMPLE_TEXT)

    async def speak(
        self, session_id: str, message_id: str, resolved: Resolved, gateway: ModelGateway | None
    ) -> Clip:
        """The clip for an assistant message, made now or found."""

        with session_scope(self.sessions) as session:
            message = session.scalar(
                select(SessionMessage).where(
                    SessionMessage.id == message_id, SessionMessage.session_id == session_id
                )
            )
            if message is None:
                raise SpeechNotFound("There is no such message")
            if message.role != "assistant":
                raise TtsError("Only answers are read aloud.")
            content = message.content
        key = cache_key(content, resolved)
        # One at a time per clip: a second click while it is being made waits, then plays it.
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            found = self._find(key)
            if found is not None:
                return found
            script = await narrate(
                content,
                gateway,
                understands_structure=resolved.provider.reads_structure,
            )
            audio = await self.synthesize(resolved, script.text)
            return await asyncio.to_thread(
                self._store, message_id, key, resolved, script.text, script.model, audio
            )

    async def begin(
        self,
        session_id: str,
        message_id: str,
        resolved: Resolved,
        gateway: ModelGateway | None,
        fresh: bool = False,
    ) -> Begun:
        """Start an answer: found on disk, or made in parts that can be played as they arrive.

        ``fresh`` drops the kept recording (and any being made) for this voice and makes it again.
        """

        with session_scope(self.sessions) as session:
            message = session.scalar(
                select(SessionMessage).where(
                    SessionMessage.id == message_id, SessionMessage.session_id == session_id
                )
            )
            if message is None:
                raise SpeechNotFound("There is no such message")
            if message.role != "assistant":
                raise TtsError("Only answers are read aloud.")
            content = message.content
        key = cache_key(content, resolved)
        if fresh:
            self._forget(key)
        found = self._find(key)
        if found is not None:
            logger.info("Speech for message %s: kept clip %s", message_id, found.id)
            return Begun(found, None)
        running = self._running.get(key)
        if running is not None:
            return Begun(None, running)
        script = await narrate(
            content, gateway, understands_structure=resolved.provider.reads_structure
        )
        running = self._running.get(key)
        if running is not None:
            return Begun(None, running)
        pieces = split_for_listening(script.text, resolved.provider.max_chars)
        if not pieces:
            raise TtsError("There is nothing in this answer to read aloud.")
        job = Job(f"job-{secrets.token_hex(8)}", pieces)
        logger.info(
            "Speech %s for message %s: %d part(s), %s %s",
            job.id,
            message_id,
            len(pieces),
            resolved.provider.kind,
            resolved.config.model,
        )
        self._running[key] = job
        self._jobs[job.id] = job
        while len(self._jobs) > MAX_JOBS:
            self._jobs.pop(next(iter(self._jobs)))
        task = asyncio.create_task(self._make(job, key, message_id, resolved, script))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return Begun(None, job)

    async def _make(
        self, job: Job, key: str, message_id: str, resolved: Resolved, script: Any
    ) -> None:
        # In order and one at a time, like synthesize(): suppliers rate-limit.
        try:
            for index, piece in enumerate(job.pieces):
                if job.cancelled:
                    logger.info("Speech %s was replaced before part %d", job.id, index + 1)
                    return
                job.audio[index] = await _made(
                    resolved, piece, f"{job.id} part {index + 1}/{len(job.pieces)}"
                )
                job.publish()
            if not job.cancelled and self._find(key) is None:
                audio = join_wav([part for part in job.audio if part is not None])
                await asyncio.to_thread(
                    self._store, message_id, key, resolved, script.text, script.model, audio
                )
        except TtsError as error:
            logger.warning("Speech %s failed: %s", job.id, error)
            job.error = error
            job.publish()
        except Exception:  # a broken join or disk must still end the wait
            logger.exception("Making speech failed")
            job.error = TtsError("The answer could not be turned into speech.")
            job.publish()
        finally:
            if self._running.get(key) is job:
                del self._running[key]

    def job_part(self, job_id: str, index: int) -> Awaitable[bytes]:
        job = self._jobs.get(job_id)
        if job is None or not 0 <= index < len(job.pieces):
            raise SpeechNotFound("This recording is gone. Read the answer again to make it.")
        return job.part(index)

    def job_script(self, job_id: str) -> str:
        job = self._jobs.get(job_id)
        if job is None:
            raise SpeechNotFound("This recording is gone. Read the answer again to make it.")
        return "\n\n".join(job.pieces)

    def _forget(self, key: str) -> None:
        """Drop the kept recording for a key, and stop one still being made."""

        running = self._running.pop(key, None)
        if running is not None:
            running.cancelled = True
            running.error = TtsError("This reading was replaced by a new one.")
            running.publish()
        with session_scope(self.sessions) as session:
            row = session.scalar(select(SpeechClip).where(SpeechClip.cache_key == key))
            name = row.audio_file if row else None
            if row is not None:
                session.delete(row)
        if name:
            (self.directory / name).unlink(missing_ok=True)

    def _find(self, key: str) -> Clip | None:
        with session_scope(self.sessions) as session:
            row = session.scalar(select(SpeechClip).where(SpeechClip.cache_key == key))
            if row is None:
                return None
            if not (self.directory / row.audio_file).is_file():
                # The audio was deleted from disk: forget the clip and make it again.
                session.delete(row)
                return None
            return _clip_of(row, cached=True)

    def _store(
        self,
        message_id: str,
        key: str,
        resolved: Resolved,
        script: str,
        described_by: str | None,
        audio: bytes,
    ) -> Clip:
        self.directory.mkdir(parents=True, exist_ok=True)
        name = f"{key[:32]}.wav"
        temporary = self.directory / f".{secrets.token_hex(4)}.tmp"
        temporary.write_bytes(audio)
        temporary.replace(self.directory / name)
        with session_scope(self.sessions) as session:
            row = SpeechClip(
                id=f"clip-{secrets.token_hex(8)}",
                message_id=message_id,
                cache_key=key,
                provider=resolved.provider.kind,
                model=resolved.config.model,
                options_json=json.dumps(resolved.config.options, ensure_ascii=False),
                script=script,
                described_by=described_by,
                audio_file=name,
                audio_bytes=len(audio),
                duration_seconds=round(wav_seconds(audio), 2),
            )
            session.add(row)
            session.flush()
            return _clip_of(row, cached=False)

    # Finding and clearing --------------------------------------------------------

    def audio_path(self, clip_id: str) -> Path:
        with session_scope(self.sessions) as session:
            row = session.get(SpeechClip, clip_id)
            path = self.directory / row.audio_file if row else None
        if path is None or not path.is_file():
            raise SpeechNotFound("This recording is gone. Read the answer again to make it.")
        return path

    def script_of(self, clip_id: str) -> str:
        with session_scope(self.sessions) as session:
            row = session.get(SpeechClip, clip_id)
            if row is None:
                raise SpeechNotFound("This recording is gone.")
            return row.script

    def stats(self) -> dict[str, Any]:
        with session_scope(self.sessions) as session:
            count, size = session.execute(
                select(
                    func.count(SpeechClip.id),
                    func.coalesce(func.sum(SpeechClip.audio_bytes), 0),
                )
            ).one()
        return {"clips": int(count), "bytes": int(size)}

    def clear(self) -> dict[str, Any]:
        """Delete every kept clip; answers are simply spoken again when asked."""

        with session_scope(self.sessions) as session:
            files = list(session.scalars(select(SpeechClip.audio_file)))
            session.execute(delete(SpeechClip))
        for name in files:
            (self.directory / name).unlink(missing_ok=True)
        return self.stats()
