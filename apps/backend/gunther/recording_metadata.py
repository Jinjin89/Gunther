"""Recording file locations, source references and measured duration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.orm import Session

from gunther.models import ProcessingJob, RecordingSession, Source

TRANSCRIPT_HEADING = re.compile(r"^##[ \t]+(?:Full transcript|Transcript)[ \t]*\r?$", re.MULTILINE)
_REFERENCE = r"Local recording:[ \t]*(?P<id>rec_[a-f0-9]{24})"
_RECORDING_HEADER = re.compile(
    rf"^(?P<label>Duration:[ \t]*[\d:]+[ \t]*·[ \t]*Captured:[ \t]*[^\r\n]+?)"
    rf"(?:[ \t]*·[ \t]*{_REFERENCE})?[ \t]*(?P<cr>\r?)$",
    re.MULTILINE,
)
_STANDALONE_REFERENCE = re.compile(rf"^{_REFERENCE}[ \t]*\r?$", re.MULTILINE)
_DURATION = re.compile(r"^Duration:[ \t]*(?:(\d+(?::\d+){1,2})|(\d+)[ \t]*seconds?)", re.MULTILINE)


def recording_file_paths(root: Path, file_name: str) -> tuple[Path, Path]:
    final_path = root / file_name
    return final_path, final_path.parent / f"{final_path.name}.part"


@dataclass(frozen=True)
class RecordingReference:
    id: str
    at_end: bool = False


def source_recording_reference(content: str) -> RecordingReference | None:
    """Resolve the saved audio and whether its reference is a transcript footer."""
    if "Local recording:" not in content:
        return None
    heading = TRANSCRIPT_HEADING.search(content)
    head_end = heading.start() if heading else len(content)
    if header := _RECORDING_HEADER.search(content, 0, head_end):
        return RecordingReference(header["id"]) if header["id"] else None
    if heading and (legacy := _STANDALONE_REFERENCE.search(content, 0, head_end)):
        return RecordingReference(legacy["id"])
    if footer := _STANDALONE_REFERENCE.fullmatch(content.rstrip().rpartition("\n")[2]):
        return RecordingReference(footer["id"], at_end=True)
    return None


def source_recording_id(content: str) -> str | None:
    reference = source_recording_reference(content)
    return reference.id if reference is not None else None


def with_recording_header(head: str, recording_id: str, duration: str, captured: str) -> str:
    """Write the current audio header into the portion before the transcript."""
    for header in _RECORDING_HEADER.finditer(head):
        if header["id"] == recording_id:
            return head
    reference = f" · Local recording: {recording_id}"
    if header := _RECORDING_HEADER.search(head):
        line = f"{header['label']}{reference}{header['cr']}"
        return head[:header.start()] + line + head[header.end():]
    line = f"Duration: {duration} · Captured: {captured}{reference}"
    if legacy := _STANDALONE_REFERENCE.search(head):
        ending = "\r" if legacy[0].endswith("\r") else ""
        return head[:legacy.start()] + line + ending + head[legacy.end():]
    return f"{head.rstrip()}\n\n{line}\n\n" if head.strip() else f"{line}\n\n"


def _duration_estimate(content: str) -> int | None:
    heading = TRANSCRIPT_HEADING.search(content)
    found = _DURATION.search(content, 0, heading.start() if heading else len(content))
    if not found:
        return None
    if found[2]:
        return int(found[2])
    total = 0
    for part in found[1].split(":"):
        total = total * 60 + int(part)
    return total


def recording_measurement_eligible() -> ColumnElement[bool]:
    """Eligibility of a source and its resolved recording for duration measurement."""
    return and_(
        Source.kind.in_(("recording", "course")),
        Source.trashed_at.is_(None),
        RecordingSession.status == "completed",
        RecordingSession.audio_duration_ms.is_(None),
    )


def recording_timing(
    session: Session, source_id: str, content: str, reference: RecordingReference | None
) -> tuple[int | None, bool]:
    if reference is None:
        return _duration_estimate(content), False
    row = session.execute(
        select(RecordingSession, recording_measurement_eligible(), ProcessingJob.state)
        .select_from(Source)
        .join(RecordingSession, RecordingSession.id == reference.id)
        .outerjoin(
            ProcessingJob, ProcessingJob.dedupe_key == f"measure_recording:{reference.id}:v1"
        )
        .where(Source.id == source_id)
    ).one_or_none()
    pending = False
    if row is not None:
        recording, eligible, state = row
        pending = eligible and state in {None, "queued", "running"}
        if recording.status == "completed":
            duration = recording.effective_duration_seconds
            if recording.audio_duration_ms is not None or duration > 0:
                return duration, pending
    # An unset timer can fall back to the source's header. A footer follows raw transcript.
    estimate = None if reference.at_end else _duration_estimate(content)
    return estimate, pending
