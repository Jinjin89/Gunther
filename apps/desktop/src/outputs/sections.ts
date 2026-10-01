/**
 * The sections of an output, split by the same rules as the service (gunther/outputs.py).
 * Both sides are tested with the same cases.
 *
 * - A report's sections start at lines beginning with "## ". What comes before the first
 *   one (the title, the brief) is the preamble.
 * - A deck's slides are the parts between lines that are only "---". Empty ones are left out.
 * - A "## " or "---" inside a code fence (a line starting with ``` or ~~~) does not count.
 * - A section's hash is the sha256 of its text with "\n" line ends, trimmed.
 * - A slide's speaker notes start at a line beginning with "Note:".
 */
import type { Artifact, OutputKind, OutputSection } from "@gunther/contracts";

const FENCE = /^\s*(```|~~~)/;

/** Each line of a document, and whether it is inside a code fence. */
function lines(content: string): Array<{ line: string; fenced: boolean }> {
  let fenced = false;
  return content.replace(/\r\n?/g, "\n").split("\n").map((line) => {
    if (FENCE.test(line)) {
      fenced = !fenced;
      return { line, fenced: true };
    }
    return { line, fenced };
  });
}

/** A document as its preamble and its sections, each trimmed. */
export function splitDocument(content: string, kind: OutputKind): { preamble: string; sections: string[] } {
  const preamble: string[] = [];
  const parts: string[][] = [[]];
  for (const { line, fenced } of lines(content)) {
    if (kind === "slides") {
      if (!fenced && line.trim() === "---") parts.push([]);
      else parts[parts.length - 1]?.push(line);
    } else if (!fenced && line.startsWith("## ")) {
      parts.push([line]);
    } else if (parts.length === 1) {
      preamble.push(line);
    } else {
      parts[parts.length - 1]?.push(line);
    }
  }
  if (kind === "slides") return { preamble: "", sections: parts.map((part) => part.join("\n").trim()).filter(Boolean) };
  return { preamble: preamble.join("\n").trim(), sections: parts.slice(1).map((part) => part.join("\n").trim()) };
}

/** The pieces put back together as the service writes them. */
export function joinDocument(preamble: string, sections: string[], kind: OutputKind): string {
  if (kind === "slides") return sections.join("\n\n---\n\n");
  return [preamble, ...sections].filter(Boolean).join("\n\n");
}

/** A section as its hash is taken: "\n" line ends, no space around it. */
export const normalizeSection = (text: string): string => text.replace(/\r\n?/g, "\n").trim();

export async function sectionHash(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(normalizeSection(text));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** The first line of a section without its "#" marks. */
export function headingOf(section: string): string {
  for (const raw of section.split("\n")) {
    const line = raw.trim();
    if (line) return line.replace(/^#{1,6}\s*/, "").slice(0, 160);
  }
  return "";
}

/** The first top-level heading outside code, at most 160 characters. */
export function titleOf(content: string): string | null {
  for (const { line, fenced } of lines(content)) {
    if (!fenced && line.startsWith("# ")) return line.slice(2).trim().slice(0, 160) || null;
  }
  return null;
}

/** A slide as what it shows and what the presenter says. */
export function splitNotes(slide: string): { body: string; notes: string } {
  const all = lines(slide);
  const at = all.findIndex(({ line, fenced }) => !fenced && line.startsWith("Note:"));
  if (at === -1) return { body: slide.trim(), notes: "" };
  const body = all.slice(0, at).map(({ line }) => line).join("\n").trim();
  const [first = "", ...rest] = all.slice(at).map(({ line }) => line);
  return { body, notes: [first.slice("Note:".length).trim(), ...rest].join("\n").trim() };
}

/** A slide as the viewer needs it: its heading, what it shows, and what the presenter says. */
export interface ParsedSlide {
  heading: string;
  body: string;
  notes: string;
}

export function parseSlides(content: string): ParsedSlide[] {
  return splitDocument(content, "slides").sections.map((text) => ({ heading: headingOf(text), ...splitNotes(text) }));
}

/**
 * The version with text typed over it, for the live preview. A section that reads as it did
 * keeps what the Checker found in it; one that was typed is "not re-checked" until it is.
 */
export function withDraft(artifact: Artifact, draft: string): Artifact {
  const before = splitDocument(artifact.content, artifact.kind).sections;
  const known = new Map<string, OutputSection>();
  before.forEach((text, index) => {
    const info = artifact.sections[index];
    if (info && !known.has(normalizeSection(text))) known.set(normalizeSection(text), info);
  });
  const sections = splitDocument(draft, artifact.kind).sections.map((text, index): OutputSection => {
    const info = known.get(normalizeSection(text));
    return info ? { ...info, index, heading: headingOf(text) } : { index, heading: headingOf(text), checked: false, issues: [] };
  });
  // One id for every keystroke, so a deck is not rebuilt each time.
  return { ...artifact, id: `${artifact.id}:draft`, content: draft, sections };
}

/** The text without its first top-level title, which the page shows on its own. */
export function withoutTitle(text: string): string {
  let skipped = false;
  return text.split("\n").filter((line) => {
    if (!skipped && line.startsWith("# ")) {
      skipped = true;
      return false;
    }
    return true;
  }).join("\n").trim();
}
