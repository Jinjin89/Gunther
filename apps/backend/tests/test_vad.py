"""Cutting live speech at its pauses: the rules (with made-up probabilities) and the model."""

import json
import wave
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest

from gunther import vad
from gunther.vad import FRAME_BYTES, Segmenter

PAD = 200 * vad.BYTES_PER_MS


def frames(milliseconds: int) -> int:
    return -(-milliseconds // vad.FRAME_MS)


def script(*parts: tuple[float, int]) -> list[float]:
    """(probability, milliseconds) runs as one probability per 32 ms frame."""

    return [p for p, ms in parts for _ in range(frames(ms))]


def segmenter_for(probabilities: list[float]) -> tuple[Segmenter, bytes]:
    source: Iterator[float] = iter(probabilities)
    return Segmenter(lambda _frame: next(source)), bytes(len(probabilities) * FRAME_BYTES)


def run(
    probabilities: list[float], chunk: int = 10 * FRAME_BYTES, flush: bool = False
) -> list[vad.Piece]:
    segmenter, audio = segmenter_for(probabilities)
    pieces: list[vad.Piece] = []
    for at in range(0, len(audio), chunk):
        pieces += segmenter.feed(audio[at : at + chunk])
    return pieces + segmenter.flush() if flush else pieces


SPEECH, QUIET = 0.9, 0.05


def test_a_piece_ends_after_half_a_second_of_quiet_and_not_before() -> None:
    probabilities = script((SPEECH, 2000), (QUIET, 1000), (SPEECH, 2000), (QUIET, 1000))
    segmenter, audio = segmenter_for(probabilities)
    speech_end = frames(2000) * FRAME_BYTES
    # 15 quiet frames (480 ms) are not enough; the 16th makes half a second.
    assert segmenter.feed(audio[: speech_end + 15 * FRAME_BYTES]) == []
    first = segmenter.feed(audio[speech_end + 15 * FRAME_BYTES : speech_end + 16 * FRAME_BYTES])
    assert first == [(0, speech_end + PAD)]
    rest = segmenter.feed(audio[speech_end + 16 * FRAME_BYTES :])
    second_start = (frames(2000) + frames(1000)) * FRAME_BYTES
    assert rest == [(second_start - PAD, second_start + frames(2000) * FRAME_BYTES + PAD)]


def test_a_short_pause_in_the_middle_of_speech_does_not_cut() -> None:
    probabilities = script((SPEECH, 2000), (QUIET, 400), (SPEECH, 2000), (QUIET, 1000))
    pieces = run(probabilities)
    assert len(pieces) == 1


def test_a_piece_is_padded_on_both_sides_without_reaching_into_the_last_one() -> None:
    probabilities = script(
        (QUIET, 2000), (SPEECH, 1500), (QUIET, 600), (SPEECH, 1500), (QUIET, 1000)
    )
    (first, second) = run(probabilities)
    start = frames(2000) * FRAME_BYTES
    assert first[0] == start - PAD
    assert first[1] == start + frames(1500) * FRAME_BYTES + PAD
    second_start = start + (frames(1500) + frames(600)) * FRAME_BYTES
    assert second[0] == second_start - PAD
    assert second[0] >= first[1]  # the paddings never overlap


def test_a_long_run_is_cut_at_the_quietest_frame_of_its_last_two_seconds() -> None:
    probabilities = script((SPEECH, 20_000))
    dip = 450  # inside the last 2 s of the first 15 s (frames 407-467)
    probabilities[dip] = 0.3
    probabilities[dip - 20] = 0.4  # quieter than speech, but not the quietest
    probabilities[300] = 0.0  # the quietest of all, but older than 2 s: ignored
    pieces = run(probabilities, flush=True)
    assert pieces[0] == (0, (dip + 1) * FRAME_BYTES)
    assert pieces[1][0] == (dip + 1) * FRAME_BYTES
    # Every piece is at most the cap plus the part that came after the cut.
    assert all((end - start) <= 15_000 * vad.BYTES_PER_MS + PAD for start, end in pieces)


def test_a_cut_at_the_cap_leaves_nothing_out_and_nothing_twice() -> None:
    pieces = run(script((SPEECH, 40_000)), flush=True)
    assert pieces[0][0] == 0
    assert all(a[1] == b[0] for a, b in zip(pieces, pieces[1:], strict=False))


def test_a_piece_shorter_than_a_second_is_made_a_second_long() -> None:
    pieces = run(script((SPEECH, 160), (QUIET, 1500)))
    assert pieces == [(0, vad.BYTES_PER_MS * 1000)]
    # Short speech that is followed by more speech before a second has passed joins it.
    joined = run(script((SPEECH, 160), (QUIET, 600), (SPEECH, 1000), (QUIET, 1000)))
    assert len(joined) == 1 and joined[0][0] == 0


def test_stretches_without_speech_are_never_yielded() -> None:
    segmenter, audio = segmenter_for(script((QUIET, 8000), (0.45, 2000)))
    assert segmenter.feed(audio) == []  # 0.45 is below the speech threshold
    assert segmenter.open_piece() is None
    assert segmenter.flush() == []


def test_flush_ends_the_piece_being_heard_at_the_audio_there_is() -> None:
    probabilities = script((SPEECH, 3000))
    segmenter, audio = segmenter_for(probabilities)
    assert segmenter.feed(audio) == []
    opened = segmenter.open_piece()
    assert opened is not None and opened[0] == 0 and opened[1] == len(audio)
    assert segmenter.flush() == [(0, len(audio))]
    assert segmenter.open_piece() is None
    assert segmenter.flush() == []


def test_flush_keeps_a_started_piece_even_when_the_quiet_has_just_begun() -> None:
    segmenter, audio = segmenter_for(script((SPEECH, 2000), (QUIET, 200)))
    assert segmenter.feed(audio) == []
    assert segmenter.flush() == [(0, frames(2000) * FRAME_BYTES + PAD)]


def test_the_cuts_do_not_depend_on_how_the_audio_is_chunked() -> None:
    probabilities = script(
        (QUIET, 700), (SPEECH, 3000), (QUIET, 900), (SPEECH, 18_000), (QUIET, 700)
    )
    whole = run(probabilities, chunk=10**9, flush=True)
    assert len(whole) >= 3
    assert run(probabilities, chunk=700, flush=True) == whole  # not a whole number of frames
    assert run(probabilities, chunk=FRAME_BYTES, flush=True) == whole


def test_retained_audio_never_cuts_into_a_piece_still_to_come() -> None:
    probabilities = script((QUIET, 1000), (SPEECH, 2000))
    segmenter, audio = segmenter_for(probabilities)
    segmenter.feed(audio[: frames(1000) * FRAME_BYTES])
    assert segmenter.retain_from <= frames(1000) * FRAME_BYTES - PAD + 1
    segmenter.feed(audio[frames(1000) * FRAME_BYTES :])
    assert segmenter.retain_from == frames(1000) * FRAME_BYTES - PAD


def test_discard_drops_the_piece_and_the_next_one_starts_after_it() -> None:
    probabilities = script(
        (SPEECH, 2000), (QUIET, 100), (QUIET, 1000), (SPEECH, 2000), (QUIET, 1000)
    )
    segmenter, audio = segmenter_for(probabilities)
    cut = frames(2000) * FRAME_BYTES
    segmenter.feed(audio[:cut])
    segmenter.discard()
    pieces = segmenter.feed(audio[cut:])
    assert len(pieces) == 1 and pieces[0][0] >= cut


def test_fixed_blocks_are_the_clock_cuts() -> None:
    blocks = vad.FixedBlocks(1000)
    assert blocks.feed(bytes(2500)) == [(0, 1000), (1000, 2000)]
    assert blocks.open_piece() is None
    assert blocks.flush() == [(2000, 2500)]
    assert blocks.flush() == []


# The real model -------------------------------------------------------------------

REAL = Path(vad.__file__).resolve().parents[1] / "models" / "silero-vad" / vad.MODEL_FILE
SAMPLE = Path(__file__).resolve().parents[1] / "evals" / "results" / "transcript" / "sample"
needs_model = pytest.mark.skipif(not REAL.is_file(), reason="run npm run models:fetch first")


@needs_model
def test_the_model_finds_silence_quiet() -> None:
    detector = vad.SileroVad(REAL)
    probabilities = [detector(bytes(FRAME_BYTES)) for _ in range(50)]
    assert max(probabilities) < 0.1
    noise = (np.random.default_rng(1).normal(0, 300, 512 * 50)).astype("<i2").tobytes()
    assert (
        max(detector(noise[i : i + FRAME_BYTES]) for i in range(0, len(noise), FRAME_BYTES)) < 0.5
    )


@needs_model
def test_a_detector_can_start_again_and_gives_the_same_answers() -> None:
    detector = vad.SileroVad(REAL)
    noise = (np.random.default_rng(2).normal(0, 3000, 512 * 8)).astype("<i2").tobytes()
    first = [detector(noise[i : i + FRAME_BYTES]) for i in range(0, len(noise), FRAME_BYTES)]
    detector.reset()
    again = [detector(noise[i : i + FRAME_BYTES]) for i in range(0, len(noise), FRAME_BYTES)]
    assert first == again


@needs_model
def test_a_missing_model_means_no_detector(tmp_path: Path) -> None:
    assert vad.new_detector(tmp_path / "nothing.onnx") is None


def sample_speech() -> bytes | None:
    """A stretch of the evals' spoken lecture (not in a fresh checkout), as 16 kHz PCM."""

    path, reference = SAMPLE / "clean.wav", SAMPLE / "reference.json"
    if not (path.is_file() and reference.is_file()):
        return None
    with wave.open(str(path), "rb") as reader:
        if reader.getframerate() != 16_000 or reader.getnchannels() != 1:
            return None
    data = path.read_bytes()
    pcm = data[data.find(b"data") + 8 :]
    first = json.loads(reference.read_text(encoding="utf-8"))["sentences"][0]
    return pcm[first["start_ms"] * vad.BYTES_PER_MS : first["end_ms"] * vad.BYTES_PER_MS]


@needs_model
def test_the_model_hears_real_speech_and_the_segmenter_yields_it() -> None:
    speech = sample_speech()
    if speech is None:
        pytest.skip("the evals' sample lecture is not here (make_sample builds it)")
    detector = vad.SileroVad(REAL)
    probabilities = [
        detector(speech[i : i + FRAME_BYTES])
        for i in range(0, len(speech) - FRAME_BYTES, FRAME_BYTES)
    ]
    assert sum(p >= 0.5 for p in probabilities) > 0.5 * len(probabilities)
    segmenter = Segmenter(vad.SileroVad(REAL))
    pieces = segmenter.feed(speech + bytes(2 * 16_000 * 2)) + segmenter.flush()
    assert pieces and pieces[0][0] < 16_000 * 2  # starts within the first second
    assert pieces[-1][1] >= len(speech) - 16_000 * 2 // 2


Factory = Callable[[], Segmenter]
