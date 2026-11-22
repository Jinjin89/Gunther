"""Register, analyze and file sources, notebook notes and assertions."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Literal

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from gunther.database import session_scope
from gunther.extraction import (
    CandidateAssertion,
    ExtractionPayload,
    analysis_job_key,
    normalize_predicate,
)
from gunther.knowledge_index import enqueue, enqueue_asset_parse
from gunther.llm import AnalysisNeedsModel
from gunther.models import (
    Assertion,
    Asset,
    Entity,
    EvidenceLink,
    Fragment,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    NotebookNote,
    ProcessingJob,
    Revision,
    Source,
    SourceIndexHead,
    WebSnapshot,
    utc_now,
)
from gunther.recording_metadata import recording_timing, source_recording_reference
from gunther.schemas import (
    AssertionOut,
    AssetOut,
    CreateNotebookNoteInput,
    CreateSourceInput,
    EntityRefOut,
    EvidenceOut,
    FileNotebookNoteInput,
    FileNotebookNoteOut,
    FileSourceInput,
    FileSourceOut,
    ImportCountsOut,
    ImportResultOut,
    InboxKnowledgeBaseRefOut,
    NotebookNoteOut,
    SourceAnalysisOut,
    SourceDetailOut,
    SourceSummaryOut,
    UpdateAssertionStatusInput,
    UpdateNotebookNoteInput,
    WebSnapshotOut,
)
from gunther.service_records import HOME_SCOPE, _id, _timestamp
from gunther.source_identity import source_fingerprint


def _normalized_label(value: str) -> str:
    return " ".join(value.casefold().split())


def _assertion_key(
    subject: str, predicate: str, object_: str, quote: str,
) -> tuple[str, str, str, str]:
    return (
        _normalized_label(subject),
        normalize_predicate(predicate),
        _normalized_label(object_),
        quote,
    )


class SourceService:
    @property
    def extraction_mode(self) -> Literal["local", "model"]:
        return "model" if self.extractor is not None else "local"

    def _source_summary(self, session: Session, source: Source) -> SourceSummaryOut:
        assertions = session.scalars(
            select(Assertion).where(Assertion.source_id == source.id)
        ).all()
        entity_ids = {
            entity_id
            for assertion in assertions
            for entity_id in (assertion.subject_entity_id, assertion.object_entity_id)
        }
        asset = session.get(Asset, source.asset_id) if source.asset_id else None
        reference = source_recording_reference(source.content)
        duration, duration_pending = (
            recording_timing(session, source.id, source.content, reference)
            if reference or source.kind in {"recording", "course"} else (None, False)
        )
        return SourceSummaryOut(
            id=source.id,
            title=source.title,
            kind=source.kind,
            created_at=_timestamp(source.created_at),
            assertion_count=len(assertions),
            entity_count=len(entity_ids),
            processing=self.index.processing(session, source.id),
            recording_id=reference.id if reference else None,
            recording_reference_at_end=reference.at_end if reference else False,
            recording_duration_seconds=duration,
            recording_duration_pending=duration_pending,
            asset=(
                AssetOut(
                    id=asset.id,
                    content_hash=asset.content_hash,
                    original_name=asset.original_name,
                    media_type=asset.media_type,
                    size_bytes=asset.size_bytes,
                    download_url=f"/api/assets/{asset.id}",
                    created_at=_timestamp(asset.created_at),
                )
                if asset
                else None
            ),
        )

    @staticmethod
    def _notebook_note_out(note: NotebookNote) -> NotebookNoteOut:
        return NotebookNoteOut(
            id=note.id,
            title=note.title,
            content=note.content,
            status=note.status,
            pinned=note.pinned,
            knowledge_base_id=note.knowledge_base_id,
            promoted_source_id=note.promoted_source_id,
            created_at=_timestamp(note.created_at),
            updated_at=_timestamp(note.updated_at),
            trashed_at=_timestamp(note.trashed_at) if note.trashed_at else None,
        )

    def _ensure_source_memberships(
        self, session: Session, knowledge_base_id: str, source_ids: list[str]
    ) -> None:
        home = knowledge_base_id == HOME_SCOPE
        if not home and self._live_knowledge_base(session, knowledge_base_id) is None:
            raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
        if not source_ids:
            return
        valid_source_ids = set(
            session.scalars(
                select(Source.id).where(Source.id.in_(source_ids), Source.trashed_at.is_(None))
            ).all()
        )
        missing_source_ids = set(source_ids) - valid_source_ids
        if missing_source_ids:
            missing = ", ".join(sorted(missing_source_ids))
            raise ValueError(f"Unknown source IDs: {missing}")
        existing_source_ids = set(
            session.scalars(
                select(KnowledgeBaseSource.source_id).where(
                    KnowledgeBaseSource.knowledge_base_id == knowledge_base_id,
                    KnowledgeBaseSource.source_id.in_(valid_source_ids),
                )
            ).all()
        )
        for source_id in valid_source_ids - existing_source_ids:
            session.add(
                KnowledgeBaseSource(
                    id=_id("kbs"),
                    knowledge_base_id=knowledge_base_id,
                    source_id=source_id,
                )
            )

    def _validate_source_scope(
        self, session: Session, knowledge_base_id: str, source_ids: list[str]
    ) -> None:
        home = knowledge_base_id == HOME_SCOPE
        if not home and self._live_knowledge_base(session, knowledge_base_id) is None:
            raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
        if not source_ids:
            return
        valid_source_ids = set(
            session.scalars(
                select(Source.id).where(Source.id.in_(source_ids), Source.trashed_at.is_(None))
            ).all()
        )
        missing_source_ids = set(source_ids) - valid_source_ids
        if missing_source_ids:
            missing = ", ".join(sorted(missing_source_ids))
            raise ValueError(f"Unknown source IDs: {missing}")
        if home:
            return
        member_source_ids = set(
            session.scalars(
                select(KnowledgeBaseSource.source_id).where(
                    KnowledgeBaseSource.knowledge_base_id == knowledge_base_id,
                    KnowledgeBaseSource.source_id.in_(valid_source_ids),
                )
            ).all()
        )
        outside_source_ids = valid_source_ids - member_source_ids
        if outside_source_ids:
            outside = ", ".join(sorted(outside_source_ids))
            raise ValueError(
                f"Source IDs are not indexed in Knowledge Base {knowledge_base_id}: {outside}"
            )

    def _assertion_query(self):
        return select(Assertion).options(
            selectinload(Assertion.subject),
            selectinload(Assertion.object),
            selectinload(Assertion.source),
            selectinload(Assertion.evidence_links).selectinload(EvidenceLink.fragment),
        )

    def _assertion_out(self, assertion: Assertion) -> AssertionOut:
        return AssertionOut(
            id=assertion.id,
            predicate=assertion.predicate,
            confidence=assertion.confidence,
            status=assertion.status,
            qualifiers=json.loads(assertion.qualifiers_json),
            subject=EntityRefOut(
                id=assertion.subject.id,
                label=assertion.subject.label,
                type=assertion.subject.type,
            ),
            object=EntityRefOut(
                id=assertion.object.id,
                label=assertion.object.label,
                type=assertion.object.type,
            ),
            source={
                "id": assertion.source.id,
                "title": assertion.source.title,
                "kind": assertion.source.kind,
            },
            evidence=[
                EvidenceOut(
                    id=link.id,
                    stance=link.stance,
                    quote=link.fragment.content,
                    locator=link.fragment.locator,
                )
                for link in assertion.evidence_links
            ],
            created_at=_timestamp(assertion.created_at),
        )

    def _find_or_create_entity(
        self, session: Session, label: str, entity_type: str
    ) -> Entity:
        normalized = _normalized_label(label)
        entity = session.scalar(
            select(Entity).where(
                Entity.normalized_label == normalized,
                Entity.type == entity_type,
            )
        )
        if entity:
            return entity

        entity = Entity(
            id=_id("ent"),
            label=label.strip(),
            normalized_label=normalized,
            type=entity_type.strip() or "Concept",
            aliases_json="[]",
        )
        session.add(entity)
        session.flush()
        return entity

    @staticmethod
    def _locator(content: str, quote: str) -> str:
        lines = content.splitlines()
        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            if quote in line or (stripped and stripped in quote):
                region_locator: str | None = None
                for preceding in reversed(lines[:index]):
                    region = re.fullmatch(
                        r"<!-- gunther:ocr-region=(\d+),(\d+),(\d+),(\d+) "
                        r"unit=ppm provider=([a-z0-9._-]+)(?: confidence=[0-9.]+)? -->",
                        preceding.strip(),
                    )
                    if region and region_locator is None:
                        region_locator = (
                            f"region {region.group(1)},{region.group(2)},"
                            f"{region.group(3)},{region.group(4)} ppm"
                        )
                    page = re.fullmatch(r"<!-- gunther:page=(\d+) -->", preceding.strip())
                    if page:
                        detail = f" · {region_locator}" if region_locator else ""
                        return f"page {page.group(1)}{detail} · line {index}"
                return f"line {index}"
        return "source text"

    @contextmanager
    def _serialize_source_import(self, content_hash: str) -> Iterator[None]:
        """Serialize identical imports while allowing unrelated sources in parallel."""
        with self._source_import_locks_guard:
            source_lock, users = self._source_import_locks.get(content_hash, (Lock(), 0))
            self._source_import_locks[content_hash] = (source_lock, users + 1)

        try:
            with source_lock:
                yield
        finally:
            with self._source_import_locks_guard:
                active_lock, users = self._source_import_locks[content_hash]
                if users == 1:
                    del self._source_import_locks[content_hash]
                else:
                    self._source_import_locks[content_hash] = (active_lock, users - 1)

    def _duplicate_import_result(
        self,
        session: Session,
        source: Source,
        knowledge_base_id: str | None,
    ) -> ImportResultOut:
        if source.trashed_at is not None:
            # Capturing something again means it is wanted: bring it back from Trash.
            source.trashed_at = None
            source.trash_batch_id = None
            session.add(
                Revision(
                    id=_id("rev"),
                    target_type="source",
                    target_id=source.id,
                    action="restored",
                    actor="user",
                    reason="Captured again",
                )
            )
            session.flush()
        if knowledge_base_id:
            self._ensure_source_memberships(session, knowledge_base_id, [source.id])
        return ImportResultOut(
            source=self._source_summary(session, source),
            created=ImportCountsOut(entities=0, assertions=0),
            extraction_mode=self.extraction_mode,
            duplicate=True,
        )

    def import_source(
        self,
        payload: CreateSourceInput,
        *,
        initialize_source: Callable[[Session, Source], None] | None = None,
        fingerprint: str | None = None,
        find_existing_capture: Callable[[Session], Source | None] | None = None,
        asset_id: str | None = None,
    ) -> ImportResultOut:
        content = payload.content.strip()
        content_hash = fingerprint or source_fingerprint(payload.title, payload.kind, content)

        with self._serialize_source_import(content_hash):
            return self._import_source(
                payload,
                content,
                content_hash,
                initialize_source=initialize_source,
                find_existing_capture=find_existing_capture,
                asset_id=asset_id,
            )

    def _import_source(
        self,
        payload: CreateSourceInput,
        content: str,
        content_hash: str,
        *,
        initialize_source: Callable[[Session, Source], None] | None = None,
        find_existing_capture: Callable[[Session], Source | None] | None = None,
        asset_id: str | None = None,
    ) -> ImportResultOut:

        with session_scope(self.sessions) as session:
            if (
                payload.knowledge_base_id
                and self._live_knowledge_base(session, payload.knowledge_base_id) is None
            ):
                raise LookupError(f"Knowledge Base {payload.knowledge_base_id} was not found")
            existing = session.scalar(select(Source).where(Source.content_hash == content_hash))
            if existing is None and find_existing_capture is not None:
                existing = find_existing_capture(session)
            if existing:
                return self._duplicate_import_result(
                    session,
                    existing,
                    payload.knowledge_base_id,
                )

        try:
            with session_scope(self.sessions) as session:
                source = Source(
                    id=_id("src"),
                    title=payload.title.strip(),
                    kind=payload.kind,
                    content=content,
                    content_hash=content_hash,
                    asset_id=asset_id,
                )
                session.add(source)
                session.flush()
                if initialize_source is not None:
                    initialize_source(session, source)
                    session.flush()
                if payload.knowledge_base_id:
                    self._ensure_source_memberships(session, payload.knowledge_base_id, [source.id])

                if asset_id is not None:
                    enqueue_asset_parse(session, source.id)
                else:
                    if session.get(SourceIndexHead, source.id) is None:
                        self.index.add_source(session, source)
                    if content:
                        # Commit the readable index and analysis job with the source.
                        job = enqueue(
                            session, source.id, "extract_assertions",
                            analysis_job_key(source.id, source.content_hash),
                        )
                        if self.extractor is None:
                            job.state, job.error = "needs_model", str(AnalysisNeedsModel())
                session.flush()
                return ImportResultOut(
                    source=self._source_summary(session, source),
                    created=ImportCountsOut(entities=0, assertions=0),
                    extraction_mode=self.extraction_mode,
                    duplicate=False,
                )
        except IntegrityError:
            # Another process or service instance may have inserted the same hash after
            # our initial lookup. The database remains the final idempotency boundary.
            with session_scope(self.sessions) as session:
                existing = session.scalar(select(Source).where(Source.content_hash == content_hash))
                if existing is None:
                    raise
                return self._duplicate_import_result(
                    session,
                    existing,
                    payload.knowledge_base_id,
                )

    def store_source_extraction(
        self, session: Session, source: Source, extraction: ExtractionPayload
    ) -> None:
        """Publish assertions and their exact evidence inside the caller's transaction."""

        fragments: dict[str, Fragment] = {}
        for candidate in extraction.assertions:
            subject = self._find_or_create_entity(
                session, candidate.subject_label, candidate.subject_type
            )
            object_entity = self._find_or_create_entity(
                session, candidate.object_label, candidate.object_type
            )
            fragment = fragments.get(candidate.evidence_quote)
            if fragment is None:
                fragment = Fragment(
                    id=_id("frg"), source_id=source.id,
                    locator=self._locator(source.content, candidate.evidence_quote),
                    content=candidate.evidence_quote,
                )
                fragments[candidate.evidence_quote] = fragment
                session.add(fragment)
            assertion = self._create_assertion(source, subject, object_entity, candidate)
            session.add(assertion)
            session.flush()
            session.add(
                EvidenceLink(
                    id=_id("evd"), assertion_id=assertion.id,
                    fragment_id=fragment.id, stance="supports",
                )
            )
            session.add(
                Revision(
                    id=_id("rev"), target_type="assertion", target_id=assertion.id,
                    action="created",
                    actor="extractor:model",
                    reason="Imported from source evidence",
                )
            )

    def replace_source_analysis(
        self, session: Session, source: Source, extraction: ExtractionPayload,
    ) -> None:
        """Replace stale model drafts while retaining reviewed and matching claims."""
        from_analysis = select(Revision.id).where(
            Revision.target_id == Assertion.id,
            Revision.target_type == "assertion",
            Revision.action == "created",
            Revision.actor == "extractor:model",
        ).exists()
        unreviewed = and_(from_analysis, Assertion.status == "provisional").label("unreviewed")
        previous = session.execute(self._assertion_query().add_columns(unreviewed).where(
            Assertion.source_id == source.id,
        ).order_by(unreviewed, Assertion.created_at, Assertion.id)).all()
        available = {
            _assertion_key(claim.subject_label, claim.predicate, claim.object_label,
                           claim.evidence_quote): claim
            for claim in extraction.assertions
        }
        replaced = []
        for assertion, can_replace in previous:
            matching = {
                _assertion_key(assertion.subject.label, assertion.predicate, assertion.object.label,
                               link.fragment.content)
                for link in assertion.evidence_links
            } & available.keys()
            for key in matching:
                del available[key]
            if can_replace and not matching:
                replaced.append(assertion)
        if replaced:
            fragments = {link.fragment_id for row in replaced for link in row.evidence_links}
            session.execute(delete(Assertion).where(Assertion.id.in_([row.id for row in replaced])))
            session.execute(delete(Fragment).where(
                Fragment.id.in_(fragments), ~Fragment.evidence_links.any(),
            ))

        self.store_source_extraction(
            session, source, extraction.model_copy(update={"assertions": list(available.values())}),
        )

    @staticmethod
    def _create_assertion(
        source: Source,
        subject: Entity,
        object_entity: Entity,
        candidate: CandidateAssertion,
    ) -> Assertion:
        qualifiers = {item.key: item.value for item in candidate.qualifiers if item.key}
        return Assertion(
            id=_id("ast"),
            subject_entity_id=subject.id,
            predicate=candidate.predicate,
            object_entity_id=object_entity.id,
            confidence=candidate.confidence,
            status="provisional",
            qualifiers_json=json.dumps(qualifiers, sort_keys=True),
            source_id=source.id,
        )

    def list_sources(self) -> list[SourceSummaryOut]:
        with session_scope(self.sessions) as session:
            sources = session.scalars(
                select(Source).where(Source.trashed_at.is_(None)).order_by(Source.created_at.desc())
            ).all()
            return [self._source_summary(session, source) for source in sources]

    def file_source(self, source_id: str, payload: FileSourceInput) -> FileSourceOut:
        with session_scope(self.sessions) as session:
            source = session.get(Source, source_id)
            if source is None or source.trashed_at is not None:
                raise LookupError(f"Source {source_id} was not found")
            knowledge_base = self._live_knowledge_base(session, payload.knowledge_base_id)
            if knowledge_base is None:
                raise LookupError(f"Knowledge Base {payload.knowledge_base_id} was not found")
            membership = session.scalar(
                select(KnowledgeBaseSource).where(
                    KnowledgeBaseSource.knowledge_base_id == payload.knowledge_base_id,
                    KnowledgeBaseSource.source_id == source_id,
                )
            )
            membership_created = membership is None
            if membership_created:
                session.add(
                    KnowledgeBaseSource(
                        id=_id("kbs"),
                        knowledge_base_id=payload.knowledge_base_id,
                        source_id=source_id,
                    )
                )
                session.add(
                    Revision(
                        id=_id("rev"),
                        target_type="source",
                        target_id=source_id,
                        action="filed_to_knowledge",
                        actor="user",
                        reason=f"Filed into {payload.knowledge_base_id}",
                    )
                )
            session.flush()
            return FileSourceOut(
                source=self._source_summary(session, source),
                knowledge_base=InboxKnowledgeBaseRefOut(
                    id=knowledge_base.id,
                    title=knowledge_base.title,
                ),
                membership_created=membership_created,
            )

    def list_notebook_notes(
        self,
        status: str | None = None,
        query: str | None = None,
    ) -> list[NotebookNoteOut]:
        with session_scope(self.sessions) as session:
            statement = select(NotebookNote).where(NotebookNote.trashed_at.is_(None))
            if status:
                statement = statement.where(NotebookNote.status == status)
            if query and query.strip():
                pattern = f"%{query.strip()}%"
                statement = statement.where(
                    or_(NotebookNote.title.ilike(pattern), NotebookNote.content.ilike(pattern))
                )
            notes = session.scalars(
                statement.order_by(NotebookNote.pinned.desc(), NotebookNote.updated_at.desc())
            ).all()
            return [self._notebook_note_out(note) for note in notes]

    def create_notebook_note(self, payload: CreateNotebookNoteInput) -> NotebookNoteOut:
        try:
            with session_scope(self.sessions) as session:
                if payload.client_capture_id:
                    existing = session.scalar(
                        select(NotebookNote).where(
                            NotebookNote.client_capture_id == payload.client_capture_id
                        )
                    )
                    if existing is not None:
                        return self._notebook_note_out(existing)
                note = NotebookNote(
                    id=_id("nte"),
                    title=payload.title.strip() or "Untitled note",
                    content=payload.content,
                    pinned=payload.pinned,
                    client_capture_id=payload.client_capture_id,
                )
                session.add(note)
                session.flush()
                return self._notebook_note_out(note)
        except IntegrityError:
            if not payload.client_capture_id:
                raise
            # The unique client capture ID is the final cross-process boundary
            # when the first success response is lost and an outbox retries.
            with session_scope(self.sessions) as session:
                existing = session.scalar(
                    select(NotebookNote).where(
                        NotebookNote.client_capture_id == payload.client_capture_id
                    )
                )
                if existing is None:
                    raise
                return self._notebook_note_out(existing)

    def update_notebook_note(
        self,
        note_id: str,
        payload: UpdateNotebookNoteInput,
    ) -> NotebookNoteOut:
        with session_scope(self.sessions) as session:
            note = session.get(NotebookNote, note_id)
            if note is None or note.trashed_at is not None:
                raise LookupError(f"Notebook note {note_id} was not found")
            content_changed = payload.content is not None or payload.title is not None
            if content_changed and note.promoted_source_id:
                raise ValueError(
                    "Filed notes are preserved as snapshots. Create a new note for further changes."
                )
            if payload.title is not None:
                note.title = payload.title.strip() or "Untitled note"
            if payload.content is not None:
                note.content = payload.content
            if payload.pinned is not None:
                note.pinned = payload.pinned
            if payload.status is not None:
                if payload.status == "filed" and note.promoted_source_id is None:
                    raise ValueError("Use the file action to add a note to a Knowledge Base")
                if payload.status == "inbox" and note.promoted_source_id is not None:
                    raise ValueError("A filed note cannot return to the unfiled inbox")
                note.status = payload.status
            note.updated_at = utc_now()
            session.flush()
            return self._notebook_note_out(note)

    def file_notebook_note(
        self,
        note_id: str,
        payload: FileNotebookNoteInput,
    ) -> FileNotebookNoteOut:
        with session_scope(self.sessions) as session:
            note = session.get(NotebookNote, note_id)
            if note is None or note.trashed_at is not None:
                raise LookupError(f"Notebook note {note_id} was not found")
            if self._live_knowledge_base(session, payload.knowledge_base_id) is None:
                raise LookupError(f"Knowledge Base {payload.knowledge_base_id} was not found")
            if note.promoted_source_id:
                raise ValueError("This note has already been filed into knowledge")
            content = note.content.strip()
            if len(content) < 3:
                raise ValueError("Add a little more detail before filing this note")
            title = note.title.strip()
            if title.casefold() == "untitled note":
                title = next((line.strip() for line in content.splitlines() if line.strip()), title)
                title = title[:160]

        imported = self.import_source(
            CreateSourceInput(
                title=title,
                kind="note",
                content=content,
                knowledge_base_id=payload.knowledge_base_id,
            )
        )

        with session_scope(self.sessions) as session:
            note = session.get(NotebookNote, note_id)
            if note is None:
                raise LookupError(f"Notebook note {note_id} was not found")
            note.title = title
            note.status = "filed"
            note.knowledge_base_id = payload.knowledge_base_id
            note.promoted_source_id = imported.source.id
            note.updated_at = utc_now()
            session.add(
                Revision(
                    id=_id("rev"),
                    target_type="notebook_note",
                    target_id=note.id,
                    action="filed_to_knowledge",
                    actor="user",
                    reason=f"Filed into {payload.knowledge_base_id} as {imported.source.id}",
                )
            )
            session.flush()
            return FileNotebookNoteOut(
                note=self._notebook_note_out(note),
                import_result=imported,
            )

    def rename_source(self, source_id: str, title: str) -> SourceSummaryOut:
        """A name the user chose; later model or paper titles leave it alone."""

        with session_scope(self.sessions) as session:
            source = session.get(Source, source_id)
            if source is None or source.trashed_at is not None:
                raise LookupError(f"Source {source_id} was not found")
            title = title.strip()[:160]
            if title != source.title:
                source.title = title
                session.add(
                    Revision(
                        id=_id("rev"),
                        target_type="source",
                        target_id=source.id,
                        action="renamed",
                        actor="user",
                        reason=None,
                    )
                )
                session.flush()
            return self._source_summary(session, source)

    @staticmethod
    def _source_analysis_job(session: Session, source: Source) -> ProcessingJob | None:
        return session.scalar(select(ProcessingJob).where(
            ProcessingJob.dedupe_key == analysis_job_key(source.id, source.content_hash)
        ))

    @staticmethod
    def _source_analysis_out(job: ProcessingJob) -> SourceAnalysisOut:
        return SourceAnalysisOut(job_id=job.id, state=job.state, error=job.error)

    def retry_source_analysis(self, source_id: str) -> SourceAnalysisOut:
        with session_scope(self.sessions) as session:
            source = session.get(Source, source_id)
            if source is None or source.trashed_at is not None:
                raise LookupError("Source was not found")
            if (job := self._source_analysis_job(session, source)) is None:
                raise LookupError("Source analysis was not found")
            now = utc_now()
            session.execute(update(ProcessingJob).where(
                ProcessingJob.id == job.id,
                ProcessingJob.state.in_(("failed", "needs_model", "cancelled")),
            ).values(
                state="queued", attempts=0, error=None, lease_token=None, lease_until=None,
                available_at=now, updated_at=now,
            ))
            session.refresh(job)
            return self._source_analysis_out(job)

    def get_source(self, source_id: str) -> SourceDetailOut:
        with session_scope(self.sessions) as session:
            source = session.get(Source, source_id)
            if source is None:
                raise LookupError(f"Source {source_id} was not found")
            summary = self._source_summary(session, source)
            assertions = session.scalars(
                self._assertion_query().where(Assertion.source_id == source_id)
            ).all()
            snapshot = session.scalar(select(WebSnapshot).where(WebSnapshot.source_id == source_id))
            analysis = self._source_analysis_job(session, source)
            knowledge_bases = session.execute(
                select(KnowledgeBaseRecord.id, KnowledgeBaseRecord.title)
                .join(
                    KnowledgeBaseSource,
                    KnowledgeBaseSource.knowledge_base_id == KnowledgeBaseRecord.id,
                )
                .where(
                    KnowledgeBaseSource.source_id == source_id,
                    KnowledgeBaseRecord.trashed_at.is_(None),
                )
                .order_by(KnowledgeBaseSource.created_at.asc())
            ).all()
            return SourceDetailOut(
                **summary.model_dump(),
                content=source.content,
                analysis=self._source_analysis_out(analysis) if analysis else None,
                trashed_at=_timestamp(source.trashed_at) if source.trashed_at else None,
                knowledge_bases=[
                    InboxKnowledgeBaseRefOut(id=base_id, title=title)
                    for base_id, title in knowledge_bases
                ],
                assertions=[self._assertion_out(assertion) for assertion in assertions],
                web_snapshot=(
                    WebSnapshotOut(
                        original_url=snapshot.original_url,
                        final_url=snapshot.final_url,
                        captured_at=_timestamp(snapshot.captured_at),
                        status=snapshot.status,
                        content_type=snapshot.content_type,
                        content_hash=snapshot.content_hash,
                        asset_id=snapshot.asset_id,
                    )
                    if snapshot
                    else None
                ),
            )

    def list_assertions(self, status: str | None = None, limit: int = 100) -> list[AssertionOut]:
        with session_scope(self.sessions) as session:
            query = self._live_assertion_query().order_by(Assertion.created_at.desc()).limit(limit)
            if status:
                query = query.where(Assertion.status == status)
            assertions = session.scalars(query).all()
            return [self._assertion_out(assertion) for assertion in assertions]

    def update_assertion_status(
        self, assertion_id: str, payload: UpdateAssertionStatusInput
    ) -> AssertionOut:
        with session_scope(self.sessions) as session:
            assertion = session.scalar(self._assertion_query().where(Assertion.id == assertion_id))
            if assertion is None:
                raise LookupError(f"Assertion {assertion_id} was not found")
            previous = assertion.status
            assertion.status = payload.status
            session.add(
                Revision(
                    id=_id("rev"),
                    target_type="assertion",
                    target_id=assertion.id,
                    action=f"status:{previous}->{payload.status}",
                    actor="user",
                    reason=payload.reason,
                )
            )
            session.flush()
            return self._assertion_out(assertion)

    def update_source_assertion_statuses(
        self, source_id: str, payload: UpdateAssertionStatusInput
    ) -> list[AssertionOut]:
        with session_scope(self.sessions) as session:
            if session.get(Source, source_id) is None:
                raise LookupError(f"Source {source_id} was not found")
            assertions = session.scalars(
                self._assertion_query().where(Assertion.source_id == source_id)
            ).all()
            for assertion in assertions:
                previous = assertion.status
                assertion.status = payload.status
                session.add(
                    Revision(
                        id=_id("rev"),
                        target_type="assertion",
                        target_id=assertion.id,
                        action=f"status:{previous}->{payload.status}",
                        actor="user",
                        reason=payload.reason,
                    )
                )
            session.flush()
            return [self._assertion_out(assertion) for assertion in assertions]
