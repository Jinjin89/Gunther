"""Trash: a reversible resting place before anything is deleted for good.

Moving a library, source, note or output to Trash only marks it. It leaves Inbox,
search, Ask and Studio, while its stored data and original files stay
in place, so Restore is exact. Everything one action put in Trash shares a
batch: trashing a library also trashes the sources that live only there (and
the filed notes promoted into them), and restoring any of them restores the
whole batch.

Delete forever is the only destructive path. It removes a batch's rows, lets the
database cascade their derived records, and deletes an original file after
its last reference is removed. Batches older than the retention period are
deleted the same way.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, case, delete, func, or_, select, tuple_, update
from sqlalchemy.orm import Session, sessionmaker

from gunther import artifact_workspace, key_results, listen_copy, papers, vector_index
from gunther.activity import Activity
from gunther.answer_runs import AnswerRuns, output_key
from gunther.artifact_read import artifact_owner
from gunther.artifact_versions import output_library
from gunther.database import session_scope
from gunther.knowledge_index import new_id
from gunther.models import (
    Artifact,
    ArtifactAsset,
    ArtifactConversation,
    ArtifactMessage,
    Asset,
    DocumentElement,
    ElementAsset,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    KnowledgeProposal,
    KnowledgeSession,
    KnowledgeUnit,
    NotebookNote,
    ProcessingAsset,
    ProcessingJob,
    RecordingSession,
    Revision,
    Source,
    SourceRevision,
    TopicNode,
    WebSnapshot,
    utc_now,
)
from gunther.output_types import output_type_label
from gunther.recording_metadata import recording_file_paths, source_recording_id
from gunther.schemas import TrashItemKind, TrashItemOut

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30
_TRASH_MODELS = (KnowledgeBaseRecord, Source, NotebookNote, Artifact, ArtifactConversation)

class TrashConflict(ValueError):
    """The item cannot move to Trash yet, for example while it is still recording."""


def _timestamp(value: datetime) -> str:
    return f"{value.isoformat(timespec='milliseconds')}Z"


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
        runs: AnswerRuns,
        activity: Activity,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        self.sessions = sessions
        self.assets_dir = assets_dir
        self.recordings_dir = recordings_dir
        self.runs = runs
        self.activity = activity
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
        with self.runs.locked_active() as runs, session_scope(self.sessions) as session:
            library = session.get(KnowledgeBaseRecord, library_id)
            if library is None:
                raise LookupError(f"Knowledge Base {library_id} was not found")
            if library.trashed_at is None:
                if any(run.scope and run.scope[1] == library_id for run in runs):
                    raise TrashConflict("Stop the output run before moving the library to Trash.")
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
            entry = self._root_entry(session, library.trash_batch_id)
        self._dismiss_output_activity(library_id)
        return entry

    def trash_output(
        self, workspace_id: str, library_id: str, item_id: str, *, conversation: bool = False,
    ) -> TrashItemOut:
        """Move one lineage or a conversation's owned lineages as one batch."""

        with self.runs.locked_active() as runs:
            with session_scope(self.sessions) as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                output_library(session, workspace_id, library_id)
                model = ArtifactConversation if conversation else Artifact
                row = session.get(model, item_id)
                if row is None or (row.workspace_id, row.knowledge_base_id) != (
                    workspace_id, library_id,
                ):
                    raise LookupError("This output was not found in the library.")
                if row.trashed_at is not None:
                    return self._root_entry(session, row.trash_batch_id)

                scope = (
                    Artifact.workspace_id == workspace_id,
                    Artifact.knowledge_base_id == library_id,
                )
                lineages = (
                    self._owned_lineages(workspace_id, library_id, item_id)
                    if conversation else [row.lineage_id]
                )
                versions = session.execute(
                    select(Artifact.id, Artifact.lineage_id, artifact_owner()).where(
                        *scope, Artifact.lineage_id.in_(lineages), Artifact.trashed_at.is_(None),
                    )
                ).all()
                version_ids = {id_ for id_, _, _ in versions}
                lineage_ids = {lineage for _, lineage, _ in versions}
                owners = {owner for _, _, owner in versions if owner}
                owners.update(session.scalars(
                    select(ArtifactMessage.conversation_id)
                    .where(ArtifactMessage.artifact_id.in_(version_ids))
                    .distinct()
                ))
                if conversation:
                    owners.add(item_id)
                keys = {output_key(owner) for owner in owners}
                keys.update(output_key(None, lineage) for lineage in lineage_ids)
                if any(
                    run.scope == (workspace_id, library_id)
                    and (run.session_id in keys or run.lineage_id in lineage_ids)
                    for run in runs
                ):
                    raise TrashConflict("Stop the output run before moving it to Trash.")

                batch, now = new_id("trash"), utc_now()
                if conversation:
                    self._mark(row, batch, now)
                session.execute(
                    update(Artifact)
                    .where(*scope, Artifact.lineage_id.in_(lineages), Artifact.trashed_at.is_(None))
                    .values(trashed_at=now, trash_batch_id=batch)
                )
                self._record(session, "output", batch, "trashed")
                entry = self._root_entry(session, batch)

            self._dismiss_output_activity(
                library_id, version_ids, lineage_ids, item_id if conversation else None,
            )
            return entry

    def _dismiss_output_activity(
        self, library_id: str, version_ids: set[str] | None = None,
        lineage_ids: set[str] | None = None, conversation_id: str | None = None,
    ) -> None:
        for item in self.activity.snapshot()["items"]:
            target = item["open"] or {}
            if item["kind"] == "output" and target.get("libraryId") == library_id and (
                version_ids is None
                or target.get("artifactId") in version_ids
                or (lineage_ids and target.get("lineageId") in lineage_ids)
                or (conversation_id and target.get("conversationId") == conversation_id)
            ):
                self.activity.dismiss(item["id"])

    @staticmethod
    def _owned_lineages(workspace_id: str, library_id: str, conversation_id: str):
        return select(Artifact.lineage_id).where(
            Artifact.workspace_id == workspace_id,
            Artifact.knowledge_base_id == library_id,
            artifact_owner() == conversation_id,
        )

    # Reading -----------------------------------------------------------------

    def list(self) -> list[TrashItemOut]:
        with session_scope(self.sessions) as session:
            entries = [self._root_entry(session, batch) for batch in self._batches(session)]
            return sorted(entries, key=lambda entry: entry.trashed_at, reverse=True)

    # Leaving Trash -----------------------------------------------------------

    def restore(self, kind: TrashItemKind, item_id: str) -> TrashItemOut:
        with session_scope(self.sessions) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            batch = self._batch_of(session, kind, item_id)
            entry = self._root_entry(session, batch)
            if kind == "output" and session.scalar(
                select(ArtifactConversation.id)
                .join(Artifact, artifact_owner() == ArtifactConversation.id)
                .where(
                    Artifact.trash_batch_id == batch,
                    ArtifactConversation.workspace_id == Artifact.workspace_id,
                    ArtifactConversation.knowledge_base_id == Artifact.knowledge_base_id,
                    ArtifactConversation.trash_batch_id != batch,
                    ArtifactConversation.trashed_at.is_not(None),
                )
                .limit(1)
            ):
                raise TrashConflict("Restore the output's conversation first.")
            for model in _TRASH_MODELS:
                session.execute(
                    update(model)
                    .where(model.trash_batch_id == batch)
                    .values(trashed_at=None, trash_batch_id=None)
                )
            self._record(session, _TARGET_TYPES[entry.kind], entry.id, "restored")
            return entry

    def delete_forever(self, kind: TrashItemKind, item_id: str) -> TrashItemOut:
        with session_scope(self.sessions) as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            batch = self._batch_of(session, kind, item_id)
            entry = self._root_entry(session, batch)
            files = self._delete_batch(session, batch)
            self._record(session, _TARGET_TYPES[entry.kind], entry.id, "deleted_forever")
        # Files go only after the rows are committed: a failed commit must never
        # leave a record pointing at a missing original.
        self._remove_files(*files)
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
    def _mark(
        row: KnowledgeBaseRecord | Source | NotebookNote | ArtifactConversation,
        batch: str,
        now: datetime,
    ) -> None:
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
        referenced = {rec for source in sources if (rec := source_recording_id(source.content))}
        if referenced and session.scalar(
            select(RecordingSession.id)
            .where(RecordingSession.id.in_(referenced), RecordingSession.status == "capturing")
            .limit(1)
        ):
            raise TrashConflict("Stop the recording before moving it to Trash.")

    @staticmethod
    def _batch_of(session: Session, kind: TrashItemKind, item_id: str) -> str:
        if kind == "output":
            # Output and conversation batches use the same kind and are addressed by batch ID.
            select_batch = select(Artifact.trash_batch_id).where(Artifact.trash_batch_id == item_id)
            if session.scalar(select_batch.limit(1)) or session.scalar(
                select(ArtifactConversation.trash_batch_id)
                .where(ArtifactConversation.trash_batch_id == item_id)
                .limit(1)
            ):
                return item_id
            raise LookupError("That output is no longer in Trash")
        model = {"library": KnowledgeBaseRecord, "source": Source, "note": NotebookNote}[kind]
        row = session.get(model, item_id)
        if row is None or row.trash_batch_id is None:
            raise LookupError(f"{kind.capitalize()} {item_id} is not in Trash")
        return row.trash_batch_id

    @staticmethod
    def _batches(session: Session, before: datetime | None = None) -> list[str]:
        batches: set[str] = set()
        for model in _TRASH_MODELS:
            statement = select(model.trash_batch_id).where(model.trash_batch_id.is_not(None))
            if before is not None:
                statement = statement.where(model.trashed_at < before)
            batches.update(session.scalars(statement))
        return sorted(batches)

    def _root_entry(self, session: Session, batch: str | None) -> TrashItemOut:
        """Describe a batch by the item that was moved."""

        if batch is None:
            raise LookupError("That item is no longer in Trash")

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
            return self._output_entry(session, batch)
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

    def _output_entry(self, session: Session, batch: str) -> TrashItemOut:
        versions = select(Artifact).where(Artifact.trash_batch_id == batch)
        conversation = session.scalar(
            select(ArtifactConversation).where(ArtifactConversation.trash_batch_id == batch)
        )
        if conversation is not None:
            title, trashed_at = conversation.title, conversation.trashed_at
            label = "Output conversation"
        else:
            head = session.execute(
                versions.with_only_columns(
                    Artifact.title, Artifact.trashed_at, Artifact.kind, Artifact.style,
                ).order_by(Artifact.version_number.desc()).limit(1)
            ).one_or_none()
            if head is None:
                raise LookupError("That item is no longer in Trash")
            title, trashed_at = head.title, head.trashed_at
            label = output_type_label(head.kind, head.style)
        return TrashItemOut(
            kind="output", id=batch, title=title, output_type=label,
            version_count=session.scalar(select(func.count()).select_from(versions.subquery())),
            trashed_at=_timestamp(trashed_at), expires_at=_timestamp(trashed_at + self.retention),
        )

    def _delete_batch_by_id(self, batch: str) -> bool:
        """Delete one batch for good; a batch that cannot go yet is left for next time."""

        try:
            with session_scope(self.sessions) as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                entry = self._root_entry(session, batch)
                files = self._delete_batch(session, batch)
                self._record(session, _TARGET_TYPES[entry.kind], entry.id, "deleted_forever")
        except (LookupError, TrashConflict):
            return False
        self._remove_files(*files)
        return True

    def _delete_batch(
        self, session: Session, batch: str,
    ) -> tuple[list[Path], list[tuple[str, str]]]:
        """Delete a batch's rows and return the original files that are now unused."""

        sources = session.scalars(select(Source).where(Source.trash_batch_id == batch)).all()
        self._refuse_live_recordings(session, sources)
        source_ids = [source.id for source in sources]
        library_ids = list(
            session.scalars(
                select(KnowledgeBaseRecord.id).where(KnowledgeBaseRecord.trash_batch_id == batch)
            )
        )
        # The final deletion of a conversation includes any of its outputs that were
        # moved to Trash separately. They would otherwise recover the conversation.
        owned = session.scalars(
            select(Artifact.lineage_id)
            .join(ArtifactConversation, artifact_owner() == ArtifactConversation.id)
            .where(
                ArtifactConversation.trash_batch_id == batch,
                Artifact.workspace_id == ArtifactConversation.workspace_id,
                Artifact.knowledge_base_id == ArtifactConversation.knowledge_base_id,
            )
            .distinct()
        ).all()
        versions = session.execute(
            select(
                Artifact.id, Artifact.lineage_id, Artifact.workspace_id, Artifact.knowledge_base_id,
            )
            .where(or_(
                Artifact.trash_batch_id == batch,
                Artifact.knowledge_base_id.in_(library_ids),
                Artifact.lineage_id.in_(owned),
            ))
        ).all()
        version_ids = [row.id for row in versions]
        asset_ids = {source.asset_id for source in sources if source.asset_id}
        asset_ids.update(
            session.scalars(
                select(ProcessingAsset.asset_id)
                .join(ProcessingJob)
                .where(ProcessingJob.source_id.in_(source_ids))
            )
        )
        asset_ids.update(
            session.scalars(
                select(ArtifactAsset.asset_id).where(ArtifactAsset.artifact_id.in_(version_ids))
            )
        )
        asset_ids.update(
            session.scalars(
                select(ElementAsset.asset_id)
                .join(DocumentElement)
                .join(SourceRevision)
                .where(SourceRevision.source_id.in_(source_ids))
            )
        )
        asset_ids.update(
            session.scalars(
                select(WebSnapshot.asset_id).where(WebSnapshot.source_id.in_(source_ids))
            )
        )
        referenced_recordings = {
            recording for source in sources if (recording := source_recording_id(source.content))
        }

        session.execute(delete(NotebookNote).where(NotebookNote.trash_batch_id == batch))
        if version_ids:
            self._delete_versions(session, version_ids)
            self._delete_output_messages(session, version_ids)
            lineages_by_library: dict[tuple[str, str], set[str]] = defaultdict(set)
            for version in versions:
                if version.knowledge_base_id not in library_ids:
                    lineages_by_library[version.workspace_id, version.knowledge_base_id].add(
                        version.lineage_id,
                    )
            for (workspace_id, library_id), lineages in lineages_by_library.items():
                for conversation in session.scalars(select(ArtifactConversation).where(
                    ArtifactConversation.workspace_id == workspace_id,
                    ArtifactConversation.knowledge_base_id == library_id,
                )):
                    workspace = artifact_workspace.load_workspace(conversation.state_json)
                    if artifact_workspace.forget_works(workspace, lineages):
                        conversation.state_json = artifact_workspace.dump_workspace(workspace)
        session.execute(
            delete(ArtifactConversation).where(or_(
                ArtifactConversation.trash_batch_id == batch,
                ArtifactConversation.knowledge_base_id.in_(library_ids),
            ))
        )
        key_results.purge(session, library_ids, source_ids)
        for library_id in library_ids:
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
        asset_files: list[tuple[str, str]] = []
        for asset_id in asset_ids:
            still_used = (
                session.scalar(select(Source.id).where(Source.asset_id == asset_id).limit(1))
                or session.scalar(
                    select(WebSnapshot.id).where(WebSnapshot.asset_id == asset_id).limit(1)
                )
                or session.scalar(
                    select(ElementAsset.element_id)
                    .where(ElementAsset.asset_id == asset_id)
                    .limit(1)
                )
                or session.scalar(
                    select(ArtifactAsset.artifact_id)
                    .where(ArtifactAsset.asset_id == asset_id)
                    .limit(1)
                )
                or session.scalar(
                    select(ProcessingAsset.job_id)
                    .where(ProcessingAsset.asset_id == asset_id)
                    .limit(1)
                )
            )
            asset = None if still_used else session.get(Asset, asset_id)
            if asset is not None:
                asset_files.append((asset.content_hash, asset.relative_path))
                session.delete(asset)
        for recording_id in referenced_recordings:
            # Retain audio when surviving text may still refer to it, including old formats.
            still_used = session.scalar(
                select(Source.id)
                .where(Source.content.contains(recording_id, autoescape=True))
                .limit(1)
            )
            recording = None if still_used else session.get(RecordingSession, recording_id)
            if recording is not None and recording.status != "capturing":
                files.extend(recording_file_paths(self.recordings_dir, recording.file_name))
                # The audio copy kept for listening again goes with the recording.
                files += list(listen_copy.copy_paths(self.recordings_dir, recording.id))
                session.delete(recording)
        return files, asset_files

    @staticmethod
    def _delete_versions(session: Session, version_ids: list[str]) -> None:
        """Remove leaves first. Checks, unit bindings and asset links cascade."""

        remaining = set(version_ids)
        superseded = select(Artifact.supersedes_artifact_id).where(
            Artifact.supersedes_artifact_id.is_not(None)
        )
        while remaining:
            leaves = session.scalars(
                select(Artifact.id).where(
                    Artifact.id.in_(remaining), Artifact.id.not_in(superseded),
                )
            ).all()
            if not leaves:
                raise TrashConflict("Another output version still depends on this batch.")
            session.execute(delete(Artifact).where(Artifact.id.in_(leaves)))
            remaining.difference_update(leaves)

    @staticmethod
    def _delete_output_messages(session: Session, version_ids: list[str]) -> None:
        reply_payload = case(
            (func.json_valid(ArtifactMessage.payload_json), ArtifactMessage.payload_json),
            else_="{}",
        )
        requests = select(
            ArtifactMessage.conversation_id, func.json_extract(reply_payload, "$.to"),
        ).where(ArtifactMessage.artifact_id.in_(version_ids), ArtifactMessage.role == "assistant")
        session.execute(delete(ArtifactMessage).where(or_(
            ArtifactMessage.artifact_id.in_(version_ids),
            and_(
                ArtifactMessage.role == "user",
                tuple_(ArtifactMessage.conversation_id, ArtifactMessage.id).in_(requests),
            ),
        )))

    @staticmethod
    def _delete_library_records(session: Session, library_id: str) -> None:
        """Delete the library and its remaining dependants after its outputs."""

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

    def _remove_files(self, paths: Iterable[Path], assets: Iterable[tuple[str, str]]) -> None:
        from gunther.asset_service import _asset_lock

        roots = (self.assets_dir.resolve(), self.recordings_dir.resolve())

        def unlink(path: Path) -> None:
            try:
                resolved = path.resolve()
                # Never follow a stored path outside Gunther's own directories.
                if path.is_symlink() or not any(resolved.is_relative_to(root) for root in roots):
                    logger.warning("Skipped removing an original outside managed storage")
                    return
                resolved.unlink(missing_ok=True)
            except OSError:
                logger.warning("An original file could not be removed", exc_info=True)

        for path in paths:
            unlink(path)
        for content_hash, relative_path in assets:
            # A fresh copy may have been captured in the gap after the purge committed.
            with _asset_lock(content_hash):
                with self.sessions() as session:
                    used = session.scalar(
                        select(Asset.id).where(Asset.relative_path == relative_path)
                    )
                if used is None:
                    unlink(self.assets_dir / relative_path)


_TARGET_TYPES: dict[str, str] = {
    "library": "knowledge_base",
    "source": "source",
    "note": "notebook_note",
    "output": "output",
}
