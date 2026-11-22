from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import secrets
import uuid
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import httpx
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
# How long to wait for a task to finish after finish-task.
TASK_END_TIMEOUT = 10.0
# How long one frame may take to go out before the live socket counts as stalled.
SEND_TIMEOUT = 10.0
CONNECT_TIMEOUT = 30.0
# Audio kept to send again if the live socket drops, and queued while it reconnects.
RETAIN_SECONDS = 30.0
RECONNECT_BACKOFF = (0.5, 1.0, 2.0, 4.0)
# The live socket goes direct, like the HTTP requests: websockets would otherwise follow the
# system's proxy settings (a SOCKS proxy from a VPN app needs python-socks, which isn't here,
# and failed the whole request).
LIVE_PROXY = None
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
            proxy=LIVE_PROXY,
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
    detector = vad.new_detector() if pause_cuts else None
    if pause_cuts and detector is None:
        _warn_no_pause_model()
        ready = {**ready, "live": "segments-fixed"}
    segmenter: vad.Segmenter | vad.FixedBlocks
    if detector is not None:
        segmenter = vad.Segmenter(detector)
    else:
        segmenter = vad.FixedBlocks(int(LIVE_RATE * 2 * max(1.0, segment_seconds)))
    drafts = choice.kind == "sensevoice" and detector is not None
    audio = bytearray()  # 16 kHz audio from ``audio_base`` on that a piece may still need
    audio_base = 0
    odd = b""  # a half sample when the client's chunk ends inside one
    # The recording clock: ``clock_ms`` at byte ``clock_byte`` of the 16 kHz stream.
    clock_byte = 0
    clock_ms = max(0, offset_ms)
    input_clock = _V2InputClock(offset_ms, sample_rate)

    def at_ms(byte: int) -> int:
        return clock_ms + (byte - clock_byte) // BYTES_PER_MS

    tag = secrets.token_hex(2)
    resampler = Pcm16Resampler(sample_rate, LIVE_RATE) if sample_rate != LIVE_RATE else None
    copy_cursor = max(0, offset_ms) * BYTES_PER_MS
    jobs: asyncio.Queue[tuple[Any, ...]] = asyncio.Queue()
    outstanding = 0  # jobs queued or running
    pieces_closed = 0  # pieces handed to the worker; the next one is number pieces_closed + 1
    draft_marks: dict[int, int] = {}  # piece number -> its end byte when the last draft went
    drafted: set[int] = set()  # pieces whose draft put text on screen
    recent = ""  # the tail of what was written, the context for the next piece

    def enqueue(job: tuple[Any, ...]) -> None:
        nonlocal outstanding
        outstanding += 1
        jobs.put_nowait(job)

    def enqueue_pieces(found: list[vad.Piece]) -> None:
        nonlocal pieces_closed
        for start, end in found:
            pieces_closed += 1
            number = pieces_closed
            chunk = bytes(audio[start - audio_base : end - audio_base])
            enqueue(("piece", number, chunk, at_ms(start), at_ms(end)))
            draft_marks.pop(number, None)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
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
                if text:
                    recent = f"{recent} {text}".strip()[-CONTEXT_CHARS:]
                elif number not in drafted:
                    return
                await websocket.send_json(_piece_event(f"{tag}-{number}", start_ms, end_ms, text))

            async def write_draft(number: int, chunk: bytes, start_ms: int) -> None:
                if number <= pieces_closed:
                    return  # its final is already queued: a draft now would only be stale
                try:
                    text = await transcribe_segment(client, choice, chunk, title=context)
                except (httpx.HTTPError, ValueError, TypeError) as error:
                    logger.debug("Draft of a live piece failed: %s", error)
                    return
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
                while True:
                    message = await websocket.receive_text()
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
                            enqueue_pieces(segmenter.flush())
                            if start > until_ms:
                                enqueue(("event", _gap_event(until_ms, start, "missing")))
                            clock_byte, clock_ms = segmenter.total, start
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
                        enqueue_pieces(segmenter.flush())
                        enqueue(("event", _flushed_event(input_clock.until_ms)))
                    elif message_type == "input_audio_buffer.clear":
                        segmenter.discard()
            finally:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
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
        self.context = context
        self.tag = secrets.token_hex(2)
        self.resampler = (
            Pcm16Resampler(sample_rate, LIVE_RATE) if sample_rate != LIVE_RATE else None
        )
        self.cursor = max(0, offset_ms) * BYTES_PER_MS  # where the next audio should start
        self.input_clock = _V2InputClock(offset_ms, sample_rate)
        self.chunks: list[_Chunk] = []  # audio no final sentence has covered yet
        self.sent = 0  # how many of ``chunks`` went to the current task
        self.task: _Task | None = None
        self.task_count = 0
        self.failures = 0
        self.recent = ""
        # Finish each task at the next pending commit's recording-clock byte offset.
        self.flush_marks: deque[tuple[int, int]] = deque()
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
        if end_byte > start_byte:
            start_ms, end_ms = start_byte // BYTES_PER_MS, end_byte // BYTES_PER_MS
            await self.send(_gap_event(start_ms, end_ms, reason))

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
            "protocol": 2,
            "check_again": self.check_again,
            "group_paragraphs": self.group_paragraphs,
        }
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
                if self.task is not None or self.chunks:
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
        while self.flush_marks and (
            not self.chunks or self.chunks[0].start >= self.flush_marks[0][0]
        ):
            _, until_ms = self.flush_marks.popleft()
            await self.send(_flushed_event(until_ms))

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
        piece_id = f"{self.tag}-{task.number}-{sentence.get('sentence_id', 0)}"
        begin = sentence.get("begin_time")
        start_ms = task.start_ms + int(begin if begin is not None else 0)
        if not sentence.get("sentence_end"):
            if text.strip():
                await self.send(_piece_event(piece_id, start_ms, None, text))
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
        await self.send(
            _piece_event(
                piece_id, sentences[0]["start_ms"], sentences[-1]["end_ms"], spoken, sentences
            )
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
                await self._flush()
                if not self.chunks:
                    return
                await self._start_task()
                continue
            if self.up is None:
                return
            try:
                while not task.finish_sent and self.sent < len(self.chunks):
                    chunk = self.chunks[self.sent]
                    if self.flush_marks and chunk.start >= self.flush_marks[0][0]:
                        break
                    self.sent += 1
                    await asyncio.wait_for(self.up.send(chunk.data), SEND_TIMEOUT)
                if self.flush_marks and not task.finish_sent:
                    task.finish_sent = True
                    await asyncio.wait_for(self.up.send(finish_task_message(task.id)), SEND_TIMEOUT)
            except ConnectionClosed as error:
                task.end("failed", f"the connection closed ({error})")
            except TimeoutError:
                # Qwen stopped taking audio (a stalled link, or a starved machine): drop this
                # socket so the task starts again on a new one, from the last final sentence.
                stalled, self.up = self.up, None
                if stalled is not None:
                    asyncio.create_task(stalled.close())
                task.end("failed", f"Qwen took no audio for {SEND_TIMEOUT:g} seconds")
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
            if task.finish_sent:
                self._remove_front(self.sent)
            self.sent = 0
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
                        proxy=LIVE_PROXY,
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
        sample_rate=sample_rate,
        offset_ms=offset_ms,
        context=context,
        listen=listen,
        check_again=check_again,
        group_paragraphs=group_paragraphs,
    )
    await live.run()


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
        ready = {**ready, "local": True, "diarize": bool(status.get("diarize"))}
        reset_url = f"{sensevoice_root(choice.base_url)}/reset"
    elif choice.kind == "qwen" and choice.stream:
        # Accept first: checking the live model can take seconds on a slow link, and the
        # client's audio waits in the socket meanwhile instead of in its own small queue.
        await _accept(websocket)
        live_model = qwen_live_id(choice.model)
        upstream, reason = await open_live_socket(choice.base_url, choice.api_key or "", live_model)
        if upstream is not None:
            await _proxy_live_stream(
                websocket,
                upstream,
                choice,
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
    await _proxy_segmented_transcription(
        websocket,
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
    )
