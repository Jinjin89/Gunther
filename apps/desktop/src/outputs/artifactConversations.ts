import type { ArtifactBriefState, ArtifactConversationDetail, ArtifactConversationMessage, ArtifactConversationSummary, ArtifactSummary, ImportedArtifactConversation, ImportedArtifactTurn, OutputScope } from "@gunther/contracts";
import { useMemo, useSyncExternalStore } from "react";
import { knowledgeApi } from "../api";
import { log } from "../log";
import type { AskChoice } from "../models/askModel";
import { persistentStorage } from "../persistentStorage";

/** A conversation as the page reads it: the service's, with this device's draft, model and selection. */
export interface ArtifactConversation {
  id: string;
  title: string;
  untitled: boolean;
  scope: OutputScope;
  choice: AskChoice | null;
  draft: string;
  createdAt: string;
  updatedAt: string;
  /** The service has this conversation. A new one is only kept here until its first message. */
  saved: boolean;
  /** The agent asked a question and has not had its answer. */
  waiting: boolean;
  artifactCount: number;
  /** The version last opened in the preview. */
  selectedArtifactId: string | null;
  /** Its messages, brief and works; null until it has been read. */
  detail: ArtifactConversationDetail | null;
}
interface ConversationState { activeId: string; conversations: ArtifactConversation[]; problem: string | null }

/** What this device keeps for a conversation: only what the service does not. */
interface Local {
  draft: string;
  choice: AskChoice | null;
  scope: OutputScope | null;
  selectedArtifactId: string | null;
  createdAt: string;
}
/** Version 2 of the device store. Version 1 held whole conversations, which the service keeps now. */
interface Stored { version: 2; activeId: string; imported: boolean; legacy: ImportedArtifactConversation[]; unsaved: string[]; local: Record<string, Local> }

export const NEW_CONVERSATION = "New conversation";
/** The conversation that holds versions made before conversations were kept. */
export const EARLIER_WORK = "earlier-work";
export const libraryScope = (): OutputScope => ({ mode: "library", sourceIds: [], unitIds: [], sessionIds: [] });
export const artifactRequestId = () => globalThis.crypto?.randomUUID?.() ?? `request_${Date.now()}_${Math.random().toString(36).slice(2)}`;

const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");
const validScope = (value: unknown): value is OutputScope => {
  if (!value || typeof value !== "object") return false;
  const scope = value as Partial<OutputScope>;
  return (scope.mode === "library" || scope.mode === "selection") && strings(scope.sourceIds) && strings(scope.unitIds) && strings(scope.sessionIds);
};
const validChoice = (value: unknown): value is AskChoice => {
  const choice = value as Partial<AskChoice> | null;
  return Boolean(choice && typeof choice.model === "string" && typeof choice.effort === "string" && ["off", "low", "medium", "high", "max"].includes(choice.effort));
};
const STATUSES = ["running", "saved", "failed", "stopped", "interrupted"];
const CHUNK = 100;

const blankLocal = (): Local => ({ draft: "", choice: null, scope: null, selectedArtifactId: null, createdAt: new Date().toISOString() });
const hasContent = (local: Local | undefined) => Boolean(local && (local.draft || local.choice || local.scope || local.selectedArtifactId));

/** The turns a device kept (version 1), as the service's import takes them. A turn that was running when the page closed was interrupted. */
function importable(item: Record<string, unknown>): ImportedArtifactConversation | null {
  const turns: ImportedArtifactTurn[] = (Array.isArray(item.turns) ? item.turns : []).filter((turn): turn is Record<string, unknown> => Boolean(turn) && typeof turn === "object" && typeof turn.id === "string" && typeof turn.instruction === "string" && STATUSES.includes(turn.status as string)).map((turn) => {
    const result = turn.result as { id?: unknown } | null;
    return {
      id: turn.id as string,
      instruction: (turn.instruction as string).slice(0, 20_000),
      status: turn.status === "running" ? "interrupted" : turn.status as ImportedArtifactTurn["status"],
      artifactId: typeof result?.id === "string" ? result.id : null,
      ...(typeof turn.createdAt === "string" ? { createdAt: turn.createdAt } : {}),
      error: typeof turn.error === "string" ? turn.error : null,
    };
  });
  if (!turns.length || typeof item.id !== "string" || item.id.length > 80) return null;
  return { id: item.id, ...(item.untitled === false && typeof item.title === "string" ? { title: item.title.slice(0, 160) } : {}), ...(validScope(item.scope) ? { scope: item.scope } : {}), ...(typeof item.createdAt === "string" ? { createdAt: item.createdAt } : {}), turns: turns.slice(-500) };
}

/**
 * The Artifacts conversations of one library, kept by the service. This device keeps only what
 * is its own: each conversation's draft, model and source selection, and which conversation is
 * open. A conversation that was kept on the device before (version 1 of this store) is sent to
 * the service once; until that works, it stays here and is sent again next time.
 */
export class ArtifactConversationStore {
  private state: ConversationState = { activeId: "", conversations: [], problem: null };
  private listeners = new Set<() => void>();
  private summaries: ArtifactConversationSummary[] = [];
  private details = new Map<string, ArtifactConversationDetail>();
  private local: Record<string, Local> = {};
  /** Conversations made here and not yet sent: they join the service with their first message. */
  private unsaved: string[] = [];
  private legacy: ImportedArtifactConversation[] = [];
  private imported = true;
  private activeId = "";
  private storageProblem: string | null = null;
  private importing: Promise<void> | null = null;
  readonly key: string;
  constructor(private readonly workspaceId: string, private readonly baseId: string) {
    this.key = `gunther:artifact-conversations:${encodeURIComponent(workspaceId)}:${encodeURIComponent(baseId)}`;
    this.read();
    if (!this.activeId) this.activeId = this.fresh().id;
    this.derive();
  }

  private read() {
    try {
      const saved = JSON.parse(persistentStorage.getItem(this.key) ?? "null") as Record<string, unknown> | null;
      if (saved?.version === 2 && saved.local && typeof saved.local === "object") {
        this.imported = saved.imported === true;
        this.legacy = Array.isArray(saved.legacy) ? saved.legacy as ImportedArtifactConversation[] : [];
        for (const [id, value] of Object.entries(saved.local as Record<string, Partial<Local>>)) {
          if (!value || typeof value !== "object") continue;
          this.local[id] = { draft: typeof value.draft === "string" ? value.draft : "", choice: validChoice(value.choice) ? value.choice : null, scope: validScope(value.scope) ? value.scope : null, selectedArtifactId: typeof value.selectedArtifactId === "string" ? value.selectedArtifactId : null, createdAt: typeof value.createdAt === "string" ? value.createdAt : new Date().toISOString() };
        }
        this.unsaved = strings(saved.unsaved) ? saved.unsaved.filter((id) => id in this.local) : [];
        if (typeof saved.activeId === "string") this.activeId = saved.activeId;
      } else if (saved?.version === 1 && Array.isArray(saved.conversations)) {
        this.imported = false;
        for (const item of saved.conversations as Array<Record<string, unknown>>) {
          if (!item || typeof item.id !== "string" || typeof item.draft !== "string") continue;
          this.local[item.id] = { draft: item.draft, choice: validChoice(item.choice) ? item.choice : null, scope: validScope(item.scope) ? item.scope : null, selectedArtifactId: typeof item.selectedArtifactId === "string" ? item.selectedArtifactId : null, createdAt: typeof item.createdAt === "string" ? item.createdAt : new Date().toISOString() };
          const found = importable(item);
          if (found) this.legacy.push(found);
          // A conversation with no turns is only a draft: it stays a new conversation here.
          else if (this.local[item.id]!.draft) this.unsaved.push(item.id);
        }
        if (typeof saved.activeId === "string") this.activeId = saved.activeId;
      }
    } catch { /* A malformed preference must not hide what the service has. */ }
  }
  private fresh(scope: OutputScope | null = null, choice: AskChoice | null = null) {
    const id = artifactRequestId();
    this.local[id] = { ...blankLocal(), scope, choice };
    this.unsaved = [id, ...this.unsaved];
    return { id };
  }

  snapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  get(id: string) { return this.state.conversations.find((item) => item.id === id); }
  active() { return this.get(this.state.activeId)!; }

  /** The page's view: the service's conversations, then the new ones made here, each with this device's own. */
  private derive() {
    const made = (id: string, summary: ArtifactConversationSummary | null): ArtifactConversation => {
      const detail = this.details.get(id) ?? null;
      const local = this.local[id] ?? blankLocal();
      const title = detail?.title ?? summary?.title ?? NEW_CONVERSATION;
      return {
        id, title, untitled: title === NEW_CONVERSATION, scope: local.scope ?? detail?.scope ?? libraryScope(), choice: local.choice, draft: local.draft,
        createdAt: detail?.createdAt ?? local.createdAt, updatedAt: summary?.updatedAt ?? detail?.updatedAt ?? local.createdAt, saved: summary !== null || detail !== null,
        waiting: detail ? detail.pending !== null : summary?.waiting ?? false, artifactCount: detail ? detail.works.length : summary?.artifactCount ?? 0,
        selectedArtifactId: local.selectedArtifactId, detail,
      };
    };
    const known = new Set(this.summaries.map((item) => item.id));
    this.unsaved = this.unsaved.filter((id) => !known.has(id));
    const conversations = [...this.unsaved.filter((id) => !known.has(id)).map((id) => made(id, null)), ...this.summaries.map((item) => made(item.id, item))];
    if (!conversations.some((item) => item.id === this.activeId)) this.activeId = conversations[0]?.id ?? this.fresh().id;
    if (!conversations.some((item) => item.id === this.activeId)) conversations.unshift(made(this.activeId, null));
    this.state = { activeId: this.activeId, conversations, problem: this.storageProblem };
    this.listeners.forEach((listener) => listener());
  }
  private save() {
    // A new conversation nobody typed in is not kept, unless it is the one open.
    this.unsaved = this.unsaved.filter((id) => id === this.activeId || hasContent(this.local[id]));
    const keep = new Set([...this.summaries.map((item) => item.id), ...this.unsaved, this.activeId]);
    const local = Object.fromEntries(Object.entries(this.local).filter(([id, value]) => keep.has(id) && (hasContent(value) || id === this.activeId || this.unsaved.includes(id))));
    const stored: Stored = { version: 2, activeId: this.activeId, imported: this.imported, legacy: this.imported ? [] : this.legacy, unsaved: this.unsaved, local };
    try { persistentStorage.setItem(this.key, JSON.stringify(stored)); this.storageProblem = null; }
    catch { this.storageProblem = "Conversation drafts could not be saved on this device. Saved artifacts remain available."; }
  }
  private commit() { this.save(); this.derive(); }

  /** What this device keeps for a conversation: its draft, model, source selection or open version. A new source selection is sent to the service. */
  update(id: string, patch: Partial<Pick<ArtifactConversation, "draft" | "choice" | "scope" | "selectedArtifactId">>) {
    this.local[id] = { ...(this.local[id] ?? blankLocal()), ...patch };
    this.commit();
    if (patch.scope && this.get(id)?.saved) {
      void knowledgeApi.updateArtifactConversation(this.baseId, id, { scope: patch.scope }, this.workspaceId).catch((reason) => {
        log.warn("artifacts", `The source selection was not saved to the conversation: ${reason instanceof Error ? reason.message : String(reason)}`);
      });
    }
  }
  select(id: string) { if (this.get(id)) { this.activeId = id; this.commit(); } }
  /** A conversation made here; the service gets it with its first message. */
  create(scope: OutputScope = libraryScope(), choice: AskChoice | null = null) {
    const { id } = this.fresh(scope, choice);
    this.activeId = id;
    this.commit();
    return this.get(id)!;
  }
  /** The service has this conversation (a new one is sent now). */
  async ensure(id: string) {
    const conversation = this.get(id);
    if (!conversation || conversation.saved) return;
    const detail = await knowledgeApi.createArtifactConversation(this.baseId, { id, scope: conversation.scope }, this.workspaceId);
    this.details.set(id, detail);
    this.summaries = [{ id, title: detail.title, updatedAt: detail.updatedAt, artifactCount: detail.artifactCount, waiting: detail.waiting }, ...this.summaries.filter((item) => item.id !== id)];
    this.unsaved = this.unsaved.filter((item) => item !== id);
    this.commit();
  }

  /** Read a conversation again from the service. */
  async open(id: string) {
    const detail = await knowledgeApi.artifactConversation(this.baseId, id, this.workspaceId);
    this.details.set(id, detail);
    if (!this.summaries.some((item) => item.id === id)) this.summaries = [detail, ...this.summaries];
    else this.summaries = this.summaries.map((item) => item.id === id ? { id, title: detail.title, updatedAt: detail.updatedAt, artifactCount: detail.artifactCount, waiting: detail.waiting } : item);
    this.summaries = [...this.summaries].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    this.unsaved = this.unsaved.filter((item) => item !== id);
    this.commit();
    return detail;
  }
  /** The list again, and one conversation read in full. */
  async sync(id: string) {
    this.summaries = await knowledgeApi.artifactConversations(this.baseId, this.workspaceId);
    this.derive();
    return this.open(id);
  }
  /** The brief as the agent or the panel left it. */
  setBrief(id: string, state: ArtifactBriefState) {
    const detail = this.details.get(id);
    if (!detail) return;
    const work = detail.works.find((item) => item.key === detail.active);
    this.details.set(id, { ...detail, brief: state.brief, works: work && state.spec ? detail.works.map((item) => item === work ? { ...item, spec: state.spec! } : item) : detail.works });
    this.derive();
  }
  /** Add a message of the user's to a conversation until the service gives the stored ones. */
  messages(id: string): ArtifactConversationMessage[] { return this.details.get(id)?.messages ?? []; }

  /**
   * Read the library's conversations, sending the device's own first: the ones this device kept
   * before the service did, and one for the versions that name no conversation. Each goes once
   * (the service skips ids it has). If sending fails, the service's list is shown anyway and
   * the next load tries again.
   */
  async load(items: ArtifactSummary[]): Promise<ArtifactConversation> {
    this.summaries = await knowledgeApi.artifactConversations(this.baseId, this.workspaceId);
    this.importing ??= this.sendKept(items).finally(() => { this.importing = null; });
    await this.importing;
    if (this.activeId && this.get(this.activeId)?.saved) await this.open(this.activeId).catch(() => undefined);
    this.derive();
    // A new conversation nobody typed in is not worth coming back to when there is one to continue.
    const blank = (id: string) => this.unsaved.includes(id) && !hasContent(this.local[id]);
    if (!this.summaries.some((item) => item.id === this.activeId) && blank(this.activeId) && this.summaries[0]) {
      this.unsaved = this.unsaved.filter((id) => id !== this.activeId);
      delete this.local[this.activeId];
      this.activeId = this.summaries[0].id;
      this.commit();
      await this.open(this.activeId).catch(() => undefined);
    }
    return this.active();
  }
  private async sendKept(items: ArtifactSummary[]) {
    const known = new Set(this.summaries.map((item) => item.id));
    const turned = new Map(items.filter((item) => item.clientRequestId).map((item) => [item.clientRequestId!, item.id]));
    const kept = this.imported ? [] : this.legacy.filter((item) => !known.has(item.id)).map((item) => ({
      ...item,
      // A request that was interrupted but did save its version is a saved turn.
      turns: item.turns.map((turn) => turn.status !== "saved" && turn.id && turned.has(turn.id) ? { ...turn, status: "saved" as const, artifactId: turned.get(turn.id)!, error: null } : turn),
    }));
    const heads = new Map<string, ArtifactSummary>();
    for (const item of items.filter((entry) => !entry.conversationId)) {
      const old = heads.get(item.lineageId);
      if (!old || item.versionNumber > old.versionNumber) heads.set(item.lineageId, item);
    }
    const earlier = [...heads.values()].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    const withEarlier = earlier.length && !known.has(EARLIER_WORK) && !kept.some((item) => item.id === EARLIER_WORK && item.turns.some((turn) => turn.artifactId))
      ? [...kept.filter((item) => item.id !== EARLIER_WORK), { id: EARLIER_WORK, title: "Earlier work", createdAt: earlier[0]!.createdAt, turns: earlier.map((item): ImportedArtifactTurn => ({ id: `earlier_${item.id}`.slice(0, 120), instruction: item.request || item.title, status: "saved", artifactId: item.id, createdAt: item.createdAt })) }]
      : kept;
    if (!withEarlier.length) { if (!this.imported) { this.imported = true; this.commit(); } return; }
    try {
      for (let at = 0; at < withEarlier.length; at += CHUNK) await knowledgeApi.importArtifactConversations(this.baseId, { conversations: withEarlier.slice(at, at + CHUNK) }, this.workspaceId);
      this.imported = true;
      this.legacy = [];
      this.summaries = await knowledgeApi.artifactConversations(this.baseId, this.workspaceId);
      this.commit();
    } catch (reason) {
      log.warn("artifacts", `This device's conversations were not sent to the service yet: ${reason instanceof Error ? reason.message : String(reason)}`);
    }
  }
}

export function useArtifactConversations(workspaceId: string | null, baseId: string) {
  const store = useMemo(() => new ArtifactConversationStore(workspaceId ?? "unverified", baseId), [workspaceId, baseId]);
  const state = useSyncExternalStore(store.subscribe, store.snapshot);
  return { store, state, active: state.conversations.find((item) => item.id === state.activeId)! };
}
