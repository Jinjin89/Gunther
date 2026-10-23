/**
 * What a designed slide's text looks like on its 1280 x 720 stage, as a list of text blocks the PowerPoint export can
 * write as real text boxes (exportDesigned.tsx collects them in the browser; designedPptx.ts writes them). Everything
 * is in stage pixels. Text inside an SVG or a `<gunther-chart>` is not collected: charts and drawings stay in the picture.
 */
export interface TextRun {
  text: string;
  /** The first font family the browser was asked for (for Chinese text, the CJK family from the stack). */
  font: string;
  /** Stage pixels. */
  size: number;
  bold: boolean;
  italic: boolean;
  underline: boolean;
  /** Hex without `#`. */
  color: string;
  /** 0 (solid) to 100 (invisible): the colour's alpha and the opacity of its ancestors. */
  transparency: number;
  /** A citation mark or other raised text. */
  superscript: boolean;
  /** Letter spacing in stage pixels. */
  spacing: number;
  /** A line break comes after this run. */
  breakLine: boolean;
}

export interface TextBlock {
  x: number;
  y: number;
  w: number;
  h: number;
  align: "left" | "center" | "right";
  /** CSS line height in stage pixels. */
  lineHeight: number;
  lines: number;
  /** Several lines wrap inside the box; one line never wraps (a slightly different font would otherwise push its last word down). */
  wrap: boolean;
  runs: TextRun[];
}

const STAGE_W = 1280;
const STAGE_H = 720;
const CJK = /[　-鿿豈-﫿가-힯＀-￯]/;
const CJK_FAMILY = /PingFang|Noto (?:Sans|Serif) (?:CJK|SC)|Microsoft YaHei|Hiragino|Source Han|WenQuanYi|SimSun|SimHei|Songti|Heiti|STHeiti/i;
const FALLBACK_CJK = "PingFang SC";
/** The collapsible whitespace of CSS (a no-break space is not). */
const SPACE = /[ \t\n\r\f]+/g;

const hex2 = (n: number) => Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, "0");

/** `rgb(…)` / `rgba(…)` / `color(srgb …)` read by the browser as a hex colour and an alpha (0 to 1). */
export function readColour(value: string): { hex: string; alpha: number } {
  const srgb = /^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+%?))?\)/.exec(value);
  if (srgb) {
    const alpha = srgb[4] === undefined ? 1 : srgb[4].endsWith("%") ? parseFloat(srgb[4]) / 100 : parseFloat(srgb[4]);
    return { hex: `${hex2(+srgb[1]! * 255)}${hex2(+srgb[2]! * 255)}${hex2(+srgb[3]! * 255)}`.toUpperCase(), alpha };
  }
  const match = /rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:[ ,/]+([\d.]+%?))?\s*\)/.exec(value);
  if (!match) return { hex: "000000", alpha: value === "transparent" ? 0 : 1 };
  const alpha = match[4] === undefined ? 1 : match[4].endsWith("%") ? parseFloat(match[4]) / 100 : parseFloat(match[4]);
  return { hex: `${hex2(+match[1]!)}${hex2(+match[2]!)}${hex2(+match[3]!)}`.toUpperCase(), alpha };
}

/** The family PowerPoint is asked for: the stack's first, or for CJK text the first CJK family in the stack (else PingFang SC). */
export function pickFont(stack: string, text: string): string {
  const families = stack.split(",").map((family) => family.trim().replace(/^["']|["']$/g, "")).filter(Boolean);
  if (CJK.test(text)) return families.find((family) => CJK_FAMILY.test(family)) ?? FALLBACK_CJK;
  const first = families.find((family) => !/^(serif|sans-serif|monospace|system-ui|ui-[a-z-]+|-apple-system|cursive|fantasy)$/i.test(family));
  return first ?? (families[0] === "serif" ? "Georgia" : "Arial");
}

const skipped = (element: Element) => /^(svg|style|script|template|noscript|gunther-chart|canvas|img|video|iframe)$/i.test(element.tagName);

function visibleElement(element: Element): boolean {
  const style = getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
}

/** An element's ancestors' opacity multiplied together, up to the stage. */
function opacityOf(element: Element, stage: Element): number {
  let alpha = 1;
  for (let at: Element | null = element; at && at !== stage.parentElement; at = at.parentElement) alpha *= parseFloat(getComputedStyle(at).opacity || "1");
  return alpha;
}

type Piece = { text: Text } | { br: true };

/** The text (and line breaks) an element sets itself, through inline children; other blocks are not entered. */
function ownPieces(element: Element, out: Piece[] = []): Piece[] {
  for (const node of element.childNodes) {
    if (node.nodeType === Node.TEXT_NODE) out.push({ text: node as Text });
    else if (node.nodeType === Node.ELEMENT_NODE) {
      const child = node as Element;
      if (child.tagName === "BR") { out.push({ br: true }); continue; }
      if (skipped(child) || !visibleElement(child)) continue;
      const display = getComputedStyle(child).display;
      if (display === "inline" || display === "contents") ownPieces(child, out);
    }
  }
  return out;
}

const hasText = (pieces: Piece[]) => pieces.some((piece) => "text" in piece && /\S/.test(piece.text.data));

/** True for an element that makes the text of a letter case change (text-transform). */
function transformed(text: string, transform: string): string {
  if (transform === "uppercase") return text.toUpperCase();
  if (transform === "lowercase") return text.toLowerCase();
  if (transform === "capitalize") return text.replace(/(^|\s)(\p{L})/gu, (_, space: string, letter: string) => space + letter.toUpperCase());
  return text;
}

/**
 * Every block of text on the stage: an element that sets text of its own (directly or through spans, strong, sup and the
 * like) is one block, its runs carry each span's font, weight, colour and so on. Boxes are measured from the real line
 * boxes, so they sit where the browser drew the text. `stage` is the `.stage` element (scale 1, or any scale).
 */
export function collectTextBlocks(stage: HTMLElement): TextBlock[] {
  const frame = stage.getBoundingClientRect();
  const factor = frame.width / STAGE_W || 1;
  const blocks: TextBlock[] = [];
  const walk = (element: Element) => {
    if (skipped(element) || !visibleElement(element)) return;
    const style = getComputedStyle(element);
    if (style.display !== "inline" && style.display !== "contents") {
      const block = oneBlock(element as HTMLElement, style);
      if (block) blocks.push(block);
    }
    for (const child of element.children) walk(child);
  };
  const oneBlock = (element: HTMLElement, style: CSSStyleDeclaration): TextBlock | null => {
    const pieces = ownPieces(element);
    if (!hasText(pieces)) return null;
    const size = parseFloat(style.fontSize) || 16;
    const lineHeight = Number.isFinite(parseFloat(style.lineHeight)) ? parseFloat(style.lineHeight) : size * 1.25;
    const runs: TextRun[] = [];
    const rects: DOMRect[] = [];
    const opacity = opacityOf(element, stage);
    for (const piece of pieces) {
      if ("br" in piece) { const last = runs[runs.length - 1]; if (last) last.breakLine = true; continue; }
      const parent = piece.text.parentElement ?? element;
      const look = getComputedStyle(parent);
      const raw = piece.text.data.replace(SPACE, " ");
      if (!/\S/.test(raw) && (runs.length === 0 || runs[runs.length - 1]!.breakLine)) continue;
      const text = transformed(raw, look.textTransform);
      if (!text) continue;
      const colour = readColour(look.color);
      const raised = !!parent.closest("sup") || look.verticalAlign === "super";
      const spacing = parseFloat(look.letterSpacing);
      runs.push({
        text, font: pickFont(look.fontFamily, text), size: raised ? size : parseFloat(look.fontSize) || size,
        bold: (parseInt(look.fontWeight, 10) || 400) >= 600, italic: look.fontStyle === "italic" || look.fontStyle === "oblique",
        underline: look.textDecorationLine.includes("underline"),
        color: colour.hex, transparency: Math.round((1 - colour.alpha * opacity * (parseFloat(look.opacity) || 1)) * 100),
        superscript: raised, spacing: Number.isFinite(spacing) ? spacing : 0, breakLine: false,
      });
      if (/\S/.test(piece.text.data)) {
        const range = document.createRange();
        range.selectNodeContents(piece.text);
        for (const box of range.getClientRects()) if (box.width > 0 && box.height > 0) rects.push(box);
      }
    }
    // Whitespace at the ends of the block is not text.
    if (runs[0]) runs[0].text = runs[0].text.replace(/^ +/, "");
    for (let at = runs.length - 1; at >= 0; at--) {
      const run = runs[at]!;
      run.text = run.text.replace(/ +$/, "");
      if (run.text) break;
    }
    const used = runs.filter((run) => run.text || run.breakLine);
    if (!used.some((run) => run.text) || rects.length === 0) return null;
    // Lines: rectangles whose centres are within half a font size sit on one line.
    const sorted = [...rects].sort((a, b) => a.top + a.height / 2 - (b.top + b.height / 2));
    const lines: DOMRect[][] = [];
    for (const rect of sorted) {
      const last = lines[lines.length - 1];
      const centre = rect.top + rect.height / 2;
      const lastCentre = last ? last[0]!.top + last[0]!.height / 2 : 0;
      if (last && Math.abs(centre - lastCentre) <= size * factor * 0.5) last.push(rect); else lines.push([rect]);
    }
    const left = Math.min(...rects.map((r) => r.left));
    const right = Math.max(...rects.map((r) => r.right));
    const tallest = lines[0]!.reduce((a, b) => (b.height > a.height ? b : a));
    const firstCentre = tallest.top + tallest.height / 2;
    const box = element.getBoundingClientRect();
    const block = /^(block|list-item|flow-root|table-cell)$/.test(style.display);
    const pad = (name: string) => (parseFloat(style.getPropertyValue(name)) || 0) + (parseFloat(style.getPropertyValue(name.replace("padding", "border").concat("-width"))) || 0);
    let x0 = left;
    let x1 = right;
    if (block) {
      x0 = Math.min(left, box.left + pad("padding-left"));
      x1 = Math.max(right, box.right - pad("padding-right"));
    } else if (lines.length > 1) x1 += size * factor * 0.5;
    const align = /^(center)$/.test(style.textAlign) ? "center" : /^(right|end)$/.test(style.textAlign) ? "right" : "left";
    const linesCount = Math.max(lines.length, used.filter((run) => run.breakLine).length ? lines.length : 1);
    const result: TextBlock = {
      x: (x0 - frame.left) / factor, y: (firstCentre - frame.top) / factor - lineHeight / 2,
      w: (x1 - x0) / factor, h: linesCount * lineHeight, align, lineHeight, lines: linesCount, wrap: linesCount > 1, runs: used,
    };
    return clampToStage(result);
  };
  for (const child of stage.children) walk(child);
  return blocks;
}

/** A box never leaves the stage: the text can be shorter than it was set but a box sticking out would be cut off. */
export function clampToStage(block: TextBlock): TextBlock {
  const x = Math.max(0, Math.min(block.x, STAGE_W - 1));
  const y = Math.max(0, Math.min(block.y, STAGE_H - 1));
  return { ...block, x, y, w: Math.max(1, Math.min(block.w, STAGE_W - x)), h: Math.max(1, Math.min(block.h, STAGE_H - y)) };
}
