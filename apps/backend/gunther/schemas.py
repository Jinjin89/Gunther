from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from gunther.brief import SettledItem
from gunther.model_profiles import Effort

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
InboxItemType = Literal["source", "quick_note"]
InboxItemState = Literal["unfiled", "needs_review"]
DeviceScope = Literal["api:access", "transcription:stream"]
# The first three are what the old builder made (versions from before agents keep them);
# new versions say "report" or "slides".
ArtifactFormat = Literal["field_guide", "teaching_path", "decision_brief", "report", "slides"]
ArtifactAudience = Literal["scientist", "student", "collaborator"]
OutputKind = Literal["report", "slides"]
# "auto": the skill chooses how the report unfolds (see skill_runner).
OutputStyle = Literal["auto", "overview", "field_guide", "teaching_path", "decision_brief"]
# How a version came about; "legacy" is a version from before agents wrote them.
OutputOrigin = Literal["legacy", "build", "rebuild", "edit", "revise"]


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
    # When the note was moved to Trash; unset while it is in use.
    trashed_at: str | None = None


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
    # When the source was moved to Trash; unset while it is in use.
    trashed_at: str | None = None


TrashItemKind = Literal["source", "note", "library"]


class TrashItemOut(ApiModel):
    """One thing moved to Trash, with everything that went in alongside it."""

    kind: TrashItemKind
    id: str
    title: str
    trashed_at: str
    # Deleted for good at this moment unless restored first.
    expires_at: str
    source_kind: SourceKind | None = None
    color: KnowledgeBaseColor | None = None
    # Sources that went to Trash with a library.
    item_count: int = 0
    # Libraries a source or note was filed in when it was trashed.
    library_titles: list[str] = Field(default_factory=list)


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
    extraction_mode: Literal["local", "model"]
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
    extraction_mode: Literal["local", "model"]
    web_search_mode: Literal["tavily", "not_configured"] = "not_configured"
    transcription_mode: Literal[
        "sensevoice_local", "qwen", "compatible", "not_configured"
    ] = "not_configured"
    transcription_provider: Literal["sensevoice", "qwen", "compatible", "none"] = "none"
    transcription_model: str = ""
    # The model that writes recording summaries ("DeepSeek · Flash"), or "off".
    summary_mode: str = "off"
    # The model that writes each capture's summary, or "off" (no model, or turned off).
    digest_mode: str = "off"
    # The models each job uses by default, e.g. "DeepSeek · Flash"; None when not set up.
    analysis_model: str | None = None
    ask_model: str | None = None
    digest_images: bool = False
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
    mode: Literal["tavily", "not_configured", "failed"]
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
    # The model that wrote it, e.g. "DeepSeek · Flash".
    engine: str


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
    # "web": a page found online (no source; ``source_id`` is empty and ``url`` is set).
    kind: Literal["library", "web"] = "library"
    url: str | None = None
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
    # Its number in the conversation's source pool: `[ref]` in an answer, the same in
    # every answer of the conversation. Older answers have none and count from 1.
    ref: int | None = None


class ConversationContextOut(ApiModel):
    sources_considered: int = 0
    assertions_considered: int = 0
    verified_assertions: int = 0
    retrieval_mode: Literal["selected", "all"] = "all"
    # "model": a language model wrote the answer; "local": the quotes themselves.
    # Messages from before model choice say "deepseek".
    responder_mode: Literal["local", "model", "deepseek"] = "local"
    # The model asked (a ref like "deepseek/deepseek-flash") and its name, even when
    # it failed and the quotes stand in; the effort asked for and the one it used.
    model: str | None = None
    model_label: str | None = None
    effort: Effort | None = None
    effort_label: str | None = None
    notes: list[str] = Field(default_factory=list)
    reasoning: str | None = None
    model_error: str | None = None
    # What the agent did to answer. `intent` is only on answers from before the source pool.
    style: str | None = None
    intent: Literal["chat", "followup", "library", "web", "both", "clarify"] | None = None
    steps: list[dict[str, object]] = Field(default_factory=list)
    web_searched: bool = False
    # The checker ran on this answer; one note per [p:n] in the text, in order, on what
    # that source does not cover.
    checked: bool = False
    support_notes: list[str] = Field(default_factory=list)
    # What this question found, for the context panel: {"subQuestions": [{id, text, query}],
    # "findings": [{ref | title, serves, says}]}. `ref` is the number in the answer's text.
    work: dict[str, object] | None = None
    # The skill the answer followed: {"name", "version", "title", "auto"}; auto when Ask
    # picked it from the message instead of the person choosing it.
    skill: dict[str, object] | None = None
    # Deep research: {"state": "asking" | "done" | "stopped_early", "budget", "used": {"searches",
    # "reads"}, "limits": {"searches", "reads"}, "coreQuestion", "doneWhen"}. "asking": the answer
    # is questions for the user, and nothing was searched.
    research: dict[str, object] | None = None
    # On a question that got no answer: "stopped" by the reader, or "failed".
    interrupted: Literal["stopped", "failed"] | None = None


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


class BriefOut(ApiModel):
    """What the conversation is for: see brief.py. `edited` lines were written by the
    user; `error` is why the last update failed, when it did."""

    goal: str = ""
    constraints: list[str] = Field(default_factory=list)
    settled: list[SettledItem] = Field(default_factory=list)
    open: list[str] = Field(default_factory=list)
    edited: list[str] = Field(default_factory=list)
    error: str | None = None


class KnowledgeSessionOut(KnowledgeSessionSummaryOut):
    messages: list[SessionMessageOut]
    brief: BriefOut = Field(default_factory=BriefOut)


class CreateSessionMessageInput(ApiModel):
    content: str = Field(min_length=1, max_length=20_000)
    # The model and effort for this answer; the Ask job's defaults when left out.
    model: str | None = Field(default=None, max_length=300)
    effort: Effort | None = None
    selected_source_ids: list[str] | None = Field(default=None, max_length=200)
    focus_chapter_id: str | None = Field(default=None, max_length=160)
    # Let the agent search the web for this question (needs a Tavily key).
    web: bool = False
    # How the answer is worded: a preset name; the default when left out or unknown.
    style: str | None = Field(default=None, max_length=40)
    # Home only: read just these libraries (the ones picked with @); none means all.
    knowledge_base_ids: list[str] | None = Field(default=None, max_length=20)
    # A skill's command, e.g. "compare" (what follows "/" in the composer).
    skill: str | None = Field(default=None, max_length=40)
    # Which of the skill's budgets to use; its first when left out.
    budget: Literal["standard", "deep"] | None = None


class AskSkillOut(ApiModel):
    command: str
    title: str
    description: str
    budgets: list[str] = Field(default_factory=list)


class FileSessionInput(ApiModel):
    knowledge_base_id: str = Field(min_length=1, max_length=160)


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


class OutputModelOut(ApiModel):
    ref: str
    label: str
    effort: str | None = None


class OutputSupplementOut(ApiModel):
    """A web result a skill added to the chosen material, and what for."""

    title: str
    url: str = ""
    role: str = "background"  # background | comparison | update | third_party
    why: str = ""


class OutputApproachOut(ApiModel):
    """What a skill decided before planning: shown above the outline, and editable."""

    question: str = ""
    answer: str = ""
    purpose: str = ""
    structure: str = ""
    structure_reason: str = ""
    pages: int | None = None
    supplements: list[OutputSupplementOut] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class SkippedStepOut(ApiModel):
    step: str
    label: str = ""
    reason: str = ""


class SkillRunOut(ApiModel):
    """The skill a version was made by, and the steps that failed and were skipped."""

    name: str
    version: int
    skipped: list[SkippedStepOut] = Field(default_factory=list)


class OutputProvenanceOut(ArtifactProvenanceOut):
    """Version 2: what the agents were asked, and which model wrote it."""

    kind: OutputKind
    style: str | None = None
    audience: ArtifactAudience
    brief: str = ""
    origin: OutputOrigin
    model: OutputModelOut | None = None
    # Made by a skill (see skillbook): which, the approach it followed, and a deck's use.
    skill: SkillRunOut | None = None
    approach: OutputApproachOut | None = None
    use: Literal["talk", "read"] | None = None


class OutputScope(ApiModel):
    """What an output is built from: the whole library, or a hand-picked mix."""

    mode: Literal["library", "selection"] = "library"
    source_ids: list[str] = Field(default_factory=list, max_length=500)
    unit_ids: list[str] = Field(default_factory=list, max_length=200)
    session_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def tidy_ids(self) -> "OutputScope":
        for name in ("source_ids", "unit_ids", "session_ids"):
            ids = [item.strip() for item in getattr(self, name)]
            if any(not item or len(item) > 80 for item in ids):
                raise ValueError("Ids must be short and not blank")
            setattr(self, name, list(dict.fromkeys(ids)))
        if self.mode == "library":
            # The whole library has nothing to pick.
            self.source_ids, self.unit_ids, self.session_ids = [], [], []
        elif not (self.source_ids or self.unit_ids or self.session_ids):
            raise ValueError("Choose at least one source, saved answer or discussion")
        return self


class OutlineItem(ApiModel):
    heading: str = Field(min_length=1, max_length=160)
    goal: str = Field(default="", max_length=400)
    # A slide's layout, when a skill planned it (see skills/slides/references/layouts.md).
    layout: str | None = Field(default=None, max_length=40)

    @model_serializer(mode="wrap")
    def _without_empty_layout(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # An outline planned without a skill reads as it always did.
        data = handler(self)
        if data.get("layout") is None:
            data.pop("layout", None)
        return data


class ApproachInput(ApiModel):
    """The approach as the person edited it; a rebuild keeps these words as they are."""

    question: str = Field(default="", max_length=600)
    answer: str = Field(default="", max_length=600)
    purpose: str = Field(default="", max_length=600)


class OutputIssueOut(ApiModel):
    claim: str
    verdict: Literal["unsupported", "unverified", "unsourced", "contradicted"]
    note: str = ""


class OutputSectionOut(ApiModel):
    index: int
    heading: str
    # A check exists for the section's current text.
    checked: bool
    issues: list[OutputIssueOut] = Field(default_factory=list)


class OutputInputsOut(ApiModel):
    sources: int = 0
    units: int = 0
    sessions: int = 0


REQUEST_ID = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class BuildOutputInput(ApiModel):
    client_request_id: str = REQUEST_ID
    kind: OutputKind
    # Reports only; slides have none. A report without one is an overview.
    style: OutputStyle | None = None
    audience: ArtifactAudience
    brief: str = Field(default="", max_length=2_000)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    scope: OutputScope = Field(default_factory=OutputScope)
    # A rebuild with the outline as the person edited it: the planner is skipped.
    outline: list[OutlineItem] | None = Field(default=None, min_length=1, max_length=14)
    supersedes_artifact_id: str | None = Field(default=None, min_length=1, max_length=80)
    # A rebuild with the approach as the person edited it (skills only).
    approach: ApproachInput | None = None
    # A deck's use: for a talk, or to be read; "auto" lets the skill tell from the brief.
    use: Literal["auto", "talk", "read"] = "auto"
    # Whether the web may be searched to add to the chosen material (when it is set up).
    web: bool = False

    @model_validator(mode="after")
    def tidy(self) -> "BuildOutputInput":
        self.style = (self.style or "auto") if self.kind == "report" else None
        if self.kind == "report":
            self.use = "auto"
        self.brief = self.brief.strip()
        if self.title is not None:
            self.title = self.title.strip()
            if not self.title:
                raise ValueError("title must not be blank")
        if self.outline is not None:
            limit = 8 if self.kind == "report" else 14
            if len(self.outline) > limit:
                raise ValueError(f"A {self.kind} has at most {limit} sections")
        return self


class ReviseOutputInput(ApiModel):
    client_request_id: str = REQUEST_ID
    instruction: str = Field(min_length=1, max_length=2_000)
    # The section or slide to change (counted from 0); the whole output when left out.
    section_index: int | None = Field(default=None, ge=0, le=50)

    @model_validator(mode="after")
    def tidy(self) -> "ReviseOutputInput":
        self.instruction = self.instruction.strip()
        if not self.instruction:
            raise ValueError("instruction must not be blank")
        return self


class EditOutputInput(ApiModel):
    client_request_id: str = REQUEST_ID
    content: str = Field(min_length=1, max_length=2_500_000)


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
    kind: OutputKind = "report"
    style: str | None = None
    origin: OutputOrigin = "legacy"


class ArtifactOut(ArtifactSummaryOut):
    content: str
    unit_snapshots: list[ArtifactUnitSnapshotOut]
    provenance: SerializeAsAny[ArtifactProvenanceOut]
    brief: str = ""
    outline: list[OutlineItem] = Field(default_factory=list)
    # The sources the text cites, numbered 1, 2, 3 in the order it first cites them.
    citations: list[ConversationCitationOut] = Field(default_factory=list)
    # What it was built from, as asked; none for a version from the old builder.
    scope: OutputScope | None = None
    inputs: OutputInputsOut = Field(default_factory=OutputInputsOut)
    model_label: str | None = None
    sections: list[OutputSectionOut] = Field(default_factory=list)


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
