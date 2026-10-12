"""The audio copy a live recording keeps so that it can be listened to again.

The recorder sends 16 kHz mono 16-bit audio. The backend writes a copy of what it hears
next to the recording: ``<id>.listen.pcm`` holds the audio, byte ``offset_ms * 32`` being
that millisecond of the recording, and ``<id>.listen.ranges`` lists the stretches that
were really written (``start_ms end_ms`` per line). A stretch nobody sent stays out of the
ranges, so "silence" and "never heard" can be told apart. Both files go with the
recording; a start-up sweep removes the copies that are left behind.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from sqlalchemy.orm import Session

from gunther.models import RecordingSession
from gunther.qwen_stream import BYTES_PER_MS

logger = logging.getLogger(__name__)

PCM_SUFFIX = ".listen.pcm"
RANGES_SUFFIX = ".listen.ranges"
STALE_DAYS = 7
# Missing stretches shorter than this are rounding between chunks, not lost audio.
MIN_MISSING_MS = 20


def copy_paths(directory: Path, recording_id: str) -> tuple[Path, Path]:
    return directory / f"{recording_id}{PCM_SUFFIX}", directory / f"{recording_id}{RANGES_SUFFIX}"


def is_copy(path: Path) -> bool:
    return path.name.endswith((PCM_SUFFIX, RANGES_SUFFIX))


def merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sorted, with overlapping and touching stretches joined."""

    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


class ListenCopy:
    """Writes one recording's audio copy; many sockets may take turns over a recording."""

    def __init__(self, directory: Path, recording_id: str) -> None:
        self.recording_id = recording_id
        self.pcm_path, self.ranges_path = copy_paths(directory, recording_id)
        self._pcm = None
        self._ranges = None

    def write(self, start_byte: int, pcm: bytes) -> None:
        """Put ``pcm`` at ``start_byte`` (16 kHz bytes on the recording clock).

        Random-access, so audio sent again after a reconnect lands on the same bytes.
        """

        pcm = pcm[: len(pcm) - len(pcm) % 2]
        if not pcm or start_byte < 0:
            return
        try:
            if self._pcm is None:
                self.pcm_path.parent.mkdir(parents=True, exist_ok=True)
                # r+b keeps what an earlier connection of the same recording wrote.
                self._pcm = open(self.pcm_path, "r+b" if self.pcm_path.exists() else "w+b")  # noqa: SIM115
                self._ranges = open(self.ranges_path, "a", encoding="utf-8")  # noqa: SIM115
            self._pcm.seek(start_byte)
            self._pcm.write(pcm)
            self._pcm.flush()
            assert self._ranges is not None
            end_byte = start_byte + len(pcm)
            start_ms = start_byte // BYTES_PER_MS
            end_ms = -(-end_byte // BYTES_PER_MS)
            self._ranges.write(f"{start_ms} {end_ms}\n")
            self._ranges.flush()
        except OSError:
            # The copy is a convenience: recording and live text go on without it.
            logger.warning("The audio copy for %s could not be written", self.recording_id)
            self.close()

    def close(self) -> None:
        for handle in (self._pcm, self._ranges):
            if handle is not None:
                with suppress(OSError):
                    handle.close()
        self._pcm = self._ranges = None


def covered(directory: Path, recording_id: str) -> list[tuple[int, int]]:
    """The stretches of the recording (ms) that the copy holds."""

    _, ranges_path = copy_paths(directory, recording_id)
    found: list[tuple[int, int]] = []
    try:
        lines = ranges_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            found.append((int(parts[0]), int(parts[1])))
        except ValueError:
            continue
    return merge(found)


def read_range(
    directory: Path, recording_id: str, start_ms: int, end_ms: int
) -> tuple[bytes, list[tuple[int, int]]]:
    """Audio for ``[start_ms, end_ms]``, silence where the copy has none.

    Returns the PCM and the stretches of the recording the copy holds (see
    ``missing_in`` and ``touches`` for what that says about this range).
    """

    start_ms = max(0, start_ms)
    length = max(0, end_ms - start_ms) * BYTES_PER_MS
    pcm_path, _ = copy_paths(directory, recording_id)
    held = covered(directory, recording_id)
    out = bytearray(length)
    try:
        with open(pcm_path, "rb") as handle:
            for begin, finish in held:
                begin, finish = max(begin, start_ms), min(finish, end_ms)
                if finish <= begin:
                    continue
                handle.seek(begin * BYTES_PER_MS)
                data = handle.read((finish - begin) * BYTES_PER_MS)
                at = (begin - start_ms) * BYTES_PER_MS
                out[at : at + len(data)] = data
    except OSError:
        held = []
    return bytes(out), held


def touches(held: list[tuple[int, int]], start_ms: int, end_ms: int) -> bool:
    """Whether the copy holds any of ``[start_ms, end_ms]``."""

    return any(begin < end_ms and finish > start_ms for begin, finish in held)


def missing_in(held: list[tuple[int, int]], start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    """The parts of ``[start_ms, end_ms]`` that ``held`` does not cover."""

    gaps: list[tuple[int, int]] = []
    cursor = start_ms
    for begin, finish in held:
        if finish <= cursor:
            continue
        if begin >= end_ms:
            break
        if begin > cursor:
            gaps.append((cursor, begin))
        cursor = max(cursor, finish)
    if cursor < end_ms:
        gaps.append((cursor, end_ms))
    return [(a, b) for a, b in gaps if b - a >= MIN_MISSING_MS]


def remove_copy(directory: Path, recording_id: str) -> None:
    for path in copy_paths(directory, recording_id):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("An audio copy could not be removed", exc_info=True)


def sweep(
    directory: Path,
    *,
    now: float | None = None,
    days: int = STALE_DAYS,
    status_of: Callable[[str], str | None] | None = None,
) -> int:
    """Remove copies older than ``days`` whose recording is finished or gone.

    ``status_of(recording_id)`` says what state the recording is in ("capturing",
    "completed", ...) or None when it no longer exists. Returns how many files went.
    """

    if not directory.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - days * 86_400
    removed = 0
    for path in directory.iterdir():
        if not path.name.endswith(PCM_SUFFIX):
            continue
        recording_id = path.name[: -len(PCM_SUFFIX)]
        try:
            if path.stat().st_mtime > cutoff:
                continue
            state = status_of(recording_id) if status_of else None
            if state == "capturing":
                continue
            remove_copy(directory, recording_id)
            removed += 1
        except OSError:
            logger.warning("An audio copy could not be checked", exc_info=True)
    # A ranges file without audio is useless as well.
    for path in directory.glob(f"*{RANGES_SUFFIX}"):
        recording_id = path.name[: -len(RANGES_SUFFIX)]
        if not copy_paths(directory, recording_id)[0].exists():
            path.unlink(missing_ok=True)
    return removed


def recording_state(session: Session, recording_id: str) -> str | None:
    row = session.get(RecordingSession, recording_id)
    return None if row is None else row.status
