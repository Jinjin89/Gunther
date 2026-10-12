"""Cut live speech at its pauses, so a recogniser hears whole phrases.

Silero VAD (MIT, ~2 MB ONNX) says how likely each 32 ms of 16 kHz audio is speech.
``Segmenter`` turns that into pieces to transcribe: a piece ends after a pause, or, if
someone talks for a long time, at the quietest moment of its last seconds. The ONNX
calls follow Silero's own ``OnnxWrapper`` (512-sample frames with the 64 samples before
them, a state tensor carried from frame to frame), ported to numpy so that torch is not
needed.

Nothing here knows a language, a speaker or a provider.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MODEL_NAME = "silero-vad"
MODEL_FILE = "silero_vad.onnx"

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512
FRAME_BYTES = FRAME_SAMPLES * 2  # 32 ms
FRAME_MS = FRAME_SAMPLES * 1000 // SAMPLE_RATE
CONTEXT_SAMPLES = 64
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000

Probability = Callable[[bytes], float]
Piece = tuple[int, int]  # start byte, end byte in the 16 kHz stream the segmenter was fed


def default_model_path() -> Path | None:
    """Where the bundled or fetched model is, if it is anywhere."""

    candidates = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:  # a frozen desktop helper carries the model as data
        candidates.append(Path(bundle) / "models" / MODEL_NAME / MODEL_FILE)
    candidates.append(Path(__file__).resolve().parents[1] / "models" / MODEL_NAME / MODEL_FILE)
    return next((path for path in candidates if path.is_file()), None)


@lru_cache(maxsize=2)
def _session(path: str) -> Any:
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return onnxruntime.InferenceSession(
        path, sess_options=options, providers=["CPUExecutionProvider"]
    )


class SileroVad:
    """Speech probability of consecutive 512-sample frames. One instance per audio stream."""

    def __init__(self, path: Path) -> None:
        self._session = _session(str(path))
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)

    def __call__(self, frame: bytes) -> float:
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
        if len(samples) != FRAME_SAMPLES:
            raise ValueError(f"A frame is {FRAME_SAMPLES} samples, got {len(samples)}")
        window = np.concatenate([self._context, samples])[None, :]
        output, self._state = self._session.run(
            None,
            {"input": window, "state": self._state, "sr": np.array(SAMPLE_RATE, dtype=np.int64)},
        )
        self._context = window[0, -CONTEXT_SAMPLES:]
        return float(output[0, 0])


def new_detector(path: Path | None = None) -> SileroVad | None:
    """A detector for one stream, or None when the model file is missing or unusable."""

    path = path or default_model_path()
    if path is None:
        return None
    try:
        return SileroVad(path)
    except Exception as error:  # a damaged file or no runtime: fall back, don't fail
        logger.warning("The pause detector could not be loaded (%s)", error)
        return None


class Segmenter:
    """Cuts a 16 kHz PCM16 stream into pieces of speech, as the bytes arrive.

    Rules (all measured on the audio itself, none on words or languages):
    - a piece starts at a frame whose speech probability reaches ``threshold``; stretches
      with no such frame are never yielded;
    - it ends once the probability has stayed below ``threshold - negative_margin`` for
      ``min_silence_ms`` (the end is where it first fell);
    - at ``max_piece_ms`` it ends at the quietest frame (lowest probability) of its last
      ``cap_window_ms``, and what follows starts the next piece;
    - ``pad_ms`` of audio is kept on each side of a pause cut (never into the neighbour);
    - a piece is at least ``min_piece_ms`` long: a short one waits for more audio (the
      pause that follows, or speech that joins it) rather than being dropped, so a
      one-word answer is still heard; only ``flush`` can end it shorter.

    ``feed`` and ``flush`` return ``(start_byte, end_byte)`` pairs in the bytes fed so far.
    """

    def __init__(
        self,
        probability: Probability,
        *,
        threshold: float = 0.5,
        negative_margin: float = 0.15,
        min_silence_ms: int = 500,
        max_piece_ms: int = 15_000,
        cap_window_ms: int = 2_000,
        min_piece_ms: int = 1_000,
        pad_ms: int = 200,
    ) -> None:
        self._probability = probability
        self.threshold = threshold
        self.negative = max(threshold - negative_margin, 0.01)
        self._silence_frames = max(1, -(-min_silence_ms // FRAME_MS))
        self._max_frames = max(1, max_piece_ms // FRAME_MS)
        self._cap_frames = max(1, cap_window_ms // FRAME_MS)
        self._min_bytes = min_piece_ms * BYTES_PER_MS
        self._pad = pad_ms * BYTES_PER_MS
        self.total = 0  # bytes fed
        self._frames = 0  # whole frames judged
        self._partial = bytearray()
        self._recent: list[float] = []  # probabilities of the newest frames
        self._open: int | None = None  # first frame of the piece being heard
        self._last_speech = 0
        self._quiet_from: int | None = None  # first frame of the current quiet run
        self._written = 0  # end byte of the last piece yielded

    # Input ---------------------------------------------------------------------

    def feed(self, pcm: bytes) -> list[Piece]:
        self.total += len(pcm)
        self._partial.extend(pcm)
        pieces: list[Piece] = []
        while len(self._partial) >= FRAME_BYTES:
            frame = bytes(self._partial[:FRAME_BYTES])
            del self._partial[:FRAME_BYTES]
            index = self._frames
            self._frames += 1
            probability = self._probability(frame)
            self._recent.append(probability)
            del self._recent[: -2 * self._max_frames]
            self._judge(index, probability, pieces)
        return pieces

    def flush(self) -> list[Piece]:
        """A pause or stop: finish the piece being heard, and start fresh after it."""

        pieces: list[Piece] = []
        if self._open is not None:
            end = self._quiet_from if self._quiet_from is not None else self._last_speech + 1
            self._close(end, True, pieces, self.total)
        self._reset_detector()
        return pieces

    def discard(self) -> None:
        """Drop the piece being heard without yielding it."""

        self._open = None
        self._quiet_from = None
        self._written = max(self._written, self._frames * FRAME_BYTES)
        self._reset_detector()

    def _reset_detector(self) -> None:
        reset = getattr(self._probability, "reset", None)
        if callable(reset):
            reset()

    # Where things are ----------------------------------------------------------

    def open_piece(self) -> Piece | None:
        """The piece still growing, as far as it has been heard (for a draft)."""

        if self._open is None:
            return None
        return self._start_byte(self._open), self._frames * FRAME_BYTES

    @property
    def retain_from(self) -> int:
        """Audio before this byte can never belong to a piece again."""

        if self._open is not None:
            return self._start_byte(self._open)
        return max(self._written, self._frames * FRAME_BYTES - self._pad)

    # Rules ---------------------------------------------------------------------

    def _start_byte(self, frame: int) -> int:
        return max(frame * FRAME_BYTES - self._pad, self._written)

    def _judge(self, index: int, probability: float, pieces: list[Piece]) -> None:
        if self._open is None:
            if probability >= self.threshold:
                self._open = index
                self._last_speech = index
                self._quiet_from = None
            return
        if probability >= self.threshold:
            self._last_speech = index
            self._quiet_from = None
        elif probability < self.negative:
            if self._quiet_from is None:
                self._quiet_from = index
            quiet = index + 1 - self._quiet_from >= self._silence_frames
            # A piece under the minimum waits (the quiet goes on being audio it may need)
            # until it is long enough, unless speech comes back first and joins it.
            if (
                quiet
                and (index + 1) * FRAME_BYTES - self._start_byte(self._open) >= self._min_bytes
            ):
                self._close(self._quiet_from, True, pieces, self._frames * FRAME_BYTES)
                return
        if index + 1 - self._open >= self._max_frames:
            self._cut_at_quietest(index, pieces)

    def _cut_at_quietest(self, index: int, pieces: list[Piece]) -> None:
        assert self._open is not None
        first = max(self._open, index + 1 - self._cap_frames)
        base = self._frames - len(self._recent)  # frame number of _recent[0]
        window = [(self._recent[frame - base], frame) for frame in range(first, index + 1)]
        quietest = min(probability for probability, _ in window)
        cut = max(frame for probability, frame in window if probability == quietest)
        self._close(cut + 1, False, pieces, self._frames * FRAME_BYTES)
        # What came after the cut is the start of the next piece.
        for frame in range(cut + 1, index + 1):
            self._judge(frame, self._recent[frame - base], pieces)

    def _close(self, end_frame: int, pad_end: bool, pieces: list[Piece], limit: int) -> None:
        assert self._open is not None
        start = self._start_byte(self._open)
        end = end_frame * FRAME_BYTES + (self._pad if pad_end else 0)
        if end - start < self._min_bytes:
            end = start + self._min_bytes
        end = min(end, limit)
        end -= end % 2
        self._open = None
        self._quiet_from = None
        if end > start:
            pieces.append((start, end))
            self._written = end


class FixedBlocks:
    """The old way, kept as a fallback: a piece every ``block_bytes`` by the clock."""

    def __init__(self, block_bytes: int) -> None:
        self.block = max(2, block_bytes - block_bytes % 2)
        self.total = 0
        self._start = 0

    def feed(self, pcm: bytes) -> list[Piece]:
        self.total += len(pcm)
        pieces: list[Piece] = []
        while self.total - self._start >= self.block:
            pieces.append((self._start, self._start + self.block))
            self._start += self.block
        return pieces

    def flush(self) -> list[Piece]:
        end = self.total - self.total % 2
        if end <= self._start:
            return []
        piece = (self._start, end)
        self._start = end
        return [piece]

    def discard(self) -> None:
        self._start = self.total

    def open_piece(self) -> Piece | None:
        return None

    @property
    def retain_from(self) -> int:
        return self._start
