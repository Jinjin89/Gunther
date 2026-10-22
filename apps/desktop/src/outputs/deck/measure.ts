/** Text measuring without a DOM: an estimate of a text's width at a font size, and where it breaks into lines. */
const CJK = /[　-鿿豈-﫿가-힯＀-￯]/;
/** A text's width at a font size, estimated (SVG text has no wrapping, so labels are broken by this). */
export const textWidth = (text: string, size: number, weight = 500): number =>
  [...text].reduce((total, char) => total + size * (CJK.test(char) ? 1 : /[A-Z0-9%$€£]/.test(char) ? (weight > 500 ? 0.66 : 0.62) : char === " " ? 0.28 : /[ilj.,'|:;!]/.test(char) ? 0.28 : (weight > 500 ? 0.58 : 0.54)), 0);

/** A label broken into lines no wider than `width`, at most `max` lines (the last is cut with an ellipsis). */
export function wrap(text: string, size: number, width: number, max: number, weight = 500): string[] {
  const lines: string[] = [];
  let line = "";
  const units = text.match(/[　-鿿豈-﫿가-힯＀-￯]|\S+\s*/g) ?? [];
  for (const unit of units) {
    if (line && textWidth((line + unit).trimEnd(), size, weight) > width) { lines.push(line.trimEnd()); line = unit; } else line += unit;
  }
  if (line) lines.push(line.trimEnd());
  if (lines.length > max) { const kept = lines.slice(0, max); kept[max - 1] = `${kept[max - 1]}…`; return kept; }
  return lines;
}

