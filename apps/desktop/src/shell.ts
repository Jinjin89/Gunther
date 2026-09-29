import type { Assertion, KnowledgeGraph, Overview, SourceSummary } from "@gunther/contracts";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

export type ServiceStatus = "connecting" | "online" | "offline";
export type ThemeMode = "light" | "dark";
export type ShortcutAction =
  | "openCommand"
  | "addKnowledge"
  | "toggleInspector"
  | "refresh"
  | "openHome"
  | "openLibrary"
  | "openMap"
  | "openStudio"
  | "openReview"
  | "openSettings";

export type ShortcutMap = Record<ShortcutAction, string>;

export interface UserProfile {
  name: string;
  email: string;
  description: string;
}

export const defaultProfile: UserProfile = {
  name: "Knowledge explorer",
  email: "",
  description: "Building a living map of what I learn.",
};

export const defaultShortcuts: ShortcutMap = {
  openCommand: "mod+k",
  addKnowledge: "mod+n",
  toggleInspector: "mod+\\",
  refresh: "mod+r",
  openHome: "mod+1",
  openLibrary: "mod+2",
  openMap: "mod+3",
  openStudio: "mod+4",
  openReview: "mod+5",
  openSettings: "mod+,",
};

export function shortcutFromEvent(event: globalThis.KeyboardEvent | ReactKeyboardEvent): string | null {
  const key = event.key.toLowerCase();
  if (["meta", "control", "alt", "shift"].includes(key)) return null;
  const parts: string[] = [];
  if (event.metaKey || event.ctrlKey) parts.push("mod");
  if (event.altKey) parts.push("alt");
  if (event.shiftKey) parts.push("shift");
  parts.push(key === " " ? "space" : key);
  return parts.join("+");
}

export function formatShortcut(shortcut: string): string {
  if (!shortcut) return "Not set";
  return shortcut
    .split("+")
    .map((part) => ({ mod: "⌘", alt: "⌥", shift: "⇧", enter: "↵", space: "Space", escape: "Esc", "\\": "\\" }[part] ?? part.toUpperCase()))
    .join("");
}

export interface KnowledgeState {
  overview: Overview;
  graph: KnowledgeGraph;
  sources: SourceSummary[];
  provisional: Assertion[];
  extractionMode: "local" | "model";
}

export const emptyKnowledgeState: KnowledgeState = {
  overview: {
    counts: { sources: 0, entities: 0, assertions: 0, provisional: 0 },
    recentSources: [],
    recentAssertions: [],
  },
  graph: { nodes: [], edges: [] },
  sources: [],
  provisional: [],
  extractionMode: "local",
};
