"""Saved recordings decoded into the 16 kHz listen copy (PyAV), whatever they were saved as."""

import struct
from pathlib import Path

import av
import numpy as np
import pytest

from gunther import listen_copy
from gunther.audio_decode import AudioUnreadable, decode_to_copy
from gunther.qwen_stream import BYTES_PER_MS


def encode(path: Path, container: str, codec: str, seconds: float, rate: int) -> Path:
    """A tone for the first half, then silence, written the way a recorder would save it."""

    count = int(seconds * rate)
    samples = (np.sin(2 * np.pi * 440 * np.arange(count) / rate) * 8000).astype(np.int16)
    samples[count // 2 :] = 0
    output = av.open(str(path), "w", format=container)
    stream = output.add_stream(codec, rate=rate)
    stream.layout = "mono"
    for start in range(0, count, 1024):
        frame = av.AudioFrame.from_ndarray(
            samples[start : start + 1024].reshape(1, -1), format="s16", layout="mono"
        )
        frame.sample_rate = rate
        frame.pts = start
        for packet in stream.encode(frame):
            output.mux(packet)
    for packet in stream.encode(None):
        output.mux(packet)
    output.close()
    return path


@pytest.mark.parametrize(
    ("name", "container", "codec", "rate"),
    [
        ("a.webm", "webm", "libopus", 48_000),
        ("a.m4a", "mp4", "aac", 44_100),
        ("a.wav", "wav", "pcm_s16le", 22_050),
    ],
)
def test_a_saved_recording_becomes_a_16_khz_copy_of_the_same_length(
    tmp_path: Path, name: str, container: str, codec: str, rate: int
) -> None:
    source = encode(tmp_path / name, container, codec, 3.0, rate)
    total_ms = decode_to_copy(source, tmp_path / "copies", "rec_x")
    assert abs(total_ms - 3_000) <= 50
    pcm_path, _ = listen_copy.copy_paths(tmp_path / "copies", "rec_x")
    assert pcm_path.stat().st_size == total_ms * BYTES_PER_MS
    assert listen_copy.covered(tmp_path / "copies", "rec_x") == [(0, total_ms)]
    audio, _ = listen_copy.read_range(tmp_path / "copies", "rec_x", 0, total_ms)
    samples = np.frombuffer(audio, dtype="<i2")
    # The tone is in the first half and the second half is quiet.
    assert np.abs(samples[: len(samples) // 2 - 1_600]).max() > 4_000
    assert np.abs(samples[len(samples) // 2 + 1_600 :]).max() < 500


def test_a_longer_recording_is_written_in_blocks_not_all_at_once(tmp_path: Path) -> None:
    source = encode(tmp_path / "long.wav", "wav", "pcm_s16le", 12.0, 16_000)
    total_ms = decode_to_copy(source, tmp_path / "copies", "rec_x")
    assert abs(total_ms - 12_000) <= 50
    ranges = (tmp_path / "copies" / "rec_x.listen.ranges").read_text().splitlines()
    assert len(ranges) >= 12


def test_decoding_again_replaces_an_earlier_copy(tmp_path: Path) -> None:
    long = encode(tmp_path / "long.wav", "wav", "pcm_s16le", 4.0, 16_000)
    short = encode(tmp_path / "short.wav", "wav", "pcm_s16le", 1.0, 16_000)
    decode_to_copy(long, tmp_path / "copies", "rec_x")
    total_ms = decode_to_copy(short, tmp_path / "copies", "rec_x")
    pcm_path, _ = listen_copy.copy_paths(tmp_path / "copies", "rec_x")
    assert abs(total_ms - 1_000) <= 50
    assert pcm_path.stat().st_size == total_ms * BYTES_PER_MS


def test_a_file_that_is_not_audio_says_so_and_leaves_no_copy(tmp_path: Path) -> None:
    broken = tmp_path / "broken.webm"
    broken.write_bytes(struct.pack("<4s", b"nope") * 500)
    with pytest.raises(AudioUnreadable, match="couldn't be read"):
        decode_to_copy(broken, tmp_path / "copies", "rec_x")
    with pytest.raises(AudioUnreadable):
        decode_to_copy(tmp_path / "missing.webm", tmp_path / "copies", "rec_x")
    assert not list((tmp_path / "copies").glob("*.listen.*"))
