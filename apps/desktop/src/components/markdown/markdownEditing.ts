/**
 * Plain-text Markdown editing helpers. Each returns the edited text and the
 * selection to restore, so the editor can apply them through the browser's
 * undo stack when it can, and fall back to a controlled update when it cannot.
 */
export interface TextEdit {
  /** Start of the replaced range in the original text. */
  from: number;
  /** End of the replaced range in the original text. */
  to: number;
  /** Replacement text. */
  insert: string;
  /** Selection after the edit, in the edited text. */
  selectionStart: number;
  selectionEnd: number;
}

export const applyEdit = (value: string, edit: TextEdit) => value.slice(0, edit.from) + edit.insert + value.slice(edit.to);

const LIST_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])(\s+)(\[[ xX]\]\s+)?/;

const lineBounds = (value: string, position: number) => {
  const start = value.lastIndexOf("\n", position - 1) + 1;
  const newline = value.indexOf("\n", position);
  return { start, end: newline === -1 ? value.length : newline };
};

/** Wrap the selection in a marker such as `**`, or unwrap it when it is already wrapped. */
export function toggleWrap(value: string, start: number, end: number, marker: string, placeholder = "text"): TextEdit {
  const selected = value.slice(start, end);
  const before = value.slice(Math.max(0, start - marker.length), start);
  const after = value.slice(end, end + marker.length);
  if (selected && before === marker && after === marker) {
    return { from: start - marker.length, to: end + marker.length, insert: selected, selectionStart: start - marker.length, selectionEnd: end - marker.length };
  }
  if (selected.length > marker.length * 2 && selected.startsWith(marker) && selected.endsWith(marker)) {
    const inner = selected.slice(marker.length, -marker.length);
    return { from: start, to: end, insert: inner, selectionStart: start, selectionEnd: start + inner.length };
  }
  const text = selected || placeholder;
  return { from: start, to: end, insert: `${marker}${text}${marker}`, selectionStart: start + marker.length, selectionEnd: start + marker.length + text.length };
}

/** Turn the selection into a link, leaving the URL placeholder selected. */
export function insertLink(value: string, start: number, end: number): TextEdit {
  const selected = value.slice(start, end);
  const looksLikeUrl = /^https?:\/\/\S+$/i.test(selected);
  if (looksLikeUrl) {
    const insert = `[link](${selected})`;
    return { from: start, to: end, insert, selectionStart: start + 1, selectionEnd: start + 5 };
  }
  const label = selected || "link";
  const insert = `[${label}](https://)`;
  const urlStart = start + label.length + 3;
  return { from: start, to: end, insert, selectionStart: urlStart, selectionEnd: urlStart + "https://".length };
}

/**
 * Toggle a line prefix (`# `, `> `, `- `, `1. `, `- [ ] `) on every line the
 * selection touches. Existing list or heading markers are replaced, not stacked.
 */
const PREFIX_TESTS: Record<string, RegExp> = {
  "1. ": /^\s*\d{1,9}[.)]\s/,
  "- [ ] ": /^\s*[-*+]\s+\[[ xX]\]\s/,
  "- ": /^\s*[-*+]\s(?!\[[ xX]\])/,
  "> ": /^\s*>/,
};

const hasLinePrefix = (line: string, prefix: string) =>
  (PREFIX_TESTS[prefix] ?? new RegExp(`^\\s*${prefix.trimEnd().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s`)).test(line);

export function toggleLinePrefix(value: string, start: number, end: number, prefix: string): TextEdit {
  const first = lineBounds(value, start).start;
  const last = lineBounds(value, Math.max(start, end - (end > start && value[end - 1] === "\n" ? 1 : 0))).end;
  const lines = value.slice(first, last).split("\n");
  // Quotes wrap whatever the line already is; every other prefix replaces an existing marker.
  const markerPattern = prefix === "> "
    ? /^(\s*)(>\s?)?/
    : /^(\s*)(#{1,6}\s+|>\s?|(?:[-*+]|\d{1,9}[.)])\s+(?:\[[ xX]\]\s+)?)?/;
  const allHavePrefix = lines.some((line) => line.trim()) && lines.every((line) => !line.trim() || hasLinePrefix(line, prefix));
  let counter = 0;
  const next = lines.map((line) => {
    if (!line.trim() && lines.length > 1) return line;
    const match = line.match(markerPattern);
    const indent = match?.[1] ?? "";
    const body = line.slice((match?.[0] ?? "").length);
    if (allHavePrefix) return indent + body;
    counter += 1;
    const marker = prefix === "1. " ? `${counter}. ` : prefix;
    return indent + marker + body;
  }).join("\n");
  const delta = next.length - (last - first);
  const collapsed = start === end;
  return {
    from: first,
    to: last,
    insert: next,
    selectionStart: collapsed ? Math.max(first, start + (next.split("\n")[0]!.length - lines[0]!.length)) : first,
    selectionEnd: collapsed ? Math.max(first, start + (next.split("\n")[0]!.length - lines[0]!.length)) : end + delta,
  };
}

/**
 * Enter inside a list item continues the list (numbers increment, tasks start
 * unchecked). Enter on an empty item ends the list. Returns null outside lists.
 */
export function continueList(value: string, start: number, end: number): TextEdit | null {
  if (start !== end) return null;
  const { start: lineStart, end: lineEnd } = lineBounds(value, start);
  const line = value.slice(lineStart, lineEnd);
  const match = line.match(LIST_ITEM);
  if (!match || start < lineStart + match[0].length) return null;
  const [marker, indent = "", bullet = "-", spacing = " ", task] = match;
  if (!line.slice(marker.length).trim()) {
    // An empty item: leave the list and keep only the indentation.
    return { from: lineStart, to: lineEnd, insert: "", selectionStart: lineStart, selectionEnd: lineStart };
  }
  const ordered = /^\d/.test(bullet);
  const nextBullet = ordered ? `${Number.parseInt(bullet, 10) + 1}${bullet.slice(-1)}` : bullet;
  const insert = `\n${indent}${nextBullet}${spacing}${task ? "[ ] " : ""}`;
  return { from: start, to: end, insert, selectionStart: start + insert.length, selectionEnd: start + insert.length };
}

/** Indent or outdent the list lines touched by the selection. Returns null outside lists. */
export function shiftListItems(value: string, start: number, end: number, outdent: boolean): TextEdit | null {
  const first = lineBounds(value, start).start;
  const last = lineBounds(value, end).end;
  const lines = value.slice(first, last).split("\n");
  if (!lines.every((line) => !line.trim() || LIST_ITEM.test(line))) return null;
  let firstDelta = 0;
  const next = lines.map((line, index) => {
    if (!line.trim()) return line;
    if (outdent) {
      const removed = line.startsWith("  ") ? 2 : line.startsWith(" ") || line.startsWith("\t") ? 1 : 0;
      if (index === 0) firstDelta = -removed;
      return line.slice(removed);
    }
    if (index === 0) firstDelta = 2;
    return `  ${line}`;
  }).join("\n");
  const delta = next.length - (last - first);
  return {
    from: first,
    to: last,
    insert: next,
    selectionStart: Math.max(first, start + firstDelta),
    selectionEnd: Math.max(first, end + delta),
  };
}

/**
 * Check or uncheck the task list item that starts at `offset` (a source
 * position reported by the renderer). Returns the text unchanged when no task
 * marker is there.
 */
export function setTaskCheckedAt(value: string, offset: number, checked: boolean): string {
  const lineEnd = value.indexOf("\n", offset);
  const line = value.slice(offset, lineEnd === -1 ? value.length : lineEnd);
  const task = line.match(/^(\s*(?:[-*+]|\d{1,9}[.)])\s+)\[([ xX])\]/);
  if (!task) return value;
  const at = offset + task[1]!.length + 1;
  return value.slice(0, at) + (checked ? "x" : " ") + value.slice(at + 1);
}

/** Words in Markdown text, ignoring syntax characters. */
export function countWords(value: string): number {
  const text = value.replace(/```[\s\S]*?```/g, " ").replace(/[#>*_`~[\]()|-]/g, " ").trim();
  if (!text) return 0;
  const cjk = text.match(/[㐀-鿿豈-﫿]/g)?.length ?? 0;
  const latin = text.replace(/[㐀-鿿豈-﫿]/g, " ").split(/\s+/).filter(Boolean).length;
  return latin + cjk;
}

/** Collapse Markdown into one line of readable text for previews. */
export function markdownToPlainText(value: string): string {
  return value
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/^\s*(?:[-*+]|\d{1,9}[.)])\s+(?:\[[ xX]\]\s+)?/gm, "")
    .replace(/(\*\*|__|~~|`)/g, "")
    .replace(/(^|\s)[*_](\S[^*_]*\S|\S)[*_](?=\s|$|[.,;:!?])/g, "$1$2")
    .replace(/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/gm, " ")
    .replace(/\|/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
