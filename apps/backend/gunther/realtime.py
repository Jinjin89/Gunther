from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import secrets
import uuid
from collections import deque
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import httpx
from anyio import CancelScope
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.protocol import State

from gunther import listen_copy, vad
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
from gunther.speech_registry import Choice, qwen_live_id
from gunther.transcription import (
    CONTEXT_CHARS,
    AsrHttpConfigurationError,
    asr_http_client,
    sensevoice_health,
    sensevoice_root,
    transcribe_segment,
)

logger = logging.getLogger(__name__)


async def _accept(websocket: WebSocket) -> None:
    """Accept the client once; a live Qwen socket is accepted before its model is checked."""

    if websocket.client_state == WebSocketState.CONNECTING:
        await websocket.accept()


# How long Qwen's live socket may take to start a task before the model counts as not live.
TASK_START_TIMEOUT = 10.0
# Leave time within the desktop's 30 s ready deadline for cleanup and upload fallback.
LIVE_SETUP_TIMEOUT = 10.0
# How long to wait for a task to finish after finish-task.
TASK_END_TIMEOUT = 10.0
# How long one frame may take to go out before the live socket counts as stalled.
SEND_TIMEOUT = 10.0
CONNECT_TIMEOUT = 30.0
# Bound the close handshake on a failed setup before starting the fallback.
CLOSE_TIMEOUT = 2.0
# Audio kept to send again if the live socket drops, and queued while it reconnects.
RETAIN_SECONDS = 30.0
# Bound non-audio messages while the client is waiting for its provider to start.
EARLY_CONTROL_LIMIT = 64
RECONNECT_BACKOFF = (0.5, 1.0, 2.0, 4.0)
# The live socket goes direct: websockets would otherwise follow the
# system's proxy settings (a SOCKS proxy from a VPN app needs python-socks, which isn't here,
# and failed the whole request).
LIVE_PROXY = None
FRAME_BYTES = 100 * BYTES_PER_MS  # 100 ms of audio per binary frame


def _task_event(message: str | bytes, task_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if isinstance(message, bytes):
        return None
    try:
        data = json.loads(message)
    except ValueError:
        return None
    header = data.get("header") if isinstance(data, dict) else None
    if isinstance(header, dict) and header.get("task_id") == task_id:
        return header, data
    return None


def _task_error(header: dict[str, Any]) -> str:
    return str(header.get("error_message") or header.get("error_code") or "task failed")


async def _next_event(socket: ClientConnection, task_id: str, name: str) -> str | None:
    """Read until this task reports the requested event or fails."""

    while True:
        matching = _task_event(await socket.recv(), task_id)
        if matching is None:
            continue
        header, _ = matching
        event = header.get("event")
        if event == name:
            return None
        if event == "task-failed":
            return _task_error(header)


async def _connect_live_socket(base_url: str, api_key: str) -> ClientConnection:
    return await connect(
        stream_url(base_url),
        additional_headers={"Authorization": f"Bearer {api_key}"},
        open_timeout=CONNECT_TIMEOUT,
        close_timeout=CLOSE_TIMEOUT,
        max_size=None,
        proxy=LIVE_PROXY,
    )


async def _start_live_task(
    socket: ClientConnection, model: str, turns: list[dict[str, Any]], number: int
) -> _Task:
    task = _Task(uuid.uuid4().hex, number)
    try:
        async with asyncio.timeout(TASK_START_TIMEOUT):
            await socket.send(run_task_message(task.id, model, turns))
            refusal = await _next_event(socket, task.id, "task-started")
    except TimeoutError as error:
        raise TimeoutError(
            f"no answer to run-task within {TASK_START_TIMEOUT:g} seconds"
        ) from error
    if refusal is not None:
        raise ConnectionError(f"Qwen refused it ({refusal})")
    return task


async def _close_live_socket(socket: ClientConnection) -> None:
    with CancelScope(shield=True), suppress(OSError, WebSocketException):
        await socket.close()


async def _prepare_live_task(
    base_url: str, api_key: str, model: str, context: str = "", *, timeout: float | None = None
) -> tuple[tuple[ClientConnection, _Task] | None, str]:
    """Hand off an open socket only after Qwen starts its first task."""

    socket = None
    task = None
    budget = asyncio.timeout(timeout)
    try:
        async with budget:
            socket = await _connect_live_socket(base_url, api_key)
            task = await _start_live_task(socket, model, context_turns(context, ""), 1)
            return (socket, task), ""
    except InvalidStatus as error:
        return None, f"Qwen answered {error.response.status_code} when connecting"
    except (OSError, WebSocketException) as error:
        if budget.expired():
            return None, f"live setup did not complete within {timeout:g} seconds"
        if socket is None:
            return None, f"could not connect ({error})"
        if isinstance(error, (ConnectionError, TimeoutError)):
            return None, str(error)
        return None, f"the socket closed ({error})"
    finally:
        if socket is not None and task is None:
            await _close_live_socket(socket)


async def try_qwen_live(base_url: str, api_key: str, model: str) -> str | None:
    """Why a Qwen model has no live version here, or None if its live id starts a task."""

    prepared, reason = await _prepare_live_task(base_url, api_key, qwen_live_id(model))
    if prepared is None:
        return reason
    socket, task = prepared
    try:
        # Settings checks that the model starts. An empty task may have no finish reply.
        with suppress(TimeoutError, ConnectionClosed):
            async with asyncio.timeout(5.0):
                await socket.send(finish_task_message(task.id))
                await _next_event(socket, task.id, "task-finished")
        return None
    finally:
        await _close_live_socket(socket)


def _early_pcm_size(message: str) -> int:
    """Count the PCM in an append's base64 field without decoding it twice."""

    try:
        payload = json.loads(message)
    except ValueError:
        return 0
    if isinstance(payload, dict) and payload.get("type") == "input_audio_buffer.append":
        audio = payload.get("audio")
        if isinstance(audio, str):
            padding = int(audio.endswith("=")) + int(audio.endswith("=="))
            return (len(audio) - padding) * 3 // 4
    return 0


class _ClientFrames:
    """Own downstream reads, including the FIFO handed over after live setup."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.buffered: deque[str] = deque()
        self.pending: asyncio.Task[str] | None = None

    async def buffer_during(self, setup: asyncio.Task[Any], sample_rate: int) -> None:
        pcm_limit = int(RETAIN_SECONDS * sample_rate * 2)
        # Budget for base64 plus JSON framing, including frames that contain no valid PCM.
        wire_limit = 2 * pcm_limit + EARLY_CONTROL_LIMIT * 1024
        pcm_bytes = controls = wire_chars = 0
        while True:
            self.pending = asyncio.create_task(self.websocket.receive_text())
            await asyncio.wait((setup, self.pending), return_when=asyncio.FIRST_COMPLETED)
            if self.pending.done():
                message = self.pending.result()  # A simultaneous disconnect wins over ready.
                self.pending = None
                wire_chars += len(message)
                size = _early_pcm_size(message) if wire_chars <= wire_limit else 0
                pcm_bytes += size
                controls += size == 0
                if (
                    pcm_bytes > pcm_limit
                    or controls > EARLY_CONTROL_LIMIT
                    or wire_chars > wire_limit
                ):
                    raise _Fatal(
                        "startup_buffer_full",
                        "Too much input arrived before transcription was ready.",
                    )
                self.buffered.append(message)
            if setup.done():
                return

    async def __aiter__(self) -> AsyncIterator[str]:
        while self.buffered:
            yield self.buffered.popleft()
        if self.pending is not None:
            # Transfer the in-flight receive so cancellation cannot lose an ASGI frame.
            message = await self.pending
            self.pending = None
            yield message
        while True:
            yield await self.websocket.receive_text()

    async def aclose(self) -> None:
        if self.pending is not None:
            await _cancel_tasks(self.pending)


async def _cancel_tasks(*tasks: asyncio.Task[Any]) -> list[Any]:
    for task in tasks:
        if not task.cancelling():
            task.cancel()
    # Finish joining owned tasks even when the ASGI request scope is already cancelled.
    with CancelScope(shield=True):
        return await asyncio.gather(*tasks, return_exceptions=True)


class _V2InputClock:
    # The recording clock counts captured PCM, with pauses excluded. Clients send all
    # PCM preceding a Pause or Stop before committing, and keep the boundary until
    # its flush or a known loss of the needed audio. On reconnect they replay retained
    # PCM at its original offsets. The first stamp sets the clock on each socket.
    # Later frames advance by sample count, absorbing differences under one append frame.
    def __init__(self, offset_ms: int, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self._byte_rate = sample_rate * 2
        self._position = max(0, offset_ms) * self._byte_rate
        self._started = False

    @property
    def until_ms(self) -> int:
        return self._position // self._byte_rate

    def append(self, pcm: bytes, offset: object) -> tuple[bytes, int | None]:
        """Return new PCM and, on a discontinuity, its new recording-clock origin."""
        frame = len(pcm) * 1000
        jump = None
        if isinstance(offset, int | float) and not isinstance(offset, bool):
            start = max(0, int(offset))
            position = start * self._byte_rate
            difference = position - self._position
            if not self._started or difference >= frame:
                if difference:
                    jump = start
                self._position = position
            elif difference <= -frame:
                return b"", None  # The whole frame precedes the next sample.
        self._started = True
        self._position += frame
        return pcm, jump


def _gap_event(start_ms: int, end_ms: int, reason: str) -> dict[str, object]:
    return {"type": "transcript.gap", "start_ms": start_ms, "end_ms": end_ms, "reason": reason}


def _piece_event(
    piece_id: str,
    start_ms: int,
    end_ms: int | None,
    text: str,
    sentences: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "transcript.piece",
        "id": piece_id,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "text": text,
        "final": end_ms is not None,
    }
    if end_ms is not None:
        if sentences is None:
            sentences = [{"start_ms": start_ms, "end_ms": end_ms, "text": text}] if text else []
        event["sentences"] = sentences
    return event


def _flushed_event(until_ms: int) -> dict[str, object]:
    return {"type": "transcript.flushed", "until_ms": until_ms}


_warned_no_pause_model = False


def _warn_no_pause_model() -> None:
    global _warned_no_pause_model
    if not _warned_no_pause_model:
        _warned_no_pause_model = True
        logger.warning(
            "The pause-detection model is not installed (run npm run models:fetch); "
            "live text is cut every few seconds instead of at pauses"
        )


async def _proxy_segmented_transcription(
    websocket: WebSocket,
    frames: _ClientFrames,
    *,
    choice: Choice,
    context: str,
    segment_seconds: float,
    ready: dict[str, object],
    reset_url: str | None = None,
    sample_rate: int = 24_000,
    pause_cuts: bool = True,
    offset_ms: int = 0,
    listen: listen_copy.ListenCopy | None = None,
    check_again: bool = False,
    group_paragraphs: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Cut live audio into pieces and have an OpenAI-style server write each one.

    SenseVoice and any ``/audio/transcriptions`` server work alike: audio in, text out,
    one piece at a time, in order, while the socket goes on being read. Audio is turned
    into 16 kHz once. With ``pause_cuts`` a piece ends at a pause (Silero VAD, ``vad.py``);
    without the model file, or for a take that goes up whole, pieces are fixed blocks.
    Final pieces use the recording clock (``offset_ms`` plus the audio received).
    SenseVoice can also send a draft of the open piece. A ``commit`` flushes the
    current piece and acknowledges its boundary after transcription.
    """

    await _accept(websocket)
    detector = await asyncio.to_thread(vad.new_detector) if pause_cuts else None
    if pause_cuts and detector is None:
        _warn_no_pause_model()
        ready = {**ready, "live": "segments-fixed"}

    def new_segmenter() -> vad.Segmenter | vad.FixedBlocks:
        if detector is not None:
            return vad.Segmenter(detector)
        return vad.FixedBlocks(int(LIVE_RATE * 2 * max(1.0, segment_seconds)))

    segmenter = new_segmenter()
    drafts = choice.kind == "sensevoice" and detector is not None
    audio = bytearray()  # 16 kHz audio from ``audio_base`` on that a piece may still need
    audio_base = 0
    odd = b""  # a half sample when the client's chunk ends inside one
    # 16 kHz bytes before the current segment, counted from ``clock_ms``.
    clock_bytes = 0
    clock_ms = max(0, offset_ms)
    input_clock = _V2InputClock(offset_ms, sample_rate)

    def at_ms(byte: int) -> int:
        return clock_ms + (clock_bytes + byte) // BYTES_PER_MS

    tag = secrets.token_hex(2)
    resampler = Pcm16Resampler(sample_rate, LIVE_RATE) if sample_rate != LIVE_RATE else None
    copy_cursor = max(0, offset_ms) * BYTES_PER_MS
    jobs: asyncio.Queue[tuple[Any, ...]] = asyncio.Queue()
    outstanding = 0  # jobs queued or running
    pieces_closed = 0  # the next open piece is number pieces_closed + 1
    draft_marks: dict[int, int] = {}  # piece number -> its end byte when the last draft went
    drafted: set[int] = set()  # pieces whose draft put text on screen
    active_draft: tuple[int, asyncio.Task[str]] | None = None
    recent = ""  # the tail of what was written, the context for the next piece

    def enqueue(job: tuple[Any, ...]) -> None:
        nonlocal outstanding
        outstanding += 1
        jobs.put_nowait(job)

    def close_piece() -> int:
        nonlocal pieces_closed
        pieces_closed += 1
        draft_marks.pop(pieces_closed, None)
        if active_draft is not None and active_draft[0] == pieces_closed:
            active_draft[1].cancel()
        return pieces_closed

    def enqueue_pieces(found: list[vad.Piece]) -> None:
        for start, end in found:
            number = close_piece()
            chunk = bytes(audio[start - audio_base : end - audio_base])
            enqueue(("piece", number, chunk, at_ms(start), at_ms(end)))

    def reset_segmenter() -> None:
        nonlocal segmenter, audio_base, clock_bytes
        # A partial VAD frame and lookbehind belong to the preceding segment.
        clock_bytes += segmenter.total
        segmenter = new_segmenter()
        audio.clear()
        audio_base = 0

    def flush_pieces() -> None:
        enqueue_pieces(segmenter.flush())
        reset_segmenter()

    try:
        async with asr_http_client(30.0, transport) as client:
            if reset_url:
                with suppress(httpx.HTTPError):
                    await client.post(reset_url)
            await websocket.send_json(
                {
                    "type": "service.ready",
                    "provider": choice.kind,
                    **ready,
                    "protocol": 2,
                    "check_again": check_again,
                    "group_paragraphs": group_paragraphs,
                }
            )

            async def write_piece(number: int, chunk: bytes, start_ms: int, end_ms: int) -> None:
                nonlocal recent
                text = ""
                if len(chunk) >= 2:
                    try:
                        text = await transcribe_segment(
                            client, choice, chunk, title=context, recent=recent
                        )
                    except (httpx.HTTPError, ValueError, TypeError) as error:
                        await websocket.send_json(
                            {
                                "type": "service.error",
                                "code": "segment_failed",
                                "message": f"{choice.provider} missed one segment: {error}",
                            }
                        )
                        text = ""
                # Empty speech clears a draft that was on screen.
                had_draft = number in drafted
                drafted.discard(number)
                if text:
                    recent = f"{recent} {text}".strip()[-CONTEXT_CHARS:]
                elif not had_draft:
                    return
                await websocket.send_json(_piece_event(f"{tag}-{number}", start_ms, end_ms, text))

            async def write_draft(number: int, chunk: bytes, start_ms: int) -> None:
                nonlocal active_draft
                if number <= pieces_closed:
                    return  # its final is already queued: a draft now would only be stale
                request = asyncio.create_task(
                    transcribe_segment(client, choice, chunk, title=context)
                )
                active_draft = number, request
                try:
                    text = await asyncio.shield(request)
                except asyncio.CancelledError:
                    if worker.cancelling() or number > pieces_closed:
                        raise
                    return
                except (httpx.HTTPError, ValueError, TypeError) as error:
                    logger.debug("Draft of a live piece failed: %s", error)
                    return
                finally:
                    active_draft = None
                if text and number > pieces_closed:
                    drafted.add(number)
                    await websocket.send_json(_piece_event(f"{tag}-{number}", start_ms, None, text))

            async def work() -> None:
                nonlocal outstanding
                try:
                    while True:
                        job = await jobs.get()
                        try:
                            if job[0] == "piece":
                                await write_piece(*job[1:])
                            elif job[0] == "draft":
                                await write_draft(*job[1:])
                            else:
                                await websocket.send_json(job[1])
                        finally:
                            outstanding -= 1
                except (WebSocketDisconnect, RuntimeError):
                    return
                except Exception as error:
                    with suppress(RuntimeError, WebSocketDisconnect):
                        await websocket.send_json(
                            {
                                "type": "service.error",
                                "code": "provider_unavailable",
                                "message": f"{choice.provider} stopped responding: {error}",
                            }
                        )
                        await websocket.close(code=1011)

            def maybe_draft() -> None:
                opened = segmenter.open_piece()
                if not drafts or opened is None or outstanding:
                    return
                start, end = opened
                number = pieces_closed + 1
                last = draft_marks.get(number, start)
                if end - start < LIVE_RATE * 2 or end - last < LIVE_RATE * 4:
                    return  # the piece is under 1 s, or under 2 s of new audio has come
                draft_marks[number] = end
                chunk = bytes(audio[start - audio_base : end - audio_base])
                enqueue(("draft", number, chunk, at_ms(start)))

            worker = asyncio.create_task(work())
            try:
                async for message in frames:
                    payload = json.loads(message)
                    message_type = payload.get("type")
                    if message_type == "input_audio_buffer.append":
                        encoded = payload.get("audio")
                        if not isinstance(encoded, str):
                            continue
                        raw = b""
                        with suppress(ValueError):
                            raw = base64.b64decode(encoded, validate=True)
                        if not raw:
                            continue
                        until_ms = input_clock.until_ms
                        raw, start = input_clock.append(raw, payload.get("offset_ms"))
                        if start is not None:
                            flush_pieces()
                            if start > until_ms:
                                enqueue(("event", _gap_event(until_ms, start, "missing")))
                            clock_bytes, clock_ms = 0, start
                            copy_cursor = start * BYTES_PER_MS
                            odd = b""
                            if resampler is not None:
                                resampler = Pcm16Resampler(sample_rate, LIVE_RATE)
                        if not raw:
                            continue
                        kept = resampler.feed(raw) if resampler else odd + raw
                        if resampler is None:
                            odd = kept[-1:] if len(kept) % 2 else b""
                            kept = kept[: len(kept) - len(odd)]
                        if listen is not None and kept:
                            listen.write(copy_cursor, kept)
                            copy_cursor += len(kept) - len(kept) % 2
                        audio.extend(kept)
                        enqueue_pieces(segmenter.feed(kept))
                        maybe_draft()
                        keep = max(segmenter.retain_from, audio_base)
                        if keep - audio_base > LIVE_RATE * 2:
                            del audio[: keep - audio_base]
                            audio_base = keep
                    elif message_type == "input_audio_buffer.commit":
                        flush_pieces()
                        enqueue(("event", _flushed_event(input_clock.until_ms)))
                    elif message_type == "input_audio_buffer.clear":
                        opened = segmenter.open_piece()
                        if opened is not None:
                            number = close_piece()
                            enqueue(("piece", number, b"", at_ms(opened[0]), input_clock.until_ms))
                        segmenter.discard()
                        reset_segmenter()
            finally:
                tasks = [worker]
                if active_draft is not None:
                    tasks.append(active_draft[1])
                await _cancel_tasks(*tasks)
    except WebSocketDisconnect:
        return
    except Exception as error:
        with suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {
                    "type": "service.error",
                    "code": "provider_unavailable",
                    "message": f"{choice.provider} stopped responding: {error}",
                }
            )
            await websocket.close(code=1011)


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
    """One upstream task whose timestamps are relative to its first submitted frame."""

    id: str
    number: int
    start_byte: int | None = None  # exact recording-clock origin once audio is submitted
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    outcome: str = ""  # "finished", or "failed" with ``error``
    error: str = ""
    finish_sent: bool = False
    submitted_until: int = 0  # recording-clock bytes
    confirmed_until_byte: int = 0  # provider-confirmed recording-clock bytes
    drafts: set[str] = field(default_factory=set)

    def end(self, outcome: str, error: str = "") -> None:
        if not self.ended.is_set():
            self.outcome, self.error = outcome, error
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
        prepared: tuple[ClientConnection, _Task],
        choice: Choice,
        *,
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
        upstream, task = prepared
        self.up: ClientConnection | None = upstream
        self.choice = choice
        self.model = qwen_live_id(choice.model)
        self.context = context
        self.tag = secrets.token_hex(2)
        self.resampler = (
            Pcm16Resampler(sample_rate, LIVE_RATE) if sample_rate != LIVE_RATE else None
        )
        self.cursor = max(0, offset_ms) * BYTES_PER_MS  # where the next audio should start
        self.client_ack_ms = max(0, offset_ms)
        self.input_clock = _V2InputClock(offset_ms, sample_rate)
        self.chunks: list[_Chunk] = []  # audio no final sentence has covered yet
        self.sent = 0  # how many of ``chunks`` went to the current task
        self.task: _Task | None = task
        self.task_count = task.number
        # Track an audio-bearing task whose loss still awaits replay or commit failure.
        self.interrupted_task: _Task | None = None
        self.failures = 0
        self.recent = ""
        # Finish each task at the next pending commit's recording-clock byte offset.
        self.flush_marks: deque[tuple[int, int]] = deque()
        self.wake = asyncio.Event()
        self.send_lock = asyncio.Lock()
        self.reader: asyncio.Task[None] | None = None
        self.retiring: dict[asyncio.Task[None], _Task] = {}
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
        start_ms, end_ms = start_byte // BYTES_PER_MS, end_byte // BYTES_PER_MS
        if end_ms > start_ms:
            await self.send(_gap_event(start_ms, end_ms, reason))

    async def run(self, frames: _ClientFrames) -> None:
        ready: dict[str, Any] = {
            "type": "service.ready",
            "provider": self.choice.kind,
            "model": self.choice.model,
            "stream": True,
            "local": False,
            "diarize": False,
            "live": "stream",
            "protocol": 2,
            "check_again": self.check_again,
            "group_paragraphs": self.group_paragraphs,
        }
        worker = None
        try:
            await self.send(ready)
            assert self.up is not None and self.task is not None
            self._attach(self.up, self.task)
            worker = asyncio.create_task(self._work())
            async for message in frames:
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                await self._on_client(payload)
        finally:
            self.gone = True
            if worker is not None:
                await _cancel_tasks(worker)
            retiring = tuple(self.retiring.items())
            for _, task in retiring:
                task.end("failed", "the client disconnected")
            await self._close_upstream()
            if retiring:
                with CancelScope(shield=True):
                    await asyncio.gather(*(job for job, _ in retiring))

    # Audio in -----------------------------------------------------------------

    async def _on_client(self, payload: dict[str, Any]) -> None:
        kind = payload.get("type")
        if kind == "input_audio_buffer.append":
            await self._append(payload)
        elif kind == "input_audio_buffer.commit":
            self._commit(self.input_clock.until_ms)
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
        if not pcm:
            return
        until_ms = self.input_clock.until_ms
        pcm, start_ms = self.input_clock.append(pcm, payload.get("offset_ms"))
        if start_ms is not None:
            if start_ms > until_ms:
                await self.send(_gap_event(until_ms, start_ms, "missing"))
                if self.chunks or (self.task is not None and self.task.start_byte is not None):
                    mark = (self.cursor, until_ms)
                    if not self.flush_marks or self.flush_marks[-1] != mark:
                        self._commit(until_ms)
            self.cursor = start_ms * BYTES_PER_MS
            if self.resampler is not None:
                self.resampler = Pcm16Resampler(self.input_clock.sample_rate, LIVE_RATE)
        if not pcm:
            return
        if self.resampler is not None:
            pcm = self.resampler.feed(pcm)
        pcm = pcm[: len(pcm) - len(pcm) % 2]
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

    def _commit(self, until_ms: int) -> None:
        """The client paused or stopped: finish the task once everything so far is sent."""

        self.flush_marks.append((self.cursor, until_ms))
        self.wake.set()

    async def _flush(self) -> None:
        while self.flush_marks:
            boundary, until_ms = self.flush_marks[0]
            if self.chunks and self.chunks[0].start < boundary:
                break  # There is still audio to send (or replay) for this commit.
            interrupted = self.interrupted_task
            if interrupted is not None and interrupted.submitted_until <= boundary:
                raise _Fatal(
                    "finish_unconfirmed", "Qwen ended before the audio could be confirmed."
                )
            self.flush_marks.popleft()
            await self.send(_flushed_event(until_ms))
            if not self.gone:
                self.client_ack_ms = max(self.client_ack_ms, until_ms)

    def _remove_front(self, count: int) -> None:
        del self.chunks[:count]
        self.sent = max(0, self.sent - count)

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
            lost_start = chunk.start if lost_start is None else lost_start
            lost_end = chunk.end
        if lost_start is not None:
            await self.gap(lost_start, lost_end, "connection")

    def _confirm_until_byte(self, end_byte: int) -> None:
        """A final sentence covered through ``end_byte``: audio before it need not be kept."""

        count = 0
        while count < self.sent and self.chunks[count].end <= end_byte:
            count += 1
        if count:
            self._remove_front(count)

    # The upstream socket ------------------------------------------------------

    def _attach(self, upstream: ClientConnection, task: _Task) -> None:
        assert self.reader is None
        self.reader = asyncio.create_task(self._read(upstream, task))

    async def _stop_reader(self) -> None:
        if self.reader is not None:
            await _cancel_tasks(self.reader)
            self.reader = None

    async def _close_upstream(self) -> None:
        await self._stop_reader()
        if self.up is not None:
            await _close_live_socket(self.up)
            self.up = None

    def _retire_unused(self, upstream: ClientConnection, task: _Task) -> None:
        reader = self.reader
        assert reader is not None
        self.reader = self.task = self.up = None
        job = asyncio.create_task(self._finish_unused(upstream, task, reader))
        self.retiring[job] = task
        job.add_done_callback(self.retiring.pop)

    @staticmethod
    async def _finish_unused(
        upstream: ClientConnection, task: _Task, reader: asyncio.Task[None]
    ) -> None:
        try:
            if not task.ended.is_set():
                task.finish_sent = True
                with suppress(OSError, WebSocketException):
                    async with asyncio.timeout(TASK_END_TIMEOUT):
                        await upstream.send(finish_task_message(task.id))
                        await task.ended.wait()
        finally:
            await _cancel_tasks(reader)
            await _close_live_socket(upstream)

    async def _read(self, upstream: ClientConnection, task: _Task) -> None:
        reason = "the connection closed"
        try:
            async for message in upstream:
                await self._on_upstream(task, message)
                if task.ended.is_set():
                    return
        except ConnectionClosed as error:
            reason = f"the connection closed ({error})"
        except Exception as error:  # noqa: BLE001
            reason = f"the connection failed ({error})"
        task.end("failed", reason)
        self.wake.set()

    async def _on_upstream(self, task: _Task, message: str | bytes) -> None:
        matching = _task_event(message, task.id)
        if matching is None:
            return
        header, data = matching
        event = header.get("event")
        if event == "task-finished":
            if task.finish_sent:
                task.end("finished")
            else:
                task.end("failed", "the task ended on its own")
            self.wake.set()
        elif event == "task-failed":
            task.end("failed", _task_error(header))
            self.wake.set()
        elif event == "result-generated" and task.start_byte is not None:
            output = (data.get("payload") or {}).get("output") or {}
            sentence = output.get("sentence") or {}
            if sentence and not sentence.get("heartbeat"):
                await self._on_result(task, sentence)

    async def _on_result(self, task: _Task, sentence: dict[str, Any]) -> None:
        assert task.start_byte is not None
        origin_ms = task.start_byte // BYTES_PER_MS
        text = str(sentence.get("text") or "")
        piece_id = f"{self.tag}-{task.number}-{sentence.get('sentence_id', 0)}"
        begin = sentence.get("begin_time")
        start_ms = origin_ms + int(begin if begin is not None else 0)
        if not sentence.get("sentence_end"):
            if text.strip():
                task.drafts.add(piece_id)
                await self.send(_piece_event(piece_id, start_ms, None, text))
            return
        end = sentence.get("end_time")
        end_rel_ms = int(end) if end is not None else int(begin if begin is not None else 0)
        end_ms = origin_ms + end_rel_ms
        sentences = split_sentences(sentence.get("words") or [], origin_ms)
        if not sentences and text.strip():
            sentences = [{"start_ms": start_ms, "end_ms": end_ms, "text": text.strip()}]
        confirmed_byte = task.start_byte + max(0, end_rel_ms) * BYTES_PER_MS
        task.confirmed_until_byte = max(task.confirmed_until_byte, confirmed_byte)
        self.failures = 0
        self._confirm_until_byte(confirmed_byte)
        had_draft = piece_id in task.drafts
        task.drafts.discard(piece_id)
        if not sentences and not had_draft:
            return
        spoken = ""
        if sentences:
            start_ms, end_ms = sentences[0]["start_ms"], sentences[-1]["end_ms"]
            spoken = " ".join(item["text"] for item in sentences)
            self.recent = f"{self.recent} {spoken}".strip()[-2 * 400 :]
        await self.send(_piece_event(piece_id, start_ms, end_ms, spoken, sentences))
        if not self.gone:
            self.client_ack_ms = max(self.client_ack_ms, end_ms)

    # Keeping a task going -----------------------------------------------------

    async def _work(self) -> None:
        try:
            while True:
                await self.wake.wait()
                self.wake.clear()
                await self._drain()
        except _Fatal as error:
            try:
                await self.send(
                    {"type": "service.error", "code": error.code, "message": error.message}
                )
                with suppress(RuntimeError, WebSocketDisconnect):
                    await self.websocket.close(code=1011)
            finally:
                self.gone = True
                await self._close_upstream()

    async def _drain(self) -> None:
        while True:
            task = self.task
            if task is not None and task.ended.is_set():
                await self._settle(task)
                continue
            if task is None:
                await self._flush()
                if not self.chunks:
                    return
                await self._start_task()
                continue
            upstream = self.up
            assert upstream is not None
            try:
                while (
                    not task.ended.is_set()
                    and not task.finish_sent
                    and self.sent < len(self.chunks)
                ):
                    chunk = self.chunks[self.sent]
                    if self.flush_marks and chunk.start >= self.flush_marks[0][0]:
                        break
                    if task.start_byte is None:
                        # The provider may receive this frame before send() returns.
                        task.start_byte = chunk.start
                        task.confirmed_until_byte = chunk.start
                        self.interrupted_task = None
                    task.submitted_until = chunk.end
                    self.sent += 1
                    await asyncio.wait_for(upstream.send(chunk.data), SEND_TIMEOUT)
                if self.flush_marks and not task.finish_sent and not task.ended.is_set():
                    if task.start_byte is None:
                        self._retire_unused(upstream, task)
                        continue
                    task.finish_sent = True
                    await asyncio.wait_for(
                        upstream.send(finish_task_message(task.id)), SEND_TIMEOUT
                    )
            except TimeoutError:
                task.end("failed", f"Qwen stopped receiving for {SEND_TIMEOUT:g} seconds")
                await self._close_upstream()
            except (OSError, WebSocketException) as error:
                task.end("failed", f"the connection closed ({error})")
                await self._close_upstream()
            if task.finish_sent and not task.ended.is_set():
                try:
                    await asyncio.wait_for(task.ended.wait(), TASK_END_TIMEOUT)
                except TimeoutError:
                    if not task.ended.is_set():
                        raise _Fatal(
                            "finish_unconfirmed",
                            "Qwen did not confirm the end of the audio "
                            f"within {TASK_END_TIMEOUT:g} seconds.",
                        ) from None
                continue
            if not task.ended.is_set():
                return

    async def _settle(self, task: _Task) -> None:
        await self._stop_reader()
        self.task = None
        if task.outcome == "finished":
            self._remove_front(self.sent)
            return
        if task.start_byte is None:
            return
        self.interrupted_task = task
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
            assert task.start_byte is not None
            confirmed_ms = self.client_ack_ms
            for piece_id in task.drafts:
                await self.send(_piece_event(piece_id, confirmed_ms, confirmed_ms, ""))
            resume = max(task.start_byte, task.confirmed_until_byte)
            while self.chunks and self.chunks[0].end <= resume:
                self._remove_front(1)
            if self.chunks and self.chunks[0].start < resume:
                cut = (resume - self.chunks[0].start) & ~1
                first = self.chunks[0]
                self.chunks[0] = _Chunk(first.start + cut, first.data[cut:])
            if self.chunks and self.chunks[0].start > resume:
                await self.gap(resume, self.chunks[0].start, "connection")
        await asyncio.sleep(RECONNECT_BACKOFF[self.failures - 1])

    async def _start_task(self) -> None:
        """Open a task at the first audio waiting, reconnecting first if the socket closed."""

        try:
            if self.up is not None and self.up.state != State.OPEN:
                await self._close_upstream()
            if self.up is None:
                self.up = await _connect_live_socket(
                    self.choice.base_url, self.choice.api_key or ""
                )
            self.task_count += 1
            self.task = await _start_live_task(
                self.up, self.model, context_turns(self.context, self.recent), self.task_count
            )
            self._attach(self.up, self.task)
        except (OSError, WebSocketException) as error:
            await self._close_upstream()
            await self._recover(str(error) or type(error).__name__, None)


async def _prepare_for_client(
    frames: _ClientFrames, choice: Choice, live_model: str, context: str, sample_rate: int
) -> tuple[tuple[ClientConnection, _Task] | None, str]:
    setup = asyncio.create_task(
        _prepare_live_task(
            choice.base_url, choice.api_key or "", live_model, context, timeout=LIVE_SETUP_TIMEOUT
        )
    )
    try:
        await frames.buffer_during(setup, sample_rate)
        return setup.result()
    except BaseException:
        (result,) = await _cancel_tasks(setup)
        if not isinstance(result, BaseException):
            prepared, _ = result
            if prepared is not None:
                await _close_live_socket(prepared[0])
        raise


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
    sample_rate: int = 24_000,
    offset_ms: int = 0,
    listen: listen_copy.ListenCopy | None = None,
    check_again: bool = False,
    group_paragraphs: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Have the model chosen for a job write the words of audio sent over the socket.

    Live works in short segments as the audio arrives; otherwise the take waits
    until it ends and goes up whole, which reads better. A live Qwen model is heard as
    one continuous stream when its live id works for this key; if not, it falls back to
    short uploaded segments (once, for the whole socket) and the recording goes on.
    """

    frames = _ClientFrames(websocket)
    try:
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
            status = await sensevoice_health(choice.base_url, transport=transport)
            if status is None:
                await _fail(
                    websocket,
                    "provider_unavailable",
                    f"SenseVoice is not running at {choice.base_url}. Start it, or choose "
                    "another model, such as Qwen, in Settings → Transcription.",
                )
                return
            ready = {**ready, "local": True, "diarize": bool(status.get("diarize"))}
            reset_url = f"{sensevoice_root(choice.base_url)}/reset"
        elif choice.kind == "qwen" and choice.stream:
            await _accept(websocket)
            live_model = qwen_live_id(choice.model)
            prepared, reason = await _prepare_for_client(
                frames, choice, live_model, context, sample_rate
            )
            if prepared is not None:
                live = _LiveStream(
                    websocket,
                    prepared,
                    choice,
                    sample_rate=sample_rate,
                    offset_ms=offset_ms,
                    context=context,
                    listen=listen,
                    check_again=check_again,
                    group_paragraphs=group_paragraphs,
                )
                await live.run(frames)
                return
            logger.warning(
                "%s has no live text here (%s); recording sends short pieces instead",
                live_model,
                reason,
            )
            ready = {**ready, "live": "segments"}
        await _proxy_segmented_transcription(
            websocket,
            frames,
            choice=choice,
            context=context,
            segment_seconds=seconds,
            ready=ready,
            reset_url=reset_url,
            sample_rate=sample_rate,
            pause_cuts=choice.stream,
            offset_ms=offset_ms,
            listen=listen,
            check_again=check_again,
            group_paragraphs=group_paragraphs,
            transport=transport,
        )
    except WebSocketDisconnect:
        return
    except _Fatal as error:
        with suppress(WebSocketDisconnect):
            await _fail(websocket, error.code, error.message)
    except AsrHttpConfigurationError as error:
        await _fail(websocket, "provider_unavailable", str(error))
    finally:
        if listen is not None:
            listen.close()
        await frames.aclose()
