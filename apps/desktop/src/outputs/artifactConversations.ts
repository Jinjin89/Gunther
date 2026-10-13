import type { Artifact, ArtifactSummary, BuildOutputRequest, OutputScope, ReviseOutputRequest } from "@gunther/contracts";
import { useMemo, useSyncExternalStore } from "react";
import type { AskChoice } from "../models/askModel";
import { persistentStorage } from "../persistentStorage";

export type ArtifactRequest = { operation: "build"; payload: BuildOutputRequest } | { operation: "revise"; artifactId: string; payload: ReviseOutputRequest };
export interface ArtifactTurn {
  id: string;
  instruction: string;
  request: ArtifactRequest | null;
  status: "running" | "saved" | "failed" | "stopped" | "interrupted";
  createdAt: string;
  result: ArtifactSummary | null;
  error: string | null;
}
export interface ArtifactConversation {
  id: string;
  title: string;
  untitled: boolean;
  scope: OutputScope;
  choice: AskChoice | null;
  draft: string;
  lineageIds: string[];
  selectedArtifactId: string | null;
  turns: ArtifactTurn[];
  createdAt: string;
}
interface ConversationState { version: 1; activeId: string; conversations: ArtifactConversation[]; problem: string | null }
export const libraryScope = (): OutputScope => ({ mode: "library", sourceIds: [], unitIds: [], sessionIds: [] });
export const artifactRequestId = () => globalThis.crypto?.randomUUID?.() ?? `request_${Date.now()}_${Math.random().toString(36).slice(2)}`;
const fresh = (scope = libraryScope(), choice: AskChoice | null = null): ArtifactConversation => ({ id: artifactRequestId(), title: "New conversation", untitled: true, scope, choice, draft: "", lineageIds: [], selectedArtifactId: null, turns: [], createdAt: new Date().toISOString() });
function summaryOf(item: ArtifactSummary): ArtifactSummary {
  return { id: item.id, workspaceId: item.workspaceId, knowledgeBaseId: item.knowledgeBaseId, lineageId: item.lineageId, versionNumber: item.versionNumber, supersedesArtifactId: item.supersedesArtifactId, format: item.format, audience: item.audience, title: item.title, contentHash: item.contentHash, manifestHash: item.manifestHash, acceptedUnitIds: item.acceptedUnitIds, unitCount: item.unitCount, createdAt: item.createdAt, kind: item.kind, style: item.style, origin: item.origin, conversationId: item.conversationId ?? null, conversationTitle: item.conversationTitle ?? null, clientRequestId: item.clientRequestId ?? null, request: item.request ?? null };
}
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");
const validScope = (value: unknown): value is OutputScope => {
  if (!value || typeof value !== "object") return false;
  const scope = value as Partial<OutputScope>;
  return (scope.mode === "library" || scope.mode === "selection") && strings(scope.sourceIds) && strings(scope.unitIds) && strings(scope.sessionIds);
};

/** Device drafts live in native persistent storage. Saved conversation ownership and
 * instructions also travel with backend artifacts, so history can be recovered. */
export class ArtifactConversationStore {
  private state: ConversationState;
  private listeners = new Set<() => void>();
  private recoverInitial = true;
  readonly key: string;
  constructor(workspaceId: string, baseId: string) {
    this.key = `gunther:artifact-conversations:${encodeURIComponent(workspaceId)}:${encodeURIComponent(baseId)}`;
    this.state = this.read();
  }
  private read(): ConversationState {
    try {
      const saved = JSON.parse(persistentStorage.getItem(this.key) ?? "null") as ConversationState | null;
      if (saved?.version === 1 && Array.isArray(saved.conversations)) {
        const conversations = saved.conversations.filter((item) => item && typeof item.id === "string" && typeof item.title === "string" && typeof item.draft === "string" && validScope(item.scope) && strings(item.lineageIds) && Array.isArray(item.turns)).map((item) => ({ ...item,
          choice: item.choice && typeof item.choice.model === "string" && ["off", "low", "medium", "high", "max"].includes(item.choice.effort) ? item.choice : null,
          turns: item.turns.filter((turn) => turn && typeof turn.id === "string" && typeof turn.instruction === "string" && ["running", "saved", "failed", "stopped", "interrupted"].includes(turn.status)).map((turn) => ({ ...turn, status: turn.status === "running" ? "interrupted" as const : turn.status })),
        }));
        if (conversations.length) { this.recoverInitial = false; return { version: 1, activeId: conversations.some((item) => item.id === saved.activeId) ? saved.activeId : conversations[0]!.id, conversations, problem: null }; }
      }
    } catch { /* A malformed preference must not hide artifacts saved by the backend. */ }
    const conversation = fresh();
    return { version: 1, activeId: conversation.id, conversations: [conversation], problem: null };
  }
  snapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private commit(next: ConversationState) {
    this.state = next;
    try { persistentStorage.setItem(this.key, JSON.stringify({ ...next, problem: null })); }
    catch { this.state = { ...next, problem: "Conversation drafts could not be saved on this device. Saved artifacts remain available." }; }
    this.listeners.forEach((listener) => listener());
  }
  get(id: string) { return this.state.conversations.find((item) => item.id === id); }
  active() { return this.get(this.state.activeId)!; }
  update(id: string, patch: Partial<ArtifactConversation>) {
    this.commit({ ...this.state, conversations: this.state.conversations.map((item) => item.id === id ? { ...item, ...patch } : item) });
  }
  select(id: string) { if (this.get(id)) this.commit({ ...this.state, activeId: id }); }
  create(scope = libraryScope(), choice: AskChoice | null = null) {
    this.recoverInitial = false;
    const conversation = fresh(scope, choice);
    this.commit({ ...this.state, activeId: conversation.id, conversations: [conversation, ...this.state.conversations] });
    return conversation;
  }
  begin(id: string, turn: ArtifactTurn) {
    const conversation = this.get(id);
    if (!conversation) return;
    this.update(id, { title: conversation.untitled ? turn.instruction.slice(0, 160) : conversation.title, untitled: false, draft: "", ...(turn.request?.payload.model ? { choice: { model: turn.request.payload.model, effort: turn.request.payload.effort ?? "high" } } : {}), turns: conversation.turns.some((item) => item.id === turn.id) ? conversation.turns.map((item) => item.id === turn.id ? turn : item) : [...conversation.turns, turn] });
  }
  settle(id: string, turnId: string, status: ArtifactTurn["status"], artifact: ArtifactSummary | null = null, error: string | null = null) {
    const conversation = this.get(id);
    if (!conversation) return;
    this.update(id, {
      turns: conversation.turns.map((turn) => turn.id === turnId ? { ...turn, status, result: artifact ? summaryOf(artifact) : turn.result, error } : turn),
      ...(artifact ? { lineageIds: [...new Set([...conversation.lineageIds, artifact.lineageId])], selectedArtifactId: artifact.id } : {}),
    });
  }
  finish(id: string, turnId: string, artifact: Artifact) {
    const owner = artifact.conversationId || id;
    let resultTurnId = turnId;
    if (owner !== id) {
      const source = this.get(id);
      const turn = source?.turns.find((item) => item.id === turnId);
      if (source && turn) {
        resultTurnId = turn.request ? turnId : artifact.clientRequestId || turnId;
        const known = this.get(owner);
        const target = known ?? { ...source, id: owner, title: artifact.conversationTitle || source.title, scope: artifact.scope ?? source.scope, turns: [], lineageIds: [], selectedArtifactId: null };
        const moved = { ...turn, id: resultTurnId };
        const next = { ...target, turns: target.turns.some((item) => item.id === resultTurnId) ? target.turns : [...target.turns, moved] };
        this.commit({ ...this.state, activeId: this.state.activeId === id ? owner : this.state.activeId, conversations: [...this.state.conversations.filter((item) => item.id !== owner && !(item.id === id && item.turns.length === 1 && item.lineageIds.length === 0)), next] });
      }
    }
    this.settle(owner, resultTurnId, "saved", artifact);
  }
  /** Reconcile request outcomes and recover groups without guessing old conversations. */
  reconcile(items: ArtifactSummary[]) {
    let conversations = this.state.conversations.map((item) => ({ ...item, turns: item.turns.map((turn) => ({ ...turn })), lineageIds: [...item.lineageIds] }));
    const heads = new Map<string, ArtifactSummary>();
    items.forEach((item) => { const old = heads.get(item.lineageId); if (!old || item.versionNumber > old.versionNumber) heads.set(item.lineageId, item); });
    for (const item of heads.values()) {
      const known = conversations.find((conversation) => conversation.lineageIds.includes(item.lineageId));
      const owner = item.conversationId || known?.id || "earlier-work";
      let conversation = conversations.find((entry) => entry.id === owner);
      if (!conversation) {
        conversation = { ...fresh(), id: owner, title: item.conversationTitle || (owner === "earlier-work" ? "Earlier work" : "Recovered conversation"), untitled: false };
        conversations.push(conversation);
      }
      if (!conversation.lineageIds.includes(item.lineageId)) conversation.lineageIds.push(item.lineageId);
      if (known && known.id !== owner) known.lineageIds = known.lineageIds.filter((lineage) => lineage !== item.lineageId);
    }
    for (const item of [...items].sort((a, b) => a.createdAt.localeCompare(b.createdAt))) {
      const conversation = conversations.find((entry) => entry.lineageIds.includes(item.lineageId));
      if (!conversation) continue;
      const turn = conversation.turns.find((entry) => entry.id === item.clientRequestId || entry.result?.id === item.id);
      if (turn) Object.assign(turn, { status: "saved", result: summaryOf(item), error: null });
      else if (item.request) conversation.turns.push({ id: item.clientRequestId || `saved_${item.id}`, instruction: item.request, status: "saved", result: summaryOf(item), request: null, error: null, createdAt: item.createdAt });
    }
    let activeId = this.state.activeId;
    const active = conversations.find((item) => item.id === activeId);
    if (this.recoverInitial && active?.untitled && !active.draft && active.turns.length === 0 && active.lineageIds.length === 0 && items.length) {
      const owner = conversations.find((item) => item.lineageIds.includes(items[0]!.lineageId));
      if (owner) { activeId = owner.id; conversations = conversations.filter((item) => item.id !== active.id); }
    }
    this.recoverInitial = false;
    this.commit({ ...this.state, activeId, conversations });
    return this.active();
  }
}

export function useArtifactConversations(workspaceId: string | null, baseId: string) {
  const store = useMemo(() => new ArtifactConversationStore(workspaceId ?? "unverified", baseId), [workspaceId, baseId]);
  const state = useSyncExternalStore(store.subscribe, store.snapshot);
  return { store, state, active: state.conversations.find((item) => item.id === state.activeId)! };
}
