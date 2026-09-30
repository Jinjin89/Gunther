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
    join_wav,
    split_for_speech,
    wav_seconds,
)

logger = logging.getLogger(__name__)

SAMPLE_TEXT = "Hello, this is how I will read your answers. 你好，这就是我朗读回答的声音。"


class TtsConfigError(ValueError):
    """Settings that cannot be saved; the message says why."""


# Settings ---------------------------------------------------------------------------


def default_config() -> dict[str, Any]:
    return {"active": None, "autoRead": False, "providers": {}}


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


def clean_provider_entry(provider: TtsProvider, raw: dict[str, Any]) -> dict[str, Any]:
    model = str(raw.get("model") or provider.models[0]).strip()
    if not model or len(model) > 200 or re.search(r"\s", model):
        raise TtsConfigError("Model: use the model's id, without spaces.")
    options: dict[str, str] = {}
    given = raw.get("options") or {}
    if not isinstance(given, dict):
        raise TtsConfigError("Options are not in a shape Gunther understands.")
    for option in provider.options:
        value = str(given.get(option.key) or option.default).strip()
        allowed = {choice.value for choice in option.choices}
        if not value or len(value) > 80:
            raise TtsConfigError(f"{option.label}: choose one.")
        if allowed and value not in allowed and not option.allow_custom:
            raise TtsConfigError(f"{option.label}: {value} is not one of this supplier's choices.")
        options[option.key] = value
    return {
        "apiKey": _clean_key(raw.get("apiKey")),
        "baseUrl": _clean_url(raw.get("baseUrl") or provider.base_url),
        "model": model,
        "options": options,
    }


def clean_config(raw: dict[str, Any]) -> dict[str, Any]:
    active = raw.get("active")
    if active is not None and active not in PROVIDERS:
        raise TtsConfigError("Choose a supplier from the list.")
    if not isinstance(raw.get("autoRead", False), bool):
        raise TtsConfigError("Reading automatically must be on or off.")
    providers = {}
    for kind, entry in (raw.get("providers") or {}).items():
        if kind not in PROVIDERS or not isinstance(entry, dict):
            raise TtsConfigError(f"Unknown supplier: {kind}")
        providers[kind] = clean_provider_entry(PROVIDERS[kind], entry)
    return {"active": active, "autoRead": bool(raw.get("autoRead", False)), "providers": providers}


def shared_key(kind: str, *registries: Any) -> str | None:
    """A key already saved for the same company elsewhere (Qwen's, for its models)."""

    for registry in registries:
        for provider in getattr(registry, "providers", []) or []:
            if provider.get("kind") == kind and provider.get("apiKey"):
                return provider["apiKey"]
    return None


@dataclass(frozen=True)
class Resolved:
    provider: TtsProvider
    config: ProviderConfig
    key_shared: bool


def resolve(
    saved: dict[str, Any] | None, *registries: Any, kind: str | None = None
) -> tuple[Resolved | None, str | None]:
    """The supplier in use and how to call it, or why there is none."""

    config = saved or default_config()
    kind = kind or config.get("active")
    if not kind:
        return None, "Choose a voice supplier in Settings → Read aloud."
    provider = PROVIDERS.get(kind)
    if provider is None:
        return None, "That supplier is no longer available."
    entry = (config.get("providers") or {}).get(kind) or clean_provider_entry(provider, {})
    key = entry.get("apiKey")
    shared = False
    if not key:
        key = shared_key(kind, *registries)
        shared = bool(key)
    if not key and not provider.key_optional:
        return None, f"{provider.name} needs an API key."
    return (
        Resolved(
            provider,
            ProviderConfig(entry["baseUrl"], key, entry["model"], dict(entry["options"])),
            shared,
        ),
        None,
    )


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


class TtsService:
    def __init__(self, sessions: sessionmaker[Session], directory: Path) -> None:
        self.sessions = sessions
        self.directory = directory
        self._locks: dict[str, asyncio.Lock] = {}

    # Making speech ---------------------------------------------------------------

    async def synthesize(self, resolved: Resolved, script: str) -> bytes:
        pieces = split_for_speech(script, resolved.provider.max_chars)
        if not pieces:
            raise TtsError("There is nothing in this answer to read aloud.")
        parts: list[bytes] = []
        # In order and one at a time: suppliers rate-limit, and a failure stops early.
        for piece in pieces:
            parts.append(await resolved.provider.synthesize(resolved.config, piece))
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
