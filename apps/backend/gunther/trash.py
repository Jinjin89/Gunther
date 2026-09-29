"""Trash: a reversible resting place before anything is deleted for good.

Moving a library, source or note to Trash only marks it. It leaves Inbox,
search, Ask and libraries, while its text, claims, index and original file stay
in place, so Restore is exact. Everything one action put in Trash shares a
batch: trashing a library also trashes the sources that live only there (and
the filed notes promoted into them), and restoring any of them restores the
whole batch.

Delete forever is the only destructive path. It removes a batch's rows, lets the
database cascade their derived records, and deletes an original file only when
no remaining source still uses it. Batches older than the retention period are
deleted the same way.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from gunther import papers, vector_index
from gunther.database import session_scope
from gunther.knowledge_index import new_id
from gunther.models import (
    Artifact,
    Asset,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    KnowledgeProposal,
    KnowledgeSession,
    KnowledgeUnit,
    NotebookNote,
    RecordingSession,
    Revision,
    Source,
    TopicNode,
    WebSnapshot,
    utc_now,
)
from gunther.schemas import TrashItemKind, TrashItemOut

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30

# A recording source names its audio in its text; the audio file has no other link.
RECORDING_REFERENCE = re.compile(r"Local recording:\s*(rec_[a-f0-9]{24})")


class TrashConflict(ValueError):
    """The item cannot move to Trash yet, for example while it is still recording."""


def _timestamp(value: datetime) -> str:
    return f"{value.isoformat(timespec='milliseconds')}Z"


def recording_ids(content: str) -> set[str]:
    return set(RECORDING_REFERENCE.findall(content))


def trashed_library_ids():
    """Libraries in Trash, as a subquery for filtering memberships."""

    return select(KnowledgeBaseRecord.id).where(KnowledgeBaseRecord.trashed_at.is_not(None))


def live_membership_source_ids():
    """Sources filed in at least one library that is not in Trash."""

    return select(KnowledgeBaseSource.source_id).where(
        KnowledgeBaseSource.knowledge_base_id.not_in(trashed_library_ids())
    )


class TrashService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        assets_dir: Path,
        recordings_dir: Path,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        self.sessions = sessions
        self.assets_dir = assets_dir
        self.recordings_dir = recordings_dir
        self.retention = timedelta(days=retention_days)

    # Moving into Trash -------------------------------------------------------

    def trash_source(self, source_id: str) -> TrashItemOut:
        with session_scope(self.sessions) as session:
            source = session.get(Source, source_id)
            if source is None:
                raise LookupError(f"Source {source_id} was not found")
            if source.trashed_at is None:
                self._refuse_live_recordings(session, [source])
                batch, now = new_id("trash"), utc_now()
                self._mark(source, batch, now)
                # A filed note is a snapshot of this source; it rests with it.
                for note in session.scalars(
                    select(NotebookNote).where(
                        NotebookNote.promoted_source_id == source.id,
                        NotebookNote.trashed_at.is_(None),
                    )
                ):
                    self._mark(note, batch, now)
                self._record(session, "source", source.id, "trashed")
            return self._root_entry(session, source.trash_batch_id)

    def trash_note(self, note_id: str) -> TrashItemOut:
        with session_scope(self.sessions) as session:
            note = session.get(NotebookNote, note_id)
            if note is None:
                raise LookupError(f"Notebook note {note_id} was not found")
            if note.trashed_at is None:
                self._mark(note, new_id("trash"), utc_now())
                self._record(session, "notebook_note", note.id, "trashed")
            return self._root_entry(session, note.trash_batch_id)

    def trash_library(self, library_id: str) -> TrashItemOut:
        with session_scope(self.sessions) as session:
            library = session.get(KnowledgeBaseRecord, library_id)
            if library is None:
                raise LookupError(f"Knowledge Base {library_id} was not found")
            if library.trashed_at is None:
                if session.scalar(
                    select(RecordingSession.id)
                    .where(
                        RecordingSession.knowledge_base_id == library_id,
                        RecordingSession.status == "capturing",
                    )
                    .limit(1)
                ):
                    raise TrashConflict(
                        "A recording is still being captured into this library. "
                        "Stop it before moving the library to Trash."
                    )
                # Sources also filed in another library that is not in Trash stay there.
                elsewhere = select(KnowledgeBaseSource.source_id).where(
                    KnowledgeBaseSource.knowledge_base_id != library_id,
                    KnowledgeBaseSource.knowledge_base_id.not_in(trashed_library_ids()),
                )
                exclusive = session.scalars(
                    select(Source).where(
                        Source.id.in_(
                            select(KnowledgeBaseSource.source_id).where(
                                KnowledgeBaseSource.knowledge_base_id == library_id
                            )
                        ),
                        Source.id.not_in(elsewhere),
                        Source.trashed_at.is_(None),
                    )
                ).all()
                self._refuse_live_recordings(session, exclusive)
                batch, now = new_id("trash"), utc_now()
                self._mark(library, batch, now)
                for source in exclusive:
                    self._mark(source, batch, now)
                if exclusive:
                    for note in session.scalars(
                        select(NotebookNote).where(
                            NotebookNote.promoted_source_id.in_([s.id for s in exclusive]),
                            NotebookNote.trashed_at.is_(None),
                        )
                    ):
                        self._mark(note, batch, now)
                self._record(session, "knowledge_base", library.id, "trashed")
            return self._root_entry(session, library.trash_batch_id)

    # Reading -----------------------------------------------------------------

    def list(self) -> list[TrashItemOut]:
        with session_scope(self.sessions) as session:
            batches: dict[str, datetime] = {}
            for model in (KnowledgeBaseRecord, Source, NotebookNote):
                for batch, trashed_at in session.execute(
                    select(model.trash_batch_id, model.trashed_at).where(
                        model.trashed_at.is_not(None)
                    )
                ):
                    batches[batch] = trashed_at
            entries = [self._root_entry(session, batch) for batch in batches]
            return sorted(entries, key=lambda entry: entry.trashed_at, reverse=True)

    # Leaving Trash -----------------------------------------------------------

    def restore(self, kind: TrashItemKind, item_id: str) -> TrashItemOut:
        with session_scope(self.sessions) as session:
            batch = self._batch_of(session, kind, item_id)
            entry = self._root_entry(session, batch)
            for model in (KnowledgeBaseRecord, Source, NotebookNote):
                session.execute(
                    update(model)
                    .where(model.trash_batch_id == batch)
                    .values(trashed_at=None, trash_batch_id=None)
                )
            self._record(session, _TARGET_TYPES[entry.kind], entry.id, "restored")
            return entry

    def delete_forever(self, kind: TrashItemKind, item_id: str) -> TrashItemOut:
        with session_scope(self.sessions) as session:
            batch = self._batch_of(session, kind, item_id)
            entry = self._root_entry(session, batch)
            files = self._delete_batch(session, batch)
            self._record(session, _TARGET_TYPES[entry.kind], entry.id, "deleted_forever")
        # Files go only after the rows are committed: a failed commit must never
        # leave a record pointing at a missing original.
        self._remove_files(files)
        return entry

    def empty(self) -> int:
        with session_scope(self.sessions) as session:
            batches = self._batches(session)
        return sum(self._delete_batch_by_id(batch) for batch in batches)

    def purge_expired(self, now: datetime | None = None) -> int:
        """Delete every batch that has rested in Trash longer than the retention period."""

        cutoff = (now or utc_now()) - self.retention
        with session_scope(self.sessions) as session:
            expired = self._batches(session, before=cutoff)
        return sum(self._delete_batch_by_id(batch) for batch in expired)

    # Internals ---------------------------------------------------------------

    @staticmethod
    def _mark(row: KnowledgeBaseRecord | Source | NotebookNote, batch: str, now: datetime) -> None:
        row.trashed_at = now
        row.trash_batch_id = batch

    @staticmethod
    def _record(session: Session, target_type: str, target_id: str, action: str) -> None:
        session.add(
            Revision(
                id=new_id("rev"),
                target_type=target_type,
                target_id=target_id,
                action=action,
                actor="user",
                reason={
                    "trashed": "Moved to Trash",
                    "restored": "Restored from Trash",
                    "deleted_forever": "Deleted from Trash",
                }[action],
            )
        )

    @staticmethod
    def _refuse_live_recordings(session: Session, sources: Iterable[Source]) -> None:
        referenced = {rec for source in sources for rec in recording_ids(source.content)}
        if referenced and session.scalar(
            select(RecordingSession.id)
            .where(RecordingSession.id.in_(referenced), RecordingSession.status == "capturing")
            .limit(1)
        ):
            raise TrashConflict("Stop the recording before moving it to Trash.")

    @staticmethod
    def _batch_of(session: Session, kind: TrashItemKind, item_id: str) -> str:
        model = {"library": KnowledgeBaseRecord, "source": Source, "note": NotebookNote}[kind]
        row = session.get(model, item_id)
        if row is None or row.trash_batch_id is None:
            raise LookupError(f"{kind.capitalize()} {item_id} is not in Trash")
        return row.trash_batch_id

    @staticmethod
    def _batches(session: Session, before: datetime | None = None) -> list[str]:
        batches: set[str] = set()
        for model in (KnowledgeBaseRecord, Source, NotebookNote):
            statement = select(model.trash_batch_id).where(model.trash_batch_id.is_not(None))
            if before is not None:
                statement = statement.where(model.trashed_at < before)
            batches.update(session.scalars(statement))
        return sorted(batches)

    def _root_entry(self, session: Session, batch: str | None) -> TrashItemOut:
        """Describe a batch by the item that was moved: a library, else a source, else a note."""

        library = session.scalar(
            select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.trash_batch_id == batch)
        )
        sources = session.scalars(
            select(Source).where(Source.trash_batch_id == batch).order_by(Source.created_at)
        ).all()
        if library is not None:
            return TrashItemOut(
                kind="library",
                id=library.id,
                title=library.title,
                trashed_at=_timestamp(library.trashed_at),
                expires_at=_timestamp(library.trashed_at + self.retention),
                color=library.color,
                item_count=len(sources),
            )
        if sources:
            source = sources[0]
            return TrashItemOut(
                kind="source",
                id=source.id,
                title=source.title,
                trashed_at=_timestamp(source.trashed_at),
                expires_at=_timestamp(source.trashed_at + self.retention),
                source_kind=source.kind,
                library_titles=list(
                    session.scalars(
                        select(KnowledgeBaseRecord.title)
                        .join(
                            KnowledgeBaseSource,
                            KnowledgeBaseSource.knowledge_base_id == KnowledgeBaseRecord.id,
                        )
                        .where(KnowledgeBaseSource.source_id == source.id)
                        .order_by(KnowledgeBaseSource.created_at)
                    )
                ),
            )
        note = session.scalar(select(NotebookNote).where(NotebookNote.trash_batch_id == batch))
        if note is None:
            raise LookupError("That item is no longer in Trash")
        library_title = (
            session.scalar(
                select(KnowledgeBaseRecord.title).where(
                    KnowledgeBaseRecord.id == note.knowledge_base_id
                )
            )
            if note.knowledge_base_id
            else None
        )
        return TrashItemOut(
            kind="note",
            id=note.id,
            title=note.title,
            trashed_at=_timestamp(note.trashed_at),
            expires_at=_timestamp(note.trashed_at + self.retention),
            source_kind="note",
            library_titles=[library_title] if library_title else [],
        )

    def _delete_batch_by_id(self, batch: str) -> bool:
        """Delete one batch for good; a batch that cannot go yet is left for next time."""

        try:
            with session_scope(self.sessions) as session:
                entry = self._root_entry(session, batch)
                files = self._delete_batch(session, batch)
                self._record(session, _TARGET_TYPES[entry.kind], entry.id, "deleted_forever")
        except (LookupError, TrashConflict):
            return False
        self._remove_files(files)
        return True

    def _delete_batch(self, session: Session, batch: str) -> list[Path]:
        """Delete a batch's rows and return the original files that are now unused."""

        sources = session.scalars(select(Source).where(Source.trash_batch_id == batch)).all()
        self._refuse_live_recordings(session, sources)
        source_ids = [source.id for source in sources]
        asset_ids = {source.asset_id for source in sources if source.asset_id}
        asset_ids.update(
            session.scalars(
                select(WebSnapshot.asset_id).where(WebSnapshot.source_id.in_(source_ids))
            )
        )
        referenced_recordings = {
            recording for source in sources for recording in recording_ids(source.content)
        }

        session.execute(delete(NotebookNote).where(NotebookNote.trash_batch_id == batch))
        for library_id in session.scalars(
            select(KnowledgeBaseRecord.id).where(KnowledgeBaseRecord.trash_batch_id == batch)
        ).all():
            self._delete_library_records(session, library_id)
        if source_ids:
            # The database cascades revisions, blocks, index entries, jobs, claims,
            # evidence, web snapshots and memberships in other libraries.
            session.execute(delete(Source).where(Source.id.in_(source_ids)))
            # Virtual tables sit outside foreign keys, so vectors go explicitly.
            vector_index.forget_sources(session, source_ids)
            papers.drop_orphan_works(session)
        session.flush()

        files: list[Path] = []
        for asset_id in asset_ids:
            still_used = session.scalar(
                select(Source.id).where(Source.asset_id == asset_id).limit(1)
            ) or session.scalar(
                select(WebSnapshot.id).where(WebSnapshot.asset_id == asset_id).limit(1)
            )
            asset = None if still_used else session.get(Asset, asset_id)
            if asset is not None:
                files.append(self.assets_dir / asset.relative_path)
                session.delete(asset)
        for recording_id in referenced_recordings:
            still_used = session.scalar(
                select(Source.id)
                .where(Source.content.contains(f"Local recording: {recording_id}"))
                .limit(1)
            )
            recording = None if still_used else session.get(RecordingSession, recording_id)
            if recording is not None and recording.status != "capturing":
                final_path = self.recordings_dir / recording.file_name
                files += [final_path, final_path.with_name(f"{final_path.name}.part")]
                session.delete(recording)
        return files

    @staticmethod
    def _delete_library_records(session: Session, library_id: str) -> None:
        """Delete everything that belongs to one library, dependants first."""

        # Outputs pin trusted knowledge, and each version restricts the one it
        # supersedes, so remove versions nothing supersedes until none remain.
        while True:
            superseded = select(Artifact.supersedes_artifact_id).where(
                Artifact.supersedes_artifact_id.is_not(None)
            )
            leaves = session.scalars(
                select(Artifact.id).where(
                    Artifact.knowledge_base_id == library_id, Artifact.id.not_in(superseded)
                )
            ).all()
            if not leaves:
                break
            session.execute(delete(Artifact).where(Artifact.id.in_(leaves)))
        # Trusted knowledge (revisions cascade) before the suggestions it came from.
        session.execute(delete(KnowledgeUnit).where(KnowledgeUnit.knowledge_base_id == library_id))
        # Conversations; their messages, branches and suggestions cascade.
        session.execute(
            delete(KnowledgeSession).where(KnowledgeSession.knowledge_base_id == library_id)
        )
        session.execute(
            delete(KnowledgeProposal).where(KnowledgeProposal.knowledge_base_id == library_id)
        )
        # Topic parents restrict deletion, so detach the tree before removing it.
        session.execute(
            update(TopicNode)
            .where(TopicNode.knowledge_base_id == library_id)
            .values(parent_id=None)
        )
        session.execute(delete(TopicNode).where(TopicNode.knowledge_base_id == library_id))
        session.execute(
            delete(KnowledgeBaseSource).where(KnowledgeBaseSource.knowledge_base_id == library_id)
        )
        # Notes and recordings outside the batch keep their content, not the stale library.
        for model in (NotebookNote, RecordingSession):
            session.execute(
                update(model).where(model.knowledge_base_id == library_id).values(
                    knowledge_base_id=None
                )
            )
        session.execute(delete(KnowledgeBaseRecord).where(KnowledgeBaseRecord.id == library_id))

    def _remove_files(self, paths: Iterable[Path]) -> None:
        roots = (self.assets_dir.resolve(), self.recordings_dir.resolve())
        for path in paths:
            try:
                resolved = path.resolve()
                # Never follow a stored path outside Gunther's own directories.
                if path.is_symlink() or not any(resolved.is_relative_to(root) for root in roots):
                    logger.warning("Skipped removing an original outside managed storage")
                    continue
                resolved.unlink(missing_ok=True)
            except OSError:
                logger.warning("An original file could not be removed", exc_info=True)


_TARGET_TYPES: dict[str, str] = {
    "library": "knowledge_base",
    "source": "source",
    "note": "notebook_note",
}
