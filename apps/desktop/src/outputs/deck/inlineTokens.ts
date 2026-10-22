import { textWidth } from "./measure";
/**
 * The inline Markdown of a slide's text as tokens: **bold**, *italic*, `code`, and citation marks
 * ([3], [1, 2], [?], and the Checker's [i:3] [p:3] [d:3]). Adjacent marks join: [1][2] is 1,2.
 * Pure, so the renderer and the PowerPoint/Word exports read the text the same way.
 */
const TOKEN = /(\*\*|__)(.+?)\1|\*([^*\s][^*]*?)\*|`([^`]+)`|\\([\\`*_{}[\]()#+\-.!|])|\[(?:[ipd]:)?(\d+(?:\s*[,，、]\s*\d+)*)\]|\[\?\]/g;

export type Token = { t: "text"; v: string } | { t: "bold"; v: string } | { t: "em"; v: string } | { t: "code"; v: string } | { t: "cite"; n: number[]; unverified: boolean };

export function tokens(raw: string): Token[] {
  const out: Token[] = [];
  let last = 0;
  for (const m of raw.matchAll(TOKEN)) {
    let before = raw.slice(last, m.index);
    const cite = m[6] !== undefined || m[0] === "[?]";
    if (cite) before = before.replace(/[ \t ]+$/, "");
    if (before) out.push({ t: "text", v: before });
    if (m[2] !== undefined) out.push({ t: "bold", v: m[2] });
    else if (m[3] !== undefined) out.push({ t: "em", v: m[3] });
    else if (m[4] !== undefined) out.push({ t: "code", v: m[4] });
    else if (m[5] !== undefined) out.push({ t: "text", v: m[5] });
    else if (m[6] !== undefined) {
      const n = m[6].split(/[,，、]/).map((part) => Number(part.trim()));
      const previous = out.at(-1);
      if (previous?.t === "cite" && !previous.unverified) previous.n.push(...n); else out.push({ t: "cite", n, unverified: false });
    } else out.push({ t: "cite", n: [], unverified: true });
    last = (m.index ?? 0) + m[0].length;
  }
  if (last < raw.length) out.push({ t: "text", v: raw.slice(last) });
  return out;
}

/** The numbers of the sources a Markdown slice cites, and whether it has an unverified [?]. */
export function citedIn(raw: string): { numbers: number[]; unverified: boolean } {
  const numbers: number[] = [];
  let unverified = false;
  for (const token of tokens(raw)) {
    if (token.t === "cite") { if (token.unverified) unverified = true; else for (const n of token.n) if (!numbers.includes(n)) numbers.push(n); }
    else if ((token.t === "bold" || token.t === "em") && token.v) { const inner = citedIn(token.v); inner.numbers.forEach((n) => { if (!numbers.includes(n)) numbers.push(n); }); }
  }
  return { numbers, unverified };
}

/** The short name of a source for a foot line: its "(Okafor 2025)" if it has one with words in it, else its title cut at a word. */
export function shortSource(source: string, limit = 40): string {
  const title = source.split(/ [·›] /)[0]!.trim();
  const paren = /\(([^()]*\p{L}[^()]*)\)\s*$/u.exec(title);
  if (paren?.[1]) return paren[1].trim();
  if (title.length <= limit) return title;
  const cut = title.slice(0, limit).replace(/\s+\S*$/, "");
  return `${cut || title.slice(0, limit)}…`;
}

/**
 * The source line of a slide: the sources its text cites, in order, each once (several citations of
 * one source are one entry), without their places. If it would not fit on one line, the first
 * sources and "+N more".
 */
export function sourceLine(text: string, numbers: number[], sources: string[] | undefined, width = 1000): string {
  const { numbers: cited, unverified } = citedIn(text);
  const names = [...new Set(cited.map((n) => sources?.[numbers.indexOf(n)]).filter((s): s is string => !!s).map((s) => shortSource(s)))];
  if (unverified) names.push("model knowledge, not verified");
  if (!names.length) return "";
  for (let keep = names.length; keep >= 1; keep -= 1) {
    const line = `Sources: ${names.slice(0, keep).join("; ")}${keep < names.length ? ` +${names.length - keep} more` : ""}`;
    if (keep === 1 || textWidth(line, 14) <= width) return line;
  }
  return "";
}

/** The same sources with their places, for a tooltip. */
export function sourceDetail(text: string, numbers: number[], sources: string[] | undefined): string {
  return [...new Set(citedIn(text).numbers.map((n) => sources?.[numbers.indexOf(n)]).filter((s): s is string => !!s))].join("\n");
}
