import { z } from "zod";

export const sourceKinds = ["note", "paper", "link", "file", "image", "table", "recording", "course"] as const;
export const sourceKindSchema = z.enum(sourceKinds);
export type SourceKind = z.infer<typeof sourceKindSchema>;

export const assertionStatuses = ["provisional", "verified", "disputed"] as const;
export const assertionStatusSchema = z.enum(assertionStatuses);
export type AssertionStatus = z.infer<typeof assertionStatusSchema>;

export const createSourceSchema = z.object({
  title: z.string().trim().min(1).max(160),
  kind: sourceKindSchema,
  content: z.string().trim().min(3).max(1_000_000),
  knowledgeBaseId: z.string().max(160).optional(),
});
export type CreateSourceInput = z.infer<typeof createSourceSchema>;

export const notebookNoteStatuses = ["inbox", "filed", "archived"] as const;
export type NotebookNoteStatus = typeof notebookNoteStatuses[number];

export const createNotebookNoteSchema = z.object({
  title: z.string().trim().min(1).max(160).default("Untitled note"),
  content: z.string().max(50_000).default(""),
  pinned: z.boolean().default(false),
  clientCaptureId: z.string().regex(/^[A-Za-z0-9_-]{8,128}$/).optional(),
});
export type CreateNotebookNoteInput = z.input<typeof createNotebookNoteSchema>;

export const updateNotebookNoteSchema = z.object({
  title: z.string().trim().min(1).max(160).optional(),
  content: z.string().max(50_000).optional(),
  pinned: z.boolean().optional(),
  status: z.enum(notebookNoteStatuses).optional(),
});
export type UpdateNotebookNoteInput = z.infer<typeof updateNotebookNoteSchema>;

export interface NotebookNote {
  id: string;
  title: string;
  content: string;
  status: NotebookNoteStatus;
  pinned: boolean;
  knowledgeBaseId: string | null;
  promotedSourceId: string | null;
  createdAt: string;
  updatedAt: string;
  /** When the note was moved to Trash; absent or null while it is in use. */
  trashedAt?: string | null;
}

export const updateAssertionStatusSchema = z.object({
  status: assertionStatusSchema,
  reason: z.string().trim().max(500).optional(),
});
export type UpdateAssertionStatusInput = z.infer<typeof updateAssertionStatusSchema>;

export interface Asset {
  id: string;
  contentHash: string;
  originalName: string;
  mediaType: string;
  sizeBytes: number;
  downloadUrl: string;
  createdAt: string;
}

export interface WebSnapshot {
  originalUrl: string;
  finalUrl: string;
  capturedAt: string;
  status: number;
  contentType: string;
  contentHash: string;
  assetId: string;
}

export const webCaptureSchema = z
  .object({
    originalUrl: z.string().trim().min(1).max(4_096).optional(),
    url: z.string().trim().min(1).max(4_096).optional(),
    title: z.string().trim().min(1).max(160).optional(),
    notes: z.string().max(5_000).default(""),
    knowledgeBaseId: z.string().max(160).optional(),
    clientCaptureId: z.string().regex(/^[A-Za-z0-9_-]{8,128}$/).optional(),
  })
  .refine((value) => Boolean(value.originalUrl || value.url), {
    message: "Provide originalUrl or url",
  })
  .refine(
    (value) => !value.originalUrl || !value.url || value.originalUrl === value.url,
    { message: "originalUrl and url must match when both are provided" },
  );
export type WebCaptureInput = z.input<typeof webCaptureSchema> & (
  | { url: string }
  | { originalUrl: string }
);

export interface SourceSummary {
  id: string;
  title: string;
  kind: SourceKind;
  createdAt: string;
  assertionCount: number;
  entityCount: number;
  asset?: Asset | null;
  processing?: SourceProcessing;
}

export interface SourceProcessing {
  state: "pending" | "queued" | "running" | "ready" | "partial" | "failed" | "cancelled";
  jobId: string | null;
  revisionId: string | null;
  warning: string | null;
}

export interface ContentBlock {
  id: string;
  parentId: string | null;
  kind: string;
  content: string;
  locator: string;
  headings: string[];
  anchor: { page?: number; bboxPpm?: number[]; startSeconds?: number; endSeconds?: number; charStart?: number; charEnd?: number };
  payload?: Record<string, unknown>;
}

export interface SourceStructure {
  sourceId: string;
  revisionId: string | null;
  processing: SourceProcessing;
  parser: string | null;
  nextOffset: number | null;
  blocks: ContentBlock[];
}

export interface KnowledgeTopic {
  id: string;
  knowledgeBaseId: string;
  parentId: string | null;
  title: string;
  description: string;
  position: number;
  version: number;
  blockIds: string[];
  /** Whole sources filed under this topic. */
  sourceIds: string[];
}

export type TopicInput = Pick<KnowledgeTopic, "title" | "description" | "parentId" | "position"> & {
  version?: number;
  /** Whole sources to file under a new topic. */
  sourceIds?: string[];
};

/** A group of papers, not yet in a topic, that could become one. */
export interface TopicSuggestion {
  key: string;
  title: string;
  keywords: string[];
  sourceIds: string[];
  /** Titles of the most typical papers in the group. */
  examples: string[];
  years: [number, number] | null;
}

export interface TopicSuggestions {
  suggestions: TopicSuggestion[];
  /** Papers not in any topic yet (copies counted once). */
  unfiled: number;
  method: "semantic" | "keywords" | null;
  /** Why there are no suggestions, when there are none. */
  reason: string | null;
}

export interface TopicOverviewCitation {
  number: number;
  sourceId: string;
  sourceTitle: string;
  blockId: string | null;
  revisionId: string;
  quote: string;
}

/** A topic's written overview, cited to its papers' abstracts. */
export interface TopicOverview {
  topicId: string;
  markdown: string;
  citations: TopicOverviewCitation[];
  /** "local" (the papers' own abstracts) or "deepseek:<model>". */
  method: string;
  sourceCount: number;
  createdAt: string;
}

/** What a source says about itself: read from its text, without a model. */
export interface SourcePaper {
  sourceId: string;
  revisionId: string;
  title: string;
  authors: string;
  year: number | null;
  doi: string | null;
  arxivId: string | null;
  abstract: string;
  abstractBlockIds: string[];
  outline: string[];
  summaryMarkdown: string;
  /** Other sources that are copies of the same paper. */
  copies: { id: string; title: string }[];
}

/** One passage a summary's key point came from. */
export interface DigestCitation {
  number: number;
  blockId: string;
  quote: string;
  locator: string;
}

/** A capture's summary, written after it was read. Never part of the evidence. */
export interface SourceDigest {
  sourceId: string;
  revisionId: string;
  profile: "lecture" | "meeting" | "memo" | "image" | "table" | "paper" | "document" | "web" | "note";
  method: string;
  /** Who wrote it: a provider's name ("DeepSeek", "Kimi", "My server"). */
  engine: string;
  model: string | null;
  suggestedTitle: string | null;
  overview: string;
  keyPoints: { text: string; citations: DigestCitation[] }[];
  actionItems: string[];
  openQuestions: string[];
  terms: string[];
  markdown: string;
  updatedAt: string;
}

export interface SourceDigestState {
  sourceId: string;
  /** reading: the source is still being read; writing: its summary is on its way. */
  state: "reading" | "writing" | "ready" | "none" | "failed" | "off";
  /** The summary describes an earlier version of the source. */
  stale: boolean;
  method: string | null;
  /** Why there are no summaries: no OpenAI or DeepSeek key, or turned off. */
  offReason: "no_key" | "setting" | null;
  /** Why the last attempt failed, when it did. */
  error: string | null;
  digest: SourceDigest | null;
}

export const inboxItemTypes = ["source", "quick_note", "knowledge_suggestion"] as const;
export type InboxItemType = typeof inboxItemTypes[number];
export const inboxItemStates = ["unfiled", "needs_review", "held"] as const;
export type InboxItemState = typeof inboxItemStates[number];

export interface InboxKnowledgeBaseRef {
  id: string;
  title: string;
}

export interface InboxItem {
  id: string;
  itemType: InboxItemType;
  state: InboxItemState;
  title: string;
  preview: string;
  sourceKind: SourceKind | null;
  knowledgeBases: InboxKnowledgeBaseRef[];
  sourceId: string | null;
  noteId: string | null;
  proposalId: string | null;
  proposalStatus: "pending" | "accepted" | "held" | "rejected" | null;
  /** Number of provisional claims that still need review. */
  assertionCount: number;
  createdAt: string;
  updatedAt: string;
}

export const fileSourceSchema = z.object({
  knowledgeBaseId: z.string().trim().min(1).max(160),
});
export type FileSourceInput = z.infer<typeof fileSourceSchema>;

export interface FileSourceResult {
  source: SourceSummary;
  knowledgeBase: InboxKnowledgeBaseRef;
  membershipCreated: boolean;
}

export interface SourceDetail extends SourceSummary {
  content: string;
  assertions: Assertion[];
  webSnapshot: WebSnapshot | null;
  /** Libraries this source is filed in; empty while it waits in Inbox. */
  knowledgeBases?: InboxKnowledgeBaseRef[];
  /** When the source was moved to Trash; absent or null while it is in use. */
  trashedAt?: string | null;
}

/** Keyword and semantic search on this device. */
export interface RetrievalStatus {
  /** Semantic search is running with the local model. */
  semanticConfigured: boolean;
  /** Why semantic search is off, when it is. */
  semanticOffReason: string | null;
  model: string | null;
  /** The last semantic lookup fell back to keywords, and why. */
  warning: string | null;
  /** Passages of current revisions, and how many of them have vectors. */
  passages: number;
  embeddedBlocks: number;
  /** Sources still waiting to be embedded. */
  embeddingJobs: number;
}

/** Where this workspace keeps its libraries on disk. */
export interface StorageStatus {
  libraryRoot: string | null;
  foldersEnabled: boolean;
  /** Why a configured library root is not in use. */
  problem: string | null;
  lastSyncedAt: string | null;
  lastError: string | null;
}

/** A level a model can do, named its own way ("High", or "On" where thinking only switches). */
export interface EffortLevel {
  id: Effort;
  label: string;
}

/** A model Ask can use now, for the model menu. */
export interface ModelChoice {
  ref: string;
  provider: string;
  label: string;
  display: string;
  vision: boolean;
  /** Empty when the model has no thinking setting. */
  levels: EffortLevel[];
}

export interface ModelMenu {
  models: ModelChoice[];
  default: { model: string; effort: Effort } | null;
  efforts: EffortLevel[];
}

export type ProviderKind = "deepseek" | "kimi" | "glm" | "qwen" | "openai" | "compatible";

export interface ProviderModel {
  id: string;
  ref: string;
  label: string;
  vision: boolean;
  visionBuiltIn: boolean;
  dialect: string;
  dialectBuiltIn: string;
  levels: EffortLevel[];
}

export interface ModelProvider {
  id: string;
  name: string;
  kind: ProviderKind;
  baseUrl: string;
  keySet: boolean;
  keyHint: string | null;
  /** saved here, the OpenAI services key, the environment (.env), or none. */
  keySource: "saved" | "environment" | "none";
  keyOptional: boolean;
  note: string;
  models: ProviderModel[];
  status: {
    state: ServiceState;
    summary: string;
    check: (ServiceCheck & { checkedAt: string }) | null;
  };
}

export interface ModelRole {
  id: "analysis" | "ask" | "photos";
  label: string;
  description: string;
  needsVision: boolean;
  model: string | null;
  effort: Effort;
  problem: string | null;
}

export interface ProviderPreset {
  kind: ProviderKind;
  name: string;
  baseUrl: string;
  models: string[];
  keyOptional: boolean;
  note: string;
}

export interface ModelsOverview {
  persisted: boolean;
  /** Providers come from LLM_* settings until some are saved here. */
  fromEnvironment: boolean;
  providers: ModelProvider[];
  roles: ModelRole[];
  presets: ProviderPreset[];
  dialects: { id: string; label: string; levels: EffortLevel[] }[];
  efforts: EffortLevel[];
}

export interface ProviderInput {
  kind?: ProviderKind;
  name?: string;
  baseUrl?: string;
  /** Left out: keep the saved key. Empty: remove it. */
  apiKey?: string;
  models?: (string | { id: string; label?: string; vision?: boolean | null; dialect?: string | null })[];
}

export interface ProviderTestResult extends ServiceCheck {
  /** The models the provider offers. */
  available: string[];
  overview?: ModelsOverview;
}

/** How a service setting is edited on the Settings page. */
export type ServiceFieldKind = "text" | "secret" | "url" | "select" | "toggle" | "number";
export type ServiceSettingValue = string | number | boolean | null;

export interface ServiceFieldOption {
  value: string;
  label: string;
  description: string;
  /** Values other fields take when this option is picked (a provider's address, say). */
  presets: Record<string, string>;
}

export interface ServiceField {
  key: string;
  label: string;
  kind: ServiceFieldKind;
  help: string;
  placeholder: string;
  options: ServiceFieldOption[];
  min: number | null;
  max: number | null;
  step: number | null;
  required: boolean;
  pattern: string | null;
  patternMessage: string;
  /** Shown only while another field has one of these values. */
  shownWhen: { key: string; values: string[] } | null;
  /** Always null for secrets: the service keeps them. */
  value: ServiceSettingValue;
  default: ServiceSettingValue;
  isSet: boolean;
  /** A secret's last four characters. */
  hint: string | null;
  /** Where the value in use comes from. */
  source: "saved" | "environment" | "default";
  envVar: string;
}

export type ServiceState = "configured" | "not_configured" | "error" | "off";

export interface ServiceCheck {
  ok: boolean;
  message: string;
  warning: string | null;
}

/** An outside service (language model, transcription…) and its settings. */
export interface ServiceSettings {
  id: string;
  title: string;
  description: string;
  note: string;
  fields: ServiceField[];
  canTest: boolean;
  status: {
    state: ServiceState;
    summary: string;
    /** The last connection test of the values in use, if any. */
    checkedAt: string | null;
    check: ServiceCheck | null;
  };
}

export interface ServiceSettingsList {
  /** Saved changes outlast a restart. */
  persisted: boolean;
  services: ServiceSettings[];
}

export interface ServiceTestResult extends ServiceCheck {
  checkedAt: string;
  service: ServiceSettings;
}

export type TrashItemKind = "source" | "note" | "library";

/** One thing moved to Trash, with everything that went in alongside it. */
export interface TrashItem {
  kind: TrashItemKind;
  id: string;
  title: string;
  trashedAt: string;
  /** Deleted for good at this moment unless restored first. */
  expiresAt: string;
  sourceKind: SourceKind | null;
  /** A library's identity colour. */
  color: "green" | "blue" | "clay" | null;
  /** Sources that went to Trash with a library. */
  itemCount: number;
  /** Libraries a source or note was filed in when it was trashed. */
  libraryTitles: string[];
}

export interface Entity {
  id: string;
  label: string;
  type: string;
  aliases: string[];
  createdAt: string;
}

export interface Assertion {
  id: string;
  predicate: string;
  confidence: number;
  status: AssertionStatus;
  qualifiers: Record<string, string>;
  subject: Pick<Entity, "id" | "label" | "type">;
  object: Pick<Entity, "id" | "label" | "type">;
  source: Pick<SourceSummary, "id" | "title" | "kind">;
  evidence: {
    id: string;
    stance: "supports" | "refutes" | "mentions";
    quote: string;
    locator: string;
  }[];
  createdAt: string;
}

export interface Overview {
  counts: {
    sources: number;
    entities: number;
    assertions: number;
    provisional: number;
  };
  recentSources: SourceSummary[];
  recentAssertions: Assertion[];
}

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  assertionCount: number;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  status: AssertionStatus;
  confidence: number;
}

export interface KnowledgeGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface ImportResult {
  source: SourceSummary;
  created: {
    entities: number;
    assertions: number;
  };
  extractionMode: "local" | "model";
  duplicate: boolean;
}

export interface AssetCaptureResult {
  asset: Asset;
  importResult: ImportResult;
}

export interface WebCaptureResult {
  asset: Asset;
  importResult: ImportResult;
  snapshot: WebSnapshot;
  idempotentReplay: boolean;
}

export interface FileNotebookNoteResult {
  note: NotebookNote;
  importResult: ImportResult;
}

export interface ApiError {
  error: string;
  details?: unknown;
}

export const createKnowledgeBaseSchema = z.object({
  title: z.string().trim().min(1).max(160),
  eyebrow: z.string().trim().min(1).max(80).default("Personal knowledge"),
  subtitle: z.string().trim().min(1).max(240).default("A field worth shaping"),
  question: z.string().trim().min(3).max(1_000),
  description: z.string().trim().min(3).max(2_000),
  color: z.enum(["green", "blue", "clay"]).default("green"),
});
export type CreateKnowledgeBaseInput = z.infer<typeof createKnowledgeBaseSchema>;
export const updateKnowledgeBaseSchema = createKnowledgeBaseSchema.partial();
export type UpdateKnowledgeBaseInput = z.infer<typeof updateKnowledgeBaseSchema>;

export interface KnowledgeBaseMetadata {
  id: string;
  title: string;
  eyebrow: string;
  subtitle: string;
  question: string;
  description: string;
  color: "green" | "blue" | "clay";
  status: "Living" | "Growing" | "Outline";
  sourceCount: number;
  sessionCount: number;
  pendingProposalCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface ConversationCitation {
  id: string;
  /** "web": a page found online. It has a url and no source (sourceId is empty). */
  kind?: "library" | "web";
  url?: string | null;
  sourceId: string;
  sourceTitle: string;
  assertionId: string | null;
  quote: string;
  locator: string;
  status: AssertionStatus;
  confidence: number;
  sourceRevisionId?: string | null;
  blockId?: string | null;
  anchor?: ContentBlock["anchor"];
}

export interface ConversationContext {
  sourcesConsidered: number;
  assertionsConsidered: number;
  verifiedAssertions: number;
  retrievalMode: "selected" | "all";
  /** "model": a language model wrote it; "local": the quotes themselves. Older answers say "deepseek". */
  responderMode: "local" | "model" | "deepseek";
  /** The model asked ("deepseek/deepseek-flash") and its name, even when it failed. */
  model?: string | null;
  modelLabel?: string | null;
  effort?: Effort | null;
  /** The level the model was sent, in its own words ("High", "On"); null when it has none. */
  effortLabel?: string | null;
  /** What the answer admits to, e.g. a level the model does not have. */
  notes?: string[];
  reasoning?: string | null;
  /** Why the chosen model's answer is not shown; the quotes stand in. */
  modelError?: string | null;
  /** What the agent made of the question. */
  intent?: "chat" | "followup" | "library" | "web" | "both" | "clarify" | null;
  /** What it did to answer: the searches it ran, in order. */
  steps?: AgentStep[];
  webSearched?: boolean;
  /** On a question that got no answer: stopped by the reader, or failed. */
  interrupted?: "stopped" | "failed" | null;
}

export interface AgentStep {
  tool: "search_library" | "search_web";
  label: string;
  query: string;
  found: number;
  error: string | null;
}

export interface SessionMessage {
  id: string;
  sessionId: string;
  role: "user" | "assistant";
  content: string;
  citations: ConversationCitation[];
  context: ConversationContext;
  createdAt: string;
}

export interface KnowledgeSessionSummary {
  id: string;
  knowledgeBaseId: string;
  title: string;
  summary: string;
  focusChapterId: string | null;
  selectedSourceIds: string[];
  pinned: boolean;
  archived: boolean;
  parentSessionId: string | null;
  branchedFromMessageId: string | null;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface KnowledgeSession extends KnowledgeSessionSummary {
  messages: SessionMessage[];
}

export const createKnowledgeSessionSchema = z.object({
  title: z.string().trim().min(1).max(160).optional(),
  focusChapterId: z.string().max(160).nullable().optional(),
  selectedSourceIds: z.array(z.string()).max(200).default([]),
});
export type CreateKnowledgeSessionInput = z.infer<typeof createKnowledgeSessionSchema>;

export const updateKnowledgeSessionSchema = z.object({
  title: z.string().trim().min(1).max(160).optional(),
  focusChapterId: z.string().max(160).nullable().optional(),
  selectedSourceIds: z.array(z.string()).max(200).optional(),
  pinned: z.boolean().optional(),
  archived: z.boolean().optional(),
});
export type UpdateKnowledgeSessionInput = z.infer<typeof updateKnowledgeSessionSchema>;

/** One thinking-effort scale for every model; each model maps it to its own setting. */
export const EFFORTS = ["off", "low", "medium", "high", "max"] as const;
export type Effort = (typeof EFFORTS)[number];

export const createSessionMessageSchema = z.object({
  content: z.string().trim().min(1).max(20_000),
  selectedSourceIds: z.array(z.string()).max(200).optional(),
  focusChapterId: z.string().max(160).nullable().optional(),
  /** The model and effort for this answer; the Ask job's defaults when left out. */
  model: z.string().max(300).optional(),
  effort: z.enum(EFFORTS).optional(),
  /** Let the agent search the web for this question. */
  web: z.boolean().optional(),
  /** Home only: read just these libraries; none means all of them. */
  knowledgeBaseIds: z.array(z.string()).max(20).optional(),
});
export type CreateSessionMessageInput = z.infer<typeof createSessionMessageSchema>;

export interface ConversationTurn {
  session: KnowledgeSessionSummary;
  userMessage: SessionMessage;
  assistantMessage: SessionMessage;
}

export type KnowledgeProposalStatus = "pending" | "accepted" | "held" | "rejected";

export interface KnowledgeProposal {
  id: string;
  knowledgeBaseId: string;
  sessionId: string;
  messageId: string;
  targetChapterId: string | null;
  kind: "knowledge_unit";
  title: string;
  content: string;
  status: KnowledgeProposalStatus;
  decisionReason: string | null;
  knowledgeUnitId: string | null;
  sourceSessionTitle: string;
  createdAt: string;
  updatedAt: string;
}

export interface KnowledgeUnit {
  id: string;
  knowledgeBaseId: string;
  title: string;
  kind: "knowledge_unit";
  status: "provisional" | "trusted" | "deprecated";
  content: string;
  revisionCount: number;
  sourceProposalId: string;
  sourceSessionId: string;
  sourceMessageId: string;
  targetChapterId: string | null;
  evidenceCount: number;
  createdAt: string;
  updatedAt: string;
}

export const artifactFormats = ["field_guide", "teaching_path", "decision_brief"] as const;
export type ArtifactFormat = typeof artifactFormats[number];
export const artifactAudiences = ["scientist", "student", "collaborator"] as const;
export type ArtifactAudience = typeof artifactAudiences[number];

export const createArtifactSchema = z.object({
  clientRequestId: z.string().regex(/^[A-Za-z0-9_-]{8,128}$/),
  format: z.enum(artifactFormats),
  audience: z.enum(artifactAudiences),
  title: z.string().trim().min(1).max(160).optional(),
  acceptedUnitIds: z.array(z.string().trim().min(1).max(80)).min(1).max(100)
    .refine((items) => new Set(items).size === items.length, "Unit IDs must be unique"),
  supersedesArtifactId: z.string().trim().min(1).max(80).optional(),
});
export type CreateArtifactInput = z.infer<typeof createArtifactSchema>;

export interface ArtifactUnitSnapshot {
  unitId: string;
  revisionId: string;
  revisionNumber: number;
  title: string;
  content: string;
  contentHash: string;
  sourceProposalId: string;
  sourceSessionId: string;
  sourceMessageId: string;
  evidenceCount: number;
}

export interface ArtifactProvenance {
  schemaVersion: number;
  generator: string;
  workspaceId: string;
  knowledgeBaseId: string;
  knowledgeBaseQuestion: string;
  acceptedOnly: boolean;
  acceptedUnitIds: string[];
  revisionIds: string[];
}

export interface ArtifactSummary {
  id: string;
  workspaceId: string;
  knowledgeBaseId: string;
  lineageId: string;
  versionNumber: number;
  supersedesArtifactId: string | null;
  format: ArtifactFormat;
  audience: ArtifactAudience;
  title: string;
  contentHash: string;
  manifestHash: string;
  acceptedUnitIds: string[];
  unitCount: number;
  createdAt: string;
}

export interface Artifact extends ArtifactSummary {
  content: string;
  unitSnapshots: ArtifactUnitSnapshot[];
  provenance: ArtifactProvenance;
}

export interface KnowledgeSearchResult {
  id: string;
  knowledgeBaseId: string | null;
  kind: "knowledge_unit" | "source" | "session" | "note";
  title: string;
  snippet: string;
  meta: string;
  updatedAt: string;
  sourceSessionId: string | null;
  sourceMessageId: string | null;
}

export type SearchScope = "knowledge" | "web" | "both";

export interface WebSearchSource {
  title: string;
  url: string;
  snippet: string | null;
}

export interface WebSearchResult {
  query: string;
  answer: string;
  sources: WebSearchSource[];
  mode: "tavily" | "not_configured" | "failed";
  message: string | null;
}

export interface LectureSummary {
  overview: string;
  keyPoints: string[];
  actionItems: string[];
  openQuestions: string[];
  terms: string[];
  /** The model that wrote it, e.g. "DeepSeek · Flash". */
  engine: string;
}

export interface CreateLectureSummaryInput {
  title: string;
  transcript: string;
  durationSeconds: number;
}

export interface RecordingAsset {
  id: string;
  fileName: string;
  contentType: string;
  sizeBytes: number;
  storedAt: string;
}

export type RecordingStatus = "capturing" | "completed" | "failed";
export type RecordingContext = "lecture" | "meeting" | "memo";

export interface RecordingMoment {
  seconds: number;
  label: string;
}

export interface RecordingCheckpointInput {
  expectedRevision: number;
  transcript: string;
  durationSeconds: number;
  moments: RecordingMoment[];
  recordingContext: RecordingContext;
  knowledgeBaseId: string | null;
}

export interface RecordingRecoveryMetadata {
  canResume: boolean;
  audioAvailable: boolean;
  nextExpectedSequence: number;
  checkpointRevision: number;
  checkpointedAt: string | null;
}

/** Durable server-side state returned by recording session endpoints. */
export interface RecordingSession extends RecordingAsset {
  title: string;
  status: RecordingStatus;
  nextExpectedSequence: number;
  transcript: string;
  durationSeconds: number;
  moments: RecordingMoment[];
  recordingContext: RecordingContext;
  knowledgeBaseId: string | null;
  checkpointRevision: number;
  checkpointedAt: string | null;
  recovery: RecordingRecoveryMetadata;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
}

/** Metadata a client sends alongside one idempotent audio chunk. */
export interface RecordingChunkIdentity {
  sequence: number;
  checksum: string;
}

export const createKnowledgeProposalSchema = z.object({
  title: z.string().trim().min(1).max(160).optional(),
  targetChapterId: z.string().max(160).nullable().optional(),
});
export type CreateKnowledgeProposalInput = z.infer<typeof createKnowledgeProposalSchema>;

export const updateKnowledgeProposalSchema = z.object({
  status: z.enum(["pending", "accepted", "held", "rejected"]),
  reason: z.string().trim().max(500).optional(),
});
export type UpdateKnowledgeProposalInput = z.infer<typeof updateKnowledgeProposalSchema>;
