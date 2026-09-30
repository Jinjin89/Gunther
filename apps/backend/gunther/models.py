from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gunther.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class WorkspaceIdentity(Base):
    """The durable identity of this one local Gunther workspace."""

    __tablename__ = "workspace_identity"
    __table_args__ = (CheckConstraint("singleton_key = 'primary'"),)

    singleton_key: Mapped[str] = mapped_column(String(16), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160), default="My Gunther Workspace")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class PairedDevice(Base):
    """A revocable device credential; plaintext tokens are never persisted."""

    __tablename__ = "paired_devices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_identity.workspace_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    platform: Mapped[str] = mapped_column(String(40))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)


class DevicePairingSession(Base):
    """A short-lived, one-use pairing secret stored only as a digest."""

    __tablename__ = "device_pairing_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_identity.workspace_id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scopes_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    used_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)


class KnowledgeBaseRecord(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    eyebrow: Mapped[str] = mapped_column(String(80), default="Personal knowledge")
    subtitle: Mapped[str] = mapped_column(String(240), default="A field worth shaping")
    question: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(24), default="green")
    status: Mapped[str] = mapped_column(String(24), default="Outline")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    # Set while in Trash. Everything one "Move to Trash" put there shares a batch,
    # so Restore brings it all back as it was.
    trashed_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    trash_batch_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)


class Asset(Base):
    """An immutable original file stored by content hash on the local device."""

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(160))
    size_bytes: Mapped[int] = mapped_column(Integer)
    relative_path: Mapped[str] = mapped_column(String(640), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    # Set while in Trash. Everything one "Move to Trash" put there shares a batch,
    # so Restore brings it all back as it was.
    trashed_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    trash_batch_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    fragments: Mapped[list[Fragment]] = relationship(back_populates="source", cascade="all, delete")
    assertions: Mapped[list[Assertion]] = relationship(back_populates="source")
    web_snapshot: Mapped[WebSnapshot | None] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        uselist=False,
    )


class WebSnapshot(Base):
    """Immutable provenance for a web response captured as a Source and Asset."""

    __tablename__ = "web_snapshots"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), unique=True, index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), index=True
    )
    client_capture_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True
    )
    request_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    original_url: Mapped[str] = mapped_column(Text)
    final_url: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    source: Mapped[Source] = relationship(back_populates="web_snapshot")


class RecordingSession(Base):
    """A durable recording upload that can be resumed after an application restart."""

    __tablename__ = "recording_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="capturing", index=True)
    content_type: Mapped[str] = mapped_column(String(120), default="audio/webm")
    file_name: Mapped[str] = mapped_column(String(255), unique=True)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    next_sequence: Mapped[int] = mapped_column(Integer, default=0)
    transcript: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    moments_json: Mapped[str] = mapped_column(Text, default="[]")
    recording_context: Mapped[str] = mapped_column(String(24), default="lecture")
    knowledge_base_id: Mapped[str | None] = mapped_column(
        String(160), nullable=True, index=True
    )
    checkpoint_revision: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkpointed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    chunks: Mapped[list[RecordingChunk]] = relationship(
        back_populates="recording",
        cascade="all, delete-orphan",
        order_by="RecordingChunk.sequence",
    )


class RecordingChunk(Base):
    """The committed checksum for one append operation.

    Keeping this ledger in SQLite makes a retry distinguishable from a new chunk,
    including after the desktop process has restarted.
    """

    __tablename__ = "recording_chunks"
    __table_args__ = (UniqueConstraint("recording_id", "sequence"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    recording_id: Mapped[str] = mapped_column(
        ForeignKey("recording_sessions.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    recording: Mapped[RecordingSession] = relationship(back_populates="chunks")


class NotebookNote(Base):
    """A low-friction thought that has not necessarily become structured knowledge yet."""

    __tablename__ = "notebook_notes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String(160), default="Untitled note")
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="inbox", index=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    client_capture_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True
    )
    knowledge_base_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    promoted_source_id: Mapped[str | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    # Set while in Trash. Everything one "Move to Trash" put there shares a batch,
    # so Restore brings it all back as it was.
    trashed_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    trash_batch_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)


class KnowledgeBaseSource(Base):
    __tablename__ = "knowledge_base_sources"
    __table_args__ = (UniqueConstraint("knowledge_base_id", "source_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(160), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Fragment(Base):
    __tablename__ = "fragments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    locator: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    source: Mapped[Source] = relationship(back_populates="fragments")
    evidence_links: Mapped[list[EvidenceLink]] = relationship(
        back_populates="fragment", cascade="all, delete"
    )


class SourceRevision(Base):
    """Immutable parsed representation; reprocessing never changes old citations."""

    __tablename__ = "source_revisions"
    __table_args__ = (UniqueConstraint("source_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    parser: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(24), default="ready")
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class SourceIndexHead(Base):
    __tablename__ = "source_index_heads"
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("source_revisions.id", ondelete="CASCADE"), index=True
    )


class ContentBlock(Base):
    __tablename__ = "content_blocks"
    __table_args__ = (UniqueConstraint("revision_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("source_revisions.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_blocks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(String(240))
    heading_path_json: Mapped[str] = mapped_column(Text, default="[]")
    anchor_json: Mapped[str] = mapped_column(Text, default="{}")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


class BlockEmbedding(Base):
    __tablename__ = "block_embeddings"
    __table_args__ = (UniqueConstraint("block_id", "model"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    block_id: Mapped[str] = mapped_column(
        ForeignKey("content_blocks.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(240), index=True)
    dimensions: Mapped[int] = mapped_column(Integer)
    vector_json: Mapped[str] = mapped_column(Text)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True)
    revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_revisions.id", ondelete="CASCADE"), nullable=True
    )
    model_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(nullable=True)
    available_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class TopicNode(Base):
    __tablename__ = "topic_nodes"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("topic_nodes.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class TopicEvidenceLink(Base):
    __tablename__ = "topic_evidence_links"
    __table_args__ = (UniqueConstraint("topic_id", "block_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    topic_id: Mapped[str] = mapped_column(
        ForeignKey("topic_nodes.id", ondelete="CASCADE"), index=True
    )
    block_id: Mapped[str] = mapped_column(
        ForeignKey("content_blocks.id", ondelete="CASCADE"), index=True
    )


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("normalized_label", "type"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    normalized_label: Mapped[str] = mapped_column(String(200), index=True)
    type: Mapped[str] = mapped_column(String(80))
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class Assertion(Base):
    __tablename__ = "assertions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    subject_entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    predicate: Mapped[str] = mapped_column(String(120))
    object_entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default="provisional", index=True)
    qualifiers_json: Mapped[str] = mapped_column(Text, default="{}")
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    subject: Mapped[Entity] = relationship(foreign_keys=[subject_entity_id])
    object: Mapped[Entity] = relationship(foreign_keys=[object_entity_id])
    source: Mapped[Source] = relationship(back_populates="assertions")
    evidence_links: Mapped[list[EvidenceLink]] = relationship(
        back_populates="assertion", cascade="all, delete"
    )


class EvidenceLink(Base):
    __tablename__ = "evidence_links"
    __table_args__ = (UniqueConstraint("assertion_id", "fragment_id", "stance"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    assertion_id: Mapped[str] = mapped_column(
        ForeignKey("assertions.id", ondelete="CASCADE"), index=True
    )
    fragment_id: Mapped[str] = mapped_column(
        ForeignKey("fragments.id", ondelete="CASCADE"), index=True
    )
    stance: Mapped[str] = mapped_column(String(24), default="supports")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    assertion: Mapped[Assertion] = relationship(back_populates="evidence_links")
    fragment: Mapped[Fragment] = relationship(back_populates="evidence_links")


class Revision(Base):
    __tablename__ = "revisions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class KnowledgeSession(Base):
    __tablename__ = "knowledge_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(160), index=True)
    title: Mapped[str] = mapped_column(String(160), default="New session")
    summary: Mapped[str] = mapped_column(String(300), default="")
    focus_chapter_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    selected_source_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)

    messages: Mapped[list[SessionMessage]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionMessage.created_at",
    )
    branch: Mapped[SessionBranch | None] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        foreign_keys="SessionBranch.session_id",
        uselist=False,
    )


class SessionMessage(Base):
    __tablename__ = "session_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(24), index=True)
    content: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)

    session: Mapped[KnowledgeSession] = relationship(back_populates="messages")


class MessageTrace(Base):
    """How an answer was made, step by step (see trace); kept while Settings → Developer asks."""

    __tablename__ = "message_traces"

    message_id: Mapped[str] = mapped_column(
        ForeignKey("session_messages.id", ondelete="CASCADE"), primary_key=True
    )
    trace_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class SpeechClip(Base):
    """An answer read aloud, kept so the second listen is only played.

    ``cache_key`` covers the text, the supplier, its model and its options, so a
    changed voice makes a new clip and the old one stays until the cache is cleared.
    """

    __tablename__ = "speech_clips"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("session_messages.id", ondelete="CASCADE"), index=True
    )
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(200))
    options_json: Mapped[str] = mapped_column(Text, default="{}")
    # What was actually spoken, and the model that described tables or pictures in it.
    script: Mapped[str] = mapped_column(Text)
    described_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    audio_file: Mapped[str] = mapped_column(String(200))
    audio_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class SessionBranch(Base):
    __tablename__ = "session_branches"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sessions.id", ondelete="CASCADE"), unique=True, index=True
    )
    parent_session_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sessions.id", ondelete="CASCADE"), index=True
    )
    branched_from_message_id: Mapped[str] = mapped_column(
        ForeignKey("session_messages.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    session: Mapped[KnowledgeSession] = relationship(
        back_populates="branch", foreign_keys=[session_id]
    )


class KnowledgeProposal(Base):
    __tablename__ = "knowledge_proposals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(160), index=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_sessions.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("session_messages.id", ondelete="CASCADE"), unique=True, index=True
    )
    target_chapter_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), default="knowledge_unit")
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class KnowledgeUnit(Base):
    __tablename__ = "knowledge_units"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(160), index=True)
    title: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(40), default="knowledge_unit")
    status: Mapped[str] = mapped_column(String(24), default="trusted", index=True)
    head_revision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)

    revisions: Mapped[list[KnowledgeUnitRevision]] = relationship(
        back_populates="unit",
        cascade="all, delete-orphan",
        order_by="KnowledgeUnitRevision.revision_number",
    )


class KnowledgeUnitRevision(Base):
    __tablename__ = "knowledge_unit_revisions"
    __table_args__ = (UniqueConstraint("unit_id", "revision_number"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    unit_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_units.id", ondelete="CASCADE"), index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    source_proposal_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_proposals.id", ondelete="RESTRICT"), unique=True, index=True
    )
    actor: Mapped[str] = mapped_column(String(120), default="user")
    created_at: Mapped[datetime] = mapped_column(default=utc_now)

    unit: Mapped[KnowledgeUnit] = relationship(back_populates="revisions")


class Artifact(Base):
    """One immutable, reproducible Output version composed from accepted knowledge."""

    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("lineage_id", "version_number", name="uq_artifact_lineage_version"),
        UniqueConstraint(
            "workspace_id",
            "client_request_id",
            name="uq_artifact_workspace_request",
        ),
        CheckConstraint("version_number >= 1", name="ck_artifact_version_positive"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_identity.workspace_id", ondelete="RESTRICT"), index=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), index=True
    )
    client_request_id: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    lineage_id: Mapped[str] = mapped_column(String(40), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    supersedes_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    format: Mapped[str] = mapped_column(String(40))
    audience: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    manifest_hash: Mapped[str] = mapped_column(String(64), index=True)
    accepted_unit_ids_json: Mapped[str] = mapped_column(Text)
    revision_snapshot_json: Mapped[str] = mapped_column(Text)
    provenance_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class ArtifactUnitBinding(Base):
    """Queryable immutable backlink from an Artifact version to a pinned Unit revision."""

    __tablename__ = "artifact_unit_bindings"
    __table_args__ = (
        UniqueConstraint("artifact_id", "position", name="uq_artifact_binding_position"),
        UniqueConstraint("artifact_id", "unit_id", name="uq_artifact_binding_unit"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    unit_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_units.id", ondelete="RESTRICT"), index=True
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_unit_revisions.id", ondelete="RESTRICT"), index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64))


class Work(Base):
    """One paper (or book, or talk), however many copies of it were captured.

    Copies share a work when they carry the same DOI or arXiv id, or failing
    that the same title. ``identifier`` is ``doi:…`` or ``arxiv:…``.
    """

    __tablename__ = "works"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    identifier: Mapped[str | None] = mapped_column(String(240), nullable=True, unique=True)
    title_key: Mapped[str] = mapped_column(String(300), index=True)
    title: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class SourcePaper(Base):
    """What a source says about itself, read from its current revision.

    Title, authors, year, identifiers, abstract and outline are drawn from the
    text without a model; ``summary_md`` is the readable ``summary.md``.
    """

    __tablename__ = "source_papers"

    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("source_revisions.id", ondelete="CASCADE"), index=True
    )
    work_id: Mapped[str | None] = mapped_column(
        ForeignKey("works.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    authors: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(200), nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    abstract: Mapped[str] = mapped_column(Text, default="")
    abstract_block_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    outline_json: Mapped[str] = mapped_column(Text, default="[]")
    summary_md: Mapped[str] = mapped_column(Text, default="")
    # The embedding model whose paper vector is stored (vector_index), if any.
    vector_model: Mapped[str | None] = mapped_column(String(240), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class SourceDigest(Base):
    """A summary of a source written after it was read (see digest).

    Derived from one revision and never part of the evidence: its key points
    cite the source's own passages. ``method`` names what wrote it.
    """

    __tablename__ = "source_digests"

    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    revision_id: Mapped[str] = mapped_column(
        ForeignKey("source_revisions.id", ondelete="CASCADE"), index=True
    )
    profile: Mapped[str] = mapped_column(String(24), default="document")
    method: Mapped[str] = mapped_column(String(240), default="local")
    suggested_title: Mapped[str] = mapped_column(String(160), default="")
    overview: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    markdown: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)


class TopicSourceLink(Base):
    """A whole source filed under a topic, as opposed to one of its passages."""

    __tablename__ = "topic_sources"
    __table_args__ = (UniqueConstraint("topic_id", "source_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    topic_id: Mapped[str] = mapped_column(
        ForeignKey("topic_nodes.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)


class TopicSynthesis(Base):
    """The latest written overview of a topic, with the passages it cites."""

    __tablename__ = "topic_syntheses"

    topic_id: Mapped[str] = mapped_column(
        ForeignKey("topic_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    markdown: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    method: Mapped[str] = mapped_column(String(120))
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
