"""The text-to-speech suppliers Gunther can read answers with.

A *provider* is one supplier (Qwen today). It is declared once here: its models,
the options only it has (voice, language, tone... each supplier differs), and a
``synthesize`` function turning one short piece of text into a WAV file. The
Settings page draws the options from these declarations, so a new supplier is a
new entry in ``PROVIDERS`` plus its function, not new UI.

``reads_structure`` says whether a supplier's model understands tables and
pictures well enough to speak them itself. None does yet, so answers with such
parts go through the narrator first (see tts_narration).
"""

from __future__ import annotations

import io
import re
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

REQUEST_TIMEOUT_SECONDS = 60.0


class TtsError(RuntimeError):
    """Speech could not be made. The message says why, in plain words."""


@dataclass(frozen=True)
class Choice:
    value: str
    label: str


@dataclass(frozen=True)
class TtsOption:
    key: str
    label: str
    default: str
    choices: tuple[Choice, ...] = ()
    # A voice the list does not have (suppliers add voices over time) may be typed.
    allow_custom: bool = False
    help: str = ""


@dataclass(frozen=True)
class ProviderConfig:
    """Everything needed to call one supplier."""

    base_url: str
    api_key: str | None
    model: str
    options: dict[str, str]


Synthesize = Callable[[ProviderConfig, str], Awaitable[bytes]]


@dataclass(frozen=True)
class TtsProvider:
    kind: str
    name: str
    base_url: str
    models: tuple[str, ...]
    options: tuple[TtsOption, ...]
    synthesize: Synthesize
    # Longest text one request takes; longer answers are cut at sentence ends.
    max_chars: int
    key_optional: bool = False
    reads_structure: bool = False
    note: str = ""
    sample: dict[str, str] = field(default_factory=dict)

    def defaults(self) -> dict[str, str]:
        return {option.key: option.default for option in self.options}


# Qwen ---------------------------------------------------------------------------

QWEN_VOICES = (
    ("Cherry", "Cherry · warm, female"),
    ("Serena", "Serena · gentle, female"),
    ("Chelsie", "Chelsie · soft, female"),
    ("Momo", "Momo · playful, female"),
    ("Vivian", "Vivian · crisp, female"),
    ("Maia", "Maia · calm, female"),
    ("Bella", "Bella · bright, female"),
    ("Ethan", "Ethan · steady, male"),
    ("Kai", "Kai · relaxed, male"),
    ("Aiden", "Aiden · young, male"),
    ("Ryan", "Ryan · dramatic, male"),
    ("Eldric Sage", "Eldric Sage · elder, male"),
    ("Neil", "Neil · newsreader, male"),
    ("Dylan", "Dylan · Beijing accent"),
    ("Jada", "Jada · Shanghai accent"),
    ("Sunny", "Sunny · Sichuan accent"),
    ("Rocky", "Rocky · Cantonese"),
)
QWEN_LANGUAGES = (
    "Auto",
    "Chinese",
    "English",
    "Japanese",
    "Korean",
    "French",
    "German",
    "Spanish",
    "Italian",
    "Portuguese",
    "Russian",
)


def _qwen_endpoint(base_url: str) -> str:
    """DashScope's speech address. The saved address may be the compatible-mode one."""

    root = base_url.rstrip("/")
    root = root.removesuffix("/compatible-mode/v1").removesuffix("/api/v1")
    return f"{root}/api/v1/services/aigc/multimodal-generation/generation"


async def _qwen_synthesize(config: ProviderConfig, text: str) -> bytes:
    body = {
        "model": config.model,
        "input": {
            "text": text,
            "voice": config.options.get("voice") or "Cherry",
            "language_type": config.options.get("language") or "Auto",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _qwen_endpoint(config.base_url),
                json=body,
                headers={"Authorization": f"Bearer {config.api_key}"},
            )
            if response.status_code in (401, 403):
                raise TtsError("Qwen did not accept this API key.")
            if response.status_code >= 400:
                raise TtsError(f"Qwen could not make speech: {_detail(response)}")
            try:
                url = response.json()["output"]["audio"]["url"]
            except (ValueError, KeyError, TypeError) as error:
                raise TtsError("Qwen answered without any audio.") from error
            # The link is short-lived and needs no key; take the file now.
            audio = await client.get(url)
            if audio.status_code >= 400:
                raise TtsError("Qwen made the speech but it could not be downloaded.")
            return audio.content
    except httpx.TimeoutException as error:
        raise TtsError("Qwen did not answer in time.") from error
    except httpx.HTTPError as error:
        raise TtsError("Could not reach Qwen. Check the address and your connection.") from error


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        return str(payload.get("message") or payload.get("code") or response.status_code)[:200]
    except (ValueError, AttributeError):
        return str(response.status_code)


PROVIDERS: dict[str, TtsProvider] = {
    "qwen": TtsProvider(
        kind="qwen",
        name="Qwen",
        base_url="https://dashscope.aliyuncs.com",
        models=("qwen3-tts-flash",),
        options=(
            TtsOption(
                "voice",
                "Voice",
                "Cherry",
                tuple(Choice(value, label) for value, label in QWEN_VOICES),
                allow_custom=True,
                help="Each voice speaks every language; some carry a regional accent.",
            ),
            TtsOption(
                "language",
                "Language",
                "Auto",
                tuple(
                    Choice(value, "Detect it" if value == "Auto" else value)
                    for value in QWEN_LANGUAGES
                ),
                help="Auto follows the text, and suits answers that mix Chinese and English.",
            ),
        ),
        synthesize=_qwen_synthesize,
        max_chars=500,
        note=(
            "Outside China use dashscope-intl.aliyuncs.com. "
            "The key is the same as Qwen's other services."
        ),
    ),
}


# Sound ---------------------------------------------------------------------------


def join_wav(parts: list[bytes]) -> bytes:
    """One WAV from several with the same format; the pieces of a long answer."""

    if len(parts) == 1:
        return parts[0]
    out = io.BytesIO()
    writer: wave.Wave_write | None = None
    try:
        for part in parts:
            with wave.open(io.BytesIO(part), "rb") as reader:
                if writer is None:
                    writer = wave.open(out, "wb")  # noqa: SIM115 - closed below
                    writer.setparams(reader.getparams())
                elif reader.getparams()[:3] != writer.getparams()[:3]:
                    raise TtsError("The supplier changed its audio format part-way through.")
                writer.writeframes(reader.readframes(reader.getnframes()))
    except (wave.Error, EOFError) as error:
        raise TtsError("The supplier's audio could not be read as a WAV file.") from error
    finally:
        if writer is not None:
            writer.close()
    return out.getvalue()


def wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            return reader.getnframes() / float(reader.getframerate() or 1)
    except (wave.Error, EOFError):
        return 0.0


def split_for_speech(text: str, limit: int) -> list[str]:
    """Pieces no longer than ``limit``, cut after sentences (and lines) where possible."""

    pieces: list[str] = []
    current = ""
    breaks = "。！？!?；;\n."
    sentences: list[str] = []
    buffer = ""
    for index, char in enumerate(text):
        buffer += char
        following = text[index + 1] if index + 1 < len(text) else " "
        # "." ends a sentence only before a space: not in 3.14 or example.com.
        if char in breaks and (char != "." or following.isspace()):
            sentences.append(buffer)
            buffer = ""
    if buffer:
        sentences.append(buffer)
    for sentence in sentences:
        while len(sentence) > limit:
            cut = max(sentence.rfind(mark, 0, limit) for mark in "，,、 ") + 1 or limit
            if len(current) + cut > limit and current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut])
            sentence = sentence[cut:]
        if len(current) + len(sentence) > limit and current:
            pieces.append(current)
            current = ""
        current += sentence
    if current:
        pieces.append(current)
    return [piece.strip() for piece in pieces if piece.strip()]


def split_for_listening(text: str, limit: int, target: int = 260) -> list[str]:
    """Parts to play one after another: a paragraph each, short ones joined up to ``target``.

    The first part is the first paragraph, so playback can begin while the rest is made.
    """

    parts: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        for piece in split_for_speech(paragraph, limit):
            if current and len(current) + 1 + len(piece) > min(target, limit):
                parts.append(current)
                current = ""
            current = f"{current}\n{piece}" if current else piece
        if current and len(current) >= target // 2:
            parts.append(current)
            current = ""
    if current:
        parts.append(current)
    return parts


def describe(provider: TtsProvider) -> dict[str, Any]:
    return {
        "kind": provider.kind,
        "name": provider.name,
        "baseUrl": provider.base_url,
        "models": list(provider.models),
        "keyOptional": provider.key_optional,
        "readsStructure": provider.reads_structure,
        "note": provider.note,
        "options": [
            {
                "key": option.key,
                "label": option.label,
                "default": option.default,
                "allowCustom": option.allow_custom,
                "help": option.help,
                "choices": [{"value": c.value, "label": c.label} for c in option.choices],
            }
            for option in provider.options
        ],
    }
