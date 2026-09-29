from __future__ import annotations

import asyncio
import base64
import io
import json
import wave
from contextlib import suppress
from urllib.parse import quote

import httpx
from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import connect


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


async def _proxy_sensevoice_transcription(
    websocket: WebSocket,
    url: str,
    segment_seconds: float,
    service_status: dict[str, object],
) -> None:
    await websocket.accept()
    root = _sensevoice_root(url)
    segment_size = max(48_000, int(24_000 * 2 * max(1.0, min(segment_seconds, 10.0))))
    segment_size -= segment_size % 2
    pending = bytearray()
    sequence = 0
    audio_cursor_seconds = 0.0
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            with suppress(httpx.HTTPError):
                await client.post(f"{root}/reset")
            await websocket.send_json(
                {
                    "type": "service.ready",
                    "provider": "sensevoice",
                    "model": service_status.get("model", "sensevoice-small"),
                    "local": True,
                    "diarize": bool(service_status.get("diarize")),
                }
            )

            async def transcribe_segment(chunk: bytes, start_seconds: float) -> None:
                nonlocal sequence
                if len(chunk) < 2:
                    return
                response = await client.post(
                    f"{root}/v1/audio/transcriptions",
                    files={"file": ("segment.wav", _pcm16_wav(chunk), "audio/wav")},
                    data={"model": "sensevoice", "response_format": "json"},
                )
                response.raise_for_status()
                payload = response.json()
                transcript = str(payload.get("text", "")).strip()
                if not transcript:
                    return
                sequence += 1
                await websocket.send_json(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": f"sensevoice-{sequence}",
                        "transcript": transcript,
                        "provider": "sensevoice",
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
                                "code": "sensevoice_segment_failed",
                                "message": f"SenseVoice missed one segment: {error}",
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
                    "code": "sensevoice_unavailable",
                    "message": f"Local SenseVoice stopped responding: {error}",
                }
            )
            await websocket.close(code=1011)


async def _proxy_openai_transcription(
    websocket: WebSocket,
    api_key: str,
    model: str,
    context: str,
    delay: str,
    languages: list[str],
) -> None:
    await websocket.accept()
    url = f"wss://api.openai.com/v1/realtime?model={quote(model)}"
    try:
        async with connect(
            url,
            additional_headers={"Authorization": f"Bearer {api_key}"},
            max_size=None,
        ) as upstream:
            transcription: dict[str, object] = {
                "model": model,
                "languages": languages or ["en", "zh-cn"],
                "delay": delay,
            }
            if context.strip():
                transcription["prompt"] = context.strip()[:1_000]
            await upstream.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "transcription",
                            "audio": {
                                "input": {
                                    "format": {"type": "audio/pcm", "rate": 24_000},
                                    "transcription": transcription,
                                    "turn_detection": {
                                        "type": "server_vad",
                                        "threshold": 0.5,
                                        "prefix_padding_ms": 300,
                                        "silence_duration_ms": 700,
                                    },
                                }
                            },
                        },
                    }
                )
            )
            await websocket.send_json(
                {"type": "service.ready", "provider": "openai", "model": model}
            )

            async def client_to_upstream() -> None:
                while True:
                    message = await websocket.receive_text()
                    payload = json.loads(message)
                    if payload.get("type") not in {
                        "input_audio_buffer.append",
                        "input_audio_buffer.commit",
                        "input_audio_buffer.clear",
                    }:
                        continue
                    await upstream.send(message)

            async def upstream_to_client() -> None:
                async for message in upstream:
                    await websocket.send_text(message)

            tasks = {
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            }
            done, pending_tasks = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending_tasks:
                task.cancel()
            for task in done:
                with suppress(WebSocketDisconnect, asyncio.CancelledError):
                    task.result()
    except WebSocketDisconnect:
        return
    except Exception:
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {
                    "type": "service.error",
                    "code": "provider_unavailable",
                    "message": (
                        "Realtime transcription could not connect. "
                        "The recording is still local."
                    ),
                }
            )
            await websocket.close(code=1011)


async def proxy_realtime_transcription(
    websocket: WebSocket,
    api_key: str | None,
    model: str,
    context: str = "",
    delay: str = "medium",
    languages: list[str] | None = None,
    provider: str = "auto",
    sensevoice_url: str = "http://127.0.0.1:8765",
    sensevoice_segment_seconds: float = 3.2,
) -> None:
    if provider != "openai":
        service_status = await sensevoice_health(sensevoice_url)
        if service_status is not None:
            await _proxy_sensevoice_transcription(
                websocket,
                sensevoice_url,
                sensevoice_segment_seconds,
                service_status,
            )
            return
    if provider != "sensevoice" and api_key:
        await _proxy_openai_transcription(
            websocket,
            api_key,
            model,
            context,
            delay,
            languages or ["en", "zh-cn"],
        )
        return

    await websocket.accept()
    await websocket.send_json(
        {
            "type": "service.error",
            "code": "provider_unavailable" if provider == "sensevoice" else "not_configured",
            "message": (
                "Local SenseVoice is unavailable and no fallback STT provider is configured."
            ),
        }
    )
    await websocket.close(code=1011)
