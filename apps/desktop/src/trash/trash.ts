import type { TrashItem, TrashItemKind } from "@gunther/contracts";
import { knowledgeApi } from "../api";

/** Something that can move to Trash: a source (recordings included), a note, or a library. */
export interface TrashTarget {
  kind: TrashItemKind;
  id: string;
}

/** Everything a Trash change can touch, so every open list refreshes. */
const TRASH_EVENTS = ["gunther:inbox-updated", "gunther:sources-updated", "gunther:trash-updated"];

export function announceTrashChange() {
  for (const name of TRASH_EVENTS) window.dispatchEvent(new CustomEvent(name));
}

export async function moveToTrash(target: TrashTarget): Promise<TrashItem> {
  const entry = target.kind === "library"
    ? await knowledgeApi.trashLibrary(target.id)
    : target.kind === "note"
      ? await knowledgeApi.trashNote(target.id)
      : await knowledgeApi.trashSource(target.id);
  announceTrashChange();
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
