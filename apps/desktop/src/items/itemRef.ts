import type { InboxItem, KnowledgeSearchResult, SourceKind } from "@gunther/contracts";

/** Anything that can be opened into its own detail page. */
export type ItemRef =
  | { type: "source"; id: string }
  | { type: "note"; id: string };

/** Where the detail page was opened from, so Back returns there. */
export type ItemOrigin = "home" | "library" | "base" | "notebook" | "inbox" | "settings" | "account" | "trash";

export const itemKey = (ref: ItemRef) => `${ref.type}:${ref.id}`;

export const sameItem = (left: ItemRef | null | undefined, right: ItemRef | null | undefined) =>
  Boolean(left && right && left.type === right.type && left.id === right.id);

export function refFromInbox(item: InboxItem): ItemRef {
  if (item.itemType === "quick_note") return { type: "note", id: item.noteId ?? item.id };
  return { type: "source", id: item.sourceId ?? item.id };
}

export function refFromSearch(result: KnowledgeSearchResult): ItemRef | null {
  if (result.kind === "note") return { type: "note", id: result.id };
  if (result.kind === "source") return { type: "source", id: result.id };
  return null;
}

/** Human names for each kind of source, used in headers and rows. */
export const SOURCE_KIND_LABEL: Record<SourceKind, string> = {
  note: "Note",
  paper: "Paper",
  link: "Web page",
  file: "Document",
  image: "Photo or scan",
  table: "Table",
  recording: "Recording",
  course: "Course recording",
};
