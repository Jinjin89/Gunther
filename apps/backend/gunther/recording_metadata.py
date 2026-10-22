"""References to saved audio and its measured duration, separate from captured text."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from gunther.models import ProcessingJob, RecordingSession

RECORDING_REFERENCE = re.compile(r"Local recording:\s*(rec_[a-f0-9]{24})")


def recording_ids(content: str) -> set[str]:
    return set(RECORDING_REFERENCE.findall(content))


def recording_timing(session: Session, content: str) -> tuple[int | None, bool]:
    # Several references are ambiguous; do not pretend they identify one audio file.
    ids = recording_ids(content[:2_000])
    if len(ids) != 1:
        return None, False
    recording = session.get(RecordingSession, next(iter(ids)))
    if recording is None or recording.status != "completed":
        return None, False
    pending = False
    if recording.audio_duration_ms is None:
        state = session.scalar(select(ProcessingJob.state).where(
            ProcessingJob.dedupe_key == f"measure_recording:{recording.id}:v1"
        ))
        pending = state is None or state in {"queued", "running"}
    duration = recording.effective_duration_seconds
    # A legacy session's unset estimate (0) must not hide a duration kept in its source.
    return (duration if recording.audio_duration_ms is not None or duration > 0 else None), pending
