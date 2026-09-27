from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from gunther.conversation import GroundingClaim, KnowledgeResponder
from gunther.database import session_scope
from gunther.extraction import CandidateAssertion, Extractor
from gunther.models import (
    Artifact,
    ArtifactUnitBinding,
    Assertion,
    Asset,
    Entity,
    EvidenceLink,
    Fragment,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    KnowledgeProposal,
    KnowledgeSession,
    KnowledgeUnit,
    KnowledgeUnitRevision,
    NotebookNote,
    Revision,
    SessionBranch,
    SessionMessage,
    Source,
    WebSnapshot,
    WorkspaceIdentity,
    utc_now,
)
from gunther.schemas import (
    ArtifactOut,
    ArtifactProvenanceOut,
    ArtifactSummaryOut,
    ArtifactUnitSnapshotOut,
    AssertionOut,
    AssetOut,
    ConversationCitationOut,
    ConversationContextOut,
    ConversationTurnOut,
    CreateArtifactInput,
    CreateKnowledgeBaseInput,
    CreateKnowledgeProposalInput,
    CreateKnowledgeSessionInput,
    CreateNotebookNoteInput,
    CreateSessionMessageInput,
    CreateSourceInput,
    EntityRefOut,
    EvidenceOut,
    FileNotebookNoteInput,
    FileNotebookNoteOut,
    FileSourceInput,
    FileSourceOut,
    GraphEdgeOut,
    GraphNodeOut,
    ImportCountsOut,
    ImportResultOut,
    InboxItemOut,
    InboxKnowledgeBaseRefOut,
    KnowledgeBaseOut,
    KnowledgeGraphOut,
    KnowledgeProposalOut,
    KnowledgeSearchResultOut,
    KnowledgeSessionOut,
    KnowledgeSessionSummaryOut,
    KnowledgeUnitOut,
    NotebookNoteOut,
    OverviewCountsOut,
    OverviewOut,
    SessionMessageOut,
    SourceDetailOut,
    SourceSummaryOut,
    UpdateAssertionStatusInput,
    UpdateKnowledgeBaseInput,
    UpdateKnowledgeProposalInput,
    UpdateKnowledgeSessionInput,
    UpdateNotebookNoteInput,
    WebSnapshotOut,
)
from gunther.source_identity import source_fingerprint


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _timestamp(value: datetime) -> str:
    return f"{value.isoformat(timespec='milliseconds')}Z"


def _normalized_label(value: str) -> str:
    return " ".join(value.casefold().split())


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:120] or "knowledge-base"


def _count_label(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or f'{singular}s'}"


class ArtifactConflictError(ValueError):
    """An idempotency key or immutable lineage head conflicts with current state."""


class ArtifactIntegrityError(RuntimeError):
    """Stored immutable Artifact bytes or provenance no longer match their hashes."""


@dataclass(frozen=True, slots=True)
class _SourcePassage:
    source: Source
    quote: str
    locator: str
    confidence: float


class KnowledgeService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        extractor: Extractor,
        responder: KnowledgeResponder,
    ) -> None:
        self.sessions = sessions
        self.extractor = extractor
        self.responder = responder
        self._source_import_locks_guard = Lock()
        self._source_import_locks: dict[str, tuple[Lock, int]] = {}

    @property
    def extraction_mode(self) -> str:
        return self.extractor.mode

    def _knowledge_base_out(
        self, session: Session, knowledge_base: KnowledgeBaseRecord
    ) -> KnowledgeBaseOut:
        source_count = (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeBaseSource)
                .where(KnowledgeBaseSource.knowledge_base_id == knowledge_base.id)
            )
            or 0
        )
        session_count = (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeSession)
                .where(KnowledgeSession.knowledge_base_id == knowledge_base.id)
            )
            or 0
        )
        pending_proposal_count = (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeProposal)
                .where(
                    KnowledgeProposal.knowledge_base_id == knowledge_base.id,
                    KnowledgeProposal.status == "pending",
                )
            )
            or 0
        )
        return KnowledgeBaseOut(
            id=knowledge_base.id,
            title=knowledge_base.title,
            eyebrow=knowledge_base.eyebrow,
            subtitle=knowledge_base.subtitle,
            question=knowledge_base.question,
            description=knowledge_base.description,
            color=knowledge_base.color,
            status=knowledge_base.status,
            source_count=source_count,
            session_count=session_count,
            pending_proposal_count=pending_proposal_count,
            created_at=_timestamp(knowledge_base.created_at),
            updated_at=_timestamp(knowledge_base.updated_at),
        )

    def list_knowledge_bases(self) -> list[KnowledgeBaseOut]:
        with session_scope(self.sessions) as session:
            knowledge_bases = session.scalars(
                select(KnowledgeBaseRecord).order_by(KnowledgeBaseRecord.updated_at.desc())
            ).all()
            return [self._knowledge_base_out(session, item) for item in knowledge_bases]

    def create_knowledge_base(self, payload: CreateKnowledgeBaseInput) -> KnowledgeBaseOut:
        with session_scope(self.sessions) as session:
            base_id = _slug(payload.title)
            if session.get(KnowledgeBaseRecord, base_id):
                base_id = f"{base_id}-{uuid4().hex[:6]}"
            knowledge_base = KnowledgeBaseRecord(
                id=base_id,
                title=payload.title.strip(),
                eyebrow=payload.eyebrow.strip(),
                subtitle=payload.subtitle.strip(),
                question=payload.question.strip(),
                description=payload.description.strip(),
                color=payload.color,
            )
            session.add(knowledge_base)
            session.flush()
            session.add(
                Revision(
                    id=_id("rev"),
                    target_type="knowledge_base",
                    target_id=knowledge_base.id,
                    action="created",
                    actor="user",
                    reason="Created from the library",
                )
            )
            return self._knowledge_base_out(session, knowledge_base)

    def update_knowledge_base(
        self, knowledge_base_id: str, payload: UpdateKnowledgeBaseInput
    ) -> KnowledgeBaseOut:
        with session_scope(self.sessions) as session:
            knowledge_base = session.get(KnowledgeBaseRecord, knowledge_base_id)
            if knowledge_base is None:
                raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
            changed: list[str] = []
            for field in (
                "title",
                "eyebrow",
                "subtitle",
                "question",
                "description",
                "color",
            ):
                value = getattr(payload, field)
                if value is None:
                    continue
                normalized = value.strip() if isinstance(value, str) else value
                if getattr(knowledge_base, field) != normalized:
                    setattr(knowledge_base, field, normalized)
                    changed.append(field)
            if changed:
                knowledge_base.updated_at = utc_now()
                session.add(
                    Revision(
                        id=_id("rev"),
                        target_type="knowledge_base",
                        target_id=knowledge_base.id,
                        action="metadata_updated",
                        actor="user",
                        reason=f"Changed: {', '.join(changed)}",
                    )
                )
            session.flush()
            return self._knowledge_base_out(session, knowledge_base)

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
        return SourceSummaryOut(
            id=source.id,
            title=source.title,
            kind=source.kind,
            created_at=_timestamp(source.created_at),
            assertion_count=len(assertions),
            entity_count=len(entity_ids),
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
        )

    def _ensure_source_memberships(
        self, session: Session, knowledge_base_id: str, source_ids: list[str]
    ) -> None:
        if session.get(KnowledgeBaseRecord, knowledge_base_id) is None:
            raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
        if not source_ids:
            return
        valid_source_ids = set(
            session.scalars(select(Source.id).where(Source.id.in_(source_ids))).all()
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
        if session.get(KnowledgeBaseRecord, knowledge_base_id) is None:
            raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
        if not source_ids:
            return
        valid_source_ids = set(
            session.scalars(select(Source.id).where(Source.id.in_(source_ids))).all()
        )
        missing_source_ids = set(source_ids) - valid_source_ids
        if missing_source_ids:
            missing = ", ".join(sorted(missing_source_ids))
            raise ValueError(f"Unknown source IDs: {missing}")
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
    ) -> tuple[Entity, bool]:
        normalized = _normalized_label(label)
        entity = session.scalar(
            select(Entity).where(
                Entity.normalized_label == normalized,
                Entity.type == entity_type,
            )
        )
        if entity:
            return entity, False

        entity = Entity(
            id=_id("ent"),
            label=label.strip(),
            normalized_label=normalized,
            type=entity_type.strip() or "Concept",
            aliases_json="[]",
        )
        session.add(entity)
        session.flush()
        return entity, True

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
        if knowledge_base_id:
            self._ensure_source_memberships(session, knowledge_base_id, [source.id])
        return ImportResultOut(
            source=self._source_summary(session, source),
            created=ImportCountsOut(entities=0, assertions=0),
            extraction_mode=self.extractor.mode,
            duplicate=True,
        )

    def import_source(
        self,
        payload: CreateSourceInput,
        *,
        initialize_source: Callable[[Session, Source], None] | None = None,
    ) -> ImportResultOut:
        content = payload.content.strip()
        content_hash = source_fingerprint(payload.title, payload.kind, content)

        with self._serialize_source_import(content_hash):
            return self._import_source(
                payload,
                content,
                content_hash,
                initialize_source=initialize_source,
            )

    def _import_source(
        self,
        payload: CreateSourceInput,
        content: str,
        content_hash: str,
        *,
        initialize_source: Callable[[Session, Source], None] | None = None,
    ) -> ImportResultOut:

        with session_scope(self.sessions) as session:
            if (
                payload.knowledge_base_id
                and session.get(KnowledgeBaseRecord, payload.knowledge_base_id) is None
            ):
                raise LookupError(f"Knowledge Base {payload.knowledge_base_id} was not found")
            existing = session.scalar(select(Source).where(Source.content_hash == content_hash))
            if existing:
                return self._duplicate_import_result(
                    session,
                    existing,
                    payload.knowledge_base_id,
                )

        extraction = self.extractor.extract(payload.title, content)

        try:
            with session_scope(self.sessions) as session:
                source = Source(
                    id=_id("src"),
                    title=payload.title.strip(),
                    kind=payload.kind,
                    content=content,
                    content_hash=content_hash,
                )
                session.add(source)
                session.flush()
                if initialize_source is not None:
                    initialize_source(session, source)
                    session.flush()
                if payload.knowledge_base_id:
                    self._ensure_source_memberships(
                        session, payload.knowledge_base_id, [source.id]
                    )

                created_entities = 0
                fragments: dict[str, Fragment] = {}
                for candidate in extraction.assertions:
                    subject, subject_created = self._find_or_create_entity(
                        session, candidate.subject_label, candidate.subject_type
                    )
                    object_entity, object_created = self._find_or_create_entity(
                        session, candidate.object_label, candidate.object_type
                    )
                    created_entities += int(subject_created) + int(object_created)

                    fragment = fragments.get(candidate.evidence_quote)
                    if fragment is None:
                        fragment = Fragment(
                            id=_id("frg"),
                            source_id=source.id,
                            locator=self._locator(content, candidate.evidence_quote),
                            content=candidate.evidence_quote,
                        )
                        fragments[candidate.evidence_quote] = fragment
                        session.add(fragment)

                    assertion = self._create_assertion(source, subject, object_entity, candidate)
                    session.add(assertion)
                    session.flush()
                    session.add(
                        EvidenceLink(
                            id=_id("evd"),
                            assertion_id=assertion.id,
                            fragment_id=fragment.id,
                            stance="supports",
                        )
                    )
                    session.add(
                        Revision(
                            id=_id("rev"),
                            target_type="assertion",
                            target_id=assertion.id,
                            action="created",
                            actor=f"extractor:{extraction.mode}",
                            reason="Imported from source evidence",
                        )
                    )

                session.flush()
                return ImportResultOut(
                    source=self._source_summary(session, source),
                    created=ImportCountsOut(
                        entities=created_entities,
                        assertions=len(extraction.assertions),
                    ),
                    extraction_mode=extraction.mode,
                    duplicate=False,
                )
        except IntegrityError:
            # Another process or service instance may have inserted the same hash after
            # our initial lookup. The database remains the final idempotency boundary.
            with session_scope(self.sessions) as session:
                existing = session.scalar(
                    select(Source).where(Source.content_hash == content_hash)
                )
                if existing is None:
                    raise
                return self._duplicate_import_result(
                    session,
                    existing,
                    payload.knowledge_base_id,
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
            sources = session.scalars(select(Source).order_by(Source.created_at.desc())).all()
            return [self._source_summary(session, source) for source in sources]

    @staticmethod
    def _inbox_preview(content: str, limit: int = 240) -> str:
        preview = re.sub(r"\s+", " ", content).strip()
        return preview[: limit - 1] + "…" if len(preview) > limit else preview

    def list_inbox(
        self,
        state: str | None = None,
        item_type: str | None = None,
    ) -> list[InboxItemOut]:
        """Build one review queue from the existing durable domain records.

        The inbox is deliberately a read model rather than another table: capture records
        remain the source of truth, and an item disappears as soon as its underlying state
        no longer needs filing or review.
        """

        with session_scope(self.sessions) as session:
            knowledge_bases = {
                knowledge_base.id: InboxKnowledgeBaseRefOut(
                    id=knowledge_base.id,
                    title=knowledge_base.title,
                )
                for knowledge_base in session.scalars(select(KnowledgeBaseRecord)).all()
            }

            membership_source_ids = select(KnowledgeBaseSource.source_id)
            unfiled_sources = session.scalars(
                select(Source).where(Source.id.not_in(membership_source_ids))
            ).all()
            review_sources = session.scalars(
                select(Source)
                .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == Source.id)
                .join(Assertion, Assertion.source_id == Source.id)
                .where(Assertion.status == "provisional")
                .distinct()
            ).all()

            source_ids = {source.id for source in (*unfiled_sources, *review_sources)}
            provisional_counts: dict[str, int] = {}
            source_knowledge_bases: dict[str, list[InboxKnowledgeBaseRefOut]] = {
                source_id: [] for source_id in source_ids
            }
            if source_ids:
                provisional_counts = {
                    source_id: count
                    for source_id, count in session.execute(
                        select(Assertion.source_id, func.count(Assertion.id))
                        .where(
                            Assertion.source_id.in_(source_ids),
                            Assertion.status == "provisional",
                        )
                        .group_by(Assertion.source_id)
                    ).all()
                }
                for source_id, knowledge_base_id in session.execute(
                    select(
                        KnowledgeBaseSource.source_id,
                        KnowledgeBaseSource.knowledge_base_id,
                    )
                    .where(KnowledgeBaseSource.source_id.in_(source_ids))
                    .order_by(KnowledgeBaseSource.created_at.asc())
                ).all():
                    knowledge_base = knowledge_bases.get(knowledge_base_id)
                    if knowledge_base:
                        source_knowledge_bases[source_id].append(knowledge_base)

            items: list[InboxItemOut] = []
            for source in unfiled_sources:
                timestamp = _timestamp(source.created_at)
                items.append(
                    InboxItemOut(
                        id=source.id,
                        item_type="source",
                        state="unfiled",
                        title=source.title,
                        preview=self._inbox_preview(source.content),
                        source_kind=source.kind,
                        source_id=source.id,
                        assertion_count=provisional_counts.get(source.id, 0),
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )

            for source in review_sources:
                timestamp = _timestamp(source.created_at)
                items.append(
                    InboxItemOut(
                        id=source.id,
                        item_type="source",
                        state="needs_review",
                        title=source.title,
                        preview=self._inbox_preview(source.content),
                        source_kind=source.kind,
                        knowledge_bases=source_knowledge_bases[source.id],
                        source_id=source.id,
                        assertion_count=provisional_counts.get(source.id, 0),
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )

            notes = session.scalars(
                select(NotebookNote).where(NotebookNote.status == "inbox")
            ).all()
            for note in notes:
                note_knowledge_bases = []
                if note.knowledge_base_id and note.knowledge_base_id in knowledge_bases:
                    note_knowledge_bases.append(knowledge_bases[note.knowledge_base_id])
                items.append(
                    InboxItemOut(
                        id=note.id,
                        item_type="quick_note",
                        state="unfiled",
                        title=note.title,
                        preview=self._inbox_preview(note.content),
                        source_kind="note",
                        knowledge_bases=note_knowledge_bases,
                        note_id=note.id,
                        created_at=_timestamp(note.created_at),
                        updated_at=_timestamp(note.updated_at),
                    )
                )

            proposals = session.scalars(
                select(KnowledgeProposal).where(
                    KnowledgeProposal.status.in_(("pending", "held"))
                )
            ).all()
            for proposal in proposals:
                proposal_knowledge_bases = []
                if proposal.knowledge_base_id in knowledge_bases:
                    proposal_knowledge_bases.append(
                        knowledge_bases[proposal.knowledge_base_id]
                    )
                items.append(
                    InboxItemOut(
                        id=proposal.id,
                        item_type="knowledge_suggestion",
                        state=("held" if proposal.status == "held" else "needs_review"),
                        title=proposal.title,
                        preview=self._inbox_preview(proposal.content),
                        knowledge_bases=proposal_knowledge_bases,
                        proposal_id=proposal.id,
                        proposal_status=proposal.status,
                        created_at=_timestamp(proposal.created_at),
                        updated_at=_timestamp(proposal.updated_at),
                    )
                )

            if state:
                items = [item for item in items if item.state == state]
            if item_type:
                items = [item for item in items if item.item_type == item_type]
            return sorted(items, key=lambda item: item.updated_at, reverse=True)

    def file_source(self, source_id: str, payload: FileSourceInput) -> FileSourceOut:
        with session_scope(self.sessions) as session:
            source = session.get(Source, source_id)
            if source is None:
                raise LookupError(f"Source {source_id} was not found")
            knowledge_base = session.get(KnowledgeBaseRecord, payload.knowledge_base_id)
            if knowledge_base is None:
                raise LookupError(
                    f"Knowledge Base {payload.knowledge_base_id} was not found"
                )
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
            statement = select(NotebookNote)
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
            if note is None:
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
            if note is None:
                raise LookupError(f"Notebook note {note_id} was not found")
            if session.get(KnowledgeBaseRecord, payload.knowledge_base_id) is None:
                raise LookupError(
                    f"Knowledge Base {payload.knowledge_base_id} was not found"
                )
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

    def get_source(self, source_id: str) -> SourceDetailOut:
        with session_scope(self.sessions) as session:
            source = session.get(Source, source_id)
            if source is None:
                raise LookupError(f"Source {source_id} was not found")
            summary = self._source_summary(session, source)
            assertions = session.scalars(
                self._assertion_query().where(Assertion.source_id == source_id)
            ).all()
            snapshot = session.scalar(
                select(WebSnapshot).where(WebSnapshot.source_id == source_id)
            )
            return SourceDetailOut(
                **summary.model_dump(),
                content=source.content,
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

    def list_knowledge_base_sources(self, knowledge_base_id: str) -> list[SourceSummaryOut]:
        with session_scope(self.sessions) as session:
            source_ids = set(
                session.scalars(
                    select(KnowledgeBaseSource.source_id).where(
                        KnowledgeBaseSource.knowledge_base_id == knowledge_base_id
                    )
                ).all()
            )
            knowledge_sessions = session.scalars(
                select(KnowledgeSession).where(
                    KnowledgeSession.knowledge_base_id == knowledge_base_id
                )
            ).all()
            for knowledge_session in knowledge_sessions:
                source_ids.update(json.loads(knowledge_session.selected_source_ids_json or "[]"))
            if not source_ids:
                return []
            sources = session.scalars(
                select(Source).where(Source.id.in_(source_ids)).order_by(Source.created_at.desc())
            ).all()
            return [self._source_summary(session, source) for source in sources]

    def list_assertions(self, status: str | None = None, limit: int = 100) -> list[AssertionOut]:
        with session_scope(self.sessions) as session:
            query = self._assertion_query().order_by(Assertion.created_at.desc()).limit(limit)
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

    def _session_query(self):
        return select(KnowledgeSession).options(
            selectinload(KnowledgeSession.messages),
            selectinload(KnowledgeSession.branch),
        )

    def _message_out(self, message: SessionMessage) -> SessionMessageOut:
        citations = [
            ConversationCitationOut.model_validate(item)
            for item in json.loads(message.citations_json or "[]")
        ]
        context_payload = json.loads(message.context_json or "{}")
        return SessionMessageOut(
            id=message.id,
            session_id=message.session_id,
            role=message.role,
            content=message.content,
            citations=citations,
            context=ConversationContextOut.model_validate(context_payload),
            created_at=_timestamp(message.created_at),
        )

    def _session_summary(self, knowledge_session: KnowledgeSession) -> KnowledgeSessionSummaryOut:
        return KnowledgeSessionSummaryOut(
            id=knowledge_session.id,
            knowledge_base_id=knowledge_session.knowledge_base_id,
            title=knowledge_session.title,
            summary=knowledge_session.summary,
            focus_chapter_id=knowledge_session.focus_chapter_id,
            selected_source_ids=json.loads(knowledge_session.selected_source_ids_json or "[]"),
            pinned=knowledge_session.pinned,
            archived=knowledge_session.archived,
            parent_session_id=(
                knowledge_session.branch.parent_session_id if knowledge_session.branch else None
            ),
            branched_from_message_id=(
                knowledge_session.branch.branched_from_message_id
                if knowledge_session.branch
                else None
            ),
            message_count=len(knowledge_session.messages),
            created_at=_timestamp(knowledge_session.created_at),
            updated_at=_timestamp(knowledge_session.updated_at),
        )

    def create_knowledge_session(
        self, knowledge_base_id: str, payload: CreateKnowledgeSessionInput
    ) -> KnowledgeSessionOut:
        now = utc_now()
        with session_scope(self.sessions) as session:
            if session.get(KnowledgeBaseRecord, knowledge_base_id) is None:
                raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
            knowledge_session = KnowledgeSession(
                id=_id("ses"),
                knowledge_base_id=knowledge_base_id,
                title=(payload.title or "New session").strip(),
                focus_chapter_id=payload.focus_chapter_id,
                selected_source_ids_json=json.dumps(payload.selected_source_ids),
                created_at=now,
                updated_at=now,
            )
            session.add(knowledge_session)
            self._validate_source_scope(session, knowledge_base_id, payload.selected_source_ids)
            session.flush()
            return KnowledgeSessionOut(
                **self._session_summary(knowledge_session).model_dump(), messages=[]
            )

    def list_knowledge_sessions(
        self, knowledge_base_id: str, include_archived: bool = False
    ) -> list[KnowledgeSessionSummaryOut]:
        with session_scope(self.sessions) as session:
            query = self._session_query().where(
                KnowledgeSession.knowledge_base_id == knowledge_base_id
            )
            if not include_archived:
                query = query.where(KnowledgeSession.archived.is_(False))
            query = query.order_by(
                KnowledgeSession.pinned.desc(), KnowledgeSession.updated_at.desc()
            )
            sessions = session.scalars(query).all()
            return [self._session_summary(item) for item in sessions]

    def get_knowledge_session(self, session_id: str) -> KnowledgeSessionOut:
        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            if knowledge_session is None:
                raise LookupError(f"Session {session_id} was not found")
            return KnowledgeSessionOut(
                **self._session_summary(knowledge_session).model_dump(),
                messages=[self._message_out(message) for message in knowledge_session.messages],
            )

    def branch_knowledge_session(self, session_id: str, message_id: str) -> KnowledgeSessionOut:
        with session_scope(self.sessions) as session:
            parent = session.scalar(self._session_query().where(KnowledgeSession.id == session_id))
            if parent is None:
                raise LookupError(f"Session {session_id} was not found")
            branch_index = next(
                (
                    index
                    for index, message in enumerate(parent.messages)
                    if message.id == message_id
                ),
                None,
            )
            if branch_index is None:
                raise LookupError(f"Message {message_id} was not found in this session")

            now = utc_now()
            branch = KnowledgeSession(
                id=_id("ses"),
                knowledge_base_id=parent.knowledge_base_id,
                title=f"Branch · {parent.title}"[:160],
                summary=parent.summary,
                focus_chapter_id=parent.focus_chapter_id,
                selected_source_ids_json=parent.selected_source_ids_json,
                created_at=now,
                updated_at=now,
            )
            for original in parent.messages[: branch_index + 1]:
                branch.messages.append(
                    SessionMessage(
                        id=_id("msg"),
                        role=original.role,
                        content=original.content,
                        citations_json=original.citations_json,
                        context_json=original.context_json,
                        created_at=original.created_at,
                    )
                )
            session.add(branch)
            self._validate_source_scope(
                session,
                branch.knowledge_base_id,
                json.loads(branch.selected_source_ids_json or "[]"),
            )
            session.flush()
            branch.branch = SessionBranch(
                id=_id("brn"),
                session_id=branch.id,
                parent_session_id=parent.id,
                branched_from_message_id=message_id,
            )
            return KnowledgeSessionOut(
                **self._session_summary(branch).model_dump(),
                messages=[self._message_out(message) for message in branch.messages],
            )

    def update_knowledge_session(
        self, session_id: str, payload: UpdateKnowledgeSessionInput
    ) -> KnowledgeSessionSummaryOut:
        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            if knowledge_session is None:
                raise LookupError(f"Session {session_id} was not found")
            if knowledge_session.archived and (
                payload.selected_source_ids is not None
                or "focus_chapter_id" in payload.model_fields_set
            ):
                raise ValueError(
                    "Restore the archived session before changing its retrieval context"
                )
            if payload.title is not None:
                knowledge_session.title = payload.title.strip()
            if "focus_chapter_id" in payload.model_fields_set:
                knowledge_session.focus_chapter_id = payload.focus_chapter_id
            if payload.selected_source_ids is not None:
                knowledge_session.selected_source_ids_json = json.dumps(payload.selected_source_ids)
                self._validate_source_scope(
                    session,
                    knowledge_session.knowledge_base_id,
                    payload.selected_source_ids,
                )
            if payload.pinned is not None:
                knowledge_session.pinned = payload.pinned
            if payload.archived is not None:
                knowledge_session.archived = payload.archived
            knowledge_session.updated_at = utc_now()
            session.flush()
            return self._session_summary(knowledge_session)

    @staticmethod
    def _query_terms(question: str) -> set[str]:
        stopwords = {
            "after",
            "and",
            "are",
            "about",
            "before",
            "can",
            "check",
            "checking",
            "could",
            "does",
            "evidence",
            "for",
            "from",
            "have",
            "how",
            "into",
            "most",
            "not",
            "should",
            "support",
            "supported",
            "supporting",
            "supports",
            "that",
            "the",
            "their",
            "there",
            "these",
            "them",
            "this",
            "what",
            "when",
            "where",
            "which",
            "with",
            "would",
            "why",
        }
        terms: set[str] = set()
        for token in re.findall(r"[\w]+(?:[-–—][\w]+)*", question.casefold()):
            # Keep a hyphenated concept together: “T-cell” should match “T cell”,
            # while “cell-type” must not degrade into the overly broad “cell”.
            normalized = re.sub(r"[-–—]+", " ", token).strip()
            if " " in normalized or (
                len(normalized) > 2 and normalized not in stopwords
            ):
                terms.add(normalized)
        return terms

    def _rank_assertions(
        self, assertions: list[Assertion], question: str, limit: int = 5
    ) -> list[Assertion]:
        terms = self._query_terms(question)

        def assertion_text(assertion: Assertion) -> str:
            raw = " ".join(
                (
                    assertion.subject.label,
                    assertion.predicate,
                    assertion.object.label,
                    assertion.source.title,
                    *(link.fragment.content for link in assertion.evidence_links),
                )
            ).casefold()
            return re.sub(r"[^\w]+", " ", raw).strip()

        def score(assertion: Assertion) -> float:
            haystack = assertion_text(assertion)
            matches = sum(term in haystack for term in terms)
            return (
                matches * 3 + (1.2 if assertion.status == "verified" else 0) + assertion.confidence
            )

        ranked = sorted(assertions, key=score, reverse=True)
        matched = [item for item in ranked if any(term in assertion_text(item) for term in terms)]
        return matched[:limit]

    @staticmethod
    def _source_text_passages(source: Source) -> list[tuple[str, str]]:
        """Split preserved source text into bounded, human-auditable citation passages."""

        locator = "Source text"
        passages: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_line in source.content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            heading = re.match(r"^#{1,6}\s+(.+)$", line)
            if heading:
                locator = heading.group(1).strip()[:120] or "Source text"
                continue
            line = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip()
            for raw_passage in re.split(r"(?<=[.!?。！？])\s+", line):
                passage = re.sub(r"\s+", " ", raw_passage).strip()
                if len(passage) < 12:
                    continue
                if len(passage) > 600:
                    passage = passage[:597].rstrip() + "…"
                identity = passage.casefold()
                if identity in seen:
                    continue
                seen.add(identity)
                passages.append((passage, locator))
        return passages

    def _rank_source_passages(
        self, sources: list[Source], question: str, limit: int = 5
    ) -> list[_SourcePassage]:
        """Retrieve from raw preserved text when extraction produced no matching claim."""

        terms = self._query_terms(question)
        if not terms:
            return []
        required_matches = 1 if len(terms) <= 1 else 2
        candidates: list[tuple[float, Source, str, str, int]] = []
        for source in sources:
            title = re.sub(r"[^\w]+", " ", source.title.casefold()).strip()
            for quote, locator in self._source_text_passages(source):
                haystack = re.sub(r"[^\w]+", " ", f"{source.title} {quote}".casefold()).strip()
                matches = sum(term in haystack for term in terms)
                if matches < required_matches:
                    continue
                title_matches = sum(term in title for term in terms)
                coverage = matches / len(terms)
                score = matches * 3 + coverage + min(title_matches, 2) * 0.2
                candidates.append((score, source, quote, locator, matches))

        candidates.sort(key=lambda item: (item[0], item[4], len(item[2])), reverse=True)
        results: list[_SourcePassage] = []
        source_counts: Counter[str] = Counter()
        for score, source, quote, locator, matches in candidates:
            if source_counts[source.id] >= 2:
                continue
            confidence = min(0.9, 0.58 + (matches / len(terms)) * 0.3 + min(score, 12) / 200)
            results.append(
                _SourcePassage(
                    source=source,
                    quote=quote,
                    locator=locator,
                    confidence=round(confidence, 2),
                )
            )
            source_counts[source.id] += 1
            if len(results) >= limit:
                break
        return results

    def create_session_turn(
        self, session_id: str, payload: CreateSessionMessageInput
    ) -> ConversationTurnOut:
        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            if knowledge_session is None:
                raise LookupError(f"Session {session_id} was not found")
            if knowledge_session.archived:
                raise ValueError("Restore the archived session before adding another message")

            if payload.selected_source_ids is not None:
                knowledge_session.selected_source_ids_json = json.dumps(payload.selected_source_ids)
                self._validate_source_scope(
                    session,
                    knowledge_session.knowledge_base_id,
                    payload.selected_source_ids,
                )
            if "focus_chapter_id" in payload.model_fields_set:
                knowledge_session.focus_chapter_id = payload.focus_chapter_id

            selected_source_ids = json.loads(knowledge_session.selected_source_ids_json or "[]")
            assertion_query = self._assertion_query().order_by(
                Assertion.status.asc(), Assertion.confidence.desc()
            )
            if selected_source_ids:
                scoped_source_ids = selected_source_ids
            else:
                scoped_source_ids = list(
                    session.scalars(
                        select(KnowledgeBaseSource.source_id).where(
                            KnowledgeBaseSource.knowledge_base_id
                            == knowledge_session.knowledge_base_id
                        )
                    ).all()
                )
            assertion_query = assertion_query.where(Assertion.source_id.in_(scoped_source_ids))
            scoped_sources = (
                list(
                    session.scalars(
                        select(Source)
                        .where(Source.id.in_(scoped_source_ids))
                        .order_by(Source.created_at.desc())
                    ).all()
                )
                if scoped_source_ids
                else []
            )
            available_assertions = list(session.scalars(assertion_query).all())
            ranked_assertions = self._rank_assertions(available_assertions, payload.content)

            citations: list[ConversationCitationOut] = []
            claims: list[GroundingClaim] = []
            for assertion in ranked_assertions:
                if not assertion.evidence_links:
                    continue
                evidence = assertion.evidence_links[0]
                citation = ConversationCitationOut(
                    id=_id("cit"),
                    source_id=assertion.source.id,
                    source_title=assertion.source.title,
                    assertion_id=assertion.id,
                    quote=evidence.fragment.content,
                    locator=evidence.fragment.locator,
                    status=assertion.status,
                    confidence=assertion.confidence,
                )
                citations.append(citation)
                claims.append(
                    GroundingClaim(
                        subject=assertion.subject.label,
                        predicate=assertion.predicate,
                        object=assertion.object.label,
                        source_title=assertion.source.title,
                        quote=evidence.fragment.content,
                        locator=evidence.fragment.locator,
                        status=assertion.status,
                        confidence=assertion.confidence,
                    )
                )

            if not claims:
                for passage in self._rank_source_passages(scoped_sources, payload.content):
                    citations.append(
                        ConversationCitationOut(
                            id=_id("cit"),
                            source_id=passage.source.id,
                            source_title=passage.source.title,
                            assertion_id=None,
                            quote=passage.quote,
                            locator=passage.locator,
                            status="provisional",
                            confidence=passage.confidence,
                        )
                    )
                    claims.append(
                        GroundingClaim(
                            subject=passage.source.title,
                            predicate="states",
                            object=passage.quote,
                            source_title=passage.source.title,
                            quote=passage.quote,
                            locator=passage.locator,
                            status="provisional",
                            confidence=passage.confidence,
                        )
                    )

            history = [(message.role, message.content) for message in knowledge_session.messages]
            response = self.responder.respond(payload.content.strip(), claims, history)
            context = ConversationContextOut(
                sources_considered=len(scoped_sources),
                assertions_considered=len(available_assertions),
                verified_assertions=sum(item.status == "verified" for item in available_assertions),
                retrieval_mode="selected" if selected_source_ids else "all",
                responder_mode=response.mode,
            )
            now = utc_now()
            user_message = SessionMessage(
                id=_id("msg"),
                session_id=knowledge_session.id,
                role="user",
                content=payload.content.strip(),
                created_at=now,
            )
            assistant_message = SessionMessage(
                id=_id("msg"),
                session_id=knowledge_session.id,
                role="assistant",
                content=response.content,
                citations_json=json.dumps(
                    [item.model_dump() for item in citations], ensure_ascii=False
                ),
                context_json=context.model_dump_json(),
                created_at=now + timedelta(microseconds=1),
            )
            knowledge_session.messages.extend([user_message, assistant_message])
            if knowledge_session.title == "New session":
                title = re.sub(r"\s+", " ", payload.content.strip())
                knowledge_session.title = title[:62] + ("…" if len(title) > 62 else "")
            knowledge_session.summary = re.sub(r"\s+", " ", response.content)[:220]
            knowledge_session.updated_at = now
            session.flush()
            return ConversationTurnOut(
                session=self._session_summary(knowledge_session),
                user_message=self._message_out(user_message),
                assistant_message=self._message_out(assistant_message),
            )

    def _proposal_out(
        self,
        knowledge_proposal: KnowledgeProposal,
        knowledge_session: KnowledgeSession,
        unit_revision: KnowledgeUnitRevision | None = None,
    ) -> KnowledgeProposalOut:
        return KnowledgeProposalOut(
            id=knowledge_proposal.id,
            knowledge_base_id=knowledge_proposal.knowledge_base_id,
            session_id=knowledge_proposal.session_id,
            message_id=knowledge_proposal.message_id,
            target_chapter_id=knowledge_proposal.target_chapter_id,
            kind=knowledge_proposal.kind,
            title=knowledge_proposal.title,
            content=knowledge_proposal.content,
            status=knowledge_proposal.status,
            decision_reason=knowledge_proposal.decision_reason,
            knowledge_unit_id=(unit_revision.unit_id if unit_revision else None),
            source_session_title=knowledge_session.title,
            created_at=_timestamp(knowledge_proposal.created_at),
            updated_at=_timestamp(knowledge_proposal.updated_at),
        )

    @staticmethod
    def _proposal_unit_revision(session: Session, proposal_id: str) -> KnowledgeUnitRevision | None:
        return session.scalar(
            select(KnowledgeUnitRevision).where(
                KnowledgeUnitRevision.source_proposal_id == proposal_id
            )
        )

    def _ensure_proposal_unit(
        self, session: Session, proposal: KnowledgeProposal
    ) -> KnowledgeUnitRevision:
        existing = self._proposal_unit_revision(session, proposal.id)
        if existing is not None:
            return existing
        knowledge_unit = KnowledgeUnit(
            id=_id("unt"),
            knowledge_base_id=proposal.knowledge_base_id,
            title=proposal.title,
            status="trusted",
        )
        session.add(knowledge_unit)
        session.flush()
        unit_revision = KnowledgeUnitRevision(
            id=_id("urv"),
            unit_id=knowledge_unit.id,
            revision_number=1,
            content=proposal.content,
            source_proposal_id=proposal.id,
        )
        session.add(unit_revision)
        session.flush()
        knowledge_unit.head_revision_id = unit_revision.id
        return unit_revision

    def create_knowledge_proposal(
        self,
        session_id: str,
        message_id: str,
        payload: CreateKnowledgeProposalInput,
    ) -> KnowledgeProposalOut:
        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            if knowledge_session is None:
                raise LookupError(f"Session {session_id} was not found")
            message = session.scalar(
                select(SessionMessage).where(
                    SessionMessage.id == message_id,
                    SessionMessage.session_id == session_id,
                )
            )
            if message is None:
                raise LookupError(f"Message {message_id} was not found in this session")
            if message.role != "assistant":
                raise ValueError("Only an assistant synthesis can become a knowledge proposal")
            if not json.loads(message.citations_json or "[]"):
                raise ValueError(
                    "An evidence-backed citation is required before an answer can become knowledge"
                )
            existing = session.scalar(
                select(KnowledgeProposal).where(KnowledgeProposal.message_id == message_id)
            )
            if existing:
                unit_revision = self._proposal_unit_revision(session, existing.id)
                return self._proposal_out(existing, knowledge_session, unit_revision)

            generated_title = knowledge_session.title
            if generated_title == "New session":
                generated_title = "Session synthesis"
            proposal = KnowledgeProposal(
                id=_id("prp"),
                knowledge_base_id=knowledge_session.knowledge_base_id,
                session_id=knowledge_session.id,
                message_id=message.id,
                target_chapter_id=payload.target_chapter_id or knowledge_session.focus_chapter_id,
                title=(payload.title or generated_title).strip(),
                content=message.content,
            )
            session.add(proposal)
            session.flush()
            session.add(
                Revision(
                    id=_id("rev"),
                    target_type="proposal",
                    target_id=proposal.id,
                    action="created_from_session",
                    actor="user",
                    reason=f"Promoted from assistant message {message.id}",
                )
            )
            return self._proposal_out(proposal, knowledge_session)

    def list_knowledge_proposals(
        self, knowledge_base_id: str, status: str | None = None
    ) -> list[KnowledgeProposalOut]:
        with session_scope(self.sessions) as session:
            query = select(KnowledgeProposal).where(
                KnowledgeProposal.knowledge_base_id == knowledge_base_id
            )
            if status:
                query = query.where(KnowledgeProposal.status == status)
            proposals = session.scalars(query.order_by(KnowledgeProposal.updated_at.desc())).all()
            result: list[KnowledgeProposalOut] = []
            for proposal in proposals:
                knowledge_session = session.get(KnowledgeSession, proposal.session_id)
                if knowledge_session:
                    unit_revision = self._proposal_unit_revision(session, proposal.id)
                    result.append(self._proposal_out(proposal, knowledge_session, unit_revision))
            return result

    def update_knowledge_proposal(
        self, proposal_id: str, payload: UpdateKnowledgeProposalInput
    ) -> KnowledgeProposalOut:
        with session_scope(self.sessions) as session:
            proposal = session.get(KnowledgeProposal, proposal_id)
            if proposal is None:
                raise LookupError(f"Proposal {proposal_id} was not found")
            knowledge_session = session.get(KnowledgeSession, proposal.session_id)
            if knowledge_session is None:
                raise LookupError(f"Session {proposal.session_id} was not found")
            previous = proposal.status
            proposal.status = payload.status
            proposal.decision_reason = payload.reason
            proposal.updated_at = utc_now()
            unit_revision = self._proposal_unit_revision(session, proposal.id)
            if payload.status == "accepted" and unit_revision is None:
                unit_revision = self._ensure_proposal_unit(session, proposal)
            elif unit_revision is not None:
                knowledge_unit = session.get(KnowledgeUnit, unit_revision.unit_id)
                if knowledge_unit:
                    knowledge_unit.status = {
                        "accepted": "trusted",
                        "rejected": "deprecated",
                    }.get(payload.status, "provisional")
                    knowledge_unit.updated_at = utc_now()
            session.add(
                Revision(
                    id=_id("rev"),
                    target_type="proposal",
                    target_id=proposal.id,
                    action=f"status:{previous}->{payload.status}",
                    actor="user",
                    reason=payload.reason,
                )
            )
            session.flush()
            return self._proposal_out(proposal, knowledge_session, unit_revision)

    def _knowledge_unit_out(
        self, session: Session, knowledge_unit: KnowledgeUnit
    ) -> KnowledgeUnitOut:
        head = next(
            (
                revision
                for revision in knowledge_unit.revisions
                if revision.id == knowledge_unit.head_revision_id
            ),
            knowledge_unit.revisions[-1] if knowledge_unit.revisions else None,
        )
        if head is None:
            raise ValueError(f"Knowledge Unit {knowledge_unit.id} has no revision")
        proposal = session.get(KnowledgeProposal, head.source_proposal_id)
        if proposal is None:
            raise ValueError(f"Knowledge Unit {knowledge_unit.id} has no source proposal")
        source_message = session.get(SessionMessage, proposal.message_id)
        citations = json.loads(source_message.citations_json or "[]") if source_message else []
        return KnowledgeUnitOut(
            id=knowledge_unit.id,
            knowledge_base_id=knowledge_unit.knowledge_base_id,
            title=knowledge_unit.title,
            kind=knowledge_unit.kind,
            status=knowledge_unit.status,
            content=head.content,
            revision_count=len(knowledge_unit.revisions),
            source_proposal_id=head.source_proposal_id,
            source_session_id=proposal.session_id,
            source_message_id=proposal.message_id,
            target_chapter_id=proposal.target_chapter_id,
            evidence_count=len(citations),
            created_at=_timestamp(knowledge_unit.created_at),
            updated_at=_timestamp(knowledge_unit.updated_at),
        )

    def list_knowledge_units(self, knowledge_base_id: str) -> list[KnowledgeUnitOut]:
        with session_scope(self.sessions) as session:
            knowledge_units = session.scalars(
                select(KnowledgeUnit)
                .options(selectinload(KnowledgeUnit.revisions))
                .where(KnowledgeUnit.knowledge_base_id == knowledge_base_id)
                .order_by(KnowledgeUnit.updated_at.desc())
            ).all()
            return [self._knowledge_unit_out(session, item) for item in knowledge_units]

    @staticmethod
    def _artifact_summary_out(artifact: Artifact) -> ArtifactSummaryOut:
        accepted_unit_ids = json.loads(artifact.accepted_unit_ids_json)
        return ArtifactSummaryOut(
            id=artifact.id,
            workspace_id=artifact.workspace_id,
            knowledge_base_id=artifact.knowledge_base_id,
            lineage_id=artifact.lineage_id,
            version_number=artifact.version_number,
            supersedes_artifact_id=artifact.supersedes_artifact_id,
            format=artifact.format,
            audience=artifact.audience,
            title=artifact.title,
            content_hash=artifact.content_hash,
            manifest_hash=artifact.manifest_hash,
            accepted_unit_ids=accepted_unit_ids,
            unit_count=len(accepted_unit_ids),
            created_at=_timestamp(artifact.created_at),
        )

    @staticmethod
    def _verified_artifact_payload(
        session: Session, artifact: Artifact
    ) -> tuple[list[ArtifactUnitSnapshotOut], ArtifactProvenanceOut]:
        try:
            accepted_unit_ids = json.loads(artifact.accepted_unit_ids_json)
            snapshots = [
                ArtifactUnitSnapshotOut.model_validate(item)
                for item in json.loads(artifact.revision_snapshot_json)
            ]
            provenance = ArtifactProvenanceOut.model_validate(
                json.loads(artifact.provenance_json)
            )
        except Exception as error:
            raise ArtifactIntegrityError(
                f"Artifact {artifact.id} contains invalid immutable provenance"
            ) from error

        content_hash = hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
        if not secrets.compare_digest(content_hash, artifact.content_hash):
            raise ArtifactIntegrityError(
                f"Artifact {artifact.id} content failed its integrity check"
            )
        if (
            not isinstance(accepted_unit_ids, list)
            or accepted_unit_ids != [snapshot.unit_id for snapshot in snapshots]
            or provenance.workspace_id != artifact.workspace_id
            or provenance.knowledge_base_id != artifact.knowledge_base_id
            or provenance.accepted_unit_ids != accepted_unit_ids
            or provenance.revision_ids != [snapshot.revision_id for snapshot in snapshots]
            or not provenance.accepted_only
        ):
            raise ArtifactIntegrityError(
                f"Artifact {artifact.id} provenance failed its integrity check"
            )

        bindings = session.scalars(
            select(ArtifactUnitBinding)
            .where(ArtifactUnitBinding.artifact_id == artifact.id)
            .order_by(ArtifactUnitBinding.position)
        ).all()
        if len(bindings) != len(snapshots):
            raise ArtifactIntegrityError(
                f"Artifact {artifact.id} binding count failed its integrity check"
            )
        for position, (snapshot, binding) in enumerate(zip(snapshots, bindings, strict=True)):
            snapshot_hash = hashlib.sha256(snapshot.content.encode("utf-8")).hexdigest()
            revision = session.get(KnowledgeUnitRevision, snapshot.revision_id)
            if (
                not secrets.compare_digest(snapshot_hash, snapshot.content_hash)
                or binding.position != position
                or binding.unit_id != snapshot.unit_id
                or binding.revision_id != snapshot.revision_id
                or not secrets.compare_digest(binding.content_hash, snapshot.content_hash)
                or revision is None
                or revision.unit_id != snapshot.unit_id
                or revision.revision_number != snapshot.revision_number
                or not secrets.compare_digest(
                    hashlib.sha256(revision.content.encode("utf-8")).hexdigest(),
                    snapshot.content_hash,
                )
            ):
                raise ArtifactIntegrityError(
                    f"Artifact {artifact.id} pinned revision failed its integrity check"
                )

        manifest_hash = hashlib.sha256(
            json.dumps(
                {
                    "format": artifact.format,
                    "audience": artifact.audience,
                    "title": artifact.title,
                    "contentHash": artifact.content_hash,
                    "acceptedUnitIds": accepted_unit_ids,
                    "revisionSnapshot": json.loads(artifact.revision_snapshot_json),
                    "provenance": json.loads(artifact.provenance_json),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if not secrets.compare_digest(manifest_hash, artifact.manifest_hash):
            raise ArtifactIntegrityError(
                f"Artifact {artifact.id} manifest failed its integrity check"
            )
        return snapshots, provenance

    @classmethod
    def _artifact_out(cls, session: Session, artifact: Artifact) -> ArtifactOut:
        snapshots, provenance = cls._verified_artifact_payload(session, artifact)
        summary = cls._artifact_summary_out(artifact)
        return ArtifactOut(
            **summary.model_dump(),
            content=artifact.content,
            unit_snapshots=snapshots,
            provenance=provenance,
        )

    @staticmethod
    def _compose_artifact_content(
        *,
        title: str,
        question: str,
        artifact_format: str,
        audience: str,
        snapshots: list[ArtifactUnitSnapshotOut],
    ) -> str:
        format_label = {
            "field_guide": "Field guide",
            "teaching_path": "Teaching path",
            "decision_brief": "Decision brief",
        }[artifact_format]
        audience_label = audience.replace("_", " ")
        purpose = {
            "teaching_path": (
                f"A teaching sequence for a {audience_label}, grounded in reviewed knowledge."
            ),
            "decision_brief": (
                f"A decision-oriented brief for a {audience_label}, "
                "grounded in reviewed knowledge."
            ),
            "field_guide": (
                f"A field guide for a {audience_label}, grounded in reviewed knowledge."
            ),
        }[artifact_format]
        sections = []
        for index, snapshot in enumerate(snapshots, start=1):
            sections.append(
                "\n".join(
                    (
                        f"## {index}. {snapshot.title}",
                        "",
                        snapshot.content.strip(),
                        "",
                        (
                            "> Provenance: accepted knowledge unit "
                            f"`{snapshot.unit_id}`, revision {snapshot.revision_number} "
                            f"(`{snapshot.revision_id}`); {snapshot.evidence_count} linked "
                            f"evidence item{'s' if snapshot.evidence_count != 1 else ''}."
                        ),
                    )
                )
            )
        return "\n".join(
            (
                f"# {title}",
                "",
                f"_{purpose}_",
                "",
                f"**Guiding question:** {question}",
                "",
                "\n\n".join(sections),
                "",
                "---",
                "",
                (
                    f"Generated locally by Gunther as a {format_label.lower()} from "
                    f"{len(snapshots)} accepted knowledge unit"
                    f"{'s' if len(snapshots) != 1 else ''}. Review before sharing."
                ),
            )
        ).strip()

    def list_artifacts(
        self, knowledge_base_id: str, workspace_id: str
    ) -> list[ArtifactSummaryOut]:
        with session_scope(self.sessions) as session:
            if session.get(KnowledgeBaseRecord, knowledge_base_id) is None:
                raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
            artifacts = session.scalars(
                select(Artifact)
                .where(
                    Artifact.knowledge_base_id == knowledge_base_id,
                    Artifact.workspace_id == workspace_id,
                )
                .order_by(Artifact.created_at.desc(), Artifact.version_number.desc())
            ).all()
            for artifact in artifacts:
                self._verified_artifact_payload(session, artifact)
            return [self._artifact_summary_out(artifact) for artifact in artifacts]

    def get_artifact(
        self, knowledge_base_id: str, artifact_id: str, workspace_id: str
    ) -> ArtifactOut:
        with session_scope(self.sessions) as session:
            artifact = session.scalar(
                select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.workspace_id == workspace_id,
                    Artifact.knowledge_base_id == knowledge_base_id,
                )
            )
            if artifact is None:
                raise LookupError(f"Artifact {artifact_id} was not found")
            return self._artifact_out(session, artifact)

    def create_artifact(
        self,
        knowledge_base_id: str,
        workspace_id: str,
        payload: CreateArtifactInput,
    ) -> ArtifactOut:
        request_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "workspaceId": workspace_id,
                    "knowledgeBaseId": knowledge_base_id,
                    "format": payload.format,
                    "audience": payload.audience,
                    "title": payload.title,
                    "acceptedUnitIds": payload.accepted_unit_ids,
                    "supersedesArtifactId": payload.supersedes_artifact_id,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            with session_scope(self.sessions) as session:
                workspace = session.get(WorkspaceIdentity, "primary")
                if workspace is None or workspace.workspace_id != workspace_id:
                    raise LookupError("The verified workspace identity was not found")
                knowledge_base = session.get(KnowledgeBaseRecord, knowledge_base_id)
                if knowledge_base is None:
                    raise LookupError(
                        f"Knowledge Base {knowledge_base_id} was not found"
                    )
                existing_request = session.scalar(
                    select(Artifact).where(
                        Artifact.workspace_id == workspace_id,
                        Artifact.client_request_id == payload.client_request_id,
                    )
                )
                if existing_request is not None:
                    if existing_request.request_fingerprint != request_fingerprint:
                        raise ArtifactConflictError(
                            "clientRequestId was already used for a different Artifact request"
                        )
                    return self._artifact_out(session, existing_request)

                units = session.scalars(
                    select(KnowledgeUnit)
                    .options(selectinload(KnowledgeUnit.revisions))
                    .where(
                        KnowledgeUnit.id.in_(payload.accepted_unit_ids),
                        KnowledgeUnit.knowledge_base_id == knowledge_base_id,
                        KnowledgeUnit.status == "trusted",
                    )
                ).all()
                units_by_id = {unit.id: unit for unit in units}
                missing = [
                    unit_id
                    for unit_id in payload.accepted_unit_ids
                    if unit_id not in units_by_id
                ]
                if missing:
                    raise ValueError(
                        "Artifacts can only use trusted Knowledge Units from this "
                        f"Knowledge Base; rejected: {', '.join(missing)}"
                    )

                snapshots: list[ArtifactUnitSnapshotOut] = []
                for unit_id in payload.accepted_unit_ids:
                    unit = units_by_id[unit_id]
                    head = next(
                        (
                            revision
                            for revision in unit.revisions
                            if revision.id == unit.head_revision_id
                        ),
                        None,
                    )
                    if head is None:
                        raise ValueError(f"Knowledge Unit {unit.id} has no head revision")
                    proposal = session.get(KnowledgeProposal, head.source_proposal_id)
                    if proposal is None:
                        raise ValueError(
                            f"Knowledge Unit {unit.id} has no source proposal"
                        )
                    source_message = session.get(SessionMessage, proposal.message_id)
                    citations = (
                        json.loads(source_message.citations_json or "[]")
                        if source_message
                        else []
                    )
                    snapshots.append(
                        ArtifactUnitSnapshotOut(
                            unit_id=unit.id,
                            revision_id=head.id,
                            revision_number=head.revision_number,
                            title=unit.title,
                            content=head.content,
                            content_hash=hashlib.sha256(
                                head.content.encode("utf-8")
                            ).hexdigest(),
                            source_proposal_id=head.source_proposal_id,
                            source_session_id=proposal.session_id,
                            source_message_id=proposal.message_id,
                            evidence_count=len(citations),
                        )
                    )
                snapshot_content_bytes = sum(
                    len(snapshot.content.encode("utf-8")) for snapshot in snapshots
                )
                if snapshot_content_bytes > 2_000_000:
                    raise ValueError(
                        "The accepted revision snapshot exceeds the 2,000,000-byte "
                        "Artifact limit"
                    )

                parent: Artifact | None = None
                if payload.supersedes_artifact_id:
                    parent = session.scalar(
                        select(Artifact).where(
                            Artifact.id == payload.supersedes_artifact_id,
                            Artifact.workspace_id == workspace_id,
                            Artifact.knowledge_base_id == knowledge_base_id,
                        )
                    )
                    if parent is None:
                        raise LookupError(
                            f"Artifact {payload.supersedes_artifact_id} was not found "
                            "in this Knowledge Base"
                        )
                    lineage_id = parent.lineage_id
                    lineage_head = session.scalar(
                        select(Artifact)
                        .where(
                            Artifact.lineage_id == lineage_id,
                            Artifact.workspace_id == workspace_id,
                            Artifact.knowledge_base_id == knowledge_base_id,
                        )
                        .order_by(Artifact.version_number.desc())
                        .limit(1)
                    )
                    if lineage_head is None or lineage_head.id != parent.id:
                        raise ArtifactConflictError(
                            "supersedesArtifactId is not the current Artifact lineage head"
                        )
                    version_number = parent.version_number + 1
                else:
                    lineage_id = _id("arl")
                    version_number = 1

                format_label = {
                    "field_guide": "Field guide",
                    "teaching_path": "Teaching path",
                    "decision_brief": "Decision brief",
                }[payload.format]
                title = payload.title or f"{knowledge_base.title} · {format_label}"
                content = self._compose_artifact_content(
                    title=title,
                    question=knowledge_base.question,
                    artifact_format=payload.format,
                    audience=payload.audience,
                    snapshots=snapshots,
                )
                if len(content.encode("utf-8")) > 2_500_000:
                    raise ValueError(
                        "The generated Artifact exceeds the 2,500,000-byte content limit"
                    )
                provenance = ArtifactProvenanceOut(
                    schema_version=1,
                    generator="gunther.local-template.v1",
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    knowledge_base_question=knowledge_base.question,
                    accepted_only=True,
                    accepted_unit_ids=payload.accepted_unit_ids,
                    revision_ids=[snapshot.revision_id for snapshot in snapshots],
                )
                accepted_unit_ids_json = json.dumps(
                    payload.accepted_unit_ids,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                revision_snapshot_json = json.dumps(
                    [snapshot.model_dump() for snapshot in snapshots],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                provenance_json = json.dumps(
                    provenance.model_dump(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                manifest_hash = hashlib.sha256(
                    json.dumps(
                        {
                            "format": payload.format,
                            "audience": payload.audience,
                            "title": title,
                            "contentHash": content_hash,
                            "acceptedUnitIds": payload.accepted_unit_ids,
                            "revisionSnapshot": json.loads(revision_snapshot_json),
                            "provenance": json.loads(provenance_json),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                artifact = Artifact(
                    id=_id("art"),
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    client_request_id=payload.client_request_id,
                    request_fingerprint=request_fingerprint,
                    lineage_id=lineage_id,
                    version_number=version_number,
                    supersedes_artifact_id=parent.id if parent else None,
                    format=payload.format,
                    audience=payload.audience,
                    title=title,
                    content=content,
                    content_hash=content_hash,
                    manifest_hash=manifest_hash,
                    accepted_unit_ids_json=accepted_unit_ids_json,
                    revision_snapshot_json=revision_snapshot_json,
                    provenance_json=provenance_json,
                )
                session.add(artifact)
                session.flush()
                for position, snapshot in enumerate(snapshots):
                    session.add(
                        ArtifactUnitBinding(
                            id=_id("aub"),
                            artifact_id=artifact.id,
                            position=position,
                            unit_id=snapshot.unit_id,
                            revision_id=snapshot.revision_id,
                            content_hash=snapshot.content_hash,
                        )
                    )
                session.flush()
                return self._artifact_out(session, artifact)
        except IntegrityError as error:
            with session_scope(self.sessions) as session:
                existing_request = session.scalar(
                    select(Artifact).where(
                        Artifact.workspace_id == workspace_id,
                        Artifact.client_request_id == payload.client_request_id,
                    )
                )
                if (
                    existing_request is not None
                    and existing_request.request_fingerprint == request_fingerprint
                ):
                    return self._artifact_out(session, existing_request)
            raise ArtifactConflictError(
                "Artifact version creation conflicted with another writer; refresh and retry"
            ) from error

    @staticmethod
    def _search_tokens(query: str) -> list[str]:
        raw_tokens = list(
            dict.fromkeys(re.findall(r"[\w-]+", query.casefold(), re.UNICODE))
        )
        if len(raw_tokens) <= 1:
            return raw_tokens[:8]
        intent_words = {
            "about",
            "are",
            "compare",
            "find",
            "for",
            "have",
            "i",
            "in",
            "latest",
            "learned",
            "me",
            "my",
            "notes",
            "our",
            "search",
            "show",
            "tell",
            "the",
            "what",
            "we",
            "where",
            "with",
        }
        topical = [token for token in raw_tokens if token not in intent_words]
        return (topical or raw_tokens)[:8]

    @staticmethod
    def _search_snippet(content: str, query: str, limit: int = 220) -> str:
        compact = re.sub(r"\s+", " ", content).replace("**", "").replace("`", "").strip()
        index = compact.casefold().find(query.casefold())
        if index < 0:
            index = next(
                (
                    candidate
                    for token in KnowledgeService._search_tokens(query)
                    if (candidate := compact.casefold().find(token.casefold())) >= 0
                ),
                -1,
            )
        start = max(0, index - 70) if index >= 0 else 0
        snippet = compact[start : start + limit]
        if start:
            snippet = f"…{snippet}"
        if start + limit < len(compact):
            snippet = f"{snippet}…"
        return snippet

    def search_knowledge(self, query: str, limit: int = 20) -> list[KnowledgeSearchResultOut]:
        needle = query.strip()
        tokens = self._search_tokens(needle)
        if not tokens:
            return []
        patterns = [
            f"%{token.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')}%"
            for token in tokens
        ]

        def match_all(*columns):
            return and_(
                *(
                    or_(*(column.ilike(pattern, escape="\\") for column in columns))
                    for pattern in patterns
                )
            )

        with session_scope(self.sessions) as session:
            results: list[KnowledgeSearchResultOut] = []
            units = (
                session.scalars(
                    select(KnowledgeUnit)
                    .options(selectinload(KnowledgeUnit.revisions))
                    .join(KnowledgeUnitRevision)
                    .where(match_all(KnowledgeUnit.title, KnowledgeUnitRevision.content))
                    .where(KnowledgeUnit.status != "deprecated")
                    .order_by(KnowledgeUnit.updated_at.desc())
                    .limit(limit)
                )
                .unique()
                .all()
            )
            for unit in units:
                detail = self._knowledge_unit_out(session, unit)
                results.append(
                    KnowledgeSearchResultOut(
                        id=unit.id,
                        knowledge_base_id=unit.knowledge_base_id,
                        kind="knowledge_unit",
                        title=unit.title,
                        snippet=self._search_snippet(detail.content, needle),
                        meta=f"{unit.status} · {_count_label(detail.revision_count, 'revision')}",
                        updated_at=_timestamp(unit.updated_at),
                        source_session_id=detail.source_session_id,
                        source_message_id=detail.source_message_id,
                    )
                )

            source_rows = session.execute(
                select(Source, KnowledgeBaseSource.knowledge_base_id)
                .join(KnowledgeBaseSource, KnowledgeBaseSource.source_id == Source.id)
                .where(match_all(Source.title, Source.content))
                .order_by(Source.created_at.desc())
                .limit(limit)
            ).all()
            for source, knowledge_base_id in source_rows:
                summary = self._source_summary(session, source)
                results.append(
                    KnowledgeSearchResultOut(
                        id=source.id,
                        knowledge_base_id=knowledge_base_id,
                        kind="source",
                        title=source.title,
                        snippet=self._search_snippet(source.content, needle),
                        meta=(
                            f"{_count_label(summary.assertion_count, 'claim')} · "
                            f"{_count_label(summary.entity_count, 'entity', 'entities')}"
                        ),
                        updated_at=_timestamp(source.created_at),
                    )
                )

            notebook_notes = session.scalars(
                select(NotebookNote)
                .where(
                    match_all(NotebookNote.title, NotebookNote.content),
                    NotebookNote.status != "archived",
                )
                .order_by(NotebookNote.updated_at.desc())
                .limit(limit)
            ).all()
            for note in notebook_notes:
                results.append(
                    KnowledgeSearchResultOut(
                        id=note.id,
                        knowledge_base_id=note.knowledge_base_id,
                        kind="note",
                        title=note.title,
                        snippet=self._search_snippet(note.content or note.title, needle),
                        meta="Notebook · " + ("filed" if note.status == "filed" else "unfiled"),
                        updated_at=_timestamp(note.updated_at),
                    )
                )

            knowledge_sessions = session.scalars(
                select(KnowledgeSession)
                .where(
                    match_all(KnowledgeSession.title, KnowledgeSession.summary),
                    KnowledgeSession.archived.is_(False),
                )
                .order_by(KnowledgeSession.updated_at.desc())
                .limit(limit)
            ).all()
            for knowledge_session in knowledge_sessions:
                results.append(
                    KnowledgeSearchResultOut(
                        id=knowledge_session.id,
                        knowledge_base_id=knowledge_session.knowledge_base_id,
                        kind="session",
                        title=knowledge_session.title,
                        snippet=self._search_snippet(
                            knowledge_session.summary or knowledge_session.title,
                            needle,
                        ),
                        meta="Conversation session",
                        updated_at=_timestamp(knowledge_session.updated_at),
                    )
                )

            return sorted(
                results,
                key=lambda item: (
                    needle.casefold() in item.title.casefold(),
                    sum(
                        token.casefold() in f"{item.title} {item.snippet}".casefold()
                        for token in tokens
                    ),
                    item.updated_at,
                ),
                reverse=True,
            )[:limit]

    def get_graph(self) -> KnowledgeGraphOut:
        with session_scope(self.sessions) as session:
            assertions = session.scalars(self._assertion_query()).all()
            counts: Counter[str] = Counter()
            entities: dict[str, Entity] = {}
            for assertion in assertions:
                entities[assertion.subject.id] = assertion.subject
                entities[assertion.object.id] = assertion.object
                counts[assertion.subject.id] += 1
                counts[assertion.object.id] += 1

            return KnowledgeGraphOut(
                nodes=[
                    GraphNodeOut(
                        id=entity.id,
                        label=entity.label,
                        type=entity.type,
                        assertion_count=counts[entity.id],
                    )
                    for entity in entities.values()
                ],
                edges=[
                    GraphEdgeOut(
                        id=assertion.id,
                        source=assertion.subject.id,
                        target=assertion.object.id,
                        label=assertion.predicate,
                        status=assertion.status,
                        confidence=assertion.confidence,
                    )
                    for assertion in assertions
                ],
            )

    def overview(self) -> OverviewOut:
        with session_scope(self.sessions) as session:
            counts = OverviewCountsOut(
                sources=session.scalar(select(func.count()).select_from(Source)) or 0,
                entities=session.scalar(select(func.count()).select_from(Entity)) or 0,
                assertions=session.scalar(select(func.count()).select_from(Assertion)) or 0,
                provisional=session.scalar(
                    select(func.count())
                    .select_from(Assertion)
                    .where(Assertion.status == "provisional")
                )
                or 0,
            )
            sources = session.scalars(
                select(Source).order_by(Source.created_at.desc()).limit(4)
            ).all()
            assertions = session.scalars(
                self._assertion_query().order_by(Assertion.created_at.desc()).limit(5)
            ).all()
            return OverviewOut(
                counts=counts,
                recent_sources=[self._source_summary(session, source) for source in sources],
                recent_assertions=[self._assertion_out(assertion) for assertion in assertions],
            )

    def seed_if_empty(self) -> None:
        seed_bases = (
            {
                "id": "single-cell-annotation",
                "title": "Single-cell RNA-seq Annotation",
                "eyebrow": "Computational biology",
                "subtitle": "From raw barcodes to defensible cell identities",
                "question": (
                    "How do we turn expression profiles into cell identities we can explain, "
                    "challenge, and reuse?"
                ),
                "description": (
                    "A living field guide connecting experimental context, data quality, "
                    "analytical choices, biological evidence, references, and uncertainty."
                ),
                "color": "green",
                "status": "Living",
            },
            {
                "id": "full-stack-development",
                "title": "Full-stack Development",
                "eyebrow": "Software engineering",
                "subtitle": "A system from interface to operations",
                "question": "How do the layers of a reliable product fit together?",
                "description": (
                    "Architecture, frontend, backend, data, delivery, and operations organized "
                    "as one coherent practice."
                ),
                "color": "blue",
                "status": "Growing",
            },
            {
                "id": "project-management",
                "title": "Project Management",
                "eyebrow": "Leadership",
                "subtitle": "Move uncertain work toward a useful outcome",
                "question": "How do we create clarity without pretending uncertainty is gone?",
                "description": (
                    "A practical knowledge base for framing, planning, coordination, risk, "
                    "delivery, and organizational learning."
                ),
                "color": "clay",
                "status": "Outline",
            },
        )
        with session_scope(self.sessions) as session:
            for payload in seed_bases:
                if not session.get(KnowledgeBaseRecord, payload["id"]):
                    session.add(KnowledgeBaseRecord(**payload))
            accepted_proposals = session.scalars(
                select(KnowledgeProposal).where(KnowledgeProposal.status == "accepted")
            ).all()
            for proposal in accepted_proposals:
                self._ensure_proposal_unit(session, proposal)
            source_count = session.scalar(select(func.count()).select_from(Source)) or 0
        if source_count:
            return

        result = self.import_source(
            CreateSourceInput(
                title="Starter knowledge",
                kind="note",
                content=(
                    "CD3D -> marker_of -> T cell\n"
                    "T cell -> is_a -> lymphocyte\n"
                    "Backpropagation -> depends_on -> chain rule\n"
                    "Gradient descent -> uses -> gradients"
                ),
                knowledge_base_id="single-cell-annotation",
            )
        )
        with session_scope(self.sessions) as session:
            assertions = session.scalars(
                select(Assertion).where(Assertion.source_id == result.source.id)
            ).all()
            for assertion in assertions:
                assertion.status = "verified"
