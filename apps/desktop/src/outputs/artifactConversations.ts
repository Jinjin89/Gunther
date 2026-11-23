import type { ArtifactSummary, ImportedArtifactConversation, ImportedArtifactTurn, OutputScope } from "@gunther/contracts";
import { knowledgeApi } from "../api";
import { persistentStorage } from "../persistentStorage";

export const EARLIER_WORK = "earlier-work";
export const libraryScope = (): OutputScope => ({ mode: "library", sourceIds: [], unitIds: [], sessionIds: [] });
export const artifactRequestId = () => globalThis.crypto?.randomUUID?.() ?? `request_${Date.now()}_${Math.random().toString(36).slice(2)}`;

const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");
const validScope = (value: unknown): value is OutputScope => {
  if (!value || typeof value !== "object") return false;
  const scope = value as Partial<OutputScope>;
  return (scope.mode === "library" || scope.mode === "selection") && strings(scope.sourceIds) && strings(scope.unitIds) && strings(scope.sessionIds);
};
const STATUSES = ["running", "saved", "failed", "stopped", "interrupted"];
const CHUNK = 100;

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

/** Import the turns saved on this device before the service kept their conversations. */
export async function importDeviceConversations(baseId: string, workspaceId: string, artifacts: ArtifactSummary[]) {
  const key = `gunther:artifact-conversations:${encodeURIComponent(workspaceId)}:${encodeURIComponent(baseId)}`;
  let saved: Record<string, unknown> | null;
  try { saved = JSON.parse(persistentStorage.getItem(key) ?? "null"); } catch { return; }
  if (!saved || saved.imported === true) return;
  const legacy = saved.version === 1 && Array.isArray(saved.conversations)
    ? saved.conversations.filter((item) => item && typeof item === "object").map(importable).filter((item) => item !== null)
    : saved.version === 2 && Array.isArray(saved.legacy) ? saved.legacy as ImportedArtifactConversation[] : [];
  const results = new Map(artifacts.map((item) => [item.clientRequestId, item.id]));
  for (let at = 0; at < legacy.length; at += CHUNK) {
    const conversations = legacy.slice(at, at + CHUNK).map((item) => ({
      ...item,
      turns: item.turns.map((turn): ImportedArtifactTurn => {
        const artifactId = results.get(turn.id);
        return turn.status !== "saved" && artifactId ? { ...turn, status: "saved", artifactId, error: null } : turn;
      }),
    }));
    await knowledgeApi.importArtifactConversations(baseId, { conversations }, workspaceId);
  }
  try { persistentStorage.setItem(key, JSON.stringify({ ...saved, imported: true, legacy: [] })); }
  catch { /* The import route can replay these conversations. */ }
}
