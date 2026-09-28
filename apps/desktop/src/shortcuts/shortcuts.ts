import { useEffect, useRef } from "react";

/**
 * Gunther's keyboard shortcuts.
 *
 * One registry describes every shortcut: it drives matching, the labels shown
 * in tooltips and menus, and the ⌘/ reference sheet, so the three can never
 * disagree. Combos use `mod` for ⌘ on macOS and Ctrl elsewhere.
 */

export type ShortcutGroup = "General" | "Go to" | "Capture" | "Inbox and items" | "Writing" | "Recordings";

export interface ShortcutSpec {
  id: string;
  keys: string[];
  label: string;
  group: ShortcutGroup;
}

export const SHORTCUTS = [
  { id: "search", keys: ["mod+k", "/"], label: "Search everything", group: "General" },
  { id: "settings", keys: ["mod+,"], label: "Settings", group: "General" },
  { id: "shortcuts", keys: ["mod+/", "?"], label: "Keyboard shortcuts", group: "General" },
  { id: "close", keys: ["escape"], label: "Close, or go back", group: "General" },
  { id: "back", keys: ["mod+["], label: "Back", group: "General" },
  { id: "theme", keys: ["mod+shift+l"], label: "Switch light and dark", group: "General" },
  { id: "go-home", keys: ["mod+1"], label: "Home", group: "Go to" },
  { id: "go-libraries", keys: ["mod+2"], label: "Libraries", group: "Go to" },
  { id: "go-inbox", keys: ["mod+3"], label: "Inbox", group: "Go to" },
  { id: "capture", keys: ["mod+shift+c"], label: "Open Capture", group: "Capture" },
  { id: "new-note", keys: ["mod+n"], label: "New note", group: "Capture" },
  { id: "new-recording", keys: ["mod+shift+r"], label: "New recording", group: "Capture" },
  { id: "capture-save", keys: ["mod+enter"], label: "Save the capture", group: "Capture" },
  { id: "capture-kind", keys: ["mod+1…6"], label: "Switch capture type", group: "Capture" },
  { id: "item-next", keys: ["j", "arrowdown"], label: "Next item", group: "Inbox and items" },
  { id: "item-previous", keys: ["k", "arrowup"], label: "Previous item", group: "Inbox and items" },
  { id: "item-open", keys: ["enter"], label: "Open the selected item", group: "Inbox and items" },
  { id: "item-primary", keys: ["mod+enter"], label: "File or accept the open item", group: "Inbox and items" },
  { id: "note-edit", keys: ["e"], label: "Edit the open note", group: "Writing" },
  { id: "note-preview", keys: ["mod+e"], label: "Switch writing and preview", group: "Writing" },
  { id: "format-bold", keys: ["mod+b"], label: "Bold", group: "Writing" },
  { id: "format-italic", keys: ["mod+i"], label: "Italic", group: "Writing" },
  { id: "format-link", keys: ["mod+k"], label: "Link the selected text", group: "Writing" },
  { id: "format-list", keys: ["mod+shift+8"], label: "Bulleted list", group: "Writing" },
  { id: "format-tasks", keys: ["mod+shift+9"], label: "Checklist", group: "Writing" },
  { id: "recording-play", keys: ["space"], label: "Play or pause", group: "Recordings" },
  { id: "recording-skip", keys: ["arrowleft", "arrowright"], label: "Back or forward 5 seconds", group: "Recordings" },
] as const satisfies readonly ShortcutSpec[];

export type ShortcutId = (typeof SHORTCUTS)[number]["id"];

export const isMac = () => typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

const SYMBOLS: Record<string, string> = {
  mod: "⌘",
  shift: "⇧",
  alt: "⌥",
  ctrl: "⌃",
  enter: "↵",
  escape: "Esc",
  arrowup: "↑",
  arrowdown: "↓",
  arrowleft: "←",
  arrowright: "→",
  space: "Space",
  backspace: "⌫",
};

/** Split a combo into display keys, e.g. "mod+shift+c" → ["⌘", "⇧", "C"]. */
export function comboKeys(combo: string): string[] {
  const mac = isMac();
  return combo.split("+").map((part) => {
    if (!mac && part === "mod") return "Ctrl";
    if (!mac && (part === "shift" || part === "alt")) return part === "shift" ? "Shift" : "Alt";
    return SYMBOLS[part] ?? (part.length === 1 ? part.toUpperCase() : part);
  });
}

/** "mod+shift+c" → "⌘⇧C" on macOS, "Ctrl+Shift+C" elsewhere. */
export function formatCombo(combo: string): string {
  return comboKeys(combo).join(isMac() ? "" : "+");
}

export function shortcutLabel(id: ShortcutId): string {
  const spec = SHORTCUTS.find((item) => item.id === id);
  return spec ? formatCombo(spec.keys[0]!) : "";
}

/** A tooltip such as "Open Capture  ⌘⇧C". */
export function withShortcut(label: string, id: ShortcutId): string {
  const keys = shortcutLabel(id);
  return keys ? `${label}  ${keys}` : label;
}

const EDITABLE = "input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=range]), textarea, select, [contenteditable='true'], [contenteditable='']";

export function isTypingTarget(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest(EDITABLE));
}

/** True when a modal surface is open and the event did not start inside it. */
export function isOutsideOpenModal(target: EventTarget | null): boolean {
  const modals = Array.from(document.querySelectorAll<HTMLElement>("[aria-modal='true']"));
  if (!modals.length) return false;
  return !(target instanceof Node && modals.some((modal) => modal.contains(target)));
}

export function matchesCombo(event: KeyboardEvent, combo: string): boolean {
  const parts = combo.toLowerCase().split("+");
  const key = parts[parts.length - 1]!;
  const mac = isMac();
  const wantsMod = parts.includes("mod");
  const hasMod = mac ? event.metaKey : event.ctrlKey;
  if (wantsMod !== hasMod) return false;
  if (parts.includes("alt") !== event.altKey) return false;
  if (!wantsMod && (mac ? event.ctrlKey : event.metaKey)) return false;
  const pressed = event.key.toLowerCase();
  const punctuation = key.length === 1 && !/[a-z0-9]/.test(key);
  // Shifted punctuation ("?") already carries its shift; digits are matched by
  // physical key so ⌘⇧8 works whatever the layout prints for ⇧8.
  if (!punctuation && parts.includes("shift") !== event.shiftKey) return false;
  if (key === "space") return event.key === " " || event.code === "Space";
  if (/^[0-9]$/.test(key)) return event.code === `Digit${key}` || event.code === `Numpad${key}` || pressed === key;
  return pressed === key;
}

interface ShortcutOptions {
  enabled?: boolean;
  /** Also fire while typing in a field (modifier shortcuts only, by default). */
  allowInInputs?: boolean;
  /** Also fire while another modal surface is open. */
  allowInModal?: boolean;
}

/**
 * Bind a shortcut for as long as the component is mounted. Single-key
 * shortcuts never fire while typing, events already handled by a focused
 * control are ignored, and page shortcuts stay quiet under an open modal.
 */
export function useShortcut(combos: string | readonly string[], handler: (event: KeyboardEvent) => void, options: ShortcutOptions = {}) {
  const latest = useRef(handler);
  latest.current = handler;
  const { enabled = true, allowInInputs, allowInModal = false } = options;
  const list = typeof combos === "string" ? [combos] : combos;
  const signature = list.join("|");
  useEffect(() => {
    if (!enabled) return undefined;
    const parsed = signature.split("|");
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
      const combo = parsed.find((candidate) => matchesCombo(event, candidate));
      if (!combo) return;
      const modified = combo.includes("mod+") || combo === "escape";
      if (isTypingTarget(event.target) && !(allowInInputs ?? modified)) return;
      if (!allowInModal && isOutsideOpenModal(event.target)) return;
      event.preventDefault();
      latest.current(event);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [allowInInputs, allowInModal, enabled, signature]);
}

/* Escape is layered: the most recently opened surface closes first. */
interface EscapeEntry {
  id: number;
  handler: () => void;
  whileTyping: "close" | "blur";
}

const escapeStack: EscapeEntry[] = [];
let escapeListening = false;
let escapeCounter = 0;

function onEscape(event: KeyboardEvent) {
  if (event.key !== "Escape" || event.defaultPrevented || event.isComposing) return;
  const top = escapeStack[escapeStack.length - 1];
  if (!top) return;
  event.preventDefault();
  if (top.whileTyping === "blur" && isTypingTarget(event.target)) {
    // Page-level layers leave the field first; the next Esc goes back.
    (event.target as HTMLElement).blur();
    return;
  }
  top.handler();
}

/**
 * Register an Esc handler while `enabled`. Later registrations sit on top, so
 * a menu inside a dialog closes before the dialog, and the dialog before the
 * page. Pages pass `whileTyping: "blur"` so Esc first leaves a focused field.
 */
export function useEscape(handler: () => void, enabled = true, whileTyping: "close" | "blur" = "close") {
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => {
    if (!enabled) return undefined;
    escapeCounter += 1;
    const entry: EscapeEntry = { id: escapeCounter, handler: () => latest.current(), whileTyping };
    escapeStack.push(entry);
    if (!escapeListening) {
      window.addEventListener("keydown", onEscape);
      escapeListening = true;
    }
    return () => {
      const index = escapeStack.findIndex((item) => item.id === entry.id);
      if (index >= 0) escapeStack.splice(index, 1);
      if (!escapeStack.length && escapeListening) {
        window.removeEventListener("keydown", onEscape);
        escapeListening = false;
      }
    };
  }, [enabled, whileTyping]);
}
