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
 * - A slide made by a skill starts with a marker line that says how it is drawn:
 *   "<!-- layout: compare -->", and on the title slide also "<!-- layout: title; accent: blue -->".
 *   It is a comment: a slide's heading is the first line under it, and the hash of the section
 *   covers it. A slide without one (an older deck) takes its outline's layout (see slideLayouts).
 */
import type { Artifact, OutputKind, OutputSection } from "@gunther/contracts";

const FENCE = /^\s*(```|~~~)/;
/** The colours a title slide's marker may name (the same list as ACCENTS in outputs.py). */
export const ACCENTS = ["ink", "blue", "teal", "red", "amber", "violet"] as const;
/** At the start of a slide, after any blank lines: the whole marker line. */
const LAYOUT_MARK = /^[ \t\r\n]*<!--[ \t]*layout:[ \t]*([a-z][a-z0-9_]*)[ \t]*(?:;[ \t]*accent:[ \t]*([a-z]+)[ \t]*)?-->[ \t]*(?:\r?\n|$)/;

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

/**
 * A slide's layout and accent from its marker, and the slide without the marker. Only the title
 * slide has an accent, and only one of the six colours. A slide with no marker has no layout and
 * is given back as it is.
 */
export function layoutOf(text: string): { layout: string | null; accent: string | null; body: string } {
  const found = LAYOUT_MARK.exec(text);
  const layout = found?.[1];
  if (!found || !layout) return { layout: null, accent: null, body: text };
  const accent = found[2];
  return {
    layout,
    accent: layout === "title" && accent !== undefined && ACCENTS.some((name) => name === accent) ? accent : null,
    body: text.slice(found[0].length).replace(/^(?:[ \t]*\r?\n)+/, ""),
  };
}

/**
 * The deck with its accent changed. The accent lives in the title slide's marker, so that one
 * line is rewritten (or added, for a first slide that has no marker) and nothing else moves.
 * "ink" is the default and is written as no accent. A deck whose first slide is drawn as some
 * other layout has no place for an accent (only a title slide has one), and neither has an empty
 * deck; an unknown name is no colour either. All three come back as they were.
 */
export function withAccent(content: string, accent: string): string {
  if (!ACCENTS.some((name) => name === accent) || content.trim() === "") return content;
  const line = (suffix: string) => `<!-- layout: title${accent === "ink" ? "" : `; accent: ${accent}`} -->${suffix}`;
  const found = LAYOUT_MARK.exec(content);
  if (!found) {
    if (accent === "ink") return content;
    const lead = /^[ \t\r\n]*/.exec(content)?.[0] ?? "";
    return `${lead}${line("\n")}${content.slice(lead.length)}`;
  }
  if (found[1] !== "title") return content;
  const lead = /^[ \t\r\n]*/.exec(found[0])?.[0] ?? "";
  const end = /\r?\n$/.exec(found[0])?.[0] ?? "";
  return `${lead}${line(end)}${content.slice(found[0].length)}`;
}

/** The first line of a section without its "#" marks, and under a slide's marker. */
export function headingOf(section: string): string {
  for (const raw of layoutOf(section).body.split("\n")) {
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

/** A slide as the viewer needs it: its heading, how it is drawn, what it shows, and what the presenter says. */
export interface ParsedSlide {
  heading: string;
  /** From the slide's marker; null when it has none. */
  layout: string | null;
  /** The title slide's accent colour, from its marker. */
  accent: string | null;
  /** What the slide shows, heading included, without the marker. */
  body: string;
  notes: string;
}

export function parseSlides(content: string): ParsedSlide[] {
  return splitDocument(content, "slides").sections.map((text) => {
    const { layout, accent, body } = layoutOf(text);
    return { heading: headingOf(body), layout, accent, ...splitNotes(body) };
  });
}

/**
 * The layout each slide is drawn in: its marker's. A deck made before markers has none, so its
 * outline's layouts stand in, but only when the outline has a part for every slide: after a hand
 * edit that added or removed slides they would not line up, and a slide gets no layout then.
 */
export function slideLayouts(artifact: Pick<Artifact, "content" | "outline">): Array<string | null> {
  const slides = parseSlides(artifact.content);
  const outline = artifact.outline ?? [];
  const lined = outline.length === slides.length;
  return slides.map((slide, index) => slide.layout ?? (lined ? outline[index]?.layout ?? null : null));
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
