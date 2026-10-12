from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import logging
import secrets
import uuid
import wave
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.protocol import State

from gunther import listen_copy
from gunther.qwen_stream import (
    BYTES_PER_MS,
    LIVE_RATE,
    Pcm16Resampler,
    context_turns,
    finish_task_message,
    run_task_message,
    split_sentences,
    stream_url,
)
from gunther.speech_registry import Choice, qwen_live_id, qwen_upload_id

logger = logging.getLogger(__name__)


async def _accept(websocket: WebSocket) -> None:
    """Accept the client once; a live Qwen socket is accepted before its model is checked."""

    if websocket.client_state == WebSocketState.CONNECTING:
        await websocket.accept()


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


def _form_transcriber(
    endpoint: str, headers: dict[str, str], form: dict[str, str], sample_rate: int = 24_000
) -> Transcriber:
    """An ``/audio/transcriptions`` server: the segment goes up as a WAV file."""

    async def transcribe(client: httpx.AsyncClient, pcm: bytes) -> str:
        response = await client.post(
            endpoint,
            headers=headers,
            files={"file": ("segment.wav", _pcm16_wav(pcm, sample_rate), "audio/wav")},
            data=form,
        )
        response.raise_for_status()
        return str(response.json().get("text", "")).strip()

    return transcribe


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
            qwen_endpoint(base_url),
            headers={"Authorization": f"Bearer {api_key}"},
            json=qwen_request(model, _pcm16_wav(b"\x00\x00" * 12_000)),
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


# How long Qwen's live socket may take to start a task before the model counts as not live.
TASK_START_TIMEOUT = 10.0
# How long to wait for a task to finish after finish-task.
TASK_END_TIMEOUT = 10.0
CONNECT_TIMEOUT = 30.0
# Audio kept to send again if the live socket drops, and queued while it reconnects.
RETAIN_SECONDS = 30.0
RECONNECT_BACKOFF = (0.5, 1.0, 2.0, 4.0)
FRAME_BYTES = 100 * BYTES_PER_MS  # 100 ms of audio per binary frame


async def _next_event(socket: ClientConnection, *names: str) -> str | None:
    """Read events until one of ``names`` arrives (None) or the task fails (its message)."""

    while True:
        message = await socket.recv()
        if isinstance(message, bytes):
            continue
        try:
            header = json.loads(message).get("header") or {}
        except (ValueError, AttributeError):
            continue
        event = header.get("event")
        if event in names:
            return None
        if event == "task-failed":
            return str(header.get("error_message") or header.get("error_code") or "task failed")


async def open_live_socket(
    base_url: str, api_key: str, model: str
) -> tuple[ClientConnection | None, str]:
    """Connect to Qwen's live socket and run one empty task to see that the model works there.

    Returns the open socket (ready for more tasks) and "", or None and why it failed:
    refused outright, or no answer within ``TASK_START_TIMEOUT``.
    """

    try:
        socket = await connect(
            stream_url(base_url),
            additional_headers={"Authorization": f"Bearer {api_key}"},
            open_timeout=CONNECT_TIMEOUT,
            max_size=None,
        )
    except InvalidStatus as error:
        return None, f"Qwen answered {error.response.status_code} when connecting"
    except (OSError, WebSocketException, TimeoutError) as error:
        return None, f"could not connect ({error})"
    task_id = uuid.uuid4().hex
    try:
        await socket.send(run_task_message(task_id, model, []))
        refusal = await asyncio.wait_for(_next_event(socket, "task-started"), TASK_START_TIMEOUT)
        if refusal is not None:
            await socket.close()
            return None, f"Qwen refused it ({refusal})"
        await socket.send(finish_task_message(task_id))
        with suppress(TimeoutError, ConnectionClosed):
            await asyncio.wait_for(_next_event(socket, "task-finished"), 5.0)
        return socket, ""
    except TimeoutError:
        await socket.close()
        return None, f"no answer to run-task within {TASK_START_TIMEOUT:g} seconds"
    except (OSError, WebSocketException) as error:
        return None, f"the socket closed ({error})"


async def try_qwen_live(base_url: str, api_key: str, model: str) -> str | None:
    """Why a Qwen model has no live version here, or None if its live id starts a task."""

    socket, reason = await open_live_socket(base_url, api_key, qwen_live_id(model))
    if socket is None:
        return reason
    await socket.close()
    return None


def _qwen_transcriber(
    base_url: str, api_key: str, model: str, sample_rate: int = 24_000
) -> Transcriber:
    """Qwen by upload: always the model's upload id, whichever of the pair was saved."""

    upload_model = qwen_upload_id(model)

    async def transcribe(client: httpx.AsyncClient, pcm: bytes) -> str:
        response = await client.post(
            qwen_endpoint(base_url),
            headers={"Authorization": f"Bearer {api_key}"},
            json=qwen_request(upload_model, _pcm16_wav(pcm, sample_rate), sample_rate),
        )
        if qwen_heard_nothing(response):
            return ""
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
    sample_rate: int = 24_000,
    protocol: int = 1,
    offset_ms: int = 0,
    listen: listen_copy.ListenCopy | None = None,
    check_again: bool = False,
    group_paragraphs: bool = False,
) -> None:
    """Cut live audio into segments and have an OpenAI-style server write each one.

    SenseVoice and any ``/audio/transcriptions`` server work alike: audio in,
    text out, one segment at a time, sent on as it arrives. A v2 client gets each
    segment as a final ``transcript.piece`` on the recording clock (``offset_ms`` plus the
    audio received), and ``transcript.flushed`` once a ``commit`` has been written out;
    a v1 client gets the old ``…completed`` events, unchanged.
    """

    await _accept(websocket)
    segment_size = max(2 * sample_rate, int(sample_rate * 2 * max(1.0, segment_seconds)))
    segment_size -= segment_size % 2
    pending = bytearray()
    sequence = 0
    audio_cursor_seconds = max(0, offset_ms) / 1000
    tag = secrets.token_hex(2)
    # The audio copy is 16 kHz whatever the client sends; ``copy_cursor`` counts its bytes.
    copy_resampler = Pcm16Resampler(sample_rate, LIVE_RATE) if sample_rate != LIVE_RATE else None
    copy_cursor = max(0, offset_ms) * BYTES_PER_MS
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if reset_url:
                with suppress(httpx.HTTPError):
                    await client.post(reset_url)
            announced = {"type": "service.ready", "provider": provider, **ready}
            if protocol >= 2:
                announced["protocol"] = protocol
                announced["check_again"] = check_again
                announced["group_paragraphs"] = group_paragraphs
            await websocket.send_json(announced)

            async def transcribe_segment(chunk: bytes, start_seconds: float) -> None:
                nonlocal sequence
                if len(chunk) < 2:
                    return
                transcript = await transcriber(client, chunk)
                if not transcript:
                    return
                sequence += 1
                if protocol >= 2:
                    start_ms = round(start_seconds * 1000)
                    end_ms = start_ms + round(len(chunk) / (sample_rate * 2) * 1000)
                    await websocket.send_json(
                        {
                            "type": "transcript.piece",
                            "id": f"{tag}-{sequence}",
                            "start_ms": start_ms,
                            "end_ms": end_ms,
                            "text": transcript,
                            "final": True,
                            "sentences": [
                                {"start_ms": start_ms, "end_ms": end_ms, "text": transcript}
                            ],
                        }
                    )
                    return
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
                    audio_cursor_seconds += len(chunk) / (sample_rate * 2)

            while True:
                message = await websocket.receive_text()
                payload = json.loads(message)
                message_type = payload.get("type")
                if message_type == "input_audio_buffer.append":
                    encoded = payload.get("audio")
                    if not isinstance(encoded, str):
                        continue
                    audio = b""
                    with suppress(ValueError):
                        audio = base64.b64decode(encoded, validate=True)
                    pending.extend(audio)
                    if listen is not None and audio:
                        kept = copy_resampler.feed(audio) if copy_resampler else audio
                        at = payload.get("offset_ms")
                        if (
                            protocol >= 2
                            and isinstance(at, int | float)
                            and not isinstance(at, bool)
                        ):
                            copy_cursor = max(0, int(at)) * BYTES_PER_MS
                        listen.write(copy_cursor, kept)
                        copy_cursor += len(kept) - len(kept) % 2
                    await flush()
                elif message_type == "input_audio_buffer.commit":
                    await flush(force=True)
                    if protocol >= 2:
                        await websocket.send_json(
                            {
                                "type": "transcript.flushed",
                                "until_ms": round(audio_cursor_seconds * 1000),
                            }
                        )
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
    finally:
        if listen is not None:
            listen.close()


class _Fatal(Exception):
    """The live stream cannot go on; carries the code and message for ``service.error``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class _Chunk:
    """Up to 100 ms of 16 kHz audio; ``start`` counts bytes on the recording clock."""

    start: int
    data: bytes

    @property
    def end(self) -> int:
        return self.start + len(self.data)


@dataclass
class _Task:
    """One upstream task: a stretch of continuous audio. Its times count from ``start_ms``."""

    id: str
    number: int
    start_ms: int
    started: asyncio.Event = field(default_factory=asyncio.Event)
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    outcome: str = ""  # "finished", or "failed" with ``error``
    error: str = ""
    finish_sent: bool = False
    last_final_end_ms: int = 0

    def end(self, outcome: str, error: str = "") -> None:
        if not self.ended.is_set():
            self.outcome, self.error = outcome, error
        self.started.set()
        self.ended.set()


class _LiveStream:
    """Qwen's streaming model heard through one live socket, for one client.

    Audio from the client is cut into 100 ms frames and sent to a *task* on the upstream
    socket; a pause (``commit``) finishes the task and the next audio starts a new one,
    so no task sits silent. Audio not yet confirmed by a final sentence is kept (up to
    ``RETAIN_SECONDS``) so a task that fails can be started again where the last final
    ended. All times sent to the client are on the recording clock, in milliseconds.
    """

    def __init__(
        self,
        websocket: WebSocket,
        upstream: ClientConnection,
        choice: Choice,
        *,
        protocol: int,
        sample_rate: int,
        offset_ms: int,
        context: str,
        listen: listen_copy.ListenCopy | None = None,
        check_again: bool = False,
        group_paragraphs: bool = False,
    ) -> None:
        self.websocket = websocket
        self.listen = listen
        self.check_again = check_again
        self.group_paragraphs = group_paragraphs
        self.up: ClientConnection | None = upstream
        self.choice = choice
        self.model = qwen_live_id(choice.model)
        self.protocol = protocol
        self.sample_rate = sample_rate
        self.context = context
        self.tag = secrets.token_hex(2)
        self.resampler = (
            Pcm16Resampler(sample_rate, LIVE_RATE) if sample_rate != LIVE_RATE else None
        )
        self.cursor = max(0, offset_ms) * BYTES_PER_MS  # where the next audio should start
        self.chunks: list[_Chunk] = []  # audio no final sentence has covered yet
        self.sent = 0  # how many of ``chunks`` went to the current task
        self.finish_at: int | None = None  # finish the task after this many chunks
        self.commit_later = False
        self.task: _Task | None = None
        self.task_count = 0
        self.failures = 0
        self.item_count = 0
        self.recent = ""
        # Recording-clock ms covered by each commit that has not been answered yet.
        self.flush_marks: list[int] = []
        self.wake = asyncio.Event()
        self.send_lock = asyncio.Lock()
        self.reader: asyncio.Task[None] | None = None
        self.worker: asyncio.Task[None] | None = None
        self.gone = False

    # Talking to the client ----------------------------------------------------

    async def send(self, payload: dict[str, Any]) -> None:
        if self.gone:
            return
        try:
            async with self.send_lock:
                await self.websocket.send_json(payload)
        except (RuntimeError, WebSocketDisconnect):
            self.gone = True

    async def gap(self, start_byte: int, end_byte: int, reason: str) -> None:
        if self.protocol >= 2 and end_byte > start_byte:
            await self.send(
                {
                    "type": "transcript.gap",
                    "start_ms": start_byte // BYTES_PER_MS,
                    "end_ms": end_byte // BYTES_PER_MS,
                    "reason": reason,
                }
            )

    async def run(self) -> None:
        await _accept(self.websocket)
        ready: dict[str, Any] = {
            "type": "service.ready",
            "provider": self.choice.kind,
            "model": self.choice.model,
            "stream": True,
            "local": False,
            "diarize": False,
            "live": "stream",
        }
        if self.protocol >= 2:
            ready["protocol"] = self.protocol
            ready["check_again"] = self.check_again
            ready["group_paragraphs"] = self.group_paragraphs
        await self.send(ready)
        self._attach(self.up)
        self.worker = asyncio.create_task(self._work())
        try:
            while True:
                try:
                    payload = json.loads(await self.websocket.receive_text())
                except json.JSONDecodeError:
                    continue
                await self._on_client(payload)
        except WebSocketDisconnect:
            return
        finally:
            if self.listen is not None:
                self.listen.close()
            for task in (self.worker, self.reader):
                if task is not None:
                    task.cancel()
            if self.up is not None:
                with suppress(Exception):
                    await self.up.close()

    # Audio in -----------------------------------------------------------------

    async def _on_client(self, payload: dict[str, Any]) -> None:
        kind = payload.get("type")
        if kind == "input_audio_buffer.append":
            await self._append(payload)
        elif kind == "input_audio_buffer.commit":
            self._commit()
        elif kind == "input_audio_buffer.clear":
            del self.chunks[self.sent :]

    async def _append(self, payload: dict[str, Any]) -> None:
        encoded = payload.get("audio")
        if not isinstance(encoded, str):
            return
        try:
            pcm = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return
        if self.resampler is not None:
            pcm = self.resampler.feed(pcm)
        pcm = pcm[: len(pcm) - len(pcm) % 2]
        offset = payload.get("offset_ms")
        start = self.cursor
        if self.protocol >= 2 and isinstance(offset, int | float) and not isinstance(offset, bool):
            start = max(0, int(offset)) * BYTES_PER_MS
            if self.cursor - start > 20 * BYTES_PER_MS:
                # Audio the backend already has (a replay after reconnecting): skip it.
                skip = min(len(pcm), (self.cursor - start) & ~1)
                pcm, start = pcm[skip:], start + skip
            elif start - self.cursor > 100 * BYTES_PER_MS:
                # Missing audio: the current stretch ends there and a new task starts after.
                await self.gap(self.cursor, start, "missing")
                self._commit()
            else:
                start = self.cursor
        if not pcm:
            return
        if self.listen is not None:
            self.listen.write(start, pcm)
        for position in range(0, len(pcm), FRAME_BYTES):
            frame = pcm[position : position + FRAME_BYTES]
            self.chunks.append(_Chunk(start + position, frame))
        self.cursor = start + len(pcm)
        await self._trim_queue()
        self.wake.set()

    def _commit(self) -> None:
        """The client paused or stopped: finish the task once everything so far is sent."""

        if self.protocol >= 2:
            self.flush_marks.append(self.cursor // BYTES_PER_MS)
        if self.task is not None and self.task.finish_sent:
            self.commit_later = True
        elif self.task is not None or self.chunks:
            self.finish_at = len(self.chunks)
        self.wake.set()

    async def _flush(self) -> None:
        """Tell a v2 client that every commit so far has been heard to its end."""

        marks, self.flush_marks = self.flush_marks, []
        for until_ms in marks:
            await self.send({"type": "transcript.flushed", "until_ms": until_ms})

    def _remove_front(self, count: int) -> None:
        del self.chunks[:count]
        self.sent = max(0, self.sent - count)
        if self.finish_at is not None:
            self.finish_at = max(0, self.finish_at - count)

    async def _trim_queue(self) -> None:
        limit = int(RETAIN_SECONDS * 1000) * BYTES_PER_MS
        # Audio already sent but not confirmed: keep the newest part only.
        while self.sent and sum(len(c.data) for c in self.chunks[: self.sent]) > limit:
            self._remove_front(1)
        # Audio waiting for a task: past the limit the oldest is lost, and the client is told.
        lost_start: int | None = None
        lost_end = 0
        while sum(len(c.data) for c in self.chunks[self.sent :]) > limit:
            chunk = self.chunks.pop(self.sent)
            if self.finish_at is not None and self.finish_at > self.sent:
                self.finish_at -= 1
            lost_start = chunk.start if lost_start is None else lost_start
            lost_end = chunk.end
        if lost_start is not None:
            await self.gap(lost_start, lost_end, "connection")

    def _confirm_until(self, end_ms: int) -> None:
        """A final sentence ended at ``end_ms``: audio before it need not be kept."""

        end = end_ms * BYTES_PER_MS
        count = 0
        while count < self.sent and self.chunks[count].end <= end:
            count += 1
        if count:
            self._remove_front(count)

    # The upstream socket ------------------------------------------------------

    def _attach(self, upstream: ClientConnection | None) -> None:
        if self.reader is not None:
            self.reader.cancel()
        if upstream is not None:
            self.reader = asyncio.create_task(self._read(upstream))

    async def _read(self, upstream: ClientConnection) -> None:
        reason = "the connection closed"
        try:
            async for message in upstream:
                if not isinstance(message, bytes):
                    await self._on_upstream(message)
        except ConnectionClosed as error:
            reason = f"the connection closed ({error})"
        except Exception as error:  # noqa: BLE001
            reason = f"the connection failed ({error})"
        if upstream is self.up and self.task is not None:
            self.task.end("failed", reason)
            self.wake.set()

    async def _on_upstream(self, message: str) -> None:
        try:
            data = json.loads(message)
            header = data.get("header") or {}
        except (ValueError, AttributeError):
            return
        task = self.task
        if task is None or header.get("task_id") not in (None, task.id):
            return
        event = header.get("event")
        if event == "task-started":
            task.started.set()
        elif event == "task-finished":
            if task.finish_sent:
                task.end("finished")
            else:
                task.end("failed", "the task ended on its own")
            self.wake.set()
        elif event == "task-failed":
            task.end("failed", str(header.get("error_message") or header.get("error_code") or ""))
            self.wake.set()
        elif event == "result-generated":
            output = (data.get("payload") or {}).get("output") or {}
            sentence = output.get("sentence") or {}
            if sentence and not sentence.get("heartbeat"):
                await self._on_result(task, sentence)

    async def _on_result(self, task: _Task, sentence: dict[str, Any]) -> None:
        text = str(sentence.get("text") or "")
        begin = sentence.get("begin_time")
        start_ms = task.start_ms + int(begin if begin is not None else 0)
        if not sentence.get("sentence_end"):
            # A draft: shown, rewritten, never kept. v1 clients only hear final sentences.
            if self.protocol >= 2 and text.strip():
                await self.send(
                    {
                        "type": "transcript.piece",
                        "id": f"{self.tag}-{task.number}-{sentence.get('sentence_id', 0)}",
                        "start_ms": start_ms,
                        "end_ms": None,
                        "text": text,
                        "final": False,
                    }
                )
            return
        end = sentence.get("end_time")
        end_ms = task.start_ms + int(end) if end is not None else start_ms
        sentences = split_sentences(sentence.get("words") or [], task.start_ms)
        if not sentences and text.strip():
            sentences = [{"start_ms": start_ms, "end_ms": end_ms, "text": text.strip()}]
        task.last_final_end_ms = max(task.last_final_end_ms, end_ms)
        self.failures = 0
        self._confirm_until(end_ms)
        if not sentences:
            return  # noise taken for speech: not a sentence
        spoken = " ".join(item["text"] for item in sentences)
        self.recent = f"{self.recent} {spoken}".strip()[-2 * 400 :]
        if self.protocol >= 2:
            await self.send(
                {
                    "type": "transcript.piece",
                    "id": f"{self.tag}-{task.number}-{sentence.get('sentence_id', 0)}",
                    "start_ms": sentences[0]["start_ms"],
                    "end_ms": sentences[-1]["end_ms"],
                    "text": spoken,
                    "final": True,
                    "sentences": sentences,
                }
            )
            return
        for item in sentences:
            self.item_count += 1
            await self.send(
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "item_id": f"{self.choice.kind}-{self.item_count}",
                    "transcript": item["text"],
                    "provider": self.choice.kind,
                    "start_seconds": round(item["start_ms"] / 1000, 2),
                }
            )

    # Keeping a task going -----------------------------------------------------

    async def _work(self) -> None:
        try:
            while True:
                await self.wake.wait()
                self.wake.clear()
                await self._drain()
        except _Fatal as error:
            await self.send({"type": "service.error", "code": error.code, "message": error.message})
            with suppress(RuntimeError, WebSocketDisconnect):
                await self.websocket.close(code=1011)
            self.gone = True

    async def _drain(self) -> None:
        while True:
            task = self.task
            if task is not None and task.ended.is_set():
                await self._settle(task)
                continue
            if task is None:
                if self.sent >= len(self.chunks):
                    self.finish_at = None
                    await self._flush()  # a commit with nothing left to finish
                    return
                await self._start_task()
                continue
            if self.up is None:
                return
            try:
                while True:
                    if task.finish_sent:
                        break
                    limit = len(self.chunks) if self.finish_at is None else self.finish_at
                    if self.sent >= limit:
                        break
                    chunk = self.chunks[self.sent]
                    self.sent += 1
                    await self.up.send(chunk.data)
                if self.finish_at is not None and not task.finish_sent:
                    task.finish_sent = True
                    await self.up.send(finish_task_message(task.id))
            except ConnectionClosed as error:
                task.end("failed", f"the connection closed ({error})")
            if task.finish_sent and not task.ended.is_set():
                try:
                    await asyncio.wait_for(task.ended.wait(), TASK_END_TIMEOUT)
                except TimeoutError:
                    task.end("finished")
                continue
            if not task.ended.is_set():
                return

    async def _settle(self, task: _Task) -> None:
        self.task = None
        if task.outcome == "finished":
            self._remove_front(self.finish_at or 0)
            self.sent = 0
            self.finish_at = None
            if task.finish_sent:
                await self._flush()
            if self.commit_later:
                self.commit_later = False
                self.finish_at = len(self.chunks) if self.chunks else None
            return
        logger.warning("Live transcription task failed: %s", task.error)
        await self._recover(f"the task failed ({task.error})", task)

    async def _recover(self, why: str, task: _Task | None) -> None:
        """Count a failure, wait a little, and prepare to start again where the last final ended."""

        self.failures += 1
        if self.failures > len(RECONNECT_BACKOFF):
            raise _Fatal(
                "provider_unavailable", f"{self.choice.provider} stopped responding: {why}"
            )
        self.sent = 0
        if task is not None:
            resume = task.last_final_end_ms * BYTES_PER_MS
            while self.chunks and self.chunks[0].end <= resume:
                self._remove_front(1)
            if self.chunks and self.chunks[0].start < resume:
                cut = (resume - self.chunks[0].start) & ~1
                first = self.chunks[0]
                self.chunks[0] = _Chunk(first.start + cut, first.data[cut:])
            if self.chunks and self.chunks[0].start > resume:
                await self.gap(resume, self.chunks[0].start, "connection")
            if self.finish_at is None and task.finish_sent:
                self.finish_at = len(self.chunks)
        if self.up is not None and self.up.state != State.OPEN:
            self.up = None
        await asyncio.sleep(RECONNECT_BACKOFF[self.failures - 1])

    async def _start_task(self) -> None:
        """Open a task at the first audio waiting, reconnecting first if the socket closed."""

        while True:
            self.task = None
            if not self.chunks:
                return
            try:
                if self.up is None:
                    self.up = await connect(
                        stream_url(self.choice.base_url),
                        additional_headers={"Authorization": f"Bearer {self.choice.api_key}"},
                        open_timeout=CONNECT_TIMEOUT,
                        max_size=None,
                    )
                    self._attach(self.up)
                self.task_count += 1
                start_ms = self.chunks[0].start // BYTES_PER_MS
                task = _Task(uuid.uuid4().hex, self.task_count, start_ms)
                task.last_final_end_ms = start_ms
                self.task = task
                await self.up.send(
                    run_task_message(task.id, self.model, context_turns(self.context, self.recent))
                )
                await asyncio.wait_for(task.started.wait(), TASK_START_TIMEOUT)
                if task.ended.is_set():
                    raise ConnectionError(task.error or "the task did not start")
                return
            except (OSError, WebSocketException, TimeoutError, ConnectionError) as error:
                self.task = None
                if self.up is not None:
                    with suppress(Exception):
                        await self.up.close()
                self.up = None
                await self._recover(str(error) or type(error).__name__, None)


async def _proxy_live_stream(
    websocket: WebSocket,
    upstream: ClientConnection,
    choice: Choice,
    *,
    protocol: int,
    sample_rate: int,
    offset_ms: int,
    context: str,
    listen: listen_copy.ListenCopy | None = None,
    check_again: bool = False,
    group_paragraphs: bool = False,
) -> None:
    live = _LiveStream(
        websocket,
        upstream,
        choice,
        protocol=protocol,
        sample_rate=sample_rate,
        offset_ms=offset_ms,
        context=context,
        listen=listen,
        check_again=check_again,
        group_paragraphs=group_paragraphs,
    )
    await live.run()


def compatible_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/audio/transcriptions"


# A take sent whole is cut only when it gets long (Qwen takes about five minutes).
WHOLE_TAKE_SECONDS = 150.0


async def _fail(websocket: WebSocket, code: str, message: str) -> None:
    await _accept(websocket)
    await websocket.send_json({"type": "service.error", "code": code, "message": message})
    await websocket.close(code=1011)


async def proxy_realtime_transcription(
    websocket: WebSocket,
    *,
    choice: Choice | None,
    problem: str = "",
    segment_seconds: float = 3.2,
    context: str = "",
    protocol: int = 1,
    sample_rate: int = 24_000,
    offset_ms: int = 0,
    listen: listen_copy.ListenCopy | None = None,
    check_again: bool = False,
    group_paragraphs: bool = False,
) -> None:
    """Have the model chosen for a job write the words of audio sent over the socket.

    Live works in short segments as the audio arrives; otherwise the take waits
    until it ends and goes up whole, which reads better. A live Qwen model is heard as
    one continuous stream when its live id works for this key; if not, it falls back to
    short uploaded segments (once, for the whole socket) and the recording goes on.
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
                f"SenseVoice is not running at {choice.base_url}. Start it, or choose "
                "another model, such as Qwen, in Settings → Transcription.",
            )
            return
        root = _sensevoice_root(choice.base_url)
        transcriber = _form_transcriber(
            f"{root}/v1/audio/transcriptions",
            {},
            {"model": "sensevoice", "response_format": "json"},
            sample_rate,
        )
        ready = {**ready, "local": True, "diarize": bool(status.get("diarize"))}
        reset_url = f"{root}/reset"
    elif choice.kind == "qwen":
        if choice.stream:
            # Accept first: checking the live model can take seconds on a slow link, and the
            # client's audio waits in the socket meanwhile instead of in its own small queue.
            await _accept(websocket)
            live_model = qwen_live_id(choice.model)
            upstream, reason = await open_live_socket(
                choice.base_url, choice.api_key or "", live_model
            )
            if upstream is not None:
                await _proxy_live_stream(
                    websocket,
                    upstream,
                    choice,
                    protocol=protocol,
                    sample_rate=sample_rate,
                    offset_ms=offset_ms,
                    context=context,
                    listen=listen,
                    check_again=check_again,
                    group_paragraphs=group_paragraphs,
                )
                return
            logger.warning(
                "%s has no live text here (%s); recording sends short pieces instead",
                live_model,
                reason,
            )
            ready = {**ready, "live": "segments"}
        transcriber = _qwen_transcriber(
            choice.base_url, choice.api_key or "", choice.model, sample_rate
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
            sample_rate,
        )
    await _proxy_segmented_transcription(
        websocket,
        transcriber=transcriber,
        provider=choice.kind,
        label=choice.provider,
        segment_seconds=seconds,
        ready=ready,
        reset_url=reset_url,
        sample_rate=sample_rate,
        protocol=protocol,
        offset_ms=offset_ms,
        listen=listen,
        check_again=check_again,
        group_paragraphs=group_paragraphs,
    )


# Listening again ---------------------------------------------------------------

# Audio is read this much past each end so a word at the edge is not cut.
RELISTEN_PAD_MS = 300
# A minute of 16 kHz audio is about 2.6 MB once encoded for Qwen.
RELISTEN_TIMEOUT = httpx.Timeout(120.0, write=300.0)
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
    return f"{label} could not be reached ({error or type(error).__name__})."


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
) -> dict[str, Any]:
    """Have a job's model write the words of ``pcm`` (16 kHz) again, with context.

    ``audio_start_ms`` is where ``pcm`` begins on the recording clock; ``start_ms`` and
    ``end_ms`` are the range asked for, the rest being padding. Qwen answers with word
    times, which become sentences on the recording clock (those lying only in the
    padding are dropped); other providers answer text only, with no sentences.
    """

    wav = _pcm16_wav(pcm, LIVE_RATE)
    term_text = ", ".join(term.strip() for term in terms if term.strip())
    async with httpx.AsyncClient(timeout=RELISTEN_TIMEOUT) as client:

        async def post(**request: Any) -> httpx.Response:
            try:
                return await client.post(**request)
            except httpx.TransportError:
                return await client.post(**request)  # one more try on a dropped connection

        try:
            if choice.kind == "qwen":
                response = await post(
                    url=qwen_endpoint(choice.base_url),
                    headers={"Authorization": f"Bearer {choice.api_key}"},
                    json=qwen_request(
                        qwen_upload_id(choice.model),
                        wav,
                        LIVE_RATE,
                        [term_text[:400], before.strip()[-400:], after.strip()[:400]],
                    ),
                )
                if qwen_heard_nothing(response):
                    return {"text": "", "sentences": []}
                response.raise_for_status()
                payload = response.json()
                heard = split_sentences(_qwen_words(payload), audio_start_ms)
                sentences = [
                    sentence
                    for sentence in heard
                    if sentence["end_ms"] > start_ms and sentence["start_ms"] < end_ms
                ]
                text = qwen_text(payload)
                if heard and len(sentences) != len(heard):
                    # Words that lie only in the padding are not part of the answer.
                    text = _join_text([sentence["text"] for sentence in sentences])
                return {"text": text, "sentences": sentences}
            if choice.kind == "sensevoice":
                root = _sensevoice_root(choice.base_url)
                endpoint, headers = f"{root}/v1/audio/transcriptions", {}
                form = {"model": "sensevoice", "response_format": "json"}
            else:
                endpoint = compatible_endpoint(choice.base_url)
                headers = {"Authorization": f"Bearer {choice.api_key}"} if choice.api_key else {}
                form = {"model": choice.model, "response_format": "json"}
                if choice.language:
                    form["language"] = choice.language
                prompt = term_text
                room = RELISTEN_PROMPT_CHARS - len(prompt) - 1
                if before.strip() and room > 0:
                    prompt = f"{prompt} {before.strip()[-room:]}".strip()
                if prompt:
                    form["prompt"] = prompt[:RELISTEN_PROMPT_CHARS]
            response = await post(
                url=endpoint,
                headers=headers,
                files={"file": ("segment.wav", wav, "audio/wav")},
                data=form,
            )
            response.raise_for_status()
            return {"text": str(response.json().get("text", "")).strip(), "sentences": []}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
            raise ListenError(_provider_message(error, choice.provider)) from error
