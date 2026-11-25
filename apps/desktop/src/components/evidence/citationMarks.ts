import type { Text } from "mdast";
import { decodeString } from "micromark-util-decode-string";
import { mapMarkdown } from "../markdown/rich/mapMarkdown";
import { parseRichMarkdown, sourceForPositions } from "../markdown/rich/plugins";

const MARK = /\[(?:([ipd]):)?(\d+(?:\s*[,，、]\s*\d+)*)\]|\[\?\]/g;
const ESCAPE_OR_REFERENCE = /\\[!-/:-@[-`{-~]|&(?:#(?:x[\da-f]+|\d+)|[a-z][\da-z]*);/gi;

type CitationMark = {
  kind: "i" | "p" | "d" | "cite" | "unverified";
  numbers: string[];
  start: number;
  end: number;
  sourceRange: { start: number; end: number } | null;
};

/** Align a text node with its source, masking characters created by escapes or entities. */
function literalSpelling(raw: string, value: string): { spelling: string; offsets?: number[] } | undefined {
  if (raw === value) return { spelling: raw };

  const lines = raw.split(/(\r\n?|\n)/);
  const offsets: number[] = [];
  const parts: string[] = [];
  const append = (text: string, offset: number) => {
    parts.push(text);
    for (let index = 0; index < text.length; index++) offsets.push(offset + index);
  };
  let lineStart = 0;
  for (let index = 0; index < lines.length; index += 2) {
    const rawLine = lines[index]!;
    const ending = lines[index + 1] ?? "";
    const line = (ending ? rawLine.replace(/[ \t]+$/, "") : rawLine).replace(/\0/g, "\uFFFD");
    // Continuation lines can include list indentation or blockquote prefixes.
    // The parsed value tells us how much of the prefix belongs to the text.
    const prefix = index === 0 ? "" : /^[\t >]*/.exec(line)![0];
    const tail = decodeString(line.slice(prefix.length)) + ending;
    let skip = prefix.length;
    while (skip >= 0 && !value.startsWith(prefix.slice(skip) + tail, offsets.length)) skip--;
    if (skip < 0) return;

    let at = skip;
    const content = line.slice(skip);
    for (const match of content.matchAll(ESCAPE_OR_REFERENCE)) {
      const start = skip + match.index;
      const token = match[0];
      const decoded = decodeString(token);
      append(line.slice(at, start), lineStart + at);
      append(token === decoded ? token : "\0".repeat(decoded.length), lineStart + start);
      at = start + token.length;
    }
    append(line.slice(at), lineStart + at);
    append(ending, lineStart + rawLine.length);
    lineStart += rawLine.length + ending.length;
  }
  if (offsets.length === value.length) return { spelling: parts.join(""), offsets };
}

export function textCitationMarks(node: Text, source: string): CitationMark[] {
  const start = node.position?.start.offset;
  const end = node.position?.end.offset;
  const hasSource = start !== undefined && end !== undefined;
  // A parser transform can supply text without a source span to check for escapes.
  const raw = hasSource ? source.slice(start, end) : node.value;
  if (!raw.includes("[")) return [];
  const literal = literalSpelling(raw, node.value);
  if (!literal) return [];

  return [...literal.spelling.matchAll(MARK)].map((match) => {
    const first = match.index;
    const last = first + match[0].length - 1;
    const payload = match[2];
    return {
      kind: (match[1] as "i" | "p" | "d" | undefined) ?? (payload ? "cite" : "unverified"),
      numbers: payload?.split(/\s*[,，、]\s*/) ?? [],
      start: first,
      end: last + 1,
      sourceRange: hasSource ? {
        start: start + (literal.offsets?.[first] ?? first),
        end: start + (literal.offsets?.[last] ?? last) + 1,
      } : null,
    };
  });
}

function proseMarks(markdown: string): CitationMark[] {
  const source = sourceForPositions(markdown);
  const marks: CitationMark[] = [];
  mapMarkdown(parseRichMarkdown(markdown), (node) => {
    if (node.type === "link" || node.type === "linkReference") return node;
    if (node.type === "text") marks.push(...textCitationMarks(node, source));
  });
  return marks;
}

export function proseCitationNumbers(markdown: string): number[] {
  return proseMarks(markdown).flatMap((mark) => mark.numbers.map(Number));
}

/** Strip prose marks while retaining the original spelling of the rest of the Markdown. */
export function withoutMarks(markdown: string): string {
  if (!markdown.includes("[")) return markdown;
  const marks = proseMarks(markdown);
  const bom = markdown.length - sourceForPositions(markdown).length;
  let cursor = 0;
  let result = "";
  for (const mark of marks) {
    if (!mark.sourceRange) continue;
    const start = mark.sourceRange.start + bom;
    const trimSpace = mark.start > 0 && /\s/.test(markdown[start - 1] ?? "");
    result += markdown.slice(cursor, trimSpace ? start - 1 : start);
    cursor = mark.sourceRange.end + bom;
  }
  return result + markdown.slice(cursor);
}
