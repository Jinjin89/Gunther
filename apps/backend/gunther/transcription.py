"""Provider requests and transcripts for audio sent as a complete WAV or a short window."""

from __future__ import annotations

import base64
import io
import wave
from dataclasses import dataclass, field
from typing import Any

import httpx
from httpx import AsyncClient

from gunther.qwen_stream import CONTEXT_TURN_CHARS, LIVE_RATE, split_sentences
from gunther.speech_registry import Choice, qwen_upload_id

# The recent text sent to OpenAI-style servers as context.
CONTEXT_CHARS = 800


class AsrHttpConfigurationError(httpx.TransportError):
    """The ASR HTTP client cannot use its configured transport."""


class AsrHttpTransport(httpx.AsyncBaseTransport):
    """Own HTTPX's direct and proxy connection pools for one app lifespan."""

    def __init__(self) -> None:
        self._routing: httpx.AsyncClient | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._routing is None:
            # Construction has no await, so requests on this lifespan's loop share it.
            try:
                self._routing = httpx.AsyncClient()
            except (ImportError, ValueError) as error:
                raise AsrHttpConfigurationError(
                    "Speech transcription cannot use the configured HTTP proxy.",
                    request=request,
                ) from error
        # Use the default proxy and NO_PROXY routing without sending through a shared client.
        # Only the take's client may process response cookies or attach credentials.
        transport = self._routing._transport_for_url(request.url)
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        if self._routing is not None:
            await self._routing.aclose()


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """Let a request client close without closing the lifespan's connection pool."""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._transport.handle_async_request(request)


def asr_http_client(timeout: float, transport: httpx.AsyncBaseTransport | None) -> AsyncClient:
    if transport is None:
        return httpx.AsyncClient(timeout=timeout)
    return AsyncClient(timeout=timeout, transport=_BorrowedTransport(transport))


def sensevoice_root(url: str) -> str:
    normalized = url.rstrip("/")
    return normalized[:-3] if normalized.endswith("/v1") else normalized


async def sensevoice_health(
    url: str, timeout: float = 1.2, *, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, object] | None:
    try:
        async with asr_http_client(timeout, transport) as client:
            response = await client.get(f"{sensevoice_root(url)}/health")
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) and payload.get("ok") is True else None
    except AsrHttpConfigurationError:
        raise
    except (httpx.HTTPError, ImportError, ValueError, TypeError):
        return None


def pcm16_wav(pcm: bytes, sample_rate: int = 24_000) -> bytes:
    aligned = pcm[: len(pcm) - (len(pcm) % 2)]
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        destination.writeframes(aligned)
    return output.getvalue()


def qwen_root(base_url: str) -> str:
    """Qwen's address without a path: the saved one may still end in /compatible-mode/v1."""

    root = base_url.rstrip("/")
    return root.removesuffix("/compatible-mode/v1").removesuffix("/api/v1")


def qwen_endpoint(base_url: str) -> str:
    return f"{qwen_root(base_url)}/api/v1/services/aigc/multimodal-generation/generation"


def qwen_request(
    model: str, wav: bytes, sample_rate: int = 24_000, context: list[str] | None = None
) -> dict[str, object]:
    """Qwen-Audio takes the audio as a data URI inside a message.

    ``context`` (terms, then what came before and after) goes first, one user message
    each, at most 400 characters; Qwen uses it to hear the same words again.
    """

    encoded = base64.b64encode(wav).decode("ascii")
    hints = [
        {"role": "user", "content": [{"type": "input_text", "text": text[:400]}]}
        for text in (context or [])
        if text.strip()
    ]
    return {
        "model": model,
        "input": {
            "messages": [
                *hints,
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:audio/wav;base64,{encoded}",
                                "format": "wav",
                            },
                        }
                    ],
                },
            ]
        },
        "parameters": {"format": "wav", "sample_rate": str(sample_rate)},
    }


def qwen_text(payload: object) -> str:
    """The words in a Qwen-Audio reply: ``output.text``."""

    output = payload.get("output") if isinstance(payload, dict) else None
    if isinstance(output, dict) and isinstance(output.get("text"), str):
        return output["text"].strip()
    return ""


def qwen_heard_nothing(response: httpx.Response) -> bool:
    """Qwen refuses audio with no speech in it (a pause, only noise) instead of returning ''.

    Qwen-Audio 3.1 says ASR_RESPONSE_HAVE_NO_WORDS; Qwen-Audio 3.0 answers silence with
    a 400 whose body is an empty JSON object.
    """

    if response.status_code != 400:
        return False
    if "ASR_RESPONSE_HAVE_NO_WORDS" in response.text:
        return True
    try:
        return response.json() == {}
    except ValueError:
        return False


def _qwen_upload_request(
    base_url: str,
    api_key: str,
    model: str,
    wav: bytes,
    sample_rate: int = 24_000,
    context: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "url": qwen_endpoint(base_url),
        "headers": {"Authorization": f"Bearer {api_key}"},
        "json": qwen_request(model, wav, sample_rate, context),
    }


async def try_qwen_model(
    client: httpx.AsyncClient, base_url: str, api_key: str, model: str
) -> str | None:
    """Send half a second of silence to a model's upload id; what is wrong, or None if it answers.

    Qwen's model list leaves out the Qwen-Audio recognisers, so asking the model is the
    only reliable check.
    """

    model = qwen_upload_id(model)
    try:
        response = await client.post(
            **_qwen_upload_request(base_url, api_key, model, pcm16_wav(b"\x00\x00" * 12_000))
        )
    except httpx.HTTPError:
        return f"{model}: Qwen did not answer. Try again."
    if response.status_code < 400 or qwen_heard_nothing(response):
        return None
    if response.status_code in (401, 403):
        return "Qwen did not accept this API key."
    text = response.text
    if response.status_code == 404 or "Model not exist" in text:
        return f"{model}: Qwen has no model with this id."
    if "Input should be" in text:
        return (
            f"{model} takes an older kind of request. "
            "Use a Qwen-Audio model such as qwen-audio-3.1-asr-flash."
        )
    # Anything else came from the model itself (silence it could not read), so it exists.
    return None


@dataclass(frozen=True)
class UploadedTranscript:
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)


async def transcribe_upload(
    client: httpx.AsyncClient,
    choice: Choice,
    pcm: bytes,
    *,
    prompt: str,
    context: list[str],
    retry_connection: bool = False,
) -> UploadedTranscript:
    """Submit a 16 kHz window and decode the provider's text and any word times."""

    wav = pcm16_wav(pcm, LIVE_RATE)
    if choice.kind == "qwen":
        request = _qwen_upload_request(
            choice.base_url,
            choice.api_key or "",
            qwen_upload_id(choice.model),
            wav,
            LIVE_RATE,
            context,
        )
    else:
        if choice.kind == "sensevoice":
            endpoint = f"{sensevoice_root(choice.base_url)}/v1/audio/transcriptions"
            headers: dict[str, str] = {}
            form = {"model": "sensevoice", "response_format": "json"}
        else:
            endpoint = f"{choice.base_url.rstrip('/')}/audio/transcriptions"
            headers = {"Authorization": f"Bearer {choice.api_key}"} if choice.api_key else {}
            form = {"model": choice.model, "response_format": "json"}
            if choice.language:
                form["language"] = choice.language
            if prompt:
                form["prompt"] = prompt
        request = {
            "url": endpoint,
            "headers": headers,
            "files": {"file": ("segment.wav", wav, "audio/wav")},
            "data": form,
        }

    try:
        response = await client.post(**request)
    except (httpx.ConnectError, httpx.RemoteProtocolError):
        # A window check can retry a lost connection once. Timeouts fail on the first try.
        if not retry_connection:
            raise
        response = await client.post(**request)

    if choice.kind == "qwen" and qwen_heard_nothing(response):
        return UploadedTranscript("")
    response.raise_for_status()
    payload = response.json()
    if choice.kind == "qwen":
        return UploadedTranscript(qwen_text(payload), _qwen_words(payload))
    return UploadedTranscript(str(payload.get("text", "")).strip())


async def transcribe_segment(
    client: httpx.AsyncClient, choice: Choice, pcm: bytes, *, title: str, recent: str = ""
) -> str:
    heard = await transcribe_upload(
        client,
        choice,
        pcm,
        prompt=f"{title.strip()[:200]}\n{recent[-CONTEXT_CHARS:]}".strip(),
        context=[title.strip()[:CONTEXT_TURN_CHARS], recent.strip()[-CONTEXT_TURN_CHARS:]],
    )
    return heard.text


# Listening again ---------------------------------------------------------------

# Audio is read this much past each end so a word at the edge is not cut.
RELISTEN_PAD_MS = 300
# A minute of 16 kHz audio is ~2.6 MB as base64. On a slow link a check fails within about a
# minute and says so (the live text stays, with Retry) rather than holding up later checks.
RELISTEN_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
RELISTEN_PROMPT_CHARS = 800


class ListenError(Exception):
    """The model could not listen again; the message is the provider's, for the user."""


def _provider_message(error: Exception, label: str) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        detail = ""
        try:
            body = error.response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            nested = body.get("error")
            candidates = [
                body.get("message"),
                nested.get("message") if isinstance(nested, dict) else nested,
                body.get("detail"),
            ]
            detail = next((str(item) for item in candidates if isinstance(item, str) and item), "")
        if not detail:
            detail = error.response.text.strip()[:200]
        status = error.response.status_code
        return f"{label} answered {status}: {detail}" if detail else f"{label} answered {status}."
    # A timeout's message is empty: name the kind of failure instead.
    return f"{label} could not be reached ({str(error) or type(error).__name__})."


def _qwen_words(payload: object) -> list[dict[str, Any]]:
    """The words of a Qwen-Audio reply: ``output.sentence`` is one object, or a list of them."""

    output = payload.get("output") if isinstance(payload, dict) else None
    sentence = output.get("sentence") if isinstance(output, dict) else None
    found: list[dict[str, Any]] = []
    for item in sentence if isinstance(sentence, list) else [sentence]:
        words = item.get("words") if isinstance(item, dict) else None
        if isinstance(words, list):
            found.extend(word for word in words if isinstance(word, dict))
    return found


def _join_text(parts: list[str]) -> str:
    """Sentences back into text: a space between words, none where either side is CJK."""

    out = ""
    for part in parts:
        if out and not (_is_cjk(out[-1]) or _is_cjk(part[:1])):
            out += " "
        out += part
    return out


def _is_cjk(character: str) -> bool:
    return bool(character) and ord(character) >= 0x2E80


async def relisten_audio(
    choice: Choice,
    pcm: bytes,
    *,
    audio_start_ms: int,
    start_ms: int,
    end_ms: int,
    terms: list[str],
    before: str,
    after: str,
    timeout: httpx.Timeout = RELISTEN_TIMEOUT,
) -> dict[str, Any]:
    """Have a job's model write the words of ``pcm`` (16 kHz) again, with context.

    ``audio_start_ms`` is where ``pcm`` begins on the recording clock; ``start_ms`` and
    ``end_ms`` are the range asked for, the rest being padding. Qwen answers with word
    times, which become sentences on the recording clock (those lying only in the
    padding are dropped); other providers answer text only, with no sentences.
    """

    term_text = ", ".join(term.strip() for term in terms if term.strip())
    prompt = term_text
    room = RELISTEN_PROMPT_CHARS - len(prompt) - 1
    if before.strip() and room > 0:
        prompt = f"{prompt} {before.strip()[-room:]}".strip()
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            heard = await transcribe_upload(
                client,
                choice,
                pcm,
                prompt=prompt[:RELISTEN_PROMPT_CHARS],
                context=[term_text, before.strip()[-CONTEXT_TURN_CHARS:], after.strip()],
                retry_connection=True,
            )
            timed = split_sentences(heard.words, audio_start_ms)
            sentences = [
                sentence
                for sentence in timed
                if sentence["end_ms"] > start_ms and sentence["start_ms"] < end_ms
            ]
            text = heard.text
            if len(sentences) != len(timed):
                # Words that lie only in the padding are not part of the answer.
                text = _join_text([sentence["text"] for sentence in sentences])
            return {"text": text, "sentences": sentences}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
            raise ListenError(_provider_message(error, choice.provider)) from error
