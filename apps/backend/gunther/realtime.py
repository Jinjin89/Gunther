from __future__ import annotations

import base64
import io
import json
import wave
from collections.abc import Awaitable, Callable
from contextlib import suppress

import httpx
from fastapi import WebSocket, WebSocketDisconnect

from gunther.speech_registry import Choice


def _sensevoice_root(url: str) -> str:
    normalized = url.rstrip("/")
    return normalized[:-3] if normalized.endswith("/v1") else normalized


async def sensevoice_health(url: str, timeout: float = 1.2) -> dict[str, object] | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{_sensevoice_root(url)}/health")
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) and payload.get("ok") is True else None
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def _pcm16_wav(pcm: bytes, sample_rate: int = 24_000) -> bytes:
    aligned = pcm[: len(pcm) - (len(pcm) % 2)]
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        destination.writeframes(aligned)
    return output.getvalue()


Transcriber = Callable[[httpx.AsyncClient, bytes], Awaitable[str]]


def _form_transcriber(endpoint: str, headers: dict[str, str], form: dict[str, str]) -> Transcriber:
    """An ``/audio/transcriptions`` server: the segment goes up as a WAV file."""

    async def transcribe(client: httpx.AsyncClient, pcm: bytes) -> str:
        response = await client.post(
            endpoint,
            headers=headers,
            files={"file": ("segment.wav", _pcm16_wav(pcm), "audio/wav")},
            data=form,
        )
        response.raise_for_status()
        return str(response.json().get("text", "")).strip()

    return transcribe


def qwen_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


def qwen_request(model: str, wav: bytes, language: str = "") -> dict[str, object]:
    """Qwen3-ASR takes the audio as a data URI inside a chat message."""

    encoded = base64.b64encode(wav).decode("ascii")
    body: dict[str, object] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": f"data:audio/wav;base64,{encoded}"},
                    }
                ],
            }
        ],
        "stream": False,
        "asr_options": {"enable_itn": False, **({"language": language} if language else {})},
    }
    return body


def qwen_text(payload: object) -> str:
    """The words in a Qwen reply, whichever of its two shapes it came in."""

    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = (str(part.get("text", "")) for part in content if isinstance(part, dict))
            return "".join(parts).strip()
    output = payload.get("output")
    if isinstance(output, dict) and isinstance(output.get("text"), str):
        return output["text"].strip()
    return ""


def _qwen_transcriber(base_url: str, api_key: str, model: str, language: str) -> Transcriber:
    async def transcribe(client: httpx.AsyncClient, pcm: bytes) -> str:
        response = await client.post(
            qwen_endpoint(base_url),
            headers={"Authorization": f"Bearer {api_key}"},
            json=qwen_request(model, _pcm16_wav(pcm), language),
        )
        response.raise_for_status()
        return qwen_text(response.json())

    return transcribe


async def _proxy_segmented_transcription(
    websocket: WebSocket,
    *,
    transcriber: Transcriber,
    provider: str,
    label: str,
    segment_seconds: float,
    ready: dict[str, object],
    reset_url: str | None = None,
) -> None:
    """Cut live audio into segments and have an OpenAI-style server write each one.

    SenseVoice and any ``/audio/transcriptions`` server work alike: audio in,
    text out, one segment at a time, sent on as it arrives.
    """

    await websocket.accept()
    segment_size = max(48_000, int(24_000 * 2 * max(1.0, segment_seconds)))
    segment_size -= segment_size % 2
    pending = bytearray()
    sequence = 0
    audio_cursor_seconds = 0.0
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if reset_url:
                with suppress(httpx.HTTPError):
                    await client.post(reset_url)
            await websocket.send_json({"type": "service.ready", "provider": provider, **ready})

            async def transcribe_segment(chunk: bytes, start_seconds: float) -> None:
                nonlocal sequence
                if len(chunk) < 2:
                    return
                transcript = await transcriber(client, chunk)
                if not transcript:
                    return
                sequence += 1
                await websocket.send_json(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": f"{provider}-{sequence}",
                        "transcript": transcript,
                        "provider": provider,
                        "start_seconds": round(start_seconds, 2),
                    }
                )

            async def flush(force: bool = False) -> None:
                nonlocal audio_cursor_seconds
                while len(pending) >= segment_size or (force and pending):
                    take = segment_size if len(pending) >= segment_size else len(pending)
                    chunk = bytes(pending[:take])
                    del pending[:take]
                    try:
                        await transcribe_segment(chunk, audio_cursor_seconds)
                    except (httpx.HTTPError, ValueError, TypeError) as error:
                        await websocket.send_json(
                            {
                                "type": "service.error",
                                "code": "segment_failed",
                                "message": f"{label} missed one segment: {error}",
                            }
                        )
                    audio_cursor_seconds += len(chunk) / (24_000 * 2)

            while True:
                message = await websocket.receive_text()
                payload = json.loads(message)
                message_type = payload.get("type")
                if message_type == "input_audio_buffer.append":
                    encoded = payload.get("audio")
                    if not isinstance(encoded, str):
                        continue
                    with suppress(ValueError):
                        pending.extend(base64.b64decode(encoded, validate=True))
                    await flush()
                elif message_type == "input_audio_buffer.commit":
                    await flush(force=True)
                elif message_type == "input_audio_buffer.clear":
                    pending.clear()
    except WebSocketDisconnect:
        return
    except Exception as error:
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {
                    "type": "service.error",
                    "code": "provider_unavailable",
                    "message": f"{label} stopped responding: {error}",
                }
            )
            await websocket.close(code=1011)


def compatible_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/audio/transcriptions"


# A take sent whole is cut only when it gets long (Qwen takes about five minutes).
WHOLE_TAKE_SECONDS = 150.0


async def _fail(websocket: WebSocket, code: str, message: str) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "service.error", "code": code, "message": message})
    await websocket.close(code=1011)


async def proxy_realtime_transcription(
    websocket: WebSocket,
    *,
    choice: Choice | None,
    problem: str = "",
    segment_seconds: float = 3.2,
    context: str = "",
) -> None:
    """Have the model chosen for a job write the words of audio sent over the socket.

    Live works in short segments as the audio arrives; otherwise the take waits
    until it ends and goes up whole, which reads better.
    """

    if choice is None:
        await _fail(
            websocket,
            "not_configured",
            f"No transcription is set up. {problem} Choose one in Settings → Transcription.",
        )
        return
    seconds = max(1.0, min(segment_seconds, 10.0)) if choice.stream else WHOLE_TAKE_SECONDS
    ready = {"model": choice.model, "stream": choice.stream, "local": False, "diarize": False}
    reset_url = None
    if choice.kind == "sensevoice":
        status = await sensevoice_health(choice.base_url)
        if status is None:
            await _fail(
                websocket,
                "provider_unavailable",
                f"SenseVoice is not running at {choice.base_url}.",
            )
            return
        root = _sensevoice_root(choice.base_url)
        transcriber = _form_transcriber(
            f"{root}/v1/audio/transcriptions",
            {},
            {"model": "sensevoice", "response_format": "json"},
        )
        ready = {**ready, "local": True, "diarize": bool(status.get("diarize"))}
        reset_url = f"{root}/reset"
    elif choice.kind == "qwen":
        transcriber = _qwen_transcriber(
            choice.base_url, choice.api_key or "", choice.model, choice.language
        )
    else:
        form = {"model": choice.model, "response_format": "json"}
        if choice.language:
            form["language"] = choice.language
        if context.strip():
            form["prompt"] = context.strip()[:1_000]
        transcriber = _form_transcriber(
            compatible_endpoint(choice.base_url),
            {"Authorization": f"Bearer {choice.api_key}"} if choice.api_key else {},
            form,
        )
    await _proxy_segmented_transcription(
        websocket,
        transcriber=transcriber,
        provider=choice.kind,
        label=choice.provider,
        segment_seconds=seconds,
        ready=ready,
        reset_url=reset_url,
    )
