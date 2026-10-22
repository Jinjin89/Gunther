from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from gunther import artifact_agent, trace
from gunther import artifact_workspace as workspaces
from gunther import brief as briefs
from gunther.agent import (
    DEFAULT_STYLE,
    STYLES,
    AskAgent,
    Evidence,
    ReadResult,
    Tool,
    Toolbox,
    ToolFailure,
)
from gunther.agent import Notes as Work
from gunther.answer_runs import Stopped
from gunther.artifact_workspace import evidence_from_citation
from gunther.conversation import GroundingClaim, KnowledgeResponder
from gunther.database import session_scope
from gunther.extraction import CandidateAssertion, ExtractionResult, Extractor
from gunther.knowledge_index import KnowledgeIndex, enqueue
from gunther.llm import ModelError, ModelGateway, ModelInfo, Turn
from gunther.model_profiles import Effort
from gunther.models import (
    Artifact,
    ArtifactAsset,
    ArtifactCheck,
    ArtifactConversation,
    ArtifactMessage,
    ArtifactUnitBinding,
    Assertion,
    Asset,
    BlockElementMap,
    ContentBlock,
    DocumentElement,
    ElementAsset,
    Entity,
    EvidenceLink,
    Fragment,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    KnowledgeProposal,
    KnowledgeSession,
    KnowledgeUnit,
    KnowledgeUnitRevision,
    MessageTrace,
    NotebookNote,
    Revision,
    SessionBranch,
    SessionMessage,
    Source,
    SourceDigest,
    SourceIndexHead,
    SourcePaper,
    SourceRevision,
    WebSnapshot,
    WorkspaceIdentity,
    utc_now,
)
from gunther.online_search import DisabledOnlineSearch, OnlineSearchProvider
from gunther.outputs import (
    BuiltOutput,
    Issue,
    Notes,
    OutputAgents,
    OutputLimits,
    Planned,
    Pool,
    Spec,
    _marked,
    heading_of,
    section_hash,
    split_document,
    title_of,
)
from gunther.recording_metadata import recording_timing
from gunther.run_budget import BudgetedGateway, RunBudget
from gunther.schemas import (
    ArtifactBriefOut,
    ArtifactBriefStateOut,
    ArtifactChangeOut,
    ArtifactConversationOut,
    ArtifactConversationSummaryOut,
    ArtifactLineOut,
    ArtifactMessageOut,
    ArtifactOut,
    ArtifactPartOut,
    ArtifactPendingOut,
    ArtifactProvenanceOut,
    ArtifactQuestionOut,
    ArtifactSpecOut,
    ArtifactStepOut,
    ArtifactSummaryOut,
    ArtifactTurnInput,
    ArtifactTurnOut,
    ArtifactUnitSnapshotOut,
    ArtifactWorkOut,
    AssertionOut,
    AssetOut,
    BriefOut,
    BuildOutputInput,
    ConversationCitationOut,
    ConversationContextOut,
    ConversationTurnOut,
    CreateArtifactConversationInput,
    CreateKnowledgeBaseInput,
    CreateKnowledgeProposalInput,
    CreateKnowledgeSessionInput,
    CreateNotebookNoteInput,
    CreateSessionMessageInput,
    CreateSourceInput,
    EditArtifactBriefInput,
    EditOutputInput,
    EntityRefOut,
    EvidenceOut,
    FileNotebookNoteInput,
    FileNotebookNoteOut,
    FileSourceInput,
    FileSourceOut,
    GraphEdgeOut,
    GraphNodeOut,
    ImportArtifactConversationsInput,
    ImportCountsOut,
    ImportedArtifactConversation,
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
    OutlineItem,
    OutputInputsOut,
    OutputIssueOut,
    OutputModelOut,
    OutputProvenanceOut,
    OutputScope,
    OutputSectionOut,
    OverviewCountsOut,
    OverviewOut,
    ReviseOutputInput,
    SessionMessageOut,
    SourceDetailOut,
    SourceSummaryOut,
    UpdateArtifactConversationInput,
    UpdateAssertionStatusInput,
    UpdateKnowledgeBaseInput,
    UpdateKnowledgeProposalInput,
    UpdateKnowledgeSessionInput,
    UpdateNotebookNoteInput,
    WebSnapshotOut,
)
from gunther.skill_runner import Approach, SkillAgents
from gunther.skillbook import AskSkill, ask_skills, skill_for
from gunther.source_identity import source_fingerprint
from gunther.topics import topic_block_ids, topic_source_ids
from gunther.trash import live_membership_source_ids, trashed_library_ids

logger = logging.getLogger(__name__)

# (plan, check) effort for Ask's own steps; see AskAgent. The exam may override them.
ASK_EFFORTS: tuple[Effort, Effort] = ("low", "off")

# Sources found by meaning, added after the exact matches of a search.
MEANING_RESULTS = 6

NEW_CONVERSATION = "New conversation"
OUTPUTS_NEED_A_MODEL = "Outputs need a model. Set one up under Settings → Models."
NOTHING_TO_BUILD_FROM = (
    "There is nothing to build from yet. Add sources, or save an answer as knowledge."
)
# The largest an output's text may be, in bytes.
MAX_OUTPUT_BYTES = 2_500_000

# Home's conversations belong to no library: this stands in for the library id.
# Library ids come from slugs, which never contain "@".
HOME_SCOPE = "@home"

# Libraries at least this large also get paper-level answers (abstracts).
PAPER_OVERVIEW_MIN_SOURCES = 12
PAPER_OVERVIEWS = 4
# How much of a source Ask reads when it reads further: a library section, a web page.
READ_SECTION_CHARS = 6000
READ_PAGE_CHARS = 8000


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def manifest_hash_of(artifact: Artifact) -> str:
    """The hash that seals a version: what it says, what it cites and where it came from.

    Version 1 (the old builder) covers its format, audience, title, content and pinned
    knowledge. Version 2 (agents) also covers the kind, style, brief, origin, citations,
    outline, scope and inputs. ``scripts/backup_gunther.py`` repeats this.
    """

    sealed: dict[str, object] = {
        "audience": artifact.audience,
        "title": artifact.title,
        "contentHash": artifact.content_hash,
        "acceptedUnitIds": json.loads(artifact.accepted_unit_ids_json),
        "revisionSnapshot": json.loads(artifact.revision_snapshot_json),
        "provenance": json.loads(artifact.provenance_json),
    }
    if json.loads(artifact.provenance_json).get("schema_version") == 2:
        sealed.update(
            kind=artifact.kind,
            style=artifact.style,
            brief=artifact.brief,
            origin=artifact.origin,
            citations=json.loads(artifact.citations_json),
            outline=json.loads(artifact.outline_json),
            scope=json.loads(artifact.scope_json),
            inputs=json.loads(artifact.inputs_json),
        )
    else:
        sealed["format"] = artifact.format
    return hashlib.sha256(_canonical(sealed)).hexdigest()


def _timestamp(value: datetime) -> str:
    return f"{value.isoformat(timespec='milliseconds')}Z"


def _normalized_label(value: str) -> str:
    return " ".join(value.casefold().split())


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:120] or "knowledge-base"


def _count_label(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or f'{singular}s'}"


def _device_time(value: str | None) -> datetime | None:
    """A time a device wrote (ISO 8601, usually ending in Z) as the UTC time the database
    keeps; None when it is missing or cannot be read."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed


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


@dataclass(frozen=True)
class _LibraryScope:
    """What one search of a library may read: sources, their claims, a topic's passages."""

    source_ids: list[str]
    assertions: list[Assertion]
    # Set when the question is about one topic: only these passages may be quoted.
    block_scope: list[str] | None
    topic_evidence: dict


@dataclass
class _OutputScope:
    """What an output may be built from, found in the database."""

    asked: OutputScope
    # Every live source of the library (what a seed passage must belong to).
    library_source_ids: list[str]
    # The sources the library tool searches.
    source_ids: list[str]
    units: list[KnowledgeUnit]
    sessions: list[KnowledgeSession]


@dataclass(frozen=True, slots=True)
class _OwnedVersion:
    """A version, as much of it as tells which conversation it belongs to and what was asked."""

    id: str
    lineage_id: str
    created_at: datetime
    client_request_id: str
    scope_json: str
    conversation_title: str | None
    request: str | None


@dataclass
class _Answer:
    """What Ask answered with, and how."""

    content: str
    mode: str
    evidence: list[Evidence]
    model_ref: str | None = None
    model_label: str | None = None
    effort: str | None = None
    effort_label: str | None = None
    notes: tuple[str, ...] = ()
    reasoning: str | None = None
    error: str | None = None
    steps: list = field(default_factory=list)
    checked: bool = False
    support_notes: tuple[str, ...] = ()
    coverage: dict | None = None
    budget_usage: dict | None = None
    work: Work | None = None
    skill: AskSkill | None = None
    skill_auto: bool = False
    research: dict | None = None


# Reasoning is kept for the model that wrote it; past this it is cut.
MAX_KEPT_REASONING = 60_000
# Traces of the latest answers kept for Settings → Developer; older ones are dropped.
KEPT_TRACES = 200


def conversation_history(messages: list[SessionMessage]) -> list[Turn]:
    """A conversation in neutral form, for whichever model answers next."""

    turns: list[Turn] = []
    for message in messages:
        if message.role == "user":
            # A question that was stopped or failed has no answer; the model never saw it.
            if '"interrupted"' in (message.context_json or "") and json.loads(
                message.context_json
            ).get("interrupted"):
                continue
            turns.append(Turn("user", message.content))
        elif message.role == "assistant":
            context = json.loads(message.context_json or "{}")
            content = _in_pool_numbers(message)
            turns.append(Turn("assistant", content, context.get("model"), context.get("reasoning")))
    return turns


def _in_pool_numbers(message: SessionMessage) -> str:
    """An answer as the model should read it: its [1], [2]… (numbered per answer, in the
    order of its source list) turned back into the conversation pool's numbers, which
    are what the model is shown for every source. Answers from before the pool have no
    such numbers, so their markers are left out."""

    refs = [item.get("ref") for item in json.loads(message.citations_json or "[]")]

    def replace(match: re.Match[str]) -> str:
        found = []
        for part in re.split(r"[,，、]", match.group(3)):
            at = int(part) - 1
            if 0 <= at < len(refs) and refs[at] is not None:
                found.append(f"[{refs[at]}]")
        if not found:
            return ""
        if match.group(2) == "d":
            return f"{match.group(1) or ' '}(disputed by {''.join(found)})"
        return f"{match.group(1)}{''.join(found)}"

    # [i:2] (an inference) and [p:2] (partly supported) are read as plain pool numbers;
    # [d:2] says the claim was disputed by that source.
    return re.sub(r"(\s?)\[(?:(i|p|d):)?(\d+(?:\s*[,，、]\s*\d+)*)\]", replace, message.content)


def _work_out(work: Work | None, cited: list[Evidence]) -> dict[str, object] | None:
    """What a question found, for the saved answer: a finding about a source the answer
    cites says its reading number (the one in the text); any other keeps its title."""

    if work is None:
        return None
    reading = {item.ref: number for number, item in enumerate(cited, start=1)}
    return {
        "subQuestions": [
            {"id": sub.id, "text": sub.text, "query": sub.query} for sub in work.sub_questions
        ],
        "findings": [
            {
                **({"ref": reading[f.ref]} if f.ref in reading else {"title": f.title}),
                "serves": f.serves,
                "says": f.says,
            }
            for f in work.findings
        ],
    }


def _around(text: str, snippet: str, limit: int = READ_PAGE_CHARS) -> str:
    """At most ``limit`` characters of a page, centred on where the search result's
    snippet is when it can be found, else from the start."""

    if len(text) <= limit:
        return text
    at = text.find(snippet[:60]) if snippet[:60].strip() else -1
    start = 0 if at < 0 else max(0, min(at - limit // 2, len(text) - limit))
    return text[start : start + limit]


def conversation_pool(messages: list[SessionMessage]) -> list[Evidence]:
    """The sources a conversation holds: everything its answers cited, each under the
    number it was given the first time."""

    held: dict[int, Evidence] = {}
    for message in messages:
        if message.role != "assistant":
            continue
        for item in json.loads(message.citations_json or "[]"):
            ref = item.get("ref")
            if ref is None or ref in held:
                continue
            held[ref] = evidence_from_citation(ConversationCitationOut.model_validate(item))
    return [held[ref] for ref in sorted(held)]


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
        # Every model that is set up (see llm); a conversation may pick any of them.
        self.models: ModelGateway | None = None
        self.web_search: OnlineSearchProvider = DisabledOnlineSearch()
        # Settings → Developer: keep how each answer is made (see trace).
        self.tracing = False
        # Settings → Developer: outputs are made by following a skill (see skill_runner);
        # off, by the agents in outputs alone.
        self.output_skills = True
        self.output_method = "skills"  # "studio": gunther/studio makes Outputs
        self.slide_renderer = None
        # Let Ask pick a skill itself from the message (settings.ask_auto_skills).
        self.auto_skills = False
        # (plan effort, check effort) for Ask's own steps; the exam sets it, nothing else.
        self.agent_efforts: tuple[Effort, Effort] | None = None
        # Reads a public web page for the agent: URL -> (text, title). Set by the app; it
        # applies the same public-address rules as capturing a page.
        self.page_reader: Callable[[str], tuple[str, str | None]] | None = None
        self.index = KnowledgeIndex(sessions)
        self._source_import_locks_guard = Lock()
        self._source_import_locks: dict[str, tuple[Lock, int]] = {}
        # How far one turn of the Artifacts agent may go; the exam and tests may change it.
        self.artifact_limits = artifact_agent.AgentLimits()
        # Rows of Artifacts conversations are made one request at a time (listing makes the
        # missing ones), so two requests for the same conversation cannot both make it.
        self._artifact_conversations_lock = Lock()

    @property
    def extraction_mode(self) -> str:
        return self.extractor.mode

    @staticmethod
    def _live_knowledge_base(
        session: Session, knowledge_base_id: str
    ) -> KnowledgeBaseRecord | None:
        """A library that exists and is not in Trash."""

        knowledge_base = session.get(KnowledgeBaseRecord, knowledge_base_id)
        return knowledge_base if knowledge_base and knowledge_base.trashed_at is None else None

    def _live_assertion_query(self):
        """Claims whose source is not in Trash."""

        return self._assertion_query().where(
            Assertion.source_id.not_in(select(Source.id).where(Source.trashed_at.is_not(None)))
        )

    def _knowledge_base_out(
        self, session: Session, knowledge_base: KnowledgeBaseRecord
    ) -> KnowledgeBaseOut:
        source_count = (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeBaseSource)
                .join(Source, Source.id == KnowledgeBaseSource.source_id)
                .where(
                    KnowledgeBaseSource.knowledge_base_id == knowledge_base.id,
                    Source.trashed_at.is_(None),
                )
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
            created_at=_timestamp(knowledge_base.created_at),
            updated_at=_timestamp(knowledge_base.updated_at),
        )

    def list_knowledge_bases(self) -> list[KnowledgeBaseOut]:
        with session_scope(self.sessions) as session:
            knowledge_bases = session.scalars(
                select(KnowledgeBaseRecord)
                .where(KnowledgeBaseRecord.trashed_at.is_(None))
                .order_by(KnowledgeBaseRecord.updated_at.desc())
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
            knowledge_base = self._live_knowledge_base(session, knowledge_base_id)
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
        duration, duration_pending = (
            recording_timing(session, source.content)
            if source.kind in {"recording", "course"} else (None, False)
        )
        return SourceSummaryOut(
            id=source.id,
            title=source.title,
            kind=source.kind,
            created_at=_timestamp(source.created_at),
            assertion_count=len(assertions),
            entity_count=len(entity_ids),
            processing=self.index.processing(session, source.id),
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
            extraction_mode=self.extractor.mode,
            duplicate=True,
        )

    def import_source(
        self,
        payload: CreateSourceInput,
        *,
        initialize_source: Callable[[Session, Source], None] | None = None,
        defer_processing: bool = False,
        defer_analysis: bool = False,
    ) -> ImportResultOut:
        content = payload.content.strip()
        content_hash = source_fingerprint(payload.title, payload.kind, content)

        with self._serialize_source_import(content_hash):
            return self._import_source(
                payload,
                content,
                content_hash,
                initialize_source=initialize_source,
                defer_processing=defer_processing,
                defer_analysis=defer_analysis,
            )

    def _import_source(
        self,
        payload: CreateSourceInput,
        content: str,
        content_hash: str,
        *,
        initialize_source: Callable[[Session, Source], None] | None = None,
        defer_processing: bool = False,
        defer_analysis: bool = False,
    ) -> ImportResultOut:

        with session_scope(self.sessions) as session:
            if (
                payload.knowledge_base_id
                and self._live_knowledge_base(session, payload.knowledge_base_id) is None
            ):
                raise LookupError(f"Knowledge Base {payload.knowledge_base_id} was not found")
            existing = session.scalar(select(Source).where(Source.content_hash == content_hash))
            if existing:
                return self._duplicate_import_result(
                    session,
                    existing,
                    payload.knowledge_base_id,
                )

        extraction = (
            ExtractionResult(assertions=[], mode=self.extractor.mode)
            if defer_processing or defer_analysis
            else self.extractor.extract(payload.title, content)
        )

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
                    self._ensure_source_memberships(session, payload.knowledge_base_id, [source.id])

                if not defer_processing and session.get(SourceIndexHead, source.id) is None:
                    self.index.add_source(session, source)

                if defer_analysis:
                    # Commit the source, its readable index and the analysis job together.
                    # Saving a capture must never wait for a model over the network.
                    enqueue(
                        session, source.id, "extract_assertions",
                        f"extract_assertions:{source.id}:{source.content_hash}",
                    )
                created_entities = self.store_source_extraction(session, source, extraction)

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
                existing = session.scalar(select(Source).where(Source.content_hash == content_hash))
                if existing is None:
                    raise
                return self._duplicate_import_result(
                    session,
                    existing,
                    payload.knowledge_base_id,
                )

    def store_source_extraction(
        self, session: Session, source: Source, extraction: ExtractionResult
    ) -> int:
        """Publish assertions and their exact evidence inside the caller's transaction."""

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
                    action="created", actor=f"extractor:{extraction.mode}",
                    reason="Imported from source evidence",
                )
            )
        return created_entities

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

    @staticmethod
    def _inbox_preview(content: str, limit: int = 240) -> str:
        preview = re.sub(r"\s+", " ", content).strip()
        return preview[: limit - 1] + "…" if len(preview) > limit else preview

    @classmethod
    def _table_preview(cls, content: str) -> str | None:
        """Summarise pasted rows by shape; collapsing whitespace would merge their cells."""

        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if len(lines) < 2:
            return None
        for delimiter in ("\t", "|", ";", ","):
            cells = [cell.strip() for cell in lines[0].strip("|").split(delimiter)]
            if len(cells) >= 2:
                break
        else:
            return None
        rows = sum(1 for line in lines[1:] if not re.fullmatch(r"[\s|:\-]+", line))
        columns = ", ".join(cell for cell in cells if cell)
        return cls._inbox_preview(f"{rows} {'row' if rows == 1 else 'rows'} · Columns: {columns}")

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
                for knowledge_base in session.scalars(
                    select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.trashed_at.is_(None))
                ).all()
            }

            # Filed means filed in a library that is not in Trash.
            unfiled_sources = session.scalars(
                select(Source).where(
                    Source.id.not_in(live_membership_source_ids()),
                    Source.trashed_at.is_(None),
                )
            ).all()
            review_sources = session.scalars(
                select(Source)
                .join(Assertion, Assertion.source_id == Source.id)
                .where(
                    Source.id.in_(live_membership_source_ids()),
                    Source.trashed_at.is_(None),
                    Assertion.status == "provisional",
                )
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
                        preview=(source.kind == "table" and self._table_preview(source.content))
                        or self._inbox_preview(source.content),
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
                        preview=(source.kind == "table" and self._table_preview(source.content))
                        or self._inbox_preview(source.content),
                        source_kind=source.kind,
                        knowledge_bases=source_knowledge_bases[source.id],
                        source_id=source.id,
                        assertion_count=provisional_counts.get(source.id, 0),
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )

            notes = session.scalars(
                select(NotebookNote).where(
                    NotebookNote.status == "inbox", NotebookNote.trashed_at.is_(None)
                )
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

            if state:
                items = [item for item in items if item.state == state]
            if item_type:
                items = [item for item in items if item.item_type == item_type]
            return sorted(items, key=lambda item: item.updated_at, reverse=True)

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
                select(Source)
                .where(Source.id.in_(source_ids), Source.trashed_at.is_(None))
                .order_by(Source.created_at.desc())
            ).all()
            return [self._source_summary(session, source) for source in sources]

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
            if (
                knowledge_base_id != HOME_SCOPE
                and self._live_knowledge_base(session, knowledge_base_id) is None
            ):
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
                brief=self._brief_out(briefs.load(knowledge_session.brief_json)),
            )

    @staticmethod
    def _brief_out(stored: briefs.Stored) -> BriefOut:
        return BriefOut(
            goal=stored.brief.goal,
            constraints=stored.brief.constraints,
            settled=stored.brief.settled,
            open=stored.brief.open,
            edited=stored.edited,
            error=stored.error,
        )

    def _brief_session(self, session: Session, session_id: str) -> KnowledgeSession:
        knowledge_session = session.scalar(
            self._session_query().where(KnowledgeSession.id == session_id)
        )
        if knowledge_session is None:
            raise LookupError(f"Session {session_id} was not found")
        return knowledge_session

    def brief(self, session_id: str) -> BriefOut:
        with session_scope(self.sessions) as session:
            knowledge_session = self._brief_session(session, session_id)
            return self._brief_out(briefs.load(knowledge_session.brief_json))

    @staticmethod
    def _last_exchange(
        messages: list[SessionMessage],
    ) -> tuple[SessionMessage, SessionMessage] | None:
        """The latest answer and the question it answers."""

        for at in range(len(messages) - 1, 0, -1):
            if messages[at].role == "assistant" and messages[at - 1].role == "user":
                return messages[at - 1], messages[at]
        return None

    def _catch_up_brief(self, session_id: str) -> None:
        """Fold the last answer into the brief before the next question, when the page
        never asked for it (closed too soon, or the update failed)."""

        try:
            with session_scope(self.sessions) as session:
                knowledge_session = self._brief_session(session, session_id)
                last = self._last_exchange(knowledge_session.messages)
                behind = (
                    last is not None
                    and briefs.load(knowledge_session.brief_json).through != last[1].id
                )
            if behind:
                self.refresh_brief(session_id)
        except LookupError:
            return  # the turn itself reports a session that is not there
        except Exception:
            # The brief is a help, never a reason a question cannot be answered.
            logger.exception("Catching up the brief of %s failed", session_id)

    def refresh_brief(self, session_id: str) -> BriefOut:
        """Bring the brief up to date with the conversation's last answer. A failed
        update keeps the old brief and records why."""

        with session_scope(self.sessions) as session:
            knowledge_session = self._brief_session(session, session_id)
            stored = briefs.load(knowledge_session.brief_json)
            last = self._last_exchange(knowledge_session.messages)
            if last is None or stored.through == last[1].id:
                return self._brief_out(stored)
            question, answered = last[0].content, last[1]
            answer = _in_pool_numbers(answered)
            answered_id = answered.id
            used = json.loads(answered.context_json or "{}").get("model")
            pool_refs = {
                item.ref for item in conversation_pool(knowledge_session.messages) if item.ref
            }
            supported: list[str] = []
            verified_refs: set[int] = set()
            for message in knowledge_session.messages:
                context = json.loads(message.context_json or "{}")
                coverage = context.get("coverage") or {}
                if (
                    coverage.get("answerHash")
                    != hashlib.sha256(message.content.encode()).hexdigest()
                ):
                    continue
                for claim in coverage.get("claims", []):
                    if claim.get("verdict") == "supported" and not re.search(
                        r"\[(?:[ipd]:|\?)", claim.get("text", "")
                    ):
                        supported.append(claim["text"])
                        verified_refs.update(claim.get("refs", []))
            pool_refs &= verified_refs
        chosen = self.models.for_role("ask") if self.models else None
        model = chosen[0] if chosen else (self.models.get(used) if self.models else None)
        if model is None or self.models is None:
            stored.error = "Set up a model under Settings → Models to keep a brief."
            return self._save_brief(session_id, stored, expect=stored.brief)
        _, check_effort = self.agent_efforts or ASK_EFFORTS
        updated = briefs.BriefKeeper(self.models, check_effort).update(
            model, stored, question, answer, pool_refs, supported_claims=supported
        )
        if updated.error is None:
            updated.through = answered_id
        return self._save_brief(session_id, updated, expect=stored.brief, was=stored.edited)

    def _save_brief(
        self,
        session_id: str,
        stored: briefs.Stored,
        expect: briefs.Brief,
        was: list[str] | None = None,
    ) -> BriefOut:
        """Keep a brief, unless the user edited it while a model was working on it: then
        theirs stays and the next question catches up."""

        with session_scope(self.sessions) as session:
            knowledge_session = self._brief_session(session, session_id)
            current = briefs.load(knowledge_session.brief_json)
            if current.brief != expect or (was is not None and current.edited != was):
                return self._brief_out(current)
            knowledge_session.brief_json = briefs.dump(stored)
            return self._brief_out(stored)

    def edit_brief(self, session_id: str, brief: briefs.Brief) -> BriefOut:
        """Save the user's edit; the lines they changed are theirs from now on."""

        with session_scope(self.sessions) as session:
            knowledge_session = self._brief_session(session, session_id)
            before = briefs.load(knowledge_session.brief_json)
            tidy = briefs.Brief(
                goal=" ".join(brief.goal.split()),
                constraints=briefs.unique(brief.constraints),
                settled=[
                    item.model_copy(update={"text": " ".join(item.text.split())})
                    for item in brief.settled
                    if item.text.strip()
                ],
                open=briefs.unique(brief.open),
            )
            stored = briefs.Stored(
                tidy, briefs.edited_paths(before, tidy), before.through, before.error
            )
            knowledge_session.brief_json = briefs.dump(stored)
            return self._brief_out(stored)

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

    def file_home_session(
        self, session_id: str, knowledge_base_id: str
    ) -> KnowledgeSessionSummaryOut:
        """Move a Home conversation into a library; it keeps its messages and citations."""

        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            if knowledge_session is None or knowledge_session.knowledge_base_id != HOME_SCOPE:
                raise LookupError(f"Home conversation {session_id} was not found")
            if self._live_knowledge_base(session, knowledge_base_id) is None:
                raise LookupError(f"Knowledge Base {knowledge_base_id} was not found")
            knowledge_session.knowledge_base_id = knowledge_base_id
            knowledge_session.selected_source_ids_json = "[]"
            knowledge_session.focus_chapter_id = None
            knowledge_session.updated_at = utc_now()
            session.flush()
            return self._session_summary(knowledge_session)

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
            if " " in normalized or (len(normalized) > 2 and normalized not in stopwords):
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

    def _web_tool(self, wanted: bool) -> tuple[Tool | None, str | None]:
        """The web as a tool for one question, or why it is not offered."""

        if not self.web_search.available:
            return None, "web search is not set up (Settings → Web search)"
        if not wanted:
            return None, "the Web toggle is off for this message"
        provider = self.web_search

        def search_web(query: str) -> list[Evidence]:
            result = provider.search(query)
            if result.mode == "failed":
                raise ToolFailure(result.message or "Web search failed.")
            found = []
            for source in result.sources:
                host = urlsplit(source.url).netloc.removeprefix("www.")
                text = source.snippet or source.title
                citation = ConversationCitationOut(
                    id=_id("cit"),
                    kind="web",
                    url=source.url,
                    source_id="",
                    source_title=source.title,
                    quote=text,
                    locator=host,
                    status="provisional",
                    confidence=0.0,
                )
                found.append(
                    Evidence(
                        kind="web",
                        title=source.title,
                        text=text,
                        locator=host,
                        url=source.url,
                        payload=(citation, None),
                    )
                )
            return found

        return Tool(
            "search_web",
            "the public web, for facts outside the library",
            search_web,
            "the web",
        ), None

    def _answer(
        self,
        question: str,
        history: list[Turn],
        toolbox: Toolbox,
        model: ModelInfo | None,
        effort: Effort | None,
        events: Callable[[dict], None] | None = None,
        pool: list[Evidence] | None = None,
        style: str | None = None,
        numbered: int = 0,
        brief: str = "",
        skill: AskSkill | None = None,
        budget: str | None = None,
        stopping: Callable[[], bool] | None = None,
    ) -> _Answer:
        """Run the agent with the chosen model (or the Ask default) and describe the outcome."""

        if model is None and self.models is not None:
            chosen = self.models.for_role("ask")
            if chosen is not None:
                model, default_effort = chosen
                effort = effort or default_effort
        if model is None or self.models is None:
            library_tool = toolbox.get("search_library")
            found = library_tool.run(question) if library_tool else []
            claims = [item.payload[1] for item in found]
            content = (
                self.responder.respond(question, claims, history).content
                if claims
                else "Ask needs a language model to answer. Set one up under Settings → Models."
            )
            return _Answer(
                content=content,
                mode="local",
                evidence=found,
                error="Ask needs a language model. Set one up under Settings → Models.",
            )
        plan_effort, check_effort = self.agent_efforts or ASK_EFFORTS
        agent = AskAgent(self.models, plan_effort=plan_effort, check_effort=check_effort)
        result = agent.run(
            question,
            history,
            toolbox,
            model,
            effort,
            events,
            pool or (),
            style,
            numbered,
            brief=brief,
            skill=skill,
            budget_name=budget,
            skills=ask_skills() if self.auto_skills and skill is None else None,
            stopping=stopping,
        )
        about = {
            "model_ref": model.ref,
            "model_label": model.display,
            "effort": effort,
            "steps": result.steps,
            "notes": result.notes,
            "skill": result.skill,
            "skill_auto": result.skill_auto,
            "research": result.research,
        }
        if result.error is not None:
            # The chosen model could not write the answer: say so; gathered quotes stand in.
            library = [item for item in result.evidence if item.kind == "library"]
            claims = [item.payload[1] for item in library]
            content = (
                self.responder.respond(question, claims, history).content
                if claims
                else f"I couldn't get an answer from {model.display}. Try again, or pick "
                "another model."
            )
            return _Answer(
                content=content, mode="local", evidence=library, error=result.error, **about
            )
        completion = result.completion
        return _Answer(
            content=result.content,
            mode="model",
            evidence=result.evidence,
            checked=result.checked,
            support_notes=result.support_notes,
            coverage=result.coverage,
            budget_usage=result.budget_usage,
            work=result.work,
            **{**about, "effort_label": completion.effort_label if completion else None},
            reasoning=completion.reasoning if completion else None,
        )

    def _home_source_ids(
        self,
        session: Session,
        selected_source_ids: list[str],
        library_ids: list[str] | None,
    ) -> list[str]:
        """What a Home question may read: every live library's sources and unfiled
        captures, or only the libraries the person pointed at with ``@``."""

        live = select(KnowledgeBaseRecord.id).where(KnowledgeBaseRecord.trashed_at.is_(None))
        alive = select(Source.id).where(Source.trashed_at.is_(None))
        if library_ids:
            query = select(KnowledgeBaseSource.source_id).where(
                KnowledgeBaseSource.knowledge_base_id.in_(library_ids),
                KnowledgeBaseSource.knowledge_base_id.in_(live),
                KnowledgeBaseSource.source_id.in_(alive),
            )
        else:
            filed = select(KnowledgeBaseSource.source_id).where(
                KnowledgeBaseSource.knowledge_base_id.in_(live)
            )
            anywhere = select(KnowledgeBaseSource.source_id)
            query = select(Source.id).where(
                Source.trashed_at.is_(None),
                or_(Source.id.in_(filed), Source.id.not_in(anywhere)),
            )
        found = list(dict.fromkeys(session.scalars(query)))
        if selected_source_ids:
            chosen = set(selected_source_ids)
            return [item for item in found if item in chosen]
        return found

    def _library_scope(
        self, session: Session, source_ids: list[str], block_scope: list[str] | None = None
    ) -> _LibraryScope:
        """The claims and passages a search over these sources may use.

        ``block_scope`` limits it to the passages of one topic (see topics).
        """

        assertions = list(
            session.scalars(
                self._assertion_query()
                .order_by(Assertion.status.asc(), Assertion.confidence.desc())
                .where(Assertion.source_id.in_(source_ids))
            ).all()
        )
        topic_evidence = {}
        if block_scope is not None:
            topic_evidence = {
                (revision.source_id, block.content): block
                for block, revision in session.execute(
                    select(ContentBlock, SourceRevision)
                    .join(SourceRevision, SourceRevision.id == ContentBlock.revision_id)
                    .where(ContentBlock.id.in_(block_scope))
                    .order_by(ContentBlock.id)
                )
            }
        return _LibraryScope(source_ids, assertions, block_scope, topic_evidence)

    def _library_tool(self, session: Session, scope: _LibraryScope, about: str) -> Tool:
        """Searching the library as a tool. Whoever uses it numbers what it finds."""

        def search_library(query: str) -> list[Evidence]:
            """The library's passages and claims for one search, numbered by the agent."""

            found: list[Evidence] = []
            ranked_assertions = self._rank_assertions(scope.assertions, query)
            citations: list[ConversationCitationOut] = []
            claims: list[GroundingClaim] = []
            for assertion in ranked_assertions:
                evidence = next(
                    (
                        item
                        for item in assertion.evidence_links
                        if item.fragment.source_id == assertion.source_id
                        and (
                            scope.block_scope is None
                            or (assertion.source_id, item.fragment.content) in scope.topic_evidence
                        )
                    ),
                    None,
                )
                if evidence is None:
                    continue
                if scope.block_scope is not None:
                    evidence_block = scope.topic_evidence[
                        (assertion.source_id, evidence.fragment.content)
                    ]
                else:
                    evidence_block = session.scalar(
                        select(ContentBlock)
                        .join(
                            SourceIndexHead, SourceIndexHead.revision_id == ContentBlock.revision_id
                        )
                        .where(
                            SourceIndexHead.source_id == assertion.source.id,
                            ContentBlock.content == evidence.fragment.content,
                        )
                        .limit(1)
                    )
                citation = ConversationCitationOut(
                    id=_id("cit"),
                    source_id=assertion.source.id,
                    source_title=assertion.source.title,
                    assertion_id=assertion.id,
                    quote=evidence.fragment.content,
                    locator=evidence.fragment.locator,
                    status=assertion.status,
                    confidence=assertion.confidence,
                    source_revision_id=evidence_block.revision_id if evidence_block else None,
                    block_id=evidence_block.id if evidence_block else None,
                    anchor=json.loads(evidence_block.anchor_json) if evidence_block else {},
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

            # Original passages participate even when an extracted claim matched.
            # Competing evidence must not disappear behind an assertion-only fallback.
            seen_quotes = {(c.source_id, c.quote) for c in citations}
            for hit in self.index.retrieve(
                session,
                scope.source_ids,
                query,
                block_ids=scope.block_scope,
            ):
                if (hit.source.id, hit.block.content) in seen_quotes:
                    continue
                seen_quotes.add((hit.source.id, hit.block.content))
                citations.append(
                    ConversationCitationOut(
                        id=_id("cit"),
                        source_id=hit.source.id,
                        source_title=hit.source.title,
                        quote=hit.block.content,
                        locator=hit.block.locator,
                        status="provisional",
                        confidence=0.0,
                        source_revision_id=hit.block.revision_id,
                        block_id=hit.block.id,
                        anchor=json.loads(hit.block.anchor_json),
                    )
                )
                claims.append(
                    GroundingClaim(
                        subject=hit.source.title,
                        predicate="states",
                        object=hit.block.content,
                        source_title=hit.source.title,
                        quote=hit.block.content,
                        locator=hit.block.locator,
                        status="provisional",
                        confidence=0.0,
                    )
                )

            # In a large library a broad question needs the papers, not only the few
            # passages that match best: add the abstracts of the closest papers.
            if scope.block_scope is None and len(scope.source_ids) >= PAPER_OVERVIEW_MIN_SOURCES:
                for paper, block in self.index.nearest_papers(
                    session,
                    scope.source_ids,
                    query,
                    limit=PAPER_OVERVIEWS,
                    exclude={c.source_id for c in citations},
                ):
                    source = session.get(Source, paper.source_id)
                    citations.append(
                        ConversationCitationOut(
                            id=_id("cit"),
                            source_id=source.id,
                            source_title=source.title,
                            quote=paper.abstract[:900],
                            locator="Abstract",
                            status="provisional",
                            confidence=0.0,
                            source_revision_id=paper.revision_id,
                            block_id=block.id if block else None,
                            anchor=json.loads(block.anchor_json) if block else {},
                        )
                    )
                    claims.append(
                        GroundingClaim(
                            subject=paper.title,
                            predicate="is summarized as",
                            object=paper.abstract[:900],
                            source_title=source.title,
                            quote=paper.abstract[:900],
                            locator="Abstract",
                            status="provisional",
                            confidence=0.0,
                        )
                    )

            for citation, claim in zip(citations, claims, strict=True):
                paper = session.get(SourcePaper, citation.source_id)
                if paper and (paper.doi or paper.arxiv_id):
                    citation.work_identifier = (
                        f"doi:{paper.doi.casefold()}" if paper.doi else f"arxiv:{paper.arxiv_id}"
                    )
                citation.figures = self._nearby_figures(session, citation)
                found.append(
                    Evidence(
                        kind="library",
                        title=citation.source_title,
                        text=citation.quote,
                        locator=citation.locator,
                        status=citation.status,
                        confidence=citation.confidence,
                        payload=(citation, claim),
                    )
                )
            return found

        return Tool(
            "search_library",
            f"the user's library ({about}), {len(scope.source_ids)} sources",
            search_library,
            "your library",
            grade=True,
        )

    def _output_search_tool(
        self, session: Session, base_id: str, primary: list[str], supplements: bool, about: str
    ) -> Tool:
        allowed = (
            self._resolve_output_scope(session, base_id, OutputScope()).source_ids
            if supplements
            else primary
        )
        tool = self._library_tool(session, self._library_scope(session, allowed), about)
        primary_ids = set(primary)

        def search(query: str) -> list[Evidence]:
            found = []
            for item in tool.run(query):
                citation, claim = item.payload
                origin = "selected" if citation.source_id in primary_ids else "library_supplement"
                found.append(
                    replace(
                        item, payload=(citation.model_copy(update={"source_origin": origin}), claim)
                    )
                )
            return sorted(found, key=lambda item: item.payload[0].source_id not in primary_ids)

        return replace(
            tool,
            run=search,
            about=tool.about + "; selected materials are primary; additions are labeled",
        )

    @staticmethod
    def _nearby_figures(session: Session, citation: ConversationCitationOut) -> list[dict]:
        if not citation.block_id or not citation.source_revision_id:
            return []
        element = session.scalar(
            select(DocumentElement)
            .join(BlockElementMap)
            .where(
                BlockElementMap.block_id == citation.block_id,
                DocumentElement.revision_id == citation.source_revision_id,
            )
        )
        if element is None:
            return []
        figures = []
        for figure in session.scalars(
            select(DocumentElement)
            .where(
                DocumentElement.revision_id == element.revision_id,
                DocumentElement.kind == "figure",
                DocumentElement.ordinal.between(element.ordinal - 4, element.ordinal + 4),
            )
            .order_by(DocumentElement.ordinal)
            .limit(4)
        ):
            payload, anchor = json.loads(figure.payload_json), json.loads(figure.anchor_json)
            asset_id = payload.get("imageAssetId")
            if not asset_id or session.get(Asset, asset_id) is None:
                continue
            if (
                anchor.get("page")
                and citation.anchor.get("page")
                and anchor["page"] != citation.anchor["page"]
            ):
                continue
            figures.append(
                {
                    "assetId": asset_id,
                    "elementId": figure.id,
                    "caption": payload.get("caption") or figure.content,
                    "page": anchor.get("page"),
                    "revisionId": element.revision_id,
                }
            )
        return figures

    def create_session_turn(
        self,
        session_id: str,
        payload: CreateSessionMessageInput,
        events: Callable[[dict], None] | None = None,
        stopping: Callable[[], bool] | None = None,
    ) -> ConversationTurnOut:
        # Refused before anything runs.
        skill = None
        if payload.skill:
            skill = ask_skills().get(payload.skill.strip().lstrip("/"))
            if skill is None:
                raise ValueError(f"Unknown skill: /{payload.skill.strip().lstrip('/')}")
        self._catch_up_brief(session_id)
        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            library = (
                session.get(KnowledgeBaseRecord, knowledge_session.knowledge_base_id)
                if knowledge_session
                else None
            )
            if knowledge_session is None or (library and library.trashed_at is not None):
                raise LookupError(f"Session {session_id} was not found")
            if knowledge_session.archived:
                raise ValueError("Restore the archived session before adding another message")
            # A conversation may pick any model that is set up, and switch at any turn.
            model = None
            if payload.model:
                model = self.models.get(payload.model) if self.models else None
                if model is None:
                    raise ValueError(
                        "That model is not set up, or its provider needs a key. "
                        "Choose another in the model menu."
                    )

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
            if knowledge_session.knowledge_base_id == HOME_SCOPE:
                scoped_source_ids = self._home_source_ids(
                    session, selected_source_ids, payload.knowledge_base_ids
                )
            else:
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
                # Recheck membership on every turn, including persisted session scope.
                scoped_source_ids = list(
                    session.scalars(
                        select(KnowledgeBaseSource.source_id).where(
                            KnowledgeBaseSource.knowledge_base_id
                            == knowledge_session.knowledge_base_id,
                            KnowledgeBaseSource.source_id.in_(scoped_source_ids),
                            KnowledgeBaseSource.source_id.not_in(
                                select(Source.id).where(Source.trashed_at.is_not(None))
                            ),
                        )
                    )
                )
            block_scope = None
            if knowledge_session.focus_chapter_id and knowledge_session.focus_chapter_id.startswith(
                "topic_"
            ):
                base_id = knowledge_session.knowledge_base_id
                topic_id = knowledge_session.focus_chapter_id
                try:
                    block_scope = topic_block_ids(session, base_id, topic_id)
                    filed = topic_source_ids(session, base_id, topic_id)
                except LookupError:
                    # The section is gone: ask the whole library.
                    block_scope, filed = None, []
                if filed:
                    # A topic of whole papers: ask all of them, and the sources of any
                    # passages linked to it one by one.
                    linked = set(filed) | set(
                        session.scalars(
                            select(SourceRevision.source_id)
                            .join(ContentBlock, ContentBlock.revision_id == SourceRevision.id)
                            .where(ContentBlock.id.in_(block_scope))
                        )
                    )
                    scoped_source_ids = [item for item in scoped_source_ids if item in linked]
                    block_scope = None
            scope = self._library_scope(session, scoped_source_ids, block_scope)
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
            available_assertions = scope.assertions

            web_tool, web_off_reason = self._web_tool(payload.web)
            about = f"{library.title}: {library.question}" if library else "all your libraries"
            offered = []
            notes = []
            if scoped_source_ids:
                offered.append(self._library_tool(session, scope, about))
            else:
                notes.append("there is no library to search here")
            if web_tool:
                offered.append(web_tool)
            else:
                notes.append(f"the web was not searched: {web_off_reason}")
            toolbox = Toolbox(
                tuple(offered),
                tuple(notes),
                reader=self._ask_reader(session, web_tool is not None),
            )
            history = conversation_history(knowledge_session.messages)
            stored = briefs.load(knowledge_session.brief_json)
            question = payload.content.strip()
            everything = conversation_pool(knowledge_session.messages)
            # Only what this question may read: a library source that is out of scope now
            # (another library was picked, a source was removed) stays out of the answer.
            in_scope = set(scoped_source_ids)
            pool = [
                item
                for item in everything
                if item.kind != "library" or item.payload[0].source_id in in_scope
            ]
            # Settings → Developer: keep how this answer is made, step by step.
            kept = trace.Trace() if self.tracing else None
            with trace.tracing(kept):
                response = self._answer(
                    question,
                    history,
                    toolbox,
                    model,
                    payload.effort,
                    events,
                    pool,
                    payload.style,
                    max((item.ref or 0 for item in everything), default=0),
                    brief=briefs.render(stored.brief, stored.edited),
                    skill=skill,
                    budget=payload.budget,
                    stopping=stopping,
                )
            citations = [
                item.payload[0].model_copy(update={"ref": item.ref}) for item in response.evidence
            ]
            context = ConversationContextOut(
                sources_considered=len(scoped_sources),
                assertions_considered=len(available_assertions),
                verified_assertions=sum(item.status == "verified" for item in available_assertions),
                retrieval_mode="selected" if selected_source_ids else "all",
                responder_mode=response.mode,
                model=response.model_ref,
                model_label=response.model_label,
                effort=response.effort,
                effort_label=response.effort_label,
                notes=list(response.notes),
                reasoning=(response.reasoning or "")[:MAX_KEPT_REASONING] or None,
                model_error=response.error,
                style=payload.style if payload.style in STYLES else DEFAULT_STYLE,
                steps=[step.out() for step in response.steps],
                web_searched=any(step.tool == "search_web" for step in response.steps),
                checked=response.checked,
                support_notes=list(response.support_notes),
                coverage=response.coverage,
                budget_usage=response.budget_usage,
                work=_work_out(response.work, response.evidence),
                skill=(
                    {
                        "name": response.skill.name,
                        "version": response.skill.version,
                        "title": response.skill.title,
                        "auto": response.skill_auto,
                    }
                    if response.skill
                    else None
                ),
                research=response.research,
            )
            if events:
                events({"type": "saving"})  # a reader who left by now gets nothing saved
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
            if kept is not None:
                self._keep_trace(session, assistant_message.id, kept)
            return ConversationTurnOut(
                session=self._session_summary(knowledge_session),
                user_message=self._message_out(user_message),
                assistant_message=self._message_out(assistant_message),
            )

    def _keep_trace(self, session: Session, message_id: str, kept: trace.Trace) -> None:
        """Save how an answer was made; only the latest traces stay."""

        text = json.dumps(kept.out(), ensure_ascii=False)
        session.add(MessageTrace(message_id=message_id, trace_json=text))
        session.flush()
        stale = session.scalars(
            select(MessageTrace.message_id)
            .order_by(MessageTrace.created_at.desc())
            .offset(KEPT_TRACES)
        ).all()
        if stale:
            session.execute(delete(MessageTrace).where(MessageTrace.message_id.in_(stale)))

    def message_trace(self, session_id: str, message_id: str) -> dict | None:
        """How an answer in this conversation was made, if it was kept."""

        with session_scope(self.sessions) as session:
            row = session.scalar(
                select(MessageTrace)
                .join(SessionMessage, SessionMessage.id == MessageTrace.message_id)
                .where(
                    MessageTrace.message_id == message_id,
                    SessionMessage.session_id == session_id,
                )
            )
            return json.loads(row.trace_json) if row else None

    def record_interrupted_question(
        self, session_id: str, content: str, reason: Literal["stopped", "failed"]
    ) -> None:
        """Keep a question that got no answer in the history, marked as such."""

        text = content.strip()
        if not text:
            return
        with session_scope(self.sessions) as session:
            knowledge_session = session.scalar(
                self._session_query().where(KnowledgeSession.id == session_id)
            )
            if knowledge_session is None or knowledge_session.archived:
                return
            now = utc_now()
            knowledge_session.messages.append(
                SessionMessage(
                    id=_id("msg"),
                    session_id=knowledge_session.id,
                    role="user",
                    content=text,
                    context_json=ConversationContextOut(interrupted=reason).model_dump_json(),
                    created_at=now,
                )
            )
            if knowledge_session.title == "New session":
                title = re.sub(r"\s+", " ", text)
                knowledge_session.title = title[:62] + ("…" if len(title) > 62 else "")
            knowledge_session.updated_at = now

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

    def _set_proposal_status(
        self,
        session: Session,
        proposal: KnowledgeProposal,
        status: str,
        reason: str | None,
    ) -> KnowledgeUnitRevision | None:
        """Record a decision on a saved answer and keep its knowledge unit in step."""

        previous = proposal.status
        proposal.status = status
        proposal.decision_reason = reason
        proposal.updated_at = utc_now()
        unit_revision = self._proposal_unit_revision(session, proposal.id)
        if status == "accepted" and unit_revision is None:
            unit_revision = self._ensure_proposal_unit(session, proposal)
        elif unit_revision is not None:
            knowledge_unit = session.get(KnowledgeUnit, unit_revision.unit_id)
            if knowledge_unit:
                knowledge_unit.status = {
                    "accepted": "trusted",
                    "rejected": "deprecated",
                }.get(status, "provisional")
                knowledge_unit.updated_at = utc_now()
        session.add(
            Revision(
                id=_id("rev"),
                target_type="proposal",
                target_id=proposal.id,
                action=f"status:{previous}->{status}",
                actor="user",
                reason=reason,
            )
        )
        session.flush()
        return unit_revision

    def ensure_saved_knowledge_units(self) -> None:
        """Give every saved answer its knowledge unit.

        Saving makes the unit at once, but databases from before that hold saved answers
        without one, so this runs on every start.
        """

        with session_scope(self.sessions) as session:
            without_unit = session.scalars(
                select(KnowledgeProposal).where(
                    KnowledgeProposal.status == "accepted",
                    KnowledgeProposal.id.not_in(select(KnowledgeUnitRevision.source_proposal_id)),
                )
            ).all()
            for proposal in without_unit:
                self._ensure_proposal_unit(session, proposal)

    def create_knowledge_proposal(
        self,
        session_id: str,
        message_id: str,
        payload: CreateKnowledgeProposalInput,
    ) -> KnowledgeProposalOut:
        """Save an answer as knowledge. This is the only review step: it counts at once."""

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
                if existing.status == "accepted":
                    unit_revision = self._proposal_unit_revision(
                        session, existing.id
                    ) or self._ensure_proposal_unit(session, existing)
                else:
                    # Saved again after it was removed from knowledge.
                    unit_revision = self._set_proposal_status(
                        session, existing, "accepted", "Saved as knowledge again"
                    )
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
            unit_revision = self._set_proposal_status(
                session, proposal, "accepted", "Saved as knowledge"
            )
            return self._proposal_out(proposal, knowledge_session, unit_revision)

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
            unit_revision = self._set_proposal_status(
                session, proposal, payload.status, payload.reason
            )
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
        provenance = json.loads(artifact.provenance_json)
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
            kind=artifact.kind,
            style=artifact.style,
            origin=artifact.origin,
            conversation_id=provenance.get("conversation_id"),
            conversation_title=provenance.get("conversation_title"),
            client_request_id=artifact.client_request_id or None,
            request=provenance.get("request"),
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
            # Version 2 is an output built by agents; version 1 is the old builder's.
            provenance: ArtifactProvenanceOut = (
                OutputProvenanceOut
                if json.loads(artifact.provenance_json).get("schema_version") == 2
                else ArtifactProvenanceOut
            ).model_validate(json.loads(artifact.provenance_json))
        except Exception as error:
            raise ArtifactIntegrityError(
                f"Artifact {artifact.id} contains invalid immutable provenance"
            ) from error
        agents = isinstance(provenance, OutputProvenanceOut)

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
            # The old builder used accepted knowledge only; agents read sources as well.
            or provenance.accepted_only == agents
        ):
            raise ArtifactIntegrityError(
                f"Artifact {artifact.id} provenance failed its integrity check"
            )
        if isinstance(provenance, OutputProvenanceOut) and (
            provenance.kind != artifact.kind
            or artifact.format != artifact.kind
            or provenance.style != artifact.style
            or provenance.audience != artifact.audience
            or provenance.brief != artifact.brief
            or provenance.origin != artifact.origin
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

        manifest_hash = manifest_hash_of(artifact)
        if not secrets.compare_digest(manifest_hash, artifact.manifest_hash):
            raise ArtifactIntegrityError(
                f"Artifact {artifact.id} manifest failed its integrity check"
            )
        return snapshots, provenance

    @classmethod
    def _artifact_out(cls, session: Session, artifact: Artifact) -> ArtifactOut:
        snapshots, provenance = cls._verified_artifact_payload(session, artifact)
        summary = cls._artifact_summary_out(artifact)
        scope = json.loads(artifact.scope_json)
        inputs = json.loads(artifact.inputs_json)
        return ArtifactOut(
            **summary.model_dump(),
            content=artifact.content,
            unit_snapshots=snapshots,
            provenance=provenance,
            brief=artifact.brief,
            outline=[
                OutlineItem.model_validate(item) for item in json.loads(artifact.outline_json)
            ],
            citations=[
                ConversationCitationOut.model_validate(item)
                for item in json.loads(artifact.citations_json)
            ],
            scope=OutputScope.model_validate(scope) if scope else None,
            inputs=OutputInputsOut(
                sources=len(inputs.get("sources", [])),
                units=len(inputs.get("units", [])),
                sessions=len(inputs.get("sessions", [])),
            ),
            model_label=(
                provenance.model.label
                if isinstance(provenance, OutputProvenanceOut) and provenance.model
                else None
            ),
            sections=cls._output_sections(session, artifact),
        )

    @staticmethod
    def _latest_checks(session: Session, artifact_id: str) -> dict[str, list[Issue]]:
        """What the Checker found in each section of a version, by the section's hash."""

        return {
            digest: [Issue(**issue) for issue in item["issues"]]
            for digest, item in KnowledgeService._latest_check_records(session, artifact_id).items()
        }

    @staticmethod
    def _latest_check_records(session: Session, artifact_id: str) -> dict[str, dict]:
        row = session.scalar(
            select(ArtifactCheck)
            .where(ArtifactCheck.artifact_id == artifact_id)
            .order_by(ArtifactCheck.created_at.desc(), ArtifactCheck.id.desc())
            .limit(1)
        )
        if row is None:
            return {}
        return {item["hash"]: item for item in json.loads(row.checks_json).get("sections", [])}

    @classmethod
    def _output_sections(cls, session: Session, artifact: Artifact) -> list[OutputSectionOut]:
        """The sections of a version, and whether the Checker has looked at each as it is now."""

        if artifact.origin == "legacy":
            return []
        known = cls._latest_checks(session, artifact.id)
        records = cls._latest_check_records(session, artifact.id)
        sections = []
        for index, text in enumerate(split_document(artifact.content, artifact.kind)[1]):
            issues = known.get(section_hash(text))
            coverage = records.get(section_hash(text), {}).get("coverage")
            sections.append(
                OutputSectionOut(
                    index=index,
                    heading=heading_of(text),
                    checked=issues is not None
                    and (coverage is None or coverage.get("status") == "complete"),
                    issues=[OutputIssueOut(**issue.out()) for issue in issues or []],
                    coverage=coverage,
                )
            )
        return sections

    def list_artifacts(self, knowledge_base_id: str, workspace_id: str) -> list[ArtifactSummaryOut]:
        with session_scope(self.sessions) as session:
            if self._live_knowledge_base(session, knowledge_base_id) is None:
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
            shown = self._artifact_out(session, artifact)
            skill = getattr(shown.provenance, "skill", None)
            if (skill and skill.render_check and skill.render_check.get("status") == "captured"
                    and self.slide_renderer
                    and not self.slide_renderer.current(skill.render_check)):
                reason = "The renderer or source figures changed after this review."
                skill.render_check = {**skill.render_check, "status": "stale", "reason": reason}
                if skill.visual_review:
                    skill.visual_review = {
                        **skill.visual_review, "status": "stale", "reason": reason}
            return shown

    def library_title(self, base_id: str) -> str:
        """A library's title, or its id when there is none."""

        with session_scope(self.sessions) as session:
            library = session.get(KnowledgeBaseRecord, base_id)
            return library.title if library else base_id

    def artifact_kind(self, base_id: str, artifact_id: str) -> str:
        """``slides`` or ``report``: what this version of an output is."""

        with session_scope(self.sessions) as session:
            artifact = session.scalar(
                select(Artifact).where(
                    Artifact.id == artifact_id, Artifact.knowledge_base_id == base_id
                )
            )
            if artifact is None:
                raise LookupError(f"Artifact {artifact_id} was not found")
            return artifact.kind

    def session_library(self, session_id: str) -> str | None:
        """The library a conversation belongs to (``@home`` for Home), None when missing."""

        with session_scope(self.sessions) as session:
            return session.scalar(
                select(KnowledgeSession.knowledge_base_id).where(KnowledgeSession.id == session_id)
            )

    # Outputs built by agents (see outputs) --------------------------------------------------

    def _output_model(
        self, model_ref: str | None = None, effort: Effort | None = None
    ) -> tuple[ModelInfo, Effort]:
        """The model that writes outputs: the Outputs job's, else Ask's."""

        chosen = None
        if self.models is not None:
            if model_ref:
                model = self.models.get(model_ref)
                if model is None:
                    raise ValueError(
                        "The selected model is not available. Choose a configured model."
                    )
                return model, effort or "high"
            chosen = self.models.for_role("outputs") or self.models.for_role("ask")
        if chosen is None:
            raise ValueError(OUTPUTS_NEED_A_MODEL)
        return chosen[0], effort or chosen[1]

    @staticmethod
    def _output_request_fields(payload: BuildOutputInput | ReviseOutputInput) -> dict:
        fields = payload.model_dump(mode="json", exclude={"client_request_id"})
        # Requests made before the conversation UI retain their original fingerprint.
        for name in ("model", "effort", "conversation_id", "conversation_title", "theme"):
            if fields.get(name) is None:
                fields.pop(name, None)
        return fields

    @staticmethod
    def _output_fingerprint(
        workspace_id: str, base_id: str, operation: str, request: dict[str, object]
    ) -> str:
        return hashlib.sha256(
            _canonical(
                {
                    "workspaceId": workspace_id,
                    "knowledgeBaseId": base_id,
                    "operation": operation,
                    "request": request,
                }
            )
        ).hexdigest()

    def _earlier_output(
        self, workspace_id: str, client_request_id: str, fingerprint: str
    ) -> ArtifactOut | None:
        """The version an earlier try of this same request already saved, if there is one."""

        with session_scope(self.sessions) as session:
            existing = session.scalar(
                select(Artifact).where(
                    Artifact.workspace_id == workspace_id,
                    Artifact.client_request_id == client_request_id,
                )
            )
            if existing is None:
                return None
            if existing.request_fingerprint != fingerprint:
                raise ArtifactConflictError(
                    "clientRequestId was already used for a different Artifact request"
                )
            return self._artifact_out(session, existing)

    def _output_library(
        self, session: Session, workspace_id: str, base_id: str
    ) -> KnowledgeBaseRecord:
        workspace = session.get(WorkspaceIdentity, "primary")
        if workspace is None or workspace.workspace_id != workspace_id:
            raise LookupError("The verified workspace identity was not found")
        library = self._live_knowledge_base(session, base_id)
        if library is None:
            raise LookupError(f"Knowledge Base {base_id} was not found")
        return library

    @staticmethod
    def _output_version(
        session: Session, workspace_id: str, base_id: str, artifact_id: str, *, latest: bool
    ) -> Artifact:
        """A version of an output. With ``latest``, only the newest of its lineage will do:
        that is the one that can be built on."""

        found = session.scalar(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.workspace_id == workspace_id,
                Artifact.knowledge_base_id == base_id,
            )
        )
        if found is None:
            raise LookupError(f"Artifact {artifact_id} was not found in this Knowledge Base")
        if latest:
            head = session.scalar(
                select(Artifact)
                .where(
                    Artifact.lineage_id == found.lineage_id,
                    Artifact.workspace_id == workspace_id,
                    Artifact.knowledge_base_id == base_id,
                )
                .order_by(Artifact.version_number.desc())
                .limit(1)
            )
            if head is None or head.id != found.id:
                raise ArtifactConflictError(
                    "That is not the latest version of this output. Open the latest one."
                )
        return found

    @staticmethod
    def _require_agent_output(artifact: Artifact) -> None:
        if artifact.origin == "legacy":
            raise ValueError(
                "This version came from the old builder. Build a new output to change it."
            )

    def _resolve_output_scope(
        self, session: Session, base_id: str, scope: OutputScope, *, strict: bool = True
    ) -> _OutputScope:
        """What an output may be built from. The whole library is every live source and every
        saved answer; a selection is what was picked. With ``strict``, a pick that is not
        in this library (or was removed since) is an error; otherwise it is left out."""

        members = list(
            session.scalars(
                select(KnowledgeBaseSource.source_id)
                .join(Source, Source.id == KnowledgeBaseSource.source_id)
                .where(
                    KnowledgeBaseSource.knowledge_base_id == base_id,
                    Source.trashed_at.is_(None),
                )
                .order_by(Source.created_at.desc())
            )
        )
        units = (
            select(KnowledgeUnit)
            .options(selectinload(KnowledgeUnit.revisions))
            .where(KnowledgeUnit.knowledge_base_id == base_id, KnowledgeUnit.status == "trusted")
        )
        if scope.mode == "library":
            found = list(session.scalars(units.order_by(KnowledgeUnit.updated_at.desc())))
            return _OutputScope(scope, members, members, found, [])
        in_library = set(members)
        found_units = {
            unit.id: unit
            for unit in session.scalars(units.where(KnowledgeUnit.id.in_(scope.unit_ids)))
        }
        found_sessions = {
            item.id: item
            for item in session.scalars(
                self._session_query().where(
                    KnowledgeSession.id.in_(scope.session_ids),
                    KnowledgeSession.knowledge_base_id == base_id,
                )
            )
        }
        if strict:
            for wanted, have, what in (
                (scope.source_ids, in_library, "sources"),
                (scope.unit_ids, found_units, "saved answers"),
                (scope.session_ids, found_sessions, "discussions"),
            ):
                gone = [item for item in wanted if item not in have]
                if gone:
                    raise ValueError(
                        f"These {what} are not in this library, or were removed: {', '.join(gone)}"
                    )
        return _OutputScope(
            scope,
            members,
            [item for item in scope.source_ids if item in in_library],
            [found_units[item] for item in scope.unit_ids if item in found_units],
            [found_sessions[item] for item in scope.session_ids if item in found_sessions],
        )

    @staticmethod
    def _unit_head(
        session: Session, unit: KnowledgeUnit
    ) -> tuple[KnowledgeUnitRevision, KnowledgeProposal, SessionMessage | None]:
        """A saved answer's current text, the proposal it came from, and the answer itself."""

        head = next((item for item in unit.revisions if item.id == unit.head_revision_id), None)
        if head is None:
            raise ValueError(f"Knowledge Unit {unit.id} has no head revision")
        proposal = session.get(KnowledgeProposal, head.source_proposal_id)
        if proposal is None:
            raise ValueError(f"Knowledge Unit {unit.id} has no source proposal")
        return head, proposal, session.get(SessionMessage, proposal.message_id)

    def _unit_snapshots(
        self, session: Session, units: list[KnowledgeUnit]
    ) -> list[ArtifactUnitSnapshotOut]:
        """The saved answers an output pins, each as it reads now."""

        snapshots = []
        for unit in units:
            head, proposal, message = self._unit_head(session, unit)
            snapshots.append(
                ArtifactUnitSnapshotOut(
                    unit_id=unit.id,
                    revision_id=head.id,
                    revision_number=head.revision_number,
                    title=unit.title,
                    content=head.content,
                    content_hash=hashlib.sha256(head.content.encode("utf-8")).hexdigest(),
                    source_proposal_id=head.source_proposal_id,
                    source_session_id=proposal.session_id,
                    source_message_id=proposal.message_id,
                    evidence_count=(
                        len(json.loads(message.citations_json or "[]")) if message else 0
                    ),
                )
            )
        if sum(len(item.content.encode("utf-8")) for item in snapshots) > 2_000_000:
            raise ValueError(
                "The saved answers chosen are larger than the 2,000,000-byte limit; choose fewer"
            )
        return snapshots

    def _output_material(
        self, session: Session, resolved: _OutputScope, limits: OutputLimits
    ) -> tuple[Notes, list[Evidence], dict[str, list[dict[str, object]]]]:
        """Notes for the planner and writer, the passages that saved answers and discussions
        cite (to seed the pool), and a record of what the output could use."""

        in_library = set(resolved.library_source_ids)
        seeds: list[Evidence] = []

        def take(messages: list[SessionMessage]) -> None:
            for item in conversation_pool(messages):
                # A passage of a source that has left this library is not the library's now.
                if item.kind == "library" and item.payload[0].source_id not in in_library:
                    continue
                seeds.append(replace(item, ref=None))

        knowledge: list[tuple[str, str]] = []
        unit_inputs: list[dict[str, object]] = []
        for unit in resolved.units:
            head, _, message = self._unit_head(session, unit)
            knowledge.append((unit.title, head.content))
            unit_inputs.append({"id": unit.id, "revisionId": head.id})
            if message is not None:
                take([message])
        discussions: list[tuple[str, str]] = []
        session_inputs: list[dict[str, object]] = []
        for item in resolved.sessions:
            discussions.append((item.title, item.summary))
            answers = [
                message
                for message in item.messages
                if message.role == "assistant" and json.loads(message.citations_json or "[]")
            ]
            session_inputs.append(
                {"id": item.id, "title": item.title, "messageIds": [m.id for m in answers]}
            )
            take(list(item.messages))
        sources = (
            list(
                session.scalars(
                    select(Source)
                    .where(Source.id.in_(resolved.source_ids))
                    .order_by(Source.created_at.desc())
                )
            )
            if resolved.source_ids
            else []
        )
        shown = [source.id for source in sources[:40]]
        digests = (
            dict(
                session.execute(
                    select(SourceDigest.source_id, SourceDigest.overview).where(
                        SourceDigest.source_id.in_(shown)
                    )
                ).all()
            )
            if shown
            else {}
        )
        heads = (
            dict(
                session.execute(
                    select(SourceIndexHead.source_id, SourceIndexHead.revision_id).where(
                        SourceIndexHead.source_id.in_([source.id for source in sources])
                    )
                ).all()
            )
            if sources
            else {}
        )
        notes = Notes(
            knowledge=tuple(knowledge[:20]),
            discussions=tuple(discussions[:10]),
            sources=tuple(
                (source.title, digests.get(source.id) or source.content[:300])
                for source in sources[:40]
            ),
        )
        inputs = {
            "sources": [
                {"id": source.id, "title": source.title, "revisionId": heads.get(source.id)}
                for source in sources
            ],
            "units": unit_inputs,
            "sessions": session_inputs,
        }
        return notes, seeds, inputs

    def _studio_reading(
        self, session: Session, source_ids: list[str], pool: Pool, *, most: int = 900
    ) -> None:
        """Every selected source read whole into the pool, in its own order, as passages of
        consecutive blocks under one heading (about 1,400 characters each). The studio reads
        the material, not search results; each passage stays citable like a search hit."""

        target = 1400
        sources = list(session.scalars(select(Source).where(Source.id.in_(source_ids))))
        heads = dict(
            session.execute(
                select(SourceIndexHead.source_id, SourceIndexHead.revision_id).where(
                    SourceIndexHead.source_id.in_(source_ids)
                )
            ).all()
        )
        for source in sorted(sources, key=lambda item: item.created_at):
            revision_id = heads.get(source.id)
            if not revision_id:
                continue
            blocks = list(
                session.scalars(
                    select(ContentBlock)
                    .where(ContentBlock.revision_id == revision_id)
                    .order_by(ContentBlock.ordinal)
                )
            )
            groups: list[list[ContentBlock]] = []
            size = 0
            for block in blocks:
                if not block.content.strip():
                    continue
                fresh = (
                    not groups
                    or block.kind == "heading"
                    or block.heading_path_json != groups[-1][-1].heading_path_json
                    and groups[-1][-1].kind != "heading"
                    or size + len(block.content) > target
                )
                if fresh:
                    groups.append([])
                    size = 0
                groups[-1].append(block)
                size += len(block.content) + 2
            for group in groups:
                if len(pool) >= most:
                    return
                snapshot = self._read_snapshot(group, 6000)
                if not snapshot.text.strip():
                    continue
                path = json.loads(group[0].heading_path_json or "[]")
                locator = " › ".join(str(part) for part in path if part) or group[0].locator
                citation = ConversationCitationOut(
                    id=_id("cit"),
                    source_id=source.id,
                    source_title=source.title,
                    quote=snapshot.text,
                    locator=locator[:240],
                    status="provisional",
                    confidence=1.0,
                    source_revision_id=revision_id,
                    block_id=group[0].id,
                    anchor=json.loads(group[0].anchor_json or "{}"),
                    support_text=snapshot.text,
                    support_spans=snapshot.spans,
                    source_origin="selected",
                )
                pool.adopt(
                    Evidence(
                        kind="library",
                        title=source.title,
                        text=snapshot.text,
                        locator=locator[:240],
                        status="provisional",
                        confidence=1.0,
                        payload=(citation, None),
                    )
                )

    def _output_pool(self, seeds: list[Evidence], limits: OutputLimits) -> Pool:
        pool = Pool(limits.pool)
        for item in seeds:
            if len(pool) >= limits.seed:
                break
            pool.adopt(item)
        return pool

    def _save_output(
        self,
        *,
        workspace_id: str,
        base_id: str,
        client_request_id: str,
        fingerprint: str,
        parent_id: str | None,
        kind: str,
        style: str | None,
        audience: str,
        title: str,
        brief: str,
        origin: str,
        content: str,
        scope: dict[str, object],
        inputs: dict[str, object],
        outline: list[dict[str, object]],
        citations: list[dict[str, object]],
        snapshots: list[ArtifactUnitSnapshotOut],
        model: OutputModelOut | None,
        checks: list[dict[str, object]],
        made_by: dict[str, object] | None = None,
    ) -> ArtifactOut:
        """Write a new version of an output, sealed with its hashes, and what the Checker found.

        ``made_by`` is for a version made by a skill: its generator, skill, approach and use.
        """

        try:
            with session_scope(self.sessions) as session:
                library = self._output_library(session, workspace_id, base_id)
                if parent_id:
                    parent = self._output_version(
                        session, workspace_id, base_id, parent_id, latest=True
                    )
                    lineage_id, version_number = parent.lineage_id, parent.version_number + 1
                else:
                    parent, lineage_id, version_number = None, _id("arl"), 1
                if len(content.encode("utf-8")) > MAX_OUTPUT_BYTES:
                    raise ValueError(
                        f"The output is larger than the {MAX_OUTPUT_BYTES:,}-byte limit"
                    )
                skilled = made_by or {}
                if kind == "slides" and isinstance(skilled.get("skill"), dict):
                    from gunther.slide_review import invalidate_reviews

                    skilled = {**skilled, "skill": invalidate_reviews(
                        skilled["skill"], content, outline, citations, skilled.get("use"))}
                parent_provenance = json.loads(parent.provenance_json) if parent else {}
                conversation_id = skilled.get("conversation_id") or parent_provenance.get(
                    "conversation_id"
                )
                if (
                    parent_provenance.get("conversation_id")
                    and conversation_id != parent_provenance["conversation_id"]
                ):
                    raise ValueError("This artifact belongs to a different conversation.")
                provenance = OutputProvenanceOut(
                    schema_version=2,
                    generator=str(skilled.get("generator") or "gunther.output-agents.v1"),
                    workspace_id=workspace_id,
                    knowledge_base_id=base_id,
                    knowledge_base_question=library.question,
                    accepted_only=False,
                    accepted_unit_ids=[item.unit_id for item in snapshots],
                    revision_ids=[item.revision_id for item in snapshots],
                    kind=kind,
                    style=style,
                    audience=audience,
                    brief=brief,
                    origin=origin,
                    model=model,
                    skill=skilled.get("skill"),
                    approach=skilled.get("approach"),
                    use=skilled.get("use"),
                    library_supplements=bool(skilled.get("library_supplements", False)),
                    conversation_id=conversation_id,
                    conversation_title=skilled.get("conversation_title")
                    or parent_provenance.get("conversation_title"),
                    request=skilled.get("request") if conversation_id else None,
                    reply=skilled.get("reply") if conversation_id else None,
                    changes=skilled.get("changes"),
                )
                sealed = provenance.model_dump()
                if skilled.get("work") is not None:
                    # The artifact's work as it was made: sealed with the version, not sent out.
                    sealed["work"] = skilled["work"]
                for key in (
                    "skill",
                    "approach",
                    "use",
                    "conversation_id",
                    "conversation_title",
                    "request",
                    "reply",
                    "changes",
                ):
                    # A version made without a skill keeps the provenance it always had.
                    if sealed.get(key) is None:
                        sealed.pop(key, None)
                artifact = Artifact(
                    id=_id("art"),
                    workspace_id=workspace_id,
                    knowledge_base_id=base_id,
                    client_request_id=client_request_id,
                    request_fingerprint=fingerprint,
                    lineage_id=lineage_id,
                    version_number=version_number,
                    supersedes_artifact_id=parent.id if parent else None,
                    format=kind,
                    audience=audience,
                    title=(title or library.title)[:160],
                    content=content,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    manifest_hash="",
                    accepted_unit_ids_json=json.dumps(
                        [item.unit_id for item in snapshots],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    revision_snapshot_json=json.dumps(
                        [item.model_dump() for item in snapshots],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    provenance_json=json.dumps(
                        sealed,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    kind=kind,
                    style=style,
                    brief=brief,
                    origin=origin,
                    scope_json=json.dumps(scope, ensure_ascii=False, separators=(",", ":")),
                    inputs_json=json.dumps(inputs, ensure_ascii=False, separators=(",", ":")),
                    outline_json=json.dumps(outline, ensure_ascii=False, separators=(",", ":")),
                    citations_json=json.dumps(citations, ensure_ascii=False, separators=(",", ":")),
                )
                artifact.manifest_hash = manifest_hash_of(artifact)
                session.add(artifact)
                session.flush()
                cited_revisions = [
                    c.get("source_revision_id") or c.get("sourceRevisionId") for c in citations
                ]
                retained_images = set(
                    session.scalars(
                        select(ElementAsset.asset_id)
                        .join(DocumentElement)
                        .where(DocumentElement.revision_id.in_([r for r in cited_revisions if r]))
                    )
                )
                requested_images = set(re.findall(r"gunther-asset:([A-Za-z0-9_-]+)", content))
                if requested_images - retained_images:
                    raise ValueError("An output figure must belong to its cited materials.")
                for asset_id in retained_images:
                    session.add(ArtifactAsset(artifact_id=artifact.id, asset_id=asset_id))
                for position, item in enumerate(snapshots):
                    session.add(
                        ArtifactUnitBinding(
                            id=_id("aub"),
                            artifact_id=artifact.id,
                            position=position,
                            unit_id=item.unit_id,
                            revision_id=item.revision_id,
                            content_hash=item.content_hash,
                        )
                    )
                if checks:
                    session.add(
                        ArtifactCheck(
                            id=_id("chk"),
                            artifact_id=artifact.id,
                            content_hash=artifact.content_hash,
                            checks_json=json.dumps({"sections": checks}, ensure_ascii=False),
                        )
                    )
                session.flush()
                return self._artifact_out(session, artifact)
        except IntegrityError as error:
            earlier = self._earlier_output(workspace_id, client_request_id, fingerprint)
            if earlier is not None:
                return earlier
            raise ArtifactConflictError(
                "Artifact version creation conflicted with another writer; refresh and retry"
            ) from error

    def build_output(
        self,
        base_id: str,
        workspace_id: str,
        payload: BuildOutputInput,
        events: Callable[[dict], None] | None = None,
    ) -> ArtifactOut:
        """Build a report or slides from the library, or from what was picked.

        Planner, researcher, writer and checker run first, with only reads; the version is
        written afterwards, so a failed build saves nothing. ``supersedesArtifactId`` makes
        it the next version of an existing output (a rebuild). ``events`` hears the
        progress and may raise to stop the work.
        """

        limits = OutputLimits()
        fingerprint = self._output_fingerprint(
            workspace_id,
            base_id,
            "build",
            self._output_request_fields(payload),
        )
        earlier = self._earlier_output(workspace_id, payload.client_request_id, fingerprint)
        if earlier is not None:
            return earlier
        model, effort = self._output_model(payload.model, payload.effort)
        skilled = self.output_skills
        # Without a skill there is nothing to choose a structure: "auto" is an overview.
        style = "overview" if not skilled and payload.style == "auto" else payload.style
        with session_scope(self.sessions) as session:
            library = self._output_library(session, workspace_id, base_id)
            parent = (
                self._output_version(
                    session, workspace_id, base_id, payload.supersedes_artifact_id, latest=True
                )
                if payload.supersedes_artifact_id
                else None
            )
            parent_theme = None
            if parent and payload.kind == "slides":
                parent_sections = split_document(parent.content, "slides")[1]
                if parent_sections:
                    parent_theme = _marked(parent_sections[0]).theme
            theme = payload.theme or parent_theme
            design_version = payload.design_version
            if design_version is None and parent and payload.kind == "slides" and parent_sections:
                design_version = _marked(parent_sections[0]).design_version
            spec = Spec(
                payload.kind,
                payload.audience,
                style,
                payload.brief,
                payload.title,
                theme,
                design_version,
            )
            if parent and payload.conversation_id:
                owner = json.loads(parent.provenance_json).get("conversation_id")
                if owner and owner != payload.conversation_id:
                    raise ValueError("This artifact belongs to a different conversation.")
            resolved = self._resolve_output_scope(session, base_id, payload.scope)
            if not (resolved.source_ids or resolved.units or resolved.sessions):
                raise ValueError(NOTHING_TO_BUILD_FROM)
            notes, seeds, inputs = self._output_material(session, resolved, limits)
            snapshots = self._unit_snapshots(session, resolved.units)
            pool = self._output_pool(seeds, limits)
            tool = self._output_search_tool(
                session,
                base_id,
                resolved.source_ids,
                payload.library_supplements,
                f"{library.title}: {library.question}",
            )
            outline = (
                [
                    Planned(
                        item.heading,
                        item.goal,
                        layout=item.layout or "",
                        link=item.link or "",
                        id=item.id,
                        message=item.message,
                        question=item.question,
                        requires=tuple(item.requires),
                        design=item.design.model_dump() if item.design else None,
                        layout_locked=item.layout_locked
                        or (
                            item.layout is not None and "layout_locked" not in item.model_fields_set
                        ),
                        design_locked=item.design_locked
                        or (
                            item.design is not None and "design_locked" not in item.model_fields_set
                        ),
                        design_attempted=item.design_attempted,
                    )
                    for item in payload.outline
                ]
                if payload.outline
                else None
            )
            fallback_title = parent.title if parent else library.title
            made_by = None
            if self.output_method == "studio":
                from gunther.studio.pipeline import StudioAgents

                self._studio_reading(session, resolved.source_ids, pool)
                studio = StudioAgents(
                    self.models,
                    limits,
                    use=None if payload.use == "auto" else payload.use,
                    tool=tool,
                )
                built = studio.make(
                    model, effort, spec, pool, notes, events, fallback_title=fallback_title
                )
                made_by = {
                    "generator": "gunther.studio.v1",
                    "skill": built.skill,
                    "approach": built.approach,
                    "use": studio.use or (built.approach or {}).get("use"),
                }
            elif skilled:
                web, _ = self._web_tool(payload.web)
                agents = SkillAgents(
                    self.models,
                    skill_for(payload.kind),
                    limits,
                    tools=[tool, *([web] if web else [])],
                    reader=self._source_reader(session),
                    use=None if payload.use == "auto" else payload.use,
                    slide_renderer=self.slide_renderer,
                )
                built = agents.make(
                    model,
                    effort,
                    spec,
                    pool,
                    notes,
                    events,
                    fixed=payload.approach.model_dump() if payload.approach else None,
                    outline=outline,
                    fallback_title=fallback_title,
                )
                made_by = {
                    "generator": "gunther.output-skills.v1",
                    "skill": built.skill,
                    "approach": built.approach,
                    "use": agents.use,
                }
            else:
                built = OutputAgents(self.models, limits).build(
                    model,
                    effort,
                    spec,
                    tool,
                    pool,
                    notes,
                    events,
                    outline=outline,
                    fallback_title=fallback_title,
                )
            made_by = {
                **(made_by or {}),
                "library_supplements": payload.library_supplements,
                "conversation_id": payload.conversation_id,
                "conversation_title": payload.conversation_title,
                "request": payload.brief,
            }
            parent_id = parent.id if parent else None
        if events:
            events({"type": "saving"})  # a reader who pressed Stop by now gets nothing saved
        return self._save_output(
            workspace_id=workspace_id,
            base_id=base_id,
            client_request_id=payload.client_request_id,
            fingerprint=fingerprint,
            parent_id=parent_id,
            kind=payload.kind,
            style=style,
            audience=payload.audience,
            title=built.title,
            brief=payload.brief,
            origin="rebuild" if parent_id else "build",
            content=built.content,
            scope=payload.scope.model_dump(),
            inputs=inputs,
            outline=built.outline,
            citations=[item.model_dump() for item in built.citations],
            snapshots=snapshots,
            model=OutputModelOut(ref=model.ref, label=model.display, effort=effort),
            checks=built.checks,
            made_by=made_by,
        )

    @staticmethod
    def _made_by(artifact: Artifact) -> dict[str, object] | None:
        """How a version was made by a skill, for the versions that follow it."""

        provenance = json.loads(artifact.provenance_json)
        skill = provenance.get("skill")
        if not isinstance(skill, dict):
            return None
        from gunther.schemas import SkillRunOut

        skill = SkillRunOut.model_validate(skill).model_dump(mode="json", by_alias=True)
        return {
            "generator": provenance.get("generator"),
            "skill": {"name": skill.get("name"), "version": skill.get("version"), "skipped": [],
                      **{key: skill[key] for key in ("renderCheck", "visualReview")
                         if key in skill}},
            "approach": provenance.get("approach"),
            "use": provenance.get("use"),
            "library_supplements": provenance.get("library_supplements", False),
        }

    @staticmethod
    def _passage_block(session: Session, citation: ConversationCitationOut) -> ContentBlock | None:
        """The block a library citation quotes, in the revision it was taken from."""

        revision_id = citation.source_revision_id or session.scalar(
            select(SourceIndexHead.revision_id).where(
                SourceIndexHead.source_id == citation.source_id
            )
        )
        if not revision_id:
            return None
        if citation.block_id:
            block = session.get(ContentBlock, citation.block_id)
            revision = session.get(SourceRevision, revision_id)
            if (
                block
                and revision
                and revision.source_id == citation.source_id
                and block.revision_id == revision_id
            ):
                return block
            return None
        exact = session.scalar(
            select(ContentBlock)
            .where(
                ContentBlock.revision_id == revision_id,
                ContentBlock.content == citation.quote,
            )
            .limit(1)
        )
        if exact is not None:
            return exact
        # A search passage is often a piece of a block, or runs over several: the block it
        # starts in.
        quote = " ".join(citation.quote.split())
        if len(quote) < 20:
            return None
        blocks = session.scalars(
            select(ContentBlock)
            .where(ContentBlock.revision_id == revision_id)
            .order_by(ContentBlock.ordinal)
        )
        for block in blocks:
            text = " ".join(block.content.split())
            if quote[:60] in text or (len(text) >= 40 and quote.startswith(text[:60])):
                return block
        return None

    @staticmethod
    def _section_blocks(session: Session, block: ContentBlock) -> list[ContentBlock]:
        """The section a passage sits in: the blocks under the same heading, or the three
        on each side when it has none. It grows outward from the passage, one block each
        way in turn, until it is as long as a source is read."""

        query = select(ContentBlock).where(ContentBlock.revision_id == block.revision_id)
        if block.heading_path_json and block.heading_path_json != "[]":
            query = query.where(ContentBlock.heading_path_json == block.heading_path_json)
        else:
            query = query.where(ContentBlock.ordinal.between(block.ordinal - 3, block.ordinal + 3))
        blocks = list(session.scalars(query.order_by(ContentBlock.ordinal)))
        at = next((i for i, item in enumerate(blocks) if item.id == block.id), 0)
        low = high = at
        size = len(blocks[at].content) if blocks else 0
        turn = 0
        while size < READ_SECTION_CHARS:
            if turn % 2 == 0 and high + 1 < len(blocks):
                step, edge = 1, high + 1
            elif low > 0:
                step, edge = -1, low - 1
            elif high + 1 < len(blocks):
                step, edge = 1, high + 1
            else:
                break
            if size + len(blocks[edge].content) + 2 > READ_SECTION_CHARS:
                break
            size += len(blocks[edge].content) + 2
            if step > 0:
                high = edge
            else:
                low = edge
            turn += 1
        return blocks[low : high + 1]

    @staticmethod
    def _read_section(session: Session, block: ContentBlock) -> str:
        return "\n\n".join(
            item.content for item in KnowledgeService._section_blocks(session, block)
        )[:READ_SECTION_CHARS]

    @staticmethod
    def _read_snapshot(blocks: list[ContentBlock], limit: int = READ_SECTION_CHARS) -> ReadResult:
        pieces: list[str] = []
        spans: list[dict[str, object]] = []
        size = 0
        for block in blocks:
            remaining = limit - size - (2 if pieces else 0)
            if remaining <= 0:
                break
            piece = block.content[:remaining]
            pieces.append(piece)
            size += len(piece) + (2 if len(pieces) > 1 else 0)
            spans.append(
                {
                    "blockId": block.id,
                    "revisionId": block.revision_id,
                    "start": 0,
                    "end": len(piece),
                    "anchor": json.loads(block.anchor_json),
                }
            )
        return ReadResult("\n\n".join(pieces), spans)

    def _ask_reader(self, session: Session, web_on: bool) -> Callable[[Evidence], str | ReadResult]:
        """Reading more of a source in Ask's pool (read_source): the section around a library
        passage, or the whole web page. Only a source already in the pool is read, and a web
        page only with the Web switch on, through the same rules as capturing a page; the
        address is the one the search result gave, never one the model wrote."""

        def read(item: Evidence) -> str | ReadResult:
            citation = item.payload[0] if isinstance(item.payload, tuple) else None
            if item.kind == "library" and citation is not None and citation.source_id:
                block = self._passage_block(session, citation)
                if block is None:
                    raise ToolFailure("That passage is no longer in its source.")
                return self._read_snapshot(self._section_blocks(session, block))
            if item.kind == "web" and item.url:
                if not web_on:
                    raise ToolFailure("The web is off for this message")
                if self.page_reader is None:
                    raise ToolFailure("This source cannot be read further")
                from gunther.web_capture import WebCaptureError

                try:
                    text, _ = self.page_reader(item.url)
                except WebCaptureError as error:
                    raise ToolFailure(str(error)) from error
                if not text.strip():
                    raise ToolFailure("The page has no readable text")
                return _around(text, item.text)
            raise ToolFailure("This source cannot be read further")

        return read

    def _source_reader(self, session: Session):
        """Reading more of a library source around one of its passages (a skill's
        read_source): the passage and the two blocks on each side, as a new passage."""

        def read(item: Evidence) -> Evidence | None:
            citation = item.payload[0] if isinstance(item.payload, tuple) else None
            if item.kind != "library" or citation is None or not citation.source_id:
                raise ToolFailure("Only a passage from the library can be read further.")
            block = self._passage_block(session, citation)
            if block is None:
                raise ToolFailure("That passage is no longer in its source.")
            around = session.scalars(
                select(ContentBlock)
                .where(
                    ContentBlock.revision_id == block.revision_id,
                    ContentBlock.ordinal.between(block.ordinal - 2, block.ordinal + 2),
                )
                .order_by(ContentBlock.ordinal)
            )
            snapshot = self._read_snapshot(list(around), 4000)
            text = snapshot.text
            if text.strip() == citation.quote.strip():
                return None
            locator = f"{citation.locator} (with context)" if citation.locator else "with context"
            fresh = citation.model_copy(
                update={
                    "id": _id("cit"),
                    "quote": text,
                    "locator": locator,
                    "ref": None,
                    "support_text": text,
                    "support_spans": snapshot.spans,
                }
            )
            return Evidence(
                kind="library",
                title=item.title,
                text=text,
                locator=locator,
                status=item.status,
                confidence=item.confidence,
                payload=(fresh, None),
            )

        return read

    def revise_output(
        self,
        base_id: str,
        workspace_id: str,
        artifact_id: str,
        payload: ReviseOutputInput,
        events: Callable[[dict], None] | None = None,
    ) -> ArtifactOut:
        """Change one section (or all) of the latest version as instructed: the next version.

        The passages the version cites keep their numbers; new research on the
        instruction searches the scope the output was built from. Sections that change
        are checked again; the others keep what the Checker found in them.
        """

        limits = OutputLimits()
        fingerprint = self._output_fingerprint(
            workspace_id,
            base_id,
            "revise",
            {
                "artifactId": artifact_id,
                **self._output_request_fields(payload),
            },
        )
        earlier = self._earlier_output(workspace_id, payload.client_request_id, fingerprint)
        if earlier is not None:
            return earlier
        model, effort = self._output_model(payload.model, payload.effort)
        with session_scope(self.sessions) as session:
            library = self._output_library(session, workspace_id, base_id)
            parent = self._output_version(session, workspace_id, base_id, artifact_id, latest=True)
            self._require_agent_output(parent)
            if payload.conversation_id:
                owner = json.loads(parent.provenance_json).get("conversation_id")
                if owner and owner != payload.conversation_id:
                    raise ValueError("This artifact belongs to a different conversation.")
            resolved = self._resolve_output_scope(
                session,
                base_id,
                OutputScope.model_validate(json.loads(parent.scope_json)),
                strict=False,
            )
            notes, _, inputs = self._output_material(session, resolved, limits)
            snapshots = self._unit_snapshots(session, resolved.units)
            pool = Pool(limits.pool)
            pool.restore(
                [
                    evidence_from_citation(ConversationCitationOut.model_validate(item))
                    for item in json.loads(parent.citations_json)
                ]
            )
            known = self._latest_checks(session, parent.id)
            records = self._latest_check_records(session, parent.id)
            carried = [
                known.get(section_hash(text))
                for text in split_document(parent.content, parent.kind)[1]
            ]
            tool = self._output_search_tool(
                session,
                base_id,
                resolved.source_ids,
                bool(json.loads(parent.provenance_json).get("library_supplements", False)),
                f"{library.title}: {library.question}",
            )
            made_by = self._made_by(parent)
            if made_by is not None:
                saved = made_by.get("approach")
                use = made_by.get("use")
                agents: OutputAgents = SkillAgents(
                    self.models,
                    skill_for(parent.kind),
                    limits,
                    tools=[tool],
                    use=use if isinstance(use, str) else None,
                    approach=Approach.saved_as(saved, use if isinstance(use, str) else None)
                    if isinstance(saved, dict)
                    else None,
                    slide_renderer=self.slide_renderer,
                )
            else:
                agents = OutputAgents(self.models, limits)
            built = agents.revise(
                model,
                effort,
                Spec(
                    parent.kind,
                    parent.audience,
                    parent.style,
                    parent.brief,
                    theme=_marked(split_document(parent.content, "slides")[1][0]).theme
                    if parent.kind == "slides" and split_document(parent.content, "slides")[1]
                    else None,
                ),
                parent.content,
                [
                    Planned(
                        item["heading"],
                        item.get("goal", ""),
                        layout=item.get("layout") or "",
                        link=item.get("link") or "",
                        id=item.get("id", ""),
                        message=item.get("message", ""),
                        question=item.get("question", ""),
                        requires=tuple(item.get("requires") or ()),
                        design=item.get("design"),
                        layout_locked=bool(
                            item.get("layoutLocked", item.get("layout_locked", False))
                        ),
                        design_locked=bool(
                            item.get("designLocked", item.get("design_locked", False))
                        ),
                        design_attempted=bool(
                            item.get("designAttempted", item.get("design_attempted", False))
                        ),
                    )
                    for item in json.loads(parent.outline_json)
                ],
                carried,
                payload.instruction,
                payload.section_index,
                tool,
                pool,
                notes,
                events,
                known_coverage={
                    digest: record["coverage"]
                    for digest, record in records.items()
                    if record.get("coverage")
                },
            )
            for check in built.checks:
                previous = records.get(check["hash"], {})
                if "coverage" not in check and previous.get("coverage"):
                    check["coverage"] = previous["coverage"]
            if isinstance(agents, SkillAgents) and parent.kind == "slides":
                built = replace(built, skill=agents.record())
                built = agents.review_rendered(built, Spec("slides", parent.audience,
                                               brief=parent.brief), events)
                made_by = {**(made_by or {}), "skill": built.skill}
            made_by = {
                **(made_by or {}),
                "conversation_id": payload.conversation_id,
                "conversation_title": payload.conversation_title,
                "request": payload.instruction,
                "library_supplements": json.loads(parent.provenance_json).get(
                    "library_supplements", False
                ),
            }
            fields = {
                "kind": parent.kind,
                "style": parent.style,
                "audience": parent.audience,
                "brief": parent.brief,
                "scope": json.loads(parent.scope_json),
                "outline": json.loads(parent.outline_json),
                "title": built.title or parent.title,
            }
        if events:
            events({"type": "saving"})
        return self._save_output(
            workspace_id=workspace_id,
            base_id=base_id,
            client_request_id=payload.client_request_id,
            fingerprint=fingerprint,
            parent_id=artifact_id,
            origin="revise",
            content=built.content,
            inputs=inputs,
            citations=[item.model_dump() for item in built.citations],
            snapshots=snapshots,
            model=OutputModelOut(ref=model.ref, label=model.display, effort=effort),
            checks=built.checks,
            made_by=made_by,
            **fields,
        )

    def review_slides(
        self, base_id, workspace_id, artifact_id, payload, events=None,
    ) -> ArtifactOut:
        """Review the current renders without running the writer. Seal a new version."""
        from gunther.outputs import BuiltOutput
        from gunther.slide_review import review_deck

        fingerprint = self._output_fingerprint(workspace_id, base_id, "visual-review",
            {"artifactId": artifact_id, **self._output_request_fields(payload)})
        earlier = self._earlier_output(workspace_id, payload.client_request_id, fingerprint)
        if earlier is not None:
            return earlier
        model, effort = self._output_model(payload.model, payload.effort)
        with session_scope(self.sessions) as session:
            self._output_library(session, workspace_id, base_id)
            parent = self._output_version(session, workspace_id, base_id, artifact_id, latest=True)
            self._require_agent_output(parent)
            if parent.kind != "slides":
                raise ValueError("Rendered-slide review is only available for decks.")
            provenance = json.loads(parent.provenance_json)
            made_by = self._made_by(parent) or {}
            previous_skill = provenance.get("skill") or {}
            skill = skill_for("slides")
            use = provenance.get("use") or "talk"
            built = BuiltOutput(parent.title, parent.content, json.loads(parent.outline_json),
                [ConversationCitationOut.model_validate(c)
                 for c in json.loads(parent.citations_json)],
                list(self._latest_check_records(session, parent.id).values()), model, effort,
                skill={**previous_skill, "name": "slides", "version": skill.version})
            fields = {
                "kind": parent.kind, "style": parent.style, "audience": parent.audience,
                "brief": parent.brief, "scope": json.loads(parent.scope_json),
                "inputs": json.loads(parent.inputs_json),
                "snapshots": [ArtifactUnitSnapshotOut.model_validate(s)
                              for s in json.loads(parent.revision_snapshot_json)],
            }
        built = review_deck(built, self.slide_renderer, self.models, model, "medium",
                            brief=fields["brief"], use=use, emit=events or (lambda _: None),
                            instructions=skill.instructions(skill.step("visual_review")))
        if events:
            events({"type": "saving"})
        return self._save_output(workspace_id=workspace_id, base_id=base_id,
            client_request_id=payload.client_request_id, fingerprint=fingerprint,
            parent_id=artifact_id, title=built.title, origin="revise", content=built.content,
            outline=built.outline, citations=[c.model_dump() for c in built.citations],
            model=OutputModelOut(ref=model.ref, label=model.display, effort="medium"),
            checks=built.checks, made_by={**made_by, "skill": built.skill, "use": use}, **fields)

    def edit_output(
        self,
        base_id: str,
        workspace_id: str,
        artifact_id: str,
        payload: EditOutputInput,
    ) -> ArtifactOut:
        """Save text the person typed as the next version, exactly as typed.

        Nothing is renumbered or rewritten, and no model runs. The Checker's findings
        stay with the sections whose text did not change; the others are not re-checked.
        """

        fingerprint = self._output_fingerprint(
            workspace_id,
            base_id,
            "edit",
            {
                "artifactId": artifact_id,
                "contentHash": hashlib.sha256(payload.content.encode("utf-8")).hexdigest(),
            },
        )
        earlier = self._earlier_output(workspace_id, payload.client_request_id, fingerprint)
        if earlier is not None:
            return earlier
        if len(payload.content.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ValueError(f"The output is larger than the {MAX_OUTPUT_BYTES:,}-byte limit")
        with session_scope(self.sessions) as session:
            self._output_library(session, workspace_id, base_id)
            parent = self._output_version(session, workspace_id, base_id, artifact_id, latest=True)
            self._require_agent_output(parent)
            known = self._latest_checks(session, parent.id)
            records = self._latest_check_records(session, parent.id)
            hashes = dict.fromkeys(
                section_hash(text) for text in split_document(payload.content, parent.kind)[1]
            )
            checks = [records[digest] for digest in hashes if digest in known]
            fields = {
                "kind": parent.kind,
                "style": parent.style,
                "audience": parent.audience,
                "brief": parent.brief,
                "scope": json.loads(parent.scope_json),
                "inputs": json.loads(parent.inputs_json),
                "outline": json.loads(parent.outline_json),
                "citations": json.loads(parent.citations_json),
                "snapshots": [
                    ArtifactUnitSnapshotOut.model_validate(item)
                    for item in json.loads(parent.revision_snapshot_json)
                ],
                "title": title_of(payload.content) or parent.title,
                "made_by": self._made_by(parent),
            }
        return self._save_output(
            workspace_id=workspace_id,
            base_id=base_id,
            client_request_id=payload.client_request_id,
            fingerprint=fingerprint,
            parent_id=artifact_id,
            origin="edit",
            content=payload.content,
            model=None,
            checks=checks,
            **fields,
        )

    def check_output(self, base_id: str, workspace_id: str, artifact_id: str) -> ArtifactOut:
        """Have the Checker look at the sections it has not looked at as they read now.

        The version is not changed: the findings are stored beside it.
        """

        model, _ = self._output_model()
        with session_scope(self.sessions) as session:
            self._output_library(session, workspace_id, base_id)
            artifact = self._output_version(
                session, workspace_id, base_id, artifact_id, latest=False
            )
            self._require_agent_output(artifact)
            known = self._latest_checks(session, artifact.id)
            records = self._latest_check_records(session, artifact.id)
            artifact_kind = artifact.kind
            sections = split_document(artifact.content, artifact.kind)[1]
            if all(
                records.get(section_hash(text), {}).get("coverage", {}).get("status") == "complete"
                for text in sections
            ):
                return self._artifact_out(session, artifact)
            known = {
                key: value
                for key, value in known.items()
                if records.get(key, {}).get("coverage", {}).get("status") == "complete"
            }
            by_ref = {
                citation.ref: evidence_from_citation(citation)
                for citation in (
                    ConversationCitationOut.model_validate(item)
                    for item in json.loads(artifact.citations_json)
                )
                if citation.ref is not None
            }
        checks = OutputAgents(self.models).check(
            model,
            sections,
            known,
            by_ref,
            title_slide=artifact_kind == "slides",
            known_coverage={
                key: value["coverage"] for key, value in records.items() if value.get("coverage")
            },
        )
        with session_scope(self.sessions) as session:
            artifact = self._output_version(
                session, workspace_id, base_id, artifact_id, latest=False
            )
            session.add(
                ArtifactCheck(
                    id=_id("chk"),
                    artifact_id=artifact.id,
                    content_hash=artifact.content_hash,
                    checks_json=json.dumps({"sections": checks}, ensure_ascii=False),
                )
            )
            session.flush()
            return self._artifact_out(session, artifact)

    # Conversations of the Artifacts agent (see artifact_workspace) ------------------------------

    @staticmethod
    def _artifact_conversation_row(
        session: Session, workspace_id: str, base_id: str, conversation_id: str
    ) -> ArtifactConversation:
        row = session.scalar(
            select(ArtifactConversation).where(
                ArtifactConversation.id == conversation_id,
                ArtifactConversation.workspace_id == workspace_id,
                ArtifactConversation.knowledge_base_id == base_id,
            )
        )
        if row is None:
            raise LookupError(f"Conversation {conversation_id} was not found")
        return row

    @staticmethod
    def _load_workspace(
        row: ArtifactConversation,
    ) -> tuple[workspaces.Workspace, workspaces.ConversationBrief]:
        """A conversation's workspace and its brief, as stored."""

        return workspaces.load_workspace(row.state_json), workspaces.load_brief(row.brief_json)

    @staticmethod
    def _store_workspace(
        row: ArtifactConversation,
        workspace: workspaces.Workspace,
        brief: workspaces.ConversationBrief,
        at: datetime | None = None,
    ) -> None:
        """Keep the workspace and the brief in the conversation, which counts as changed."""

        row.state_json = workspaces.dump_workspace(workspace)
        row.brief_json = workspaces.dump_brief(brief)
        row.updated_at = at or utc_now()

    @staticmethod
    def _conversation_scope(row: ArtifactConversation) -> OutputScope:
        try:
            return OutputScope.model_validate(json.loads(row.scope_json))
        except ValueError:
            return OutputScope()

    @staticmethod
    def _artifact_line_out(line: workspaces.Line | None) -> ArtifactLineOut | None:
        return ArtifactLineOut(text=line.text, mark=line.mark) if line else None

    @classmethod
    def _artifact_brief_out(cls, brief: workspaces.ConversationBrief) -> ArtifactBriefOut:
        return ArtifactBriefOut(
            goal=cls._artifact_line_out(brief.goal),
            audience=cls._artifact_line_out(brief.audience),
            constraints=[cls._artifact_line_out(line) for line in brief.constraints],
            open=[cls._artifact_line_out(line) for line in brief.open],
        )

    @classmethod
    def _artifact_spec_out(cls, spec: workspaces.WorkSpec) -> ArtifactSpecOut:
        return ArtifactSpecOut(
            kind=spec.kind,
            use=cls._artifact_line_out(spec.use),
            length=cls._artifact_line_out(spec.length),
            structure=cls._artifact_line_out(spec.structure),
        )

    @classmethod
    def _artifact_work_out(
        cls, work: workspaces.Work, brief: workspaces.ConversationBrief
    ) -> ArtifactWorkOut:
        approach = workspaces.approach_state(work, brief)
        outline = workspaces.outline_state(work)
        return ArtifactWorkOut(
            key=work.key,
            kind=work.spec.kind,
            title=work.title,
            version_id=work.version_id,
            spec=cls._artifact_spec_out(work.spec),
            approach=ArtifactStepOut(state=approach.state, why=approach.why),
            outline=ArtifactStepOut(state=outline.state, why=outline.why),
            parts=[ArtifactPartOut(**part.model_dump()) for part in workspaces.status(work, brief)],
        )

    @staticmethod
    def _artifact_message_out(message: ArtifactMessage) -> ArtifactMessageOut:
        return ArtifactMessageOut(
            id=message.id,
            role=message.role,
            kind=message.kind,
            text=message.text,
            payload=json.loads(message.payload_json or "{}"),
            artifact_id=message.artifact_id,
            client_request_id=message.client_request_id,
            created_at=_timestamp(message.created_at),
        )

    @staticmethod
    def _artifact_conversation_summary(
        row: ArtifactConversation, workspace: workspaces.Workspace | None = None
    ) -> ArtifactConversationSummaryOut:
        workspace = workspace or workspaces.load_workspace(row.state_json)
        return ArtifactConversationSummaryOut(
            id=row.id,
            title=row.title,
            updated_at=_timestamp(row.updated_at),
            artifact_count=len(workspace.works),
            waiting=workspace.pending is not None,
        )

    def _artifact_conversation_out(
        self, session: Session, row: ArtifactConversation
    ) -> ArtifactConversationOut:
        workspace, brief = self._load_workspace(row)
        messages = session.scalars(
            select(ArtifactMessage)
            .where(ArtifactMessage.conversation_id == row.id)
            # A question and its answer can share a second: the user's message comes first.
            .order_by(ArtifactMessage.created_at, ArtifactMessage.role.desc())
        ).all()
        pending = workspace.pending
        return ArtifactConversationOut(
            **self._artifact_conversation_summary(row, workspace).model_dump(),
            scope=self._conversation_scope(row),
            brief=self._artifact_brief_out(brief),
            messages=[self._artifact_message_out(message) for message in messages],
            works=[self._artifact_work_out(work, brief) for work in workspace.works],
            active=workspace.active,
            pending=(
                ArtifactPendingOut(
                    question=pending.question,
                    options=pending.options,
                    message_id=pending.message_id,
                )
                if pending
                else None
            ),
            archived=row.archived,
            created_at=_timestamp(row.created_at),
        )

    @staticmethod
    def _owned_versions(
        session: Session, workspace_id: str, base_id: str
    ) -> dict[str, list[_OwnedVersion]]:
        """The library's versions that name a conversation in their provenance, by that
        conversation, oldest first."""

        owned: dict[str, list[_OwnedVersion]] = {}
        rows = session.execute(
            select(
                Artifact.id,
                Artifact.lineage_id,
                Artifact.created_at,
                Artifact.client_request_id,
                Artifact.scope_json,
                Artifact.provenance_json,
            )
            .where(Artifact.workspace_id == workspace_id, Artifact.knowledge_base_id == base_id)
            .order_by(Artifact.created_at, Artifact.version_number)
        )
        for row in rows:
            provenance = json.loads(row.provenance_json)
            conversation_id = provenance.get("conversation_id")
            if conversation_id:
                owned.setdefault(conversation_id, []).append(
                    _OwnedVersion(
                        row.id,
                        row.lineage_id,
                        row.created_at,
                        row.client_request_id,
                        row.scope_json,
                        provenance.get("conversation_title"),
                        provenance.get("request"),
                    )
                )
        return owned

    def _add_artifact_turn(
        self,
        session: Session,
        conversation_id: str,
        *,
        instruction: str,
        client_request_id: str | None,
        asked_at: datetime,
        answer: str,
        artifact_id: str | None = None,
        error: str | None = None,
    ) -> datetime:
        """A turn a device (or an earlier version of the app) made, as a user message and the
        reply to it, a moment later; the reply's time is returned. ``answer`` is the reply's
        kind. The reply is marked ``imported``, for it was not made by the agent."""

        answered_at = asked_at + timedelta(milliseconds=1)
        session.add(
            ArtifactMessage(
                id=_id("acm"),
                conversation_id=conversation_id,
                role="user",
                kind="message",
                text=instruction,
                payload_json="{}",
                client_request_id=client_request_id,
                created_at=asked_at,
            )
        )
        session.add(
            ArtifactMessage(
                id=_id("acm"),
                conversation_id=conversation_id,
                role="assistant",
                kind=answer,
                text="",
                payload_json=json.dumps(
                    {"imported": True, **({"error": error} if error else {})}, ensure_ascii=False
                ),
                artifact_id=artifact_id,
                created_at=answered_at,
            )
        )
        return answered_at

    @staticmethod
    def _lineages_by_last_use(versions: list[_OwnedVersion]) -> list[str]:
        """The lineages of these versions (oldest first), the one worked on last at the end."""

        order: list[str] = []
        for version in versions:
            if version.lineage_id in order:
                order.remove(version.lineage_id)
            order.append(version.lineage_id)
        return order

    def _import_lineages(
        self,
        session: Session,
        row: ArtifactConversation,
        workspace: workspaces.Workspace,
        brief: workspaces.ConversationBrief,
        lineage_ids: list[str],
    ) -> None:
        """Make a work of the newest version of each lineage that has none yet (see
        artifact_workspace.import_work). The last lineage becomes the active one. Versions
        from the old builder cannot be revised, so they are left out."""

        for lineage_id in lineage_ids:
            if any(work.key == lineage_id for work in workspace.works):
                continue
            head = session.scalar(
                select(Artifact)
                .where(
                    Artifact.lineage_id == lineage_id,
                    Artifact.workspace_id == row.workspace_id,
                    Artifact.knowledge_base_id == row.knowledge_base_id,
                )
                .order_by(Artifact.version_number.desc())
                .limit(1)
            )
            if head is None or head.origin == "legacy":
                continue
            workspaces.import_work(
                self._artifact_out(session, head),
                self._latest_check_records(session, head.id),
                workspace,
                brief,
            )
        if lineage_ids and any(work.key == lineage_ids[-1] for work in workspace.works):
            workspace.active = lineage_ids[-1]

    def _recover_artifact_conversations(
        self, session: Session, workspace_id: str, base_id: str
    ) -> None:
        """Give a conversation a row when versions name it and it has none (the device it
        lived on is gone, or it never reached the backend), made from what the versions say:
        the title, the scope of the newest one, a turn for each request, a work for each
        lineage. A conversation that has a row, archived or not, is left as it is."""

        owned = self._owned_versions(session, workspace_id, base_id)
        if not owned:
            return
        taken = set(session.scalars(select(ArtifactConversation.id)))
        for conversation_id, versions in owned.items():
            if conversation_id in taken or len(conversation_id) > 80:
                continue
            try:
                scope = OutputScope.model_validate(json.loads(versions[-1].scope_json))
            except ValueError:
                scope = OutputScope()
            title = next(
                (v.conversation_title for v in reversed(versions) if v.conversation_title), None
            )
            row = ArtifactConversation(
                id=conversation_id,
                workspace_id=workspace_id,
                knowledge_base_id=base_id,
                title=(title or "Recovered conversation")[:160],
                scope_json=json.dumps(scope.model_dump()),
                created_at=versions[0].created_at,
                updated_at=versions[-1].created_at,
            )
            session.add(row)
            session.flush()
            for version in versions:
                if version.request:
                    self._add_artifact_turn(
                        session,
                        row.id,
                        instruction=version.request,
                        client_request_id=version.client_request_id or None,
                        asked_at=version.created_at,
                        answer="result",
                        artifact_id=version.id,
                    )
            workspace, brief = self._load_workspace(row)
            self._import_lineages(
                session, row, workspace, brief, self._lineages_by_last_use(versions)
            )
            self._store_workspace(row, workspace, brief, at=versions[-1].created_at)

    def artifact_conversations(
        self, base_id: str, workspace_id: str
    ) -> list[ArtifactConversationSummaryOut]:
        """The library's Artifacts conversations, the latest first. Listing first gives a row to
        any conversation that versions name and that has none."""

        with self._artifact_conversations_lock, session_scope(self.sessions) as session:
            self._output_library(session, workspace_id, base_id)
            self._recover_artifact_conversations(session, workspace_id, base_id)
            rows = session.scalars(
                select(ArtifactConversation)
                .where(
                    ArtifactConversation.workspace_id == workspace_id,
                    ArtifactConversation.knowledge_base_id == base_id,
                    ArtifactConversation.archived.is_(False),
                )
                .order_by(ArtifactConversation.updated_at.desc(), ArtifactConversation.id)
            ).all()
            return [self._artifact_conversation_summary(row) for row in rows]

    def create_artifact_conversation(
        self, base_id: str, workspace_id: str, payload: CreateArtifactConversationInput
    ) -> ArtifactConversationOut:
        """Start a conversation, under the id the client gives it (a device's conversation
        keeps its own). Asking again for an id that exists returns that conversation."""

        with self._artifact_conversations_lock, session_scope(self.sessions) as session:
            self._output_library(session, workspace_id, base_id)
            scope = payload.scope or OutputScope()
            self._resolve_output_scope(session, base_id, scope)
            conversation_id = payload.id or str(uuid4())
            existing = session.get(ArtifactConversation, conversation_id)
            if existing is not None:
                if existing.workspace_id != workspace_id or existing.knowledge_base_id != base_id:
                    raise ArtifactConflictError("That conversation id is used somewhere else.")
                return self._artifact_conversation_out(session, existing)
            now = utc_now()
            row = ArtifactConversation(
                id=conversation_id,
                workspace_id=workspace_id,
                knowledge_base_id=base_id,
                title=((payload.title or "").strip() or "New conversation")[:160],
                scope_json=json.dumps(scope.model_dump()),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return self._artifact_conversation_out(session, row)

    def import_artifact_conversations(
        self, base_id: str, workspace_id: str, payload: ImportArtifactConversationsInput
    ) -> list[ArtifactConversationSummaryOut]:
        """Take in conversations a device kept before the backend did, once. A conversation
        whose id has a row is skipped, so asking again changes nothing; the summaries are of
        those that were made.

        Each turn becomes a user message and the reply to it. The newest version of each
        lineage the turns saved (and of any other that names the conversation) becomes a work,
        and the lineage of the last saved turn is the active one.
        """

        with self._artifact_conversations_lock, session_scope(self.sessions) as session:
            self._output_library(session, workspace_id, base_id)
            owned = self._owned_versions(session, workspace_id, base_id)
            made: list[ArtifactConversation] = []
            for imported in payload.conversations:
                if session.get(ArtifactConversation, imported.id) is not None:
                    continue
                made.append(
                    self._import_artifact_conversation(
                        session, workspace_id, base_id, imported, owned.get(imported.id, [])
                    )
                )
            return [self._artifact_conversation_summary(row) for row in made]

    def _import_artifact_conversation(
        self,
        session: Session,
        workspace_id: str,
        base_id: str,
        imported: ImportedArtifactConversation,
        owned: list[_OwnedVersion],
    ) -> ArtifactConversation:
        started = _device_time(imported.created_at) or utc_now()
        try:
            scope = OutputScope.model_validate(imported.scope or {})
        except ValueError:
            scope = OutputScope()
        first = imported.turns[0].instruction if imported.turns else ""
        row = ArtifactConversation(
            id=imported.id,
            workspace_id=workspace_id,
            knowledge_base_id=base_id,
            title=((imported.title or "").strip() or first.strip() or "New conversation")[:160],
            scope_json=json.dumps(scope.model_dump()),
            created_at=started,
            updated_at=started,
        )
        session.add(row)
        session.flush()
        # The turns are in the order they happened; their messages keep that order even when a
        # device's times are missing or run backwards.
        last, lineages, used = started, [], set()
        for turn in imported.turns:
            asked = max(_device_time(turn.created_at) or started, last + timedelta(milliseconds=1))
            artifact = (
                session.scalar(
                    select(Artifact).where(
                        Artifact.id == turn.artifact_id,
                        Artifact.workspace_id == workspace_id,
                        Artifact.knowledge_base_id == base_id,
                    )
                )
                if turn.status == "saved" and turn.artifact_id
                else None
            )
            if artifact is not None and artifact.lineage_id in lineages:
                lineages.remove(artifact.lineage_id)  # the lineage worked on last comes last
            if artifact is not None:
                lineages.append(artifact.lineage_id)
            request_id = turn.id if turn.id and turn.id not in used else None
            if request_id:
                used.add(request_id)
            last = self._add_artifact_turn(
                session,
                row.id,
                instruction=turn.instruction,
                client_request_id=request_id,
                asked_at=asked,
                answer={"saved": "result", "failed": "failed"}.get(turn.status, "stopped"),
                artifact_id=artifact.id if artifact is not None else None,
                error=turn.error if turn.status == "failed" else None,
            )
        # Versions that name the conversation but that no turn reached still belong to it; the
        # lineage of the last saved turn stays the one worked on last.
        unreached = [v for v in owned if v.lineage_id not in lineages]
        lineages = [*self._lineages_by_last_use(unreached), *lineages]
        workspace, brief = self._load_workspace(row)
        self._import_lineages(session, row, workspace, brief, lineages)
        self._store_workspace(row, workspace, brief, at=last)
        return row

    def artifact_conversation(
        self, base_id: str, workspace_id: str, conversation_id: str
    ) -> ArtifactConversationOut:
        with session_scope(self.sessions) as session:
            self._output_library(session, workspace_id, base_id)
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            return self._artifact_conversation_out(session, row)

    def update_artifact_conversation(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: UpdateArtifactConversationInput,
    ) -> ArtifactConversationOut:
        """Rename a conversation, change what it reads from, or archive it."""

        with session_scope(self.sessions) as session:
            self._output_library(session, workspace_id, base_id)
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            if payload.title is not None:
                title = payload.title.strip()
                if not title:
                    raise ValueError("title must not be blank")
                row.title = title[:160]
            if payload.scope is not None:
                self._resolve_output_scope(session, base_id, payload.scope)
                row.scope_json = json.dumps(payload.scope.model_dump())
            if payload.archived is not None:
                row.archived = payload.archived
            row.updated_at = utc_now()
            session.flush()
            return self._artifact_conversation_out(session, row)

    def edit_artifact_brief(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: EditArtifactBriefInput,
    ) -> ArtifactBriefStateOut:
        """Set the lines the user typed in the Brief panel. They are marked as theirs, so no
        later guess replaces them. Use, length and structure belong to the artifact being
        worked on."""

        with session_scope(self.sessions) as session:
            self._output_library(session, workspace_id, base_id)
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            workspace, brief = self._load_workspace(row)
            work = workspaces.active_work(workspace)
            spec = payload.spec
            brief, edited = workspaces.edit_brief(
                brief,
                work,
                goal=payload.goal,
                audience=payload.audience,
                constraints=payload.constraints,
                open=payload.open,
                use=spec.use if spec else None,
                length=spec.length if spec else None,
                structure=spec.structure if spec else None,
            )
            if work is not None and edited is not None:
                work.spec = edited
            self._store_workspace(row, workspace, brief)
            session.flush()
            return ArtifactBriefStateOut(
                brief=self._artifact_brief_out(brief),
                spec=self._artifact_spec_out(work.spec) if work is not None else None,
            )

    # A turn of the Artifacts agent (see artifact_agent) -----------------------------------------

    def artifact_conversation_title(
        self, base_id: str, workspace_id: str, conversation_id: str, text: str = ""
    ) -> str:
        """What a conversation is called, for the top bar; a conversation not yet named takes
        its first message's words."""

        with session_scope(self.sessions) as session:
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            if row.title == NEW_CONVERSATION and text.strip():
                return " ".join(text.split())[:60]
            return row.title

    def _earlier_turn(
        self, session: Session, row: ArtifactConversation, payload: ArtifactTurnInput
    ) -> ArtifactTurnOut | None:
        """The result of an earlier try of this same message, if the turn was finished."""

        user = session.scalar(
            select(ArtifactMessage).where(
                ArtifactMessage.conversation_id == row.id,
                ArtifactMessage.client_request_id == payload.client_request_id,
            )
        )
        if user is None:
            return None
        if user.text != payload.text or user.kind != payload.kind:
            raise ArtifactConflictError("clientRequestId was already used for a different message")
        for reply in session.scalars(
            select(ArtifactMessage)
            .where(ArtifactMessage.conversation_id == row.id, ArtifactMessage.role == "assistant")
            .order_by(ArtifactMessage.created_at)
        ):
            found = json.loads(reply.payload_json or "{}")
            if found.get("to") == user.id:
                return self._turn_out(session, reply, found)
        return None

    def _turn_out(
        self, session: Session, reply: ArtifactMessage, found: dict | None = None
    ) -> ArtifactTurnOut:
        found = found if found is not None else json.loads(reply.payload_json or "{}")
        outcome = {
            "question": "asked",
            "answer": "answered",
            "result": "saved",
            "reply": "replied",
            "paused": "paused",
        }[reply.kind]
        artifact = (
            self._artifact_out(session, session.get(Artifact, reply.artifact_id))
            if reply.kind == "result" and reply.artifact_id
            else None
        )
        asked = found.get("question")
        return ArtifactTurnOut(
            outcome=outcome,  # type: ignore[arg-type]
            reply=reply.text,
            question=ArtifactQuestionOut(**asked) if asked else None,
            artifact=artifact,
            changes=[ArtifactChangeOut(**item) for item in found.get("changes", [])],
            message=self._artifact_message_out(reply),
        )

    def artifact_turn(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: ArtifactTurnInput,
        events: Callable[[dict], None] | None = None,
    ) -> ArtifactTurnOut:
        """One message of the user to the Artifacts agent: it decides what the message needs
        and does that, in small steps, until it asks, answers, saves a version or replies.

        The workspace of the conversation is brought in line with each artifact's newest
        version first (a hand edit may have changed it). Searches use the conversation's scope.
        Whatever ends the turn, the workspace keeps the progress made, except that a version is
        saved only by a turn that ends with one. Sending the same ``clientRequestId`` again
        returns the result of the first try. ``events`` hears the progress and may raise to stop.
        """

        emit = events or (lambda _event: None)
        limits = OutputLimits()
        agent_limits = self.artifact_limits
        with session_scope(self.sessions) as session:
            self._output_library(session, workspace_id, base_id)
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            earlier = self._earlier_turn(session, row, payload)
            if earlier is not None:
                return earlier
        model, effort = self._output_model(payload.model, payload.effort)
        user_id, reply_id = _id("acm"), _id("acm")
        emit({"type": "turn", "conversationId": conversation_id, "messageId": user_id})
        failure: BaseException | None = None
        result: artifact_agent.TurnResult | None = None
        with session_scope(self.sessions) as session:
            library = self._output_library(session, workspace_id, base_id)
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            workspace, brief = self._load_workspace(row)
            began = (
                brief.model_copy(deep=True),
                {work.key: work.spec.model_copy(deep=True) for work in workspace.works},
            )
            scope = self._conversation_scope(row)
            resolved = self._resolve_output_scope(session, base_id, scope, strict=False)
            if not (resolved.source_ids or resolved.units or resolved.sessions):
                raise ValueError(NOTHING_TO_BUILD_FROM)
            notes, seeds, inputs = self._output_material(session, resolved, limits)
            snapshots = self._unit_snapshots(session, resolved.units)
            tool = self._output_search_tool(
                session, base_id, resolved.source_ids, False, f"{library.title}: {library.question}"
            )
            web, web_reason = self._web_tool(payload.web)
            reader = self._source_reader(session)
            versions: dict[str, int] = {}
            saved: dict[str, str] = {}
            heads: dict[str, str] = {}
            for work in list(workspace.works):
                head = None
                if not work.key.startswith("draft_"):
                    head = session.scalar(
                        select(Artifact)
                        .where(
                            Artifact.lineage_id == work.key,
                            Artifact.workspace_id == workspace_id,
                            Artifact.knowledge_base_id == base_id,
                        )
                        .order_by(Artifact.version_number.desc())
                        .limit(1)
                    )
                if head is None:
                    continue
                shown = self._artifact_out(session, head)
                workspaces.sync(
                    work, shown, self._latest_check_records(session, head.id), workspace, brief
                )
                versions[work.key], saved[work.key], heads[work.key] = (
                    shown.version_number,
                    shown.content,
                    head.id,
                )
            history = self._turn_history(session, row.id, agent_limits.history)
            budget = RunBudget(
                agent_limits.searches,
                agent_limits.searches,
                agent_limits.calls,
                agent_limits.calls,
                phase="turn",
            )
            gateway = BudgetedGateway(self.models, budget)

            def make_agents(kind: str) -> SkillAgents:
                return SkillAgents(
                    gateway,  # type: ignore[arg-type]
                    skill_for(kind),
                    limits,
                    tools=[tool, *([web] if web else [])],
                    reader=reader,
                    slide_renderer=self.slide_renderer,
                )

            studio = make_studio_agents = None
            if self.output_method == "studio":
                from gunther.studio.pipeline import StudioAgents
                from gunther.studio.work import StudioWorkAgents

                def studio(spec: Spec, made: Pool, use: str | None) -> BuiltOutput:
                    self._studio_reading(session, resolved.source_ids, made)
                    return StudioAgents(gateway, limits, use=use, tool=tool).make(  # type: ignore[arg-type]
                        model, effort, spec, made, notes, emit, fallback_title=library.title
                    )

                def make_studio_agents(kind: str) -> StudioWorkAgents:
                    return StudioWorkAgents(
                        gateway, kind, limits, tools=[tool, *([web] if web else [])], reader=reader
                    )

            agent = artifact_agent.ArtifactAgent(
                artifact_agent.TurnContext(
                    studio=studio,
                    make_studio_agents=make_studio_agents,
                    workspace=workspace,
                    brief=brief,
                    gateway=gateway,  # type: ignore[arg-type]
                    budget=budget,
                    model=model,
                    effort=effort,
                    make_agents=make_agents,
                    notes=notes,
                    library_tool=tool,
                    web_tool=web,
                    web_reason=web_reason or "",
                    reader=reader,
                    library=library.title,
                    kind=payload.kind,
                    text=payload.text,
                    focus=payload.focus.model_dump() if payload.focus else None,
                    history=history,
                    reply_id=reply_id,
                    versions=versions,
                    saved=saved,
                    seeds=[seed for seed in seeds[: limits.seed]],
                    emit=emit,
                    limits=agent_limits,
                )
            )
            try:
                result = agent.run()
            except Exception as error:
                failure = error
            brief = agent.brief
        if result is not None and result.save is not None:
            try:
                artifact = self._save_turn_version(
                    base_id,
                    workspace_id,
                    conversation_id,
                    payload,
                    model,
                    effort,
                    result,
                    workspace,
                    brief,
                    scope,
                    inputs,
                    snapshots,
                    heads,
                    emit,
                )
            except Exception as error:
                result, failure = None, error
        else:
            artifact = None
        if failure is not None:
            stopped = isinstance(failure, Stopped)
            error = (
                ""
                if stopped
                else (
                    str(failure)
                    if isinstance(failure, (ModelError, ValueError, LookupError))
                    else "The message could not be handled."
                )
            )
            if not stopped and not isinstance(failure, (ModelError, ValueError, LookupError)):
                logger.error("An Artifacts turn failed", exc_info=failure)
            self._store_turn(
                base_id, workspace_id, conversation_id, payload, user_id, reply_id, workspace,
                brief, began, kind="stopped" if stopped else "failed", text=error,
                extra={"error": error} if error else {}, artifact_id=None,
                steps=[], request_id=None,
            )  # fmt: skip
            raise failure
        assert result is not None
        kind = {
            "asked": "question",
            "answered": "answer",
            "saved": "result",
            "replied": "reply",
            "paused": "paused",
        }[result.outcome]
        extra: dict[str, object] = {"steps": result.steps}
        if result.question:
            extra["question"] = result.question
            extra["options"] = result.question["options"]
        if result.changes:
            extra["changes"] = result.changes
        message = self._store_turn(
            base_id, workspace_id, conversation_id, payload, user_id, reply_id, workspace, brief,
            began, kind=kind, text=result.reply, extra=extra,
            artifact_id=artifact.id if artifact else None, steps=result.steps,
            request_id=payload.client_request_id,
        )  # fmt: skip
        return ArtifactTurnOut(
            outcome=result.outcome,
            reply=result.reply,
            question=ArtifactQuestionOut(**result.question) if result.question else None,
            artifact=artifact,
            changes=[ArtifactChangeOut(**item) for item in result.changes],
            message=message,
        )

    def _turn_history(
        self, session: Session, conversation_id: str, count: int
    ) -> list[tuple[str, str]]:
        """The last exchanges of a conversation, word for word: what the user said and what
        the agent replied (a question, an answer, a summary of a change)."""

        messages = session.scalars(
            select(ArtifactMessage)
            .where(ArtifactMessage.conversation_id == conversation_id)
            .order_by(ArtifactMessage.created_at, ArtifactMessage.role.desc())
        ).all()
        pairs: list[tuple[str, str]] = []
        asked: str | None = None
        for message in messages:
            if message.role == "user":
                asked = message.text
            elif asked is not None and message.text.strip():
                pairs.append((asked, message.text))
                asked = None
        return pairs[-count:]

    def _save_turn_version(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: ArtifactTurnInput,
        model: ModelInfo,
        effort: Effort,
        result: artifact_agent.TurnResult,
        workspace: workspaces.Workspace,
        brief: workspaces.ConversationBrief,
        scope: OutputScope,
        inputs: dict[str, list[dict[str, object]]],
        snapshots: list[ArtifactUnitSnapshotOut],
        heads: dict[str, str],
        emit: Callable[[dict], None],
    ) -> ArtifactOut:
        """Seal the artifact a turn finished as a version, and make its work match it."""

        request = result.save
        assert request is not None
        work, built = request.work, request.built
        kind = work.spec.kind
        title = self.artifact_conversation_title(base_id, workspace_id, conversation_id)
        record = work.model_copy(deep=True)
        record.version_id, record.version_map, record.skipped = None, request.version_map, []
        parent = heads.get(work.key)
        emit({"type": "saving"})  # a reader who pressed Stop by now gets nothing saved
        artifact = self._save_output(
            workspace_id=workspace_id,
            base_id=base_id,
            client_request_id=payload.client_request_id,
            fingerprint=self._output_fingerprint(
                workspace_id,
                base_id,
                "artifact-turn",
                {"conversationId": conversation_id, "kind": payload.kind, "text": payload.text},
            ),
            parent_id=parent,
            kind=kind,
            style=(workspaces._style_of(work) or "auto") if kind == "report" else None,
            audience=artifact_agent.audience_of(brief),
            title=built.title,
            brief=workspaces.render_brief(brief, work),
            origin="revise" if parent else "build",
            content=built.content,
            scope=scope.model_dump(),
            inputs=inputs,
            outline=built.outline,
            citations=[item.model_dump() for item in built.citations],
            snapshots=snapshots,
            model=OutputModelOut(ref=model.ref, label=model.display, effort=effort),
            checks=built.checks,
            made_by={
                "generator": "gunther.artifact-agent.v1",
                "skill": built.skill,
                "approach": built.approach,
                "use": (
                    work.spec.use.text
                    if work.spec.use and work.spec.use.text in ("talk", "read")
                    else None
                ),
                "conversation_id": conversation_id,
                "conversation_title": title,
                "request": payload.text,
                "reply": request.summary,
                "changes": request.changes,
                "work": record.model_dump(mode="json"),
            },
        )
        # The work is now the version: its numbers, its title, and the lineage as its key.
        was = work.key
        work.version_id, work.version_map = artifact.id, request.version_map
        work.title, work.skipped = artifact.title, []
        work.key = artifact.lineage_id
        if workspace.active == was:
            workspace.active = work.key
        name = "deck" if kind == "slides" else "report"
        workspace.log.append(f"v{artifact.version_number} ({name}): {request.log}")
        return artifact

    def _store_turn(
        self,
        base_id: str,
        workspace_id: str,
        conversation_id: str,
        payload: ArtifactTurnInput,
        user_id: str,
        reply_id: str,
        workspace: workspaces.Workspace,
        brief: workspaces.ConversationBrief,
        began: tuple[workspaces.ConversationBrief, dict[str, workspaces.WorkSpec]],
        *,
        kind: str,
        text: str,
        extra: dict[str, object],
        artifact_id: str | None,
        steps: list,
        request_id: str | None,
    ) -> ArtifactMessageOut:
        """Keep the turn: both messages, the workspace and the brief. What the user typed in the
        panel while it ran is kept over the turn's version of it. A turn that did not finish
        keeps its messages without the request id, so that sending it again runs it again."""

        with session_scope(self.sessions) as session:
            row = self._artifact_conversation_row(session, workspace_id, base_id, conversation_id)
            stored, now = self._load_workspace(row)
            brief = workspaces.keep_edits(began[0], brief, now)
            theirs = {work.key: work.spec for work in stored.works}
            for work in workspace.works:
                before = began[1].get(work.key)
                if before is not None and work.key in theirs:
                    work.spec = workspaces.keep_spec_edits(before, work.spec, theirs[work.key])
            asked = utc_now()
            if row.title == NEW_CONVERSATION:
                row.title = " ".join(payload.text.split())[:160] or NEW_CONVERSATION
            focus = payload.focus.model_dump() if payload.focus else None
            session.add(
                ArtifactMessage(
                    id=user_id,
                    conversation_id=row.id,
                    role="user",
                    kind=payload.kind,
                    text=payload.text,
                    payload_json=json.dumps({"focus": focus} if focus else {}, ensure_ascii=False),
                    client_request_id=request_id,
                    created_at=asked,
                )
            )
            reply = ArtifactMessage(
                id=reply_id,
                conversation_id=row.id,
                role="assistant",
                kind=kind,
                text=text,
                payload_json=json.dumps({"to": user_id, **extra}, ensure_ascii=False),
                artifact_id=artifact_id,
                created_at=asked + timedelta(milliseconds=1),
            )
            session.add(reply)
            self._store_workspace(row, workspace, brief, at=asked + timedelta(milliseconds=1))
            session.flush()
            return self._artifact_message_out(reply)

    @staticmethod
    def _search_tokens(query: str) -> list[str]:
        raw_tokens = list(dict.fromkeys(re.findall(r"[\w-]+", query.casefold(), re.UNICODE)))
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

    def search_knowledge(
        self,
        query: str,
        limit: int = 20,
        knowledge_base_ids: list[str] | None = None,
    ) -> list[KnowledgeSearchResultOut]:
        """Search accepted knowledge, sources, notes and sessions.

        ``knowledge_base_ids`` narrows every result type to those knowledge bases;
        unfiled material (Inbox sources, unfiled notes) is then excluded.
        """
        needle = query.strip()
        tokens = self._search_tokens(needle)
        if not tokens:
            return []
        scoped = knowledge_base_ids is not None
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
                    .where(KnowledgeUnit.knowledge_base_id.not_in(trashed_library_ids()))
                    .where(
                        KnowledgeUnit.knowledge_base_id.in_(knowledge_base_ids)
                        if scoped
                        else true()
                    )
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

            # Unfiled captures still wait in Inbox, but they are the user's material and
            # must be findable; they come back without a library (knowledge_base_id=None).
            source_query = select(Source, KnowledgeBaseSource.knowledge_base_id)
            source_query = (
                source_query.join(
                    KnowledgeBaseSource,
                    and_(
                        KnowledgeBaseSource.source_id == Source.id,
                        KnowledgeBaseSource.knowledge_base_id.in_(knowledge_base_ids),
                        KnowledgeBaseSource.knowledge_base_id.not_in(trashed_library_ids()),
                    ),
                )
                if scoped
                else source_query.outerjoin(
                    KnowledgeBaseSource,
                    and_(
                        KnowledgeBaseSource.source_id == Source.id,
                        KnowledgeBaseSource.knowledge_base_id.not_in(trashed_library_ids()),
                    ),
                )
            )
            source_rows = session.execute(
                source_query.where(match_all(Source.title, Source.content))
                .where(Source.trashed_at.is_(None))
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

            # Passages found by meaning (and by several words at once): a sentence such as
            # "which marker identifies T cells?" rarely has every word in one note.
            meaning = self._sources_by_meaning(
                session, needle, knowledge_base_ids, {item.id for item in results}
            )

            notebook_notes = session.scalars(
                select(NotebookNote)
                .where(
                    match_all(NotebookNote.title, NotebookNote.content),
                    NotebookNote.status != "archived",
                    NotebookNote.trashed_at.is_(None),
                    or_(
                        NotebookNote.knowledge_base_id.is_(None),
                        NotebookNote.knowledge_base_id.not_in(trashed_library_ids()),
                    ),
                    NotebookNote.knowledge_base_id.in_(knowledge_base_ids) if scoped else true(),
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
                    KnowledgeSession.knowledge_base_id != HOME_SCOPE,
                    KnowledgeSession.knowledge_base_id.not_in(trashed_library_ids()),
                    KnowledgeSession.knowledge_base_id.in_(knowledge_base_ids)
                    if scoped
                    else true(),
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

            ranked = sorted(
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
            # Exact matches first, then up to a few more the words alone did not find.
            return [*ranked, *meaning[:MEANING_RESULTS]]

    def _sources_by_meaning(
        self,
        session: Session,
        query: str,
        knowledge_base_ids: list[str] | None,
        already: set[str],
    ) -> list[KnowledgeSearchResultOut]:
        """Sources whose passages match the query by meaning or by most of its words.

        Uses the retrieval Ask uses: keyword and, when Search by meaning is on,
        vector matches together. The best passage of each source is its snippet.
        """

        source_ids = self._home_source_ids(session, [], knowledge_base_ids)
        found: list[KnowledgeSearchResultOut] = []
        for hit in self.index.retrieve(session, source_ids, query, limit=MEANING_RESULTS * 3):
            source = hit.source
            if source.id in already or source.trashed_at is not None:
                continue
            already.add(source.id)
            library_id = session.scalar(
                select(KnowledgeBaseSource.knowledge_base_id)
                .join(
                    KnowledgeBaseRecord,
                    KnowledgeBaseRecord.id == KnowledgeBaseSource.knowledge_base_id,
                )
                .where(
                    KnowledgeBaseSource.source_id == source.id,
                    KnowledgeBaseRecord.trashed_at.is_(None),
                )
                .limit(1)
            )
            summary = self._source_summary(session, source)
            found.append(
                KnowledgeSearchResultOut(
                    id=source.id,
                    knowledge_base_id=library_id,
                    kind="source",
                    title=source.title,
                    snippet=self._search_snippet(hit.block.content, query),
                    meta=(
                        f"{_count_label(summary.assertion_count, 'claim')} · related by "
                        + ("meaning" if hit.method != "keyword" else "its words")
                    ),
                    updated_at=_timestamp(source.created_at),
                )
            )
        return found

    def get_graph(self) -> KnowledgeGraphOut:
        with session_scope(self.sessions) as session:
            assertions = session.scalars(self._live_assertion_query()).all()
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
        live_claim = Assertion.source_id.not_in(
            select(Source.id).where(Source.trashed_at.is_not(None))
        )
        with session_scope(self.sessions) as session:
            counts = OverviewCountsOut(
                sources=session.scalar(
                    select(func.count()).select_from(Source).where(Source.trashed_at.is_(None))
                )
                or 0,
                entities=session.scalar(select(func.count()).select_from(Entity)) or 0,
                assertions=session.scalar(
                    select(func.count()).select_from(Assertion).where(live_claim)
                )
                or 0,
                provisional=session.scalar(
                    select(func.count())
                    .select_from(Assertion)
                    .where(live_claim, Assertion.status == "provisional")
                )
                or 0,
            )
            sources = session.scalars(
                select(Source)
                .where(Source.trashed_at.is_(None))
                .order_by(Source.created_at.desc())
                .limit(4)
            ).all()
            assertions = session.scalars(
                self._live_assertion_query().order_by(Assertion.created_at.desc()).limit(5)
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
            source_count = session.scalar(select(func.count()).select_from(Source)) or 0
        self.ensure_saved_knowledge_units()
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
