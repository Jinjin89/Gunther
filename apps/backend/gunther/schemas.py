from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceKind = Literal[
    "note", "paper", "link", "file", "image", "table", "recording", "course"
]
AssertionStatus = Literal["provisional", "verified", "disputed"]
MessageRole = Literal["user", "assistant"]
ProposalStatus = Literal["pending", "accepted", "held", "rejected"]
KnowledgeBaseColor = Literal["green", "blue", "clay"]
NotebookNoteStatus = Literal["inbox", "filed", "archived"]
RecordingStatus = Literal["capturing", "completed", "failed"]
RecordingContext = Literal["lecture", "meeting", "memo"]
InboxItemType = Literal["source", "quick_note", "knowledge_suggestion"]
InboxItemState = Literal["unfiled", "needs_review", "held"]
DeviceScope = Literal["api:access", "transcription:stream"]
ArtifactFormat = Literal["field_guide", "teaching_path", "decision_brief"]
ArtifactAudience = Literal["scientist", "student", "collaborator"]


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.capitalize() for word in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class CreateSourceInput(ApiModel):
    title: str = Field(min_length=1, max_length=160)
    kind: SourceKind
    content: str = Field(min_length=3, max_length=1_000_000)
    knowledge_base_id: str | None = Field(default=None, max_length=160)


class CreateNotebookNoteInput(ApiModel):
    title: str = Field(default="Untitled note", min_length=1, max_length=160)
    content: str = Field(default="", max_length=50_000)
    pinned: bool = False
    client_capture_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class UpdateNotebookNoteInput(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    content: str | None = Field(default=None, max_length=50_000)
    pinned: bool | None = None
    status: NotebookNoteStatus | None = None


class FileNotebookNoteInput(ApiModel):
    knowledge_base_id: str = Field(min_length=1, max_length=160)


class NotebookNoteOut(ApiModel):
    id: str
    title: str
    content: str
    status: NotebookNoteStatus
    pinned: bool
    knowledge_base_id: str | None
    promoted_source_id: str | None
    created_at: str
    updated_at: str


class UpdateAssertionStatusInput(ApiModel):
    status: AssertionStatus
    reason: str | None = Field(default=None, max_length=500)


class AssetOut(ApiModel):
    id: str
    content_hash: str
    original_name: str
    media_type: str
    size_bytes: int
    download_url: str
    created_at: str


class AssetProcessingOut(ApiModel):
    ocr_status: Literal["not_needed", "completed", "degraded"] = "not_needed"
    ocr_provider: str | None = None
    note: str | None = None


class SourceSummaryOut(ApiModel):
    id: str
    title: str
    kind: SourceKind
    created_at: str
    assertion_count: int
    entity_count: int
    asset: AssetOut | None = None
    processing: dict[str, object] = Field(default_factory=dict)


class EntityRefOut(ApiModel):
    id: str
    label: str
    type: str


class EvidenceOut(ApiModel):
    id: str
    stance: Literal["supports", "refutes", "mentions"]
    quote: str
    locator: str


class AssertionOut(ApiModel):
    id: str
    predicate: str
    confidence: float
    status: AssertionStatus
    qualifiers: dict[str, str]
    subject: EntityRefOut
    object: EntityRefOut
    source: dict[str, str]
    evidence: list[EvidenceOut]
    created_at: str


class WebCaptureInput(ApiModel):
    original_url: str | None = Field(default=None, min_length=1, max_length=4_096)
    url: str | None = Field(default=None, min_length=1, max_length=4_096)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    notes: str = Field(default="", max_length=5_000)
    knowledge_base_id: str | None = Field(default=None, max_length=160)
    client_capture_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    @model_validator(mode="after")
    def require_one_url(self) -> "WebCaptureInput":
        original = self.original_url.strip() if self.original_url else None
        alias = self.url.strip() if self.url else None
        if not original and not alias:
            raise ValueError("Provide originalUrl or url")
        if original and alias and original != alias:
            raise ValueError("originalUrl and url must match when both are provided")
        self.original_url = original
        self.url = alias
        return self

    @property
    def requested_url(self) -> str:
        return self.original_url or self.url or ""


class WebSnapshotOut(ApiModel):
    original_url: str
    final_url: str
    captured_at: str
    status: int
    content_type: str
    content_hash: str
    asset_id: str


class InboxKnowledgeBaseRefOut(ApiModel):
    id: str
    title: str


class SourceDetailOut(SourceSummaryOut):
    content: str
    assertions: list[AssertionOut] = Field(default_factory=list)
    web_snapshot: WebSnapshotOut | None = None
    # Libraries this source is filed in; empty while it waits in Inbox.
    knowledge_bases: list[InboxKnowledgeBaseRefOut] = Field(default_factory=list)


class InboxItemOut(ApiModel):
    """A normalized item that still needs filing or a human decision."""

    id: str
    item_type: InboxItemType
    state: InboxItemState
    title: str
    preview: str
    source_kind: SourceKind | None = None
    knowledge_bases: list[InboxKnowledgeBaseRefOut] = Field(default_factory=list)
    source_id: str | None = None
    note_id: str | None = None
    proposal_id: str | None = None
    proposal_status: ProposalStatus | None = None
    assertion_count: int = 0
    created_at: str
    updated_at: str


class FileSourceInput(ApiModel):
    knowledge_base_id: str = Field(min_length=1, max_length=160)


class FileSourceOut(ApiModel):
    source: SourceSummaryOut
    knowledge_base: InboxKnowledgeBaseRefOut
    membership_created: bool


class OverviewCountsOut(ApiModel):
    sources: int
    entities: int
    assertions: int
    provisional: int


class OverviewOut(ApiModel):
    counts: OverviewCountsOut
    recent_sources: list[SourceSummaryOut]
    recent_assertions: list[AssertionOut]


class GraphNodeOut(ApiModel):
    id: str
    label: str
    type: str
    assertion_count: int


class GraphEdgeOut(ApiModel):
    id: str
    source: str
    target: str
    label: str
    status: AssertionStatus
    confidence: float


class KnowledgeGraphOut(ApiModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


class ImportCountsOut(ApiModel):
    entities: int
    assertions: int


class ImportResultOut(ApiModel):
    source: SourceSummaryOut
    created: ImportCountsOut
    extraction_mode: Literal["local", "deepseek"]
    duplicate: bool


class AssetCaptureOut(ApiModel):
    asset: AssetOut
    processing: AssetProcessingOut = Field(default_factory=AssetProcessingOut)
    import_result: ImportResultOut


class WebCaptureOut(ApiModel):
    asset: AssetOut
    import_result: ImportResultOut
    snapshot: WebSnapshotOut
    idempotent_replay: bool = False


class FileNotebookNoteOut(ApiModel):
    note: NotebookNoteOut
    import_result: ImportResultOut


class HealthOut(ApiModel):
    status: Literal["ok"] = "ok"
    extraction_mode: Literal["local", "deepseek"]
    web_search_mode: Literal["openai", "not_configured"] = "not_configured"
    transcription_mode: Literal[
        "sensevoice_local", "openai_realtime", "not_configured"
    ] = "not_configured"
    transcription_provider: Literal["sensevoice", "openai", "none"] = "none"
    transcription_model: str = "gpt-live-transcribe"
    transcription_delay: Literal["low", "medium", "high"] = "medium"
    transcription_languages: list[str] = Field(default_factory=lambda: ["en", "zh-cn"])
    summary_mode: Literal["local", "deepseek", "openai"] = "local"
    ocr_mode: Literal["local", "not_configured"] = "not_configured"
    ocr_provider: str = "none"


class CreateDevicePairingInput(ApiModel):
    scopes: list[DeviceScope] = Field(
        default_factory=lambda: ["api:access", "transcription:stream"],
        min_length=1,
        max_length=2,
    )
    expires_in_seconds: int = Field(default=120, ge=30, le=300)


class DevicePairingSessionOut(ApiModel):
    pairing_id: str
    pairing_code: str
    workspace_id: str
    workspace_name: str
    protocol_version: int
    scopes: list[DeviceScope]
    created_at: str
    expires_at: str


class ExchangeDevicePairingInput(ApiModel):
    pairing_id: str = Field(
        min_length=29,
        max_length=40,
        pattern=r"^pair_[a-f0-9]+$",
    )
    pairing_code: str = Field(
        min_length=43,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    device_name: str = Field(min_length=1, max_length=120)
    platform: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._ -]+$")


class PairedDeviceOut(ApiModel):
    id: str
    workspace_id: str
    name: str
    platform: str
    scopes: list[DeviceScope]
    created_at: str
    last_used_at: str | None
    revoked_at: str | None


class DeviceCredentialOut(ApiModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    protocol_version: int
    workspace_id: str
    workspace_name: str
    device: PairedDeviceOut


class WorkspaceBootstrapOut(ApiModel):
    workspace_id: str
    workspace_name: str
    protocol_version: int
    minimum_protocol_version: int
    auth_kind: Literal["sidecar", "device", "development"]
    device_id: str | None = None
    scopes: list[DeviceScope] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class MobileGatewayStatusOut(ApiModel):
    enabled: bool
    running: bool
    address: str | None
    ca_fingerprint: str | None
    ca_certificate_pem: str | None
    protocol_version: int
    error: str | None


class WebSearchSourceOut(ApiModel):
    title: str
    url: str
    snippet: str | None = None


class WebSearchOut(ApiModel):
    query: str
    answer: str
    sources: list[WebSearchSourceOut] = Field(default_factory=list)
    mode: Literal["openai", "not_configured", "failed"]
    message: str | None = None


class CreateLectureSummaryInput(ApiModel):
    title: str = Field(min_length=1, max_length=160)
    transcript: str = Field(min_length=3, max_length=1_000_000)
    duration_seconds: int = Field(default=0, ge=0, le=86_400)


class LectureSummaryOut(ApiModel):
    overview: str
    key_points: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    engine: Literal["local", "deepseek", "openai"]


class RecordingAssetOut(ApiModel):
    id: str
    file_name: str
    content_type: str
    size_bytes: int
    stored_at: str


class RecordingMoment(ApiModel):
    seconds: int = Field(ge=0, le=604_800)
    label: str = Field(min_length=1, max_length=160)


class RecordingCheckpointInput(ApiModel):
    expected_revision: int = Field(ge=0)
    transcript: str = Field(default="", max_length=1_000_000)
    duration_seconds: int = Field(default=0, ge=0, le=604_800)
    moments: list[RecordingMoment] = Field(default_factory=list, max_length=500)
    recording_context: RecordingContext = "lecture"
    knowledge_base_id: str | None = Field(default=None, min_length=1, max_length=160)


class RecordingRecoveryOut(ApiModel):
    can_resume: bool
    audio_available: bool
    next_expected_sequence: int
    checkpoint_revision: int
    checkpointed_at: str | None = None


class RecordingSessionOut(RecordingAssetOut):
    title: str
    status: RecordingStatus
    next_expected_sequence: int
    transcript: str
    duration_seconds: int
    moments: list[RecordingMoment] = Field(default_factory=list)
    recording_context: RecordingContext
    knowledge_base_id: str | None = None
    checkpoint_revision: int
    checkpointed_at: str | None = None
    recovery: RecordingRecoveryOut
    created_at: str
    updated_at: str
    completed_at: str | None = None


class CreateKnowledgeBaseInput(ApiModel):
    title: str = Field(min_length=1, max_length=160)
    eyebrow: str = Field(default="Personal knowledge", min_length=1, max_length=80)
    subtitle: str = Field(default="A field worth shaping", min_length=1, max_length=240)
    question: str = Field(min_length=3, max_length=1_000)
    description: str = Field(min_length=3, max_length=2_000)
    color: KnowledgeBaseColor = "green"


class UpdateKnowledgeBaseInput(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    eyebrow: str | None = Field(default=None, min_length=1, max_length=80)
    subtitle: str | None = Field(default=None, min_length=1, max_length=240)
    question: str | None = Field(default=None, min_length=3, max_length=1_000)
    description: str | None = Field(default=None, min_length=3, max_length=2_000)
    color: KnowledgeBaseColor | None = None


class KnowledgeBaseOut(ApiModel):
    id: str
    title: str
    eyebrow: str
    subtitle: str
    question: str
    description: str
    color: KnowledgeBaseColor
    status: Literal["Living", "Growing", "Outline"]
    source_count: int
    session_count: int
    pending_proposal_count: int
    created_at: str
    updated_at: str


class CreateKnowledgeSessionInput(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    focus_chapter_id: str | None = Field(default=None, max_length=160)
    selected_source_ids: list[str] = Field(default_factory=list, max_length=200)


class UpdateKnowledgeSessionInput(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    focus_chapter_id: str | None = Field(default=None, max_length=160)
    selected_source_ids: list[str] | None = Field(default=None, max_length=200)
    pinned: bool | None = None
    archived: bool | None = None


class ConversationCitationOut(ApiModel):
    id: str
    source_id: str
    source_title: str
    assertion_id: str | None = None
    quote: str
    locator: str
    status: AssertionStatus
    confidence: float
    source_revision_id: str | None = None
    block_id: str | None = None
    anchor: dict[str, object] = Field(default_factory=dict)


class ConversationContextOut(ApiModel):
    sources_considered: int = 0
    assertions_considered: int = 0
    verified_assertions: int = 0
    retrieval_mode: Literal["selected", "all"] = "all"
    responder_mode: Literal["local", "deepseek"] = "local"


class SessionMessageOut(ApiModel):
    id: str
    session_id: str
    role: MessageRole
    content: str
    citations: list[ConversationCitationOut] = Field(default_factory=list)
    context: ConversationContextOut = Field(default_factory=ConversationContextOut)
    created_at: str


class KnowledgeSessionSummaryOut(ApiModel):
    id: str
    knowledge_base_id: str
    title: str
    summary: str
    focus_chapter_id: str | None
    selected_source_ids: list[str]
    pinned: bool
    archived: bool
    parent_session_id: str | None = None
    branched_from_message_id: str | None = None
    message_count: int
    created_at: str
    updated_at: str


class KnowledgeSessionOut(KnowledgeSessionSummaryOut):
    messages: list[SessionMessageOut]


class CreateSessionMessageInput(ApiModel):
    content: str = Field(min_length=1, max_length=20_000)
    selected_source_ids: list[str] | None = Field(default=None, max_length=200)
    focus_chapter_id: str | None = Field(default=None, max_length=160)


class ConversationTurnOut(ApiModel):
    session: KnowledgeSessionSummaryOut
    user_message: SessionMessageOut
    assistant_message: SessionMessageOut


class CreateKnowledgeProposalInput(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    target_chapter_id: str | None = Field(default=None, max_length=160)


class UpdateKnowledgeProposalInput(ApiModel):
    status: ProposalStatus
    reason: str | None = Field(default=None, max_length=500)


class KnowledgeProposalOut(ApiModel):
    id: str
    knowledge_base_id: str
    session_id: str
    message_id: str
    target_chapter_id: str | None
    kind: Literal["knowledge_unit"]
    title: str
    content: str
    status: ProposalStatus
    decision_reason: str | None
    knowledge_unit_id: str | None = None
    source_session_title: str
    created_at: str
    updated_at: str


class KnowledgeUnitOut(ApiModel):
    id: str
    knowledge_base_id: str
    title: str
    kind: Literal["knowledge_unit"]
    status: Literal["provisional", "trusted", "deprecated"]
    content: str
    revision_count: int
    source_proposal_id: str
    source_session_id: str
    source_message_id: str
    target_chapter_id: str | None
    evidence_count: int
    created_at: str
    updated_at: str


class CreateArtifactInput(ApiModel):
    client_request_id: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    format: ArtifactFormat
    audience: ArtifactAudience
    title: str | None = Field(default=None, min_length=1, max_length=160)
    accepted_unit_ids: list[str] = Field(min_length=1, max_length=100)
    supersedes_artifact_id: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def normalize_and_validate_units(self) -> "CreateArtifactInput":
        normalized = [unit_id.strip() for unit_id in self.accepted_unit_ids]
        if any(not unit_id or len(unit_id) > 80 for unit_id in normalized):
            raise ValueError("acceptedUnitIds must contain valid unit identifiers")
        if len(set(normalized)) != len(normalized):
            raise ValueError("acceptedUnitIds must not contain duplicates")
        self.accepted_unit_ids = normalized
        if self.title is not None:
            self.title = self.title.strip()
            if not self.title:
                raise ValueError("title must not be blank")
        if self.supersedes_artifact_id is not None:
            self.supersedes_artifact_id = self.supersedes_artifact_id.strip()
            if not self.supersedes_artifact_id:
                raise ValueError("supersedesArtifactId must not be blank")
        return self


class ArtifactUnitSnapshotOut(ApiModel):
    unit_id: str
    revision_id: str
    revision_number: int
    title: str
    content: str
    content_hash: str
    source_proposal_id: str
    source_session_id: str
    source_message_id: str
    evidence_count: int


class ArtifactProvenanceOut(ApiModel):
    schema_version: int
    generator: str
    workspace_id: str
    knowledge_base_id: str
    knowledge_base_question: str
    accepted_only: bool
    accepted_unit_ids: list[str]
    revision_ids: list[str]


class ArtifactSummaryOut(ApiModel):
    id: str
    workspace_id: str
    knowledge_base_id: str
    lineage_id: str
    version_number: int
    supersedes_artifact_id: str | None
    format: ArtifactFormat
    audience: ArtifactAudience
    title: str
    content_hash: str
    manifest_hash: str
    accepted_unit_ids: list[str]
    unit_count: int
    created_at: str


class ArtifactOut(ArtifactSummaryOut):
    content: str
    unit_snapshots: list[ArtifactUnitSnapshotOut]
    provenance: ArtifactProvenanceOut


class KnowledgeSearchResultOut(ApiModel):
    id: str
    knowledge_base_id: str | None
    kind: Literal["knowledge_unit", "source", "session", "note"]
    title: str
    snippet: str
    meta: str
    updated_at: str
    source_session_id: str | None = None
    source_message_id: str | None = None
