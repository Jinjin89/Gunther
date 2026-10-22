import { splitDocument, layoutOf } from "../sections";

/** A deck whose title slide's marker says `design: v2` (the studio design). */
export function isDeckV2(content: string): boolean {
  const first = splitDocument(content, "slides").sections[0];
  return first !== undefined && layoutOf(first).designVersion === 2;
}

/** A report that starts with the line `<!-- report: v2 -->` (the studio design). */
export const isReportV2 = (content: string): boolean => /^\s*<!--\s*report:\s*v2\s*-->/.test(content);
