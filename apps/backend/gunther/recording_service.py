from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gunther.database import session_scope
from gunther.models import RecordingChunk, RecordingSession, utc_now
from gunther.schemas import (
    RecordingCheckpointInput,
    RecordingMoment,
    RecordingRecoveryOut,
    RecordingSessionOut,
)
from gunther.storage_budget import StorageBudget, StorageReservation

MAX_CHUNK_BYTES = 16 * 1024 * 1024
MAX_RECORDING_BYTES = 2 * 1024 * 1024 * 1024
MAX_DIRECT_UPLOAD_BYTES = 200 * 1024 * 1024


class RecordingError(Exception):
    pass


class RecordingNotFoundError(RecordingError):
    pass


class RecordingConflictError(RecordingError):
    pass


class RecordingValidationError(RecordingError):
    pass


class RecordingTooLargeError(RecordingError):
    pass


_lock_guard = threading.Lock()
_recording_locks: dict[str, threading.Lock] = {}
_integrity_cache_guard = threading.Lock()
_recording_integrity_cache: dict[
    str, tuple[int, int, int, int, int, str]
] = {}


def _recording_lock(recording_id: str) -> threading.Lock:
    with _lock_guard:
        return _recording_locks.setdefault(recording_id, threading.Lock())


def _timestamp(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def recording_extension(media_type: str) -> str:
    normalized = media_type.lower().split(";", 1)[0].strip()
    return {
        "audio/aac": "aac",
        "audio/flac": "flac",
        "audio/x-flac": "flac",
        "audio/m4a": "m4a",
        "audio/mp4": "m4a",
        "audio/x-m4a": "m4a",
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/webm": "webm",
    }.get(normalized, "webm")


def recording_media_type(path: Path) -> str:
    return {
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
        ".webm": "audio/webm",
    }.get(path.suffix.lower(), "audio/webm")


class RecordingService:
    """Persist recording state and bytes as one recoverable upload protocol."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        recordings_dir: Path,
        storage_budget: StorageBudget | None = None,
    ) -> None:
        self.sessions = sessions
        self.recordings_dir = recordings_dir
        self.storage_budget = storage_budget or StorageBudget((recordings_dir,))

    def _paths(self, recording: RecordingSession) -> tuple[Path, Path]:
        if Path(recording.file_name).name != recording.file_name:
            raise RecordingConflictError("The recording path is invalid.")
        final_path = self.recordings_dir / recording.file_name
        part_path = final_path.with_name(f"{final_path.name}.part")
        self.storage_budget.assert_safe_path(final_path)
        self.storage_budget.assert_safe_path(part_path)
        return final_path, part_path

    def _out(self, recording: RecordingSession) -> RecordingSessionOut:
        try:
            raw_moments = json.loads(recording.moments_json)
            moments = [RecordingMoment.model_validate(moment) for moment in raw_moments]
        except (TypeError, ValueError):
            moments = []
        checkpointed_at = (
            _timestamp(recording.checkpointed_at)
            if recording.checkpointed_at is not None
            else None
        )
        return RecordingSessionOut(
            id=recording.id,
            title=recording.title,
            status=recording.status,  # type: ignore[arg-type]
            file_name=recording.file_name,
            content_type=recording.content_type,
            size_bytes=recording.byte_size,
            next_expected_sequence=recording.next_sequence,
            transcript=recording.transcript,
            duration_seconds=recording.duration_seconds,
            moments=moments,
            recording_context=recording.recording_context,  # type: ignore[arg-type]
            knowledge_base_id=recording.knowledge_base_id,
            checkpoint_revision=recording.checkpoint_revision,
            checkpointed_at=checkpointed_at,
            recovery=RecordingRecoveryOut(
                can_resume=recording.status == "capturing",
                audio_available=recording.byte_size > 0,
                next_expected_sequence=recording.next_sequence,
                checkpoint_revision=recording.checkpoint_revision,
                checkpointed_at=checkpointed_at,
            ),
            created_at=_timestamp(recording.created_at),
            updated_at=_timestamp(recording.updated_at),
            completed_at=(
                _timestamp(recording.completed_at) if recording.completed_at is not None else None
            ),
            stored_at=_timestamp(recording.completed_at or recording.updated_at),
        )

    def _get(self, session: Session, recording_id: str) -> RecordingSession:
        if not re.fullmatch(r"rec_[a-f0-9]{24}", recording_id):
            raise RecordingNotFoundError("Recording session not found")
        recording = session.get(RecordingSession, recording_id)
        if recording is None:
            raise RecordingNotFoundError("Recording session not found")
        return recording

    def _recover_files(self, recording: RecordingSession) -> Path | None:
        """Reconcile an interrupted rename/write with the last committed DB state."""
        final_path, part_path = self._paths(recording)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

        if recording.status == "capturing":
            if not part_path.exists() and final_path.exists():
                # Completion renamed the file but its DB transaction did not commit.
                os.replace(final_path, part_path)
            if not part_path.exists() and recording.byte_size == 0:
                part_path.touch(exist_ok=False)
            if not part_path.is_file():
                recording.status = "failed"
                recording.updated_at = utc_now()
                return None
            actual_size = part_path.stat().st_size
            if actual_size > recording.byte_size:
                # Bytes were flushed but their chunk ledger transaction was interrupted.
                with part_path.open("r+b") as recording_file:
                    recording_file.truncate(recording.byte_size)
                    recording_file.flush()
                    os.fsync(recording_file.fileno())
            elif actual_size < recording.byte_size:
                recording.status = "failed"
                recording.updated_at = utc_now()
                return None
            return part_path

        if recording.status == "completed":
            if not final_path.exists() and part_path.exists():
                os.replace(part_path, final_path)
            if not final_path.is_file() or final_path.stat().st_size != recording.byte_size:
                recording.status = "failed"
                recording.updated_at = utc_now()
                return None
            return final_path

        return final_path if final_path.is_file() else part_path if part_path.is_file() else None

    @staticmethod
    def _ledger_fingerprint(recording: RecordingSession) -> str:
        digest = hashlib.sha256()
        digest.update(f"{recording.byte_size}:{recording.next_sequence}\n".encode())
        for chunk in recording.chunks:
            digest.update(
                f"{chunk.sequence}:{chunk.size_bytes}:{chunk.checksum}\n".encode()
            )
        return digest.hexdigest()

    def _verify_chunk_ledger(
        self, recording: RecordingSession, path: Path, *, force_full: bool = False
    ) -> bool:
        """Verify every committed byte, caching only an unchanged filesystem identity.

        Downloads force an unconditional scan. Metadata/list calls use the cache,
        so an
        already verified recording avoids another multi-gigabyte scan while inode,
        size, mtime, ctime, and the database ledger are unchanged. A same-size write
        changes ctime on supported desktop filesystems and therefore invalidates the
        cache; backup verification independently performs an unconditional scan.
        """

        chunks = list(recording.chunks)
        if (
            recording.byte_size < 0
            or recording.next_sequence < 0
            or len(chunks) != recording.next_sequence
        ):
            return False
        if any(
            chunk.sequence != expected
            or chunk.size_bytes <= 0
            or re.fullmatch(r"[a-f0-9]{64}", chunk.checksum) is None
            for expected, chunk in enumerate(chunks)
        ):
            return False
        if sum(chunk.size_bytes for chunk in chunks) != recording.byte_size:
            return False

        try:
            file_status = path.stat()
        except OSError:
            return False
        ledger_fingerprint = self._ledger_fingerprint(recording)
        cache_key = (
            file_status.st_dev,
            file_status.st_ino,
            file_status.st_size,
            file_status.st_mtime_ns,
            file_status.st_ctime_ns,
            ledger_fingerprint,
        )
        with _integrity_cache_guard:
            if not force_full and _recording_integrity_cache.get(recording.id) == cache_key:
                return True
        if file_status.st_size != recording.byte_size:
            return False

        try:
            with path.open("rb") as source:
                for chunk in chunks:
                    remaining = chunk.size_bytes
                    digest = hashlib.sha256()
                    while remaining:
                        block = source.read(min(1024 * 1024, remaining))
                        if not block:
                            return False
                        digest.update(block)
                        remaining -= len(block)
                    if not secrets.compare_digest(digest.hexdigest(), chunk.checksum):
                        return False
                if source.read(1):
                    return False
        except OSError:
            return False

        try:
            verified_status = path.stat()
        except OSError:
            return False
        verified_key = (
            verified_status.st_dev,
            verified_status.st_ino,
            verified_status.st_size,
            verified_status.st_mtime_ns,
            verified_status.st_ctime_ns,
            ledger_fingerprint,
        )
        if verified_key != cache_key:
            return False
        with _integrity_cache_guard:
            _recording_integrity_cache[recording.id] = verified_key
        return True

    def _recover_and_verify(
        self,
        recording: RecordingSession,
        *,
        mark_failed: bool = True,
        force_full: bool = False,
    ) -> Path | None:
        path = self._recover_files(recording)
        if path is None or recording.status == "failed":
            return None
        if self._verify_chunk_ledger(recording, path, force_full=force_full):
            return path
        if mark_failed:
            recording.status = "failed"
            recording.updated_at = utc_now()
        with _integrity_cache_guard:
            _recording_integrity_cache.pop(recording.id, None)
        return None

    def start(self, title: str, content_type: str) -> RecordingSessionOut:
        media_type = content_type.lower().split(";", 1)[0].strip() or "audio/webm"
        recording_id = f"rec_{uuid4().hex[:24]}"
        safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-")[:80] or "recording"
        file_name = f"{recording_id}.{safe_title}.{recording_extension(media_type)}"
        part_path = (self.recordings_dir / file_name).with_name(f"{file_name}.part")
        with self.storage_budget.reserve(self.recordings_dir):
            self.recordings_dir.mkdir(parents=True, exist_ok=True)
            self.storage_budget.assert_safe_path(part_path)
            part_path.touch(exist_ok=False)
            try:
                with session_scope(self.sessions) as session:
                    now = utc_now()
                    recording = RecordingSession(
                        id=recording_id,
                        title=title,
                        status="capturing",
                        content_type=media_type,
                        file_name=file_name,
                        byte_size=0,
                        next_sequence=0,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(recording)
                    session.flush()
                    result = self._out(recording)
                return result
            except Exception:
                part_path.unlink(missing_ok=True)
                raise

    def save(
        self,
        title: str,
        content_type: str,
        data: bytes,
        *,
        reservation: StorageReservation | None = None,
    ) -> RecordingSessionOut:
        if not data:
            raise RecordingValidationError("The recording is empty.")
        if len(data) > MAX_DIRECT_UPLOAD_BYTES:
            raise RecordingTooLargeError("Recordings are limited to 200 MB.")

        if reservation is None:
            with self.storage_budget.reserve(
                self.recordings_dir,
                expected_bytes=len(data),
            ) as owned_reservation:
                return self._save_reserved(title, content_type, data, owned_reservation)
        return self._save_reserved(title, content_type, data, reservation)

    def _save_reserved(
        self,
        title: str,
        content_type: str,
        data: bytes,
        reservation: StorageReservation,
    ) -> RecordingSessionOut:

        media_type = content_type.lower().split(";", 1)[0].strip() or "audio/webm"
        recording_id = f"rec_{uuid4().hex[:24]}"
        safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-")[:80] or "lecture"
        file_name = f"{recording_id}.{safe_title}.{recording_extension(media_type)}"
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        final_path = self.recordings_dir / file_name
        part_path = final_path.with_name(f"{file_name}.part")
        self.storage_budget.assert_safe_path(final_path)
        self.storage_budget.assert_safe_path(part_path)
        checksum = hashlib.sha256(data).hexdigest()
        try:
            reservation.prepare_write(len(data))
            with part_path.open("xb") as recording_file:
                recording_file.write(data)
                recording_file.flush()
                os.fsync(recording_file.fileno())
            reservation.consume(len(data))
            os.replace(part_path, final_path)
            with session_scope(self.sessions) as session:
                now = utc_now()
                recording = RecordingSession(
                    id=recording_id,
                    title=title,
                    status="completed",
                    content_type=media_type,
                    file_name=file_name,
                    byte_size=len(data),
                    next_sequence=1,
                    created_at=now,
                    updated_at=now,
                    completed_at=now,
                )
                recording.chunks.append(
                    RecordingChunk(
                        id=f"rch_{uuid4().hex}",
                        recording_id=recording_id,
                        sequence=0,
                        checksum=checksum,
                        size_bytes=len(data),
                    )
                )
                session.add(recording)
                session.flush()
                result = self._out(recording)
            return result
        except Exception:
            part_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            raise

    def list(self) -> list[RecordingSessionOut]:
        with session_scope(self.sessions) as session:
            recordings = list(
                session.scalars(
                    select(RecordingSession).order_by(RecordingSession.updated_at.desc())
                )
            )
            for recording in recordings:
                with _recording_lock(recording.id):
                    self._recover_and_verify(recording)
            return [self._out(recording) for recording in recordings]

    def metadata(self, recording_id: str) -> RecordingSessionOut:
        with _recording_lock(recording_id), session_scope(self.sessions) as session:
            recording = self._get(session, recording_id)
            self._recover_and_verify(recording)
            return self._out(recording)

    def checkpoint(
        self,
        recording_id: str,
        payload: RecordingCheckpointInput,
    ) -> RecordingSessionOut:
        moments = [moment.model_dump(mode="json") for moment in payload.moments]
        checkpoint_document = {
            "transcript": payload.transcript,
            "durationSeconds": payload.duration_seconds,
            "moments": moments,
            "recordingContext": payload.recording_context,
            "knowledgeBaseId": payload.knowledge_base_id,
        }
        serialized = json.dumps(
            checkpoint_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        checkpoint_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

        with _recording_lock(recording_id), session_scope(self.sessions) as session:
            recording = self._get(session, recording_id)
            self._recover_files(recording)
            if recording.status == "failed":
                raise RecordingConflictError("A failed recording cannot be checkpointed.")

            if payload.expected_revision != recording.checkpoint_revision:
                if (
                    payload.expected_revision == recording.checkpoint_revision - 1
                    and recording.checkpoint_hash == checkpoint_hash
                ):
                    return self._out(recording)
                raise RecordingConflictError(
                    "Stale checkpoint; refresh recording metadata before retrying "
                    f"revision {recording.checkpoint_revision}."
                )
            if recording.checkpoint_hash == checkpoint_hash:
                return self._out(recording)

            now = utc_now()
            recording.transcript = payload.transcript
            recording.duration_seconds = payload.duration_seconds
            recording.moments_json = json.dumps(
                moments,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            recording.recording_context = payload.recording_context
            recording.knowledge_base_id = payload.knowledge_base_id
            recording.checkpoint_revision += 1
            recording.checkpoint_hash = checkpoint_hash
            recording.checkpointed_at = now
            recording.updated_at = now
            session.flush()
            return self._out(recording)

    def append(
        self,
        recording_id: str,
        data: bytes,
        *,
        sequence: int | None,
        checksum: str | None,
    ) -> RecordingSessionOut:
        if not data:
            raise RecordingValidationError("The recording chunk is empty.")
        if len(data) > MAX_CHUNK_BYTES:
            raise RecordingTooLargeError("Recording chunks are limited to 16 MB.")

        digest = hashlib.sha256(data).hexdigest()
        if sequence is not None:
            if checksum is None:
                raise RecordingValidationError(
                    "X-Chunk-SHA256 is required when sequence is provided."
                )
            normalized_checksum = checksum.strip().lower()
            if not re.fullmatch(r"[a-f0-9]{64}", normalized_checksum):
                raise RecordingValidationError("X-Chunk-SHA256 must be a SHA-256 hex digest.")
            if normalized_checksum != digest:
                raise RecordingValidationError("The chunk checksum does not match its bytes.")

        lock = _recording_lock(recording_id)
        previous_size: int | None = None
        appended_path: Path | None = None
        with lock:
            try:
                with session_scope(self.sessions) as session:
                    recording = self._get(session, recording_id)
                    active_path = self._recover_files(recording)
                    if recording.status != "capturing" or active_path is None:
                        raise RecordingConflictError("The recording is no longer accepting chunks.")

                    requested_sequence = (
                        recording.next_sequence if sequence is None else sequence
                    )
                    if requested_sequence < recording.next_sequence:
                        existing = session.scalar(
                            select(RecordingChunk).where(
                                RecordingChunk.recording_id == recording.id,
                                RecordingChunk.sequence == requested_sequence,
                            )
                        )
                        if (
                            existing is not None
                            and existing.checksum == digest
                            and existing.size_bytes == len(data)
                        ):
                            return self._out(recording)
                        raise RecordingConflictError(
                            "That sequence was already committed with different content."
                        )
                    if requested_sequence > recording.next_sequence:
                        raise RecordingConflictError(
                            f"Out-of-order chunk; expected sequence {recording.next_sequence}."
                        )
                    if recording.byte_size + len(data) > MAX_RECORDING_BYTES:
                        raise RecordingTooLargeError("Recordings are limited to 2 GB.")

                    with self.storage_budget.reserve(
                        self.recordings_dir,
                        expected_bytes=len(data),
                    ) as reservation:
                        previous_size = recording.byte_size
                        appended_path = active_path
                        reservation.prepare_write(len(data))
                        with active_path.open("ab") as recording_file:
                            recording_file.write(data)
                            recording_file.flush()
                            os.fsync(recording_file.fileno())
                        reservation.consume(len(data))

                        recording.chunks.append(
                            RecordingChunk(
                                id=f"rch_{uuid4().hex}",
                                recording_id=recording.id,
                                sequence=requested_sequence,
                                checksum=digest,
                                size_bytes=len(data),
                            )
                        )
                        recording.byte_size += len(data)
                        recording.next_sequence += 1
                        recording.updated_at = utc_now()
                        session.flush()
                        result = self._out(recording)
                return result
            except Exception:
                if (
                    previous_size is not None
                    and appended_path is not None
                    and appended_path.exists()
                ):
                    with appended_path.open("r+b") as recording_file:
                        recording_file.truncate(previous_size)
                        recording_file.flush()
                        os.fsync(recording_file.fileno())
                raise

    def complete(self, recording_id: str) -> RecordingSessionOut:
        lock = _recording_lock(recording_id)
        final_path: Path | None = None
        part_path: Path | None = None
        renamed = False
        integrity_failed = False
        result: RecordingSessionOut | None = None
        with lock:
            try:
                with session_scope(self.sessions) as session:
                    recording = self._get(session, recording_id)
                    self._recover_files(recording)
                    if recording.status == "completed":
                        return self._out(recording)
                    if recording.status != "capturing":
                        raise RecordingConflictError("The recording cannot be completed.")
                    if recording.byte_size == 0:
                        raise RecordingValidationError("The recording is empty.")

                    final_path, part_path = self._paths(recording)
                    if not part_path.is_file():
                        raise RecordingConflictError("The recording bytes are unavailable.")
                    if not self._verify_chunk_ledger(
                        recording, part_path, force_full=True
                    ):
                        recording.status = "failed"
                        recording.updated_at = utc_now()
                        integrity_failed = True
                    else:
                        os.replace(part_path, final_path)
                        renamed = True
                        now = utc_now()
                        recording.status = "completed"
                        recording.updated_at = now
                        recording.completed_at = now
                        result = self._out(recording)
                    session.flush()
                if integrity_failed:
                    raise RecordingConflictError(
                        "The recording failed its chunk integrity check."
                    )
                if result is None:
                    raise RecordingConflictError("The recording could not be completed.")
                return result
            except Exception:
                if (
                    renamed
                    and final_path is not None
                    and part_path is not None
                    and final_path.exists()
                    and not part_path.exists()
                ):
                    os.replace(final_path, part_path)
                raise

    def download(self, recording_id: str) -> tuple[Path, str, str]:
        result: tuple[Path, str, str] | None = None
        with _recording_lock(recording_id), session_scope(self.sessions) as session:
            recording = self._get(session, recording_id)
            path = self._recover_and_verify(recording, force_full=True)
            if path is not None and recording.status != "failed":
                result = path, recording.content_type, recording.file_name
        if result is None:
            raise RecordingNotFoundError("Recording bytes not found")
        return result
