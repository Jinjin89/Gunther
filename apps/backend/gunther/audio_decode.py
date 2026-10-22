"""Decode a saved recording into the 16 kHz audio copy that listening again reads.

Recordings are kept in whatever the recorder produced (WebM/Opus, MP4/AAC, MP3, WAV...).
PyAV (FFmpeg's Python binding) reads them and a resampler turns them into 16 kHz mono
16-bit audio, which goes into a ``ListenCopy`` about a second at a time, so an hour of
audio is never held in memory. The length of the recording is the number of samples that
came out: the container's duration and time stamps are not trusted (a file from
``MediaRecorder`` can have none).
"""

from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

import av
import av.error

from gunther import listen_copy
from gunther.qwen_stream import BYTES_PER_MS

SAMPLE_RATE = 16_000
BLOCK_BYTES = 1_000 * BYTES_PER_MS


class AudioUnreadable(Exception):
    """The audio file could not be decoded; the message is plain words for the user."""


def audio_duration_ms(path: Path, *, stopping: Callable[[], bool] | None = None) -> int:
    """Count decoded samples without trusting container metadata or writing a PCM copy."""

    counts: dict[int, int] = {}
    try:
        before = path.stat()
        with av.open(str(path)) as container:
            stream = next((item for item in container.streams if item.type == "audio"), None)
            if stream is None:
                raise AudioUnreadable("The audio file couldn't be read: it has no sound.")
            for frame in container.decode(stream):
                if stopping and stopping():
                    raise RuntimeError("Worker is stopping")
                if frame.sample_rate <= 0:
                    raise AudioUnreadable("The audio sample rate is unavailable.")
                counts[frame.sample_rate] = counts.get(frame.sample_rate, 0) + frame.samples
        after = path.stat()
    except (av.error.FFmpegError, OSError) as error:
        raise AudioUnreadable("The audio file couldn't be read.") from error
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        raise AudioUnreadable("The audio file changed while its duration was being measured.")
    duration = sum(
        (Fraction(samples * 1_000, rate) for rate, samples in counts.items()), Fraction()
    )
    if duration <= 0:
        raise AudioUnreadable("The audio file couldn't be read: it has no sound.")
    return round(duration)


def decode_to_copy(path: Path, directory: Path, recording_id: str) -> int:
    """Write the recording at ``path`` as a listen copy under ``recording_id``.

    Any earlier copy of that recording is replaced. Returns the length in milliseconds.
    """

    listen_copy.remove_copy(directory, recording_id)
    copy = listen_copy.ListenCopy(directory, recording_id)
    written = 0
    pending = bytearray()

    def put(data: bytes) -> None:
        nonlocal written
        pending.extend(data)
        while len(pending) >= BLOCK_BYTES:
            copy.write(written, bytes(pending[:BLOCK_BYTES]))
            written += BLOCK_BYTES
            del pending[:BLOCK_BYTES]

    try:
        try:
            with av.open(str(path)) as container:
                stream = next((item for item in container.streams if item.type == "audio"), None)
                if stream is None:
                    raise AudioUnreadable("The audio file couldn't be read: it has no sound.")
                resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
                for frame in container.decode(stream):
                    for out in resampler.resample(frame):
                        put(out.to_ndarray().tobytes())
                for out in resampler.resample(None):
                    put(out.to_ndarray().tobytes())
        except av.error.FFmpegError as error:
            raise AudioUnreadable(f"The audio file couldn't be read: {error}") from error
        except OSError as error:
            raise AudioUnreadable(f"The audio file couldn't be read: {error}") from error
        if pending:
            copy.write(written, bytes(pending))
            written += len(pending) - len(pending) % 2
        if written == 0:
            raise AudioUnreadable("The audio file couldn't be read: it has no sound.")
    except BaseException:
        copy.close()
        listen_copy.remove_copy(directory, recording_id)
        raise
    copy.close()
    return written // BYTES_PER_MS
