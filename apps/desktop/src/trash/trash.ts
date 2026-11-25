import type { TrashItem, TrashItemKind } from "@gunther/contracts";
import { knowledgeApi } from "../api";

export type TrashTarget =
  | { kind: Exclude<TrashItemKind, "output">; id: string }
  | { kind: "output"; id: string; libraryId: string; workspaceId: string; conversation: boolean };

/** Everything a Trash change can touch, so every open list refreshes. */
const TRASH_EVENTS = ["gunther:inbox-updated", "gunther:sources-updated", "gunther:trash-updated"];

export interface SourceUpdate { trashedSourceId?: string }

export function announceTrashChange(trashedSourceId?: string) {
  for (const name of TRASH_EVENTS) {
    const detail = name === "gunther:sources-updated" && trashedSourceId ? { trashedSourceId } : undefined;
    window.dispatchEvent(new CustomEvent<SourceUpdate>(name, detail ? { detail } : undefined));
  }
}

export async function moveToTrash(target: TrashTarget): Promise<TrashItem> {
  let entry: TrashItem;
  switch (target.kind) {
    case "library": entry = await knowledgeApi.trashLibrary(target.id); break;
    case "note": entry = await knowledgeApi.trashNote(target.id); break;
    case "source": entry = await knowledgeApi.trashSource(target.id); break;
    case "output":
      entry = target.conversation
        ? await knowledgeApi.trashOutputConversation(target.libraryId, target.id, target.workspaceId)
        : await knowledgeApi.trashOutput(target.libraryId, target.id, target.workspaceId);
      break;
  }
  announceTrashChange(entry.kind === "source" ? entry.id : undefined);
  return entry;
}

export async function restoreFromTrash(entry: Pick<TrashItem, "kind" | "id">): Promise<TrashItem> {
  const restored = await knowledgeApi.restoreFromTrash(entry.kind, entry.id);
  announceTrashChange();
  return restored;
}

const plural = (count: number, singular: string) => `${count} ${singular}${count === 1 ? "" : "s"}`;

/** "Moved “Cells” and 12 sources to Trash." */
export function trashedMessage(entry: TrashItem): string {
  const alongside = entry.kind === "library" && entry.itemCount > 0 ? ` and ${plural(entry.itemCount, "source")}` : "";
  return `Moved “${entry.title}”${alongside} to Trash.`;
}

/** Whole days left before Trash deletes an item for good. */
export function daysLeft(entry: Pick<TrashItem, "expiresAt">, now = Date.now()): number {
  return Math.max(0, Math.ceil((new Date(entry.expiresAt).getTime() - now) / 86_400_000));
}
