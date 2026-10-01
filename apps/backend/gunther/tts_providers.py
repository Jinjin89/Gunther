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
    # Where an OpenAI-style list of the key's models lives, for Fetch models; None: none.
    models_url: Callable[[str], str] | None = None

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


def _qwen_models_url(base_url: str) -> str:
    """DashScope lists a key's models on its OpenAI-compatible address."""

    root = base_url.rstrip("/")
    root = root.removesuffix("/compatible-mode/v1").removesuffix("/api/v1")
    return f"{root}/compatible-mode/v1"


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
        base_url="https://maas.qianwenaiapi.com",
        models=(),
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
        models_url=_qwen_models_url,
        note=(
            "The address is the one the Qwen AI platform shows. "
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


def _data_chunk(data: bytes) -> int | None:
    """Where the audio of a WAV starts (just after its ``data`` chunk header), if it has one."""

    offset = 12
    while offset + 8 <= len(data):
        chunk = data[offset : offset + 4]
        size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        if chunk == b"data":
            return offset + 8
        offset += 8 + size + (size & 1)
    return None


def repair_wav(data: bytes) -> bytes:
    """The same WAV with its sizes saying how much audio it really holds.

    Qwen writes its files as a stream and leaves both sizes at their maximum
    (about 2 GB). Chrome plays such a file anyway; WebKit, which the macOS app
    uses, trusts the header and plays silence. Anything that is not a WAV, or
    already agrees with itself, comes back unchanged.
    """

    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    body = _data_chunk(data)
    if body is None:
        return data
    audio = len(data) - body
    riff = (len(data) - 8).to_bytes(4, "little")
    size = audio.to_bytes(4, "little")
    if data[4:8] == riff and data[body - 4 : body] == size:
        return data
    fixed = bytearray(data)
    fixed[4:8] = riff
    fixed[body - 4 : body] = size
    return bytes(fixed)


def describe_wav(data: bytes) -> tuple[str, bool]:
    """The format of a WAV for the log, and whether its header agrees with its length.

    A header that claims more or less audio than the file holds plays in Chrome but
    can play as silence in the macOS app's WebKit, so a mismatch is worth a warning.
    """

    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return f"{len(data)} bytes, not a WAV file (starts {data[:4]!r})", False
    problems: list[str] = []
    riff = int.from_bytes(data[4:8], "little")
    if riff != len(data) - 8:
        problems.append(f"RIFF size says {riff + 8} bytes")
    form, seconds, found_data = "no format chunk", None, False
    rate_bytes = 0
    offset = 12
    while offset + 8 <= len(data):
        chunk = data[offset : offset + 4]
        size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        body = offset + 8
        if chunk == b"fmt " and size >= 16 and body + 16 <= len(data):
            channels = int.from_bytes(data[body + 2 : body + 4], "little")
            rate = int.from_bytes(data[body + 4 : body + 8], "little")
            rate_bytes = int.from_bytes(data[body + 8 : body + 12], "little")
            bits = int.from_bytes(data[body + 14 : body + 16], "little")
            form = f"{rate} Hz, {bits}-bit, {channels} channel(s)"
        elif chunk == b"data":
            found_data = True
            actual = len(data) - body
            if size != actual:
                problems.append(f"data size says {size} bytes but {actual} follow")
            if rate_bytes:
                seconds = actual / rate_bytes
            break
        offset = body + size + (size & 1)
    if not found_data:
        problems.append("no data chunk")
    text = f"{len(data)} bytes, {form}"
    if seconds is not None:
        text += f", {seconds:.1f} s"
    if problems:
        text += "; header disagrees: " + "; ".join(problems)
    return text, not problems


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
