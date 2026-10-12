"""Pieces of Qwen's live (duplex WebSocket) speech recognition that need no connection.

The socket flow is: ``run-task`` (wait for ``task-started``), binary 16 kHz PCM frames,
``finish-task``, then ``result-generated`` events until ``task-finished``. Times in the
events count from the start of the task's audio.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import numpy as np

LIVE_RATE = 16_000
# 16 kHz mono 16-bit audio is 32 bytes per millisecond.
BYTES_PER_MS = 32
# Qwen keeps at most 5 context turns of 400 characters each and cuts longer ones silently.
CONTEXT_TURN_CHARS = 400
SENTENCE_END = tuple("。！？!?.；;")


def stream_url(base_url: str) -> str:
    """The live socket address on the same host as the saved address (path and scheme swapped)."""

    parts = urlsplit(base_url.strip())
    scheme = "ws" if parts.scheme == "http" else "wss"
    return f"{scheme}://{parts.netloc}/api-ws/v1/inference"


def context_turns(terms: str, recent: str) -> list[dict[str, Any]]:
    """Context turns for ``run-task``: a terms turn, then what was said just before.

    Qwen cuts turns over 400 characters, so the terms keep their start (the first
    terms matter most) and the recent text keeps its end (the newest words).
    """

    turns = []
    for text in (terms.strip()[:CONTEXT_TURN_CHARS], recent.strip()[-CONTEXT_TURN_CHARS:]):
        if text:
            turns.append({"role": "user", "content": [{"type": "input_text", "text": text}]})
    return turns


def run_task_message(
    task_id: str, model: str, context: list[dict[str, Any]], sample_rate: int = LIVE_RATE
) -> str:
    payload_input: dict[str, Any] = {}
    if context:
        payload_input["context"] = context
    return json.dumps(
        {
            "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": model,
                "parameters": {"format": "pcm", "sample_rate": sample_rate},
                "input": payload_input,
            },
        }
    )


def finish_task_message(task_id: str) -> str:
    return json.dumps(
        {
            "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {"input": {}},
        }
    )


def _sentence(words: list[dict[str, Any]], offset_ms: int) -> dict[str, Any]:
    return {
        "start_ms": offset_ms + int(words[0]["begin_time"]),
        "end_ms": offset_ms + int(words[-1].get("end_time") or words[-1]["begin_time"]),
        "text": "".join(
            str(word.get("text") or "") + str(word.get("punctuation") or "") for word in words
        ).strip(),
    }


def split_sentences(words: list[dict[str, Any]], offset_ms: int = 0) -> list[dict[str, Any]]:
    """Real sentences, with times, from Qwen's words: joined up to a sentence-ending mark.

    Qwen's own pieces can run for many sentences in a noisy room, so sentences come from
    the word times and punctuation. ``offset_ms`` moves task times onto the recording
    clock. Words without a time are skipped; pieces with no text are not sentences.
    """

    out: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        if word.get("begin_time") is None:
            continue
        current.append(word)
        ending = (str(word.get("text") or "") + str(word.get("punctuation") or "")).rstrip()
        if ending.endswith(SENTENCE_END):
            out.append(_sentence(current, offset_ms))
            current = []
    if current:
        out.append(_sentence(current, offset_ms))
    return [sentence for sentence in out if sentence["text"]]


class Pcm16Resampler:
    """Turns a stream of 16-bit mono PCM from one sample rate into another.

    A windowed-sinc filter that low-passes below the new rate's limit while it
    interpolates, so downsampling does not fold high sounds back into the speech. It
    remembers the end of the previous chunk, so chunk edges leave no clicks.
    """

    HALF = 16  # filter taps on each side

    def __init__(self, source_rate: int, target_rate: int) -> None:
        self.step = source_rate / target_rate
        self.cutoff = min(1.0, target_rate / source_rate)
        self._buffer = np.zeros(0, dtype=np.float32)
        self._start = 0  # absolute input index of _buffer[0]
        self._next = 0.0  # absolute input position of the next output sample
        self._odd = b""

    def feed(self, pcm: bytes) -> bytes:
        pcm = self._odd + pcm
        self._odd = pcm[-1:] if len(pcm) % 2 else b""
        pcm = pcm[: len(pcm) - len(pcm) % 2]
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        self._buffer = np.concatenate([self._buffer, samples])
        end = self._start + len(self._buffer)
        count = int(np.floor((end - self.HALF - self._next) / self.step))
        if count <= 0:
            return b""
        positions = self._next + self.step * np.arange(count)
        base = np.floor(positions).astype(np.int64)
        taps = np.arange(1 - self.HALF, self.HALF + 1)
        index = base[:, None] + taps[None, :]
        distance = positions[:, None] - index
        window = 0.5 * (1.0 + np.cos(np.pi * distance / self.HALF))
        weights = self.cutoff * np.sinc(self.cutoff * distance) * window
        local = index - self._start
        inside = (local >= 0) & (local < len(self._buffer))
        values = np.where(inside, self._buffer[np.clip(local, 0, len(self._buffer) - 1)], 0.0)
        out = (weights * values).sum(axis=1)
        self._next = float(positions[-1] + self.step)
        keep_from = int(np.floor(self._next)) - self.HALF + 1
        drop = max(0, min(keep_from - self._start, len(self._buffer)))
        self._buffer = self._buffer[drop:]
        self._start += drop
        return np.clip(np.rint(out), -32768, 32767).astype("<i2").tobytes()


def resample_pcm16(pcm: bytes, source_rate: int, target_rate: int = LIVE_RATE) -> bytes:
    """All of a 16-bit mono PCM clip at the new sample rate (see ``Pcm16Resampler``)."""

    if source_rate == target_rate:
        return pcm[: len(pcm) - len(pcm) % 2]
    return Pcm16Resampler(source_rate, target_rate).feed(pcm)
