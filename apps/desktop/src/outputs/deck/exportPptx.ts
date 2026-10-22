/**
 * A studio deck as a PowerPoint file (pptxgenjs): one slide per deck slide on the same 1280 x 720
 * geometry (13.333 x 7.5 in; 96 px to the inch), real text boxes, native charts, real tables,
 * speaker notes, the source line as a small text box, and citation marks as superscript numbers.
 */
import PptxGenJS from "pptxgenjs";
import { wrap } from "./measure";
import { tokens } from "./inlineTokens";
import { sourceLine } from "./inlineTokens";
import { parseDeck, type DeckData, type DeckSlide } from "./parseDeck";
import { DECK_THEME_TOKENS, deckAccent } from "./theme";
import { countUnits, visible } from "./units";

export interface PptxOptions {
  /** What each citation names, in citation-number order ("Harlow 2024 · p. 3"). */
  sources?: string[];
  /** The bytes of a kept figure as a data URL, or null when it cannot be had. */
  resolveImage?: (assetId: string) => Promise<string | null>;
}

const inch = (px: number) => px / 96;
const pt = (px: number) => Math.round(px * 75) / 100;
const hex = (colour: string) => colour.replace("#", "");
const SANS = "Arial";
const SERIF = "Georgia";
const CJK = /[　-鿿豈-﫿가-힯＀-￯]/;

interface Ink { bg: string; ink: string; muted: string; rule: string; soft: string; bar: string; onAccent: string; accent: string; titleFont: string }
interface Run { text: string; options: Record<string, unknown> }
interface Base { size: number; color: string; bold?: boolean; italic?: boolean; font?: string }

/** Runs for one paragraph of Markdown: bold, italic, and citation marks as superscript numbers. */
function runs(raw: string, base: Base, ink: Ink, last = true, extra: Record<string, unknown> = {}): Run[] {
  const out: Run[] = [];
  const add = (text: string, options: Record<string, unknown>) => out.push({ text, options: { fontFace: base.font ?? SANS, fontSize: pt(base.size), color: hex(base.color), bold: base.bold, italic: base.italic, ...(CJK.test(text) ? { lang: "zh-CN" } : {}), ...options } });
  const walk = (parts: ReturnType<typeof tokens>, inherit: Record<string, unknown>) => {
    for (const token of parts) {
      if (token.t === "text") add(token.v, inherit);
      else if (token.t === "bold") walk(tokens(token.v), { ...inherit, bold: true });
      else if (token.t === "em") walk(tokens(token.v), { ...inherit, italic: true });
      else if (token.t === "code") add(token.v, { ...inherit, fontFace: "Courier New" });
      else add(token.unverified ? "?" : token.n.join(","), { ...inherit, superscript: true, color: hex(token.unverified ? ink.muted : ink.accent), bold: true });
    }
  };
  for (const [at, line] of raw.split("\n").entries()) {
    if (at > 0) out.push({ text: "", options: { breakLine: true } });
    walk(tokens(line), {});
  }
  if (out.length === 0) add("", {});
  const tail = out[out.length - 1]!;
  tail.options = { ...tail.options, ...extra, ...(last ? {} : { breakLine: true }) };
  return out;
}

/** Height of a text at a font size in a width (an estimate: the same one the charts use). */
const heightOf = (raw: string, size: number, width: number, line = 1.34, weight = 500) => Math.max(1, wrap(visible(raw), size, width, 30, weight).length) * size * line;

export async function buildPptx(content: string, options: PptxOptions = {}): Promise<PptxGenJS> {
  const deck = parseDeck(content);
  const theme = DECK_THEME_TOKENS[deck.theme];
  const ink: Ink = { ...theme, accent: hex(deckAccent(deck.accent, deck.theme)), titleFont: theme.titleFont === "serif" ? SERIF : SANS };
  for (const key of ["bg", "ink", "muted", "rule", "soft", "bar", "onAccent"] as const) (ink as unknown as Record<string, string>)[key] = hex(theme[key]);
  const numbers = (options.sources ?? []).map((_, at) => at + 1);
  const pptx = new PptxGenJS();
  pptx.layout = "LAYOUT_WIDE";
  pptx.title = deck.slides[0] ? visible((deck.slides[0].data as { title?: string }).title ?? "") : "Deck";
  for (const [at, source] of deck.slides.entries()) {
    const slide = pptx.addSlide();
    slide.background = { color: ink.bg };
    const foot = sourceLine(source.body, numbers, options.sources);
    await draw(pptx, slide, source, at === 0 ? "title" : source.data.layout, ink, options);
    if (foot) slide.addText(foot, { x: inch(80), y: inch(692), w: inch(1000), h: inch(22), fontFace: SANS, fontSize: pt(14), color: ink.muted, margin: 0, valign: "middle", fit: "none", wrap: false });
    if (source.data.layout !== "title" && source.data.layout !== "section") slide.addText(String(at + 1), { x: inch(1100), y: inch(692), w: inch(100), h: inch(22), fontFace: SANS, fontSize: pt(14), color: ink.muted, align: "right", margin: 0, valign: "middle" });
    if (source.notes) slide.addNotes(source.notes);
  }
  return pptx;
}

/** The deck as the bytes of a .pptx file. */
export async function pptxBytes(content: string, options: PptxOptions = {}): Promise<Uint8Array> {
  const pptx = await buildPptx(content, options);
  return new Uint8Array(await pptx.write({ outputType: "arraybuffer" }) as ArrayBuffer);
}

type Slide = ReturnType<PptxGenJS["addSlide"]>;

function box(slide: Slide, x: number, y: number, w: number, h: number, fill: string) {
  slide.addShape("rect", { x: inch(x), y: inch(y), w: inch(w), h: inch(h), fill: { color: fill }, line: { type: "none" } });
}

function text(slide: Slide, parts: Run[], x: number, y: number, w: number, h: number, extra: Record<string, unknown> = {}) {
  slide.addText(parts as never, { x: inch(x), y: inch(y), w: inch(w), h: inch(Math.max(h, 8)), margin: 0, valign: "top", fit: "none", wrap: true, ...extra });
}

const bodyBottom = 648;

async function draw(pptx: PptxGenJS, slide: Slide, source: DeckSlide, layout: string, ink: Ink, options: PptxOptions) {
  const d: DeckData = source.data;
  if (d.layout === "title" || d.layout === "section") return cover(slide, d, ink);
  // Action title and the little accent mark at the top edge.
  box(slide, 80, 0, 64, 6, ink.accent);
  const kicker = d.layout === "statement";
  const titleLines = Math.min(2, wrap(visible(d.title), kicker ? 22 : 40, kicker ? 960 : 1120, 3, 700).length);
  if (kicker) text(slide, runs(d.title, { size: 22, color: ink.muted, bold: true }, ink), 80, 48, 960, 60);
  else text(slide, runs(d.title, { size: 40, color: ink.ink, bold: true, font: ink.titleFont }, ink).map((r) => ({ ...r, options: { ...r.options, charSpacing: -0.5 } })), 80, 48, 1120, 98, { lineSpacingMultiple: 1.0 });
  const top = 48 + (kicker ? titleLines * 28.6 : titleLines * 47.2) + (kicker ? 0 : 28);
  const height = bodyBottom - top;
  const after = (raw: string | null, y: number, takeaway = false) => {
    if (!raw) return 0;
    const h = heightOf(raw, 24, takeaway ? 1096 : 1120, 1.38);
    if (takeaway) { box(slide, 80, y, 4, h, ink.accent); text(slide, runs(raw, { size: 24, color: ink.ink, bold: false }, ink), 104, y, 1096, h); } else {
      slide.addShape("line", { x: inch(80), y: inch(y - 18), w: inch(1120), h: 0, line: { color: ink.rule, width: 0.75 } });
      text(slide, runs(raw, { size: 24, color: ink.muted }, ink), 80, y, 1120, h);
    }
    return h;
  };

  switch (d.layout) {
    case "summary": {
      const n = d.items.length;
      const afterH = d.after ? heightOf(d.after, 24, 1120, 1.38) + 42 : 0;
      const rh = Math.min(190, (height - afterH) / n);
      const y0 = top + (height - afterH - rh * n) / 2;
      d.items.forEach((item, at) => {
        const y = y0 + at * rh;
        slide.addShape("line", { x: inch(80), y: inch(y), w: inch(1120), h: 0, line: { color: ink.rule, width: 0.75 } });
        text(slide, runs(String(at + 1).padStart(2, "0"), { size: n === 2 ? 48 : 36, color: ink.accent }, ink), 80, y, 88, rh, { valign: "middle" });
        const parts = [...(item.lead ? runs(item.lead, { size: n === 2 ? 34 : n === 4 ? 26 : 28, color: ink.ink, bold: true }, ink, false) : []), ...runs(item.detail, { size: n === 2 ? 26 : 24, color: ink.muted }, ink)];
        text(slide, parts, 168, y, 1032, rh, { valign: "middle", lineSpacingMultiple: 1.05 });
      });
      slide.addShape("line", { x: inch(80), y: inch(y0 + rh * n), w: inch(1120), h: 0, line: { color: ink.rule, width: 0.75 } });
      if (d.after) after(d.after, y0 + rh * n + afterH - heightOf(d.after, 24, 1120, 1.38));
      break;
    }
    case "stat": if (countUnits(d.value) <= 7) {
      const size = Math.max(40, Math.min(160, Math.floor(470 / (Math.max(1, countUnits(d.value)) * 0.6))));
      const labelH = heightOf(d.label, 34, 560, 1.25, 600);
      const ctxH = d.context ? heightOf(d.context, 24, 560, 1.38) + 20 : 0;
      const left = size * 1.12 + 28 + 4;
      const right = labelH + ctxH;
      const y0 = top + (height - Math.max(left, right)) / 2;
      text(slide, runs(d.value, { size, color: ink.accent, bold: true }, ink), 80, y0 + (Math.max(left, right) - left) / 2, 470, size * 1.12, { valign: "middle", wrap: false });
      box(slide, 80, y0 + (Math.max(left, right) - left) / 2 + size * 1.12 + 28, 64, 4, ink.ink);
      slide.addShape("line", { x: inch(622), y: inch(y0 - 10), w: 0, h: inch(Math.max(left, right) + 20), line: { color: ink.rule, width: 0.75 } });
      const ry = y0 + (Math.max(left, right) - right) / 2;
      text(slide, runs(d.label, { size: 34, color: ink.ink, bold: true }, ink), 678, ry, 522, labelH);
      if (d.context) text(slide, runs(d.context, { size: 24, color: ink.muted }, ink), 678, ry + labelH + 20, 522, ctxH - 20);
      break;
    } else {
      const size = Math.max(40, Math.min(160, Math.floor(1100 / (Math.max(1, countUnits(d.value)) * 0.6))));
      const labelH = heightOf(d.label, 32, 960, 1.25, 600);
      const ctxH = d.context ? heightOf(d.context, 24, 900, 1.38) + 14 : 0;
      const total = size * 1.12 + 32 + 4 + 26 + labelH + ctxH;
      let y = top + (height - total) / 2;
      text(slide, runs(d.value, { size, color: ink.accent, bold: true }, ink), 80, y, 1120, size * 1.12, { valign: "middle", fit: "none", wrap: false });
      y += size * 1.12 + 32;
      box(slide, 80, y, 64, 4, ink.ink);
      y += 30;
      text(slide, runs(d.label, { size: 32, color: ink.ink, bold: true }, ink), 80, y, 960, labelH);
      if (d.context) text(slide, runs(d.context, { size: 24, color: ink.muted }, ink), 80, y + labelH + 14, 900, ctxH - 14);
      break;
    }
    case "stats": {
      const n = d.items.length;
      const widest = Math.max(...d.items.map((item) => countUnits(item.value)));
      const mode = n === 1 || widest > 10 || (n === 3 && widest > 7) ? "rows" : n === 4 && widest > 5 ? "grid" : "cols";
      const afterH = d.after ? heightOf(d.after, 24, 1120, 1.38) + 42 : 0;
      const room = height - afterH;
      const sizeFor = (value: string, w: number, want: number) => Math.max(40, Math.min(want, Math.floor(w / (Math.max(1, countUnits(value)) * 0.6))));
      if (mode === "rows") {
        const rh = Math.min(150, room / n);
        const y0 = top + (room - rh * n) / 2;
        d.items.forEach((item, at) => {
          const y = y0 + at * rh;
          slide.addShape("line", { x: inch(80), y: inch(y), w: inch(1120), h: 0, line: { color: ink.rule, width: 0.75 } });
          text(slide, runs(item.value, { size: sizeFor(item.value, 440, n >= 4 ? 56 : 80), color: ink.accent, bold: true }, ink), 80, y, 460, rh, { valign: "middle", wrap: false });
          text(slide, runs(item.label, { size: 26, color: ink.ink }, ink), 572, y, 628, rh, { valign: "middle" });
        });
      } else {
        const cols = mode === "grid" ? 2 : n;
        const gap = 48;
        const w = (1120 - (cols - 1) * gap) / cols;
        const rows = Math.ceil(n / cols);
        const cell = Math.min(mode === "grid" ? 220 : 300, (room - (rows - 1) * 24) / rows);
        const y0 = top + (room - cell * rows - (rows - 1) * 24) / 2;
        d.items.forEach((item, at) => {
          const x = 80 + (at % cols) * (w + gap);
          const y = y0 + Math.floor(at / cols) * (cell + 24);
          box(slide, x, y, w, 4, at === 0 ? ink.accent : ink.ink);
          const size = sizeFor(item.value, w, n === 2 ? 144 : n === 3 ? 120 : 104);
          text(slide, runs(item.value, { size, color: ink.accent, bold: true }, ink), x, y + 20, w, size * 1.12, { wrap: false, valign: "middle" });
          text(slide, runs(item.label, { size: n === 2 ? 28 : 24, color: ink.ink }, ink), x, y + 20 + size * 1.12 + 12, Math.min(w, n === 2 ? 480 : 440), cell - size * 1.12 - 40);
        });
      }
      if (d.after) after(d.after, top + room + 24);
      break;
    }
    case "chart": {
      const afterH = d.after ? heightOf(d.after, 24, 1096, 1.38) + 20 : 0;
      const m = d.series.length;
      const measure = m === 1 ? `${visible(d.series[0]!.name)}${d.series[0]!.unit ? ` (${d.series[0]!.unit})` : ""}` : "";
      let y = top;
      if (measure) { text(slide, runs(measure, { size: 20, color: ink.muted }, ink), 80, y, 1120, 28); y += 36; }
      const labels = d.categories.map((c) => visible(c.label));
      const data = d.series.map((s) => ({ name: visible(s.name), labels, values: s.values.map((v) => v.n) }));
      const colours = m === 1 ? (d.categories.some((c) => c.highlight) ? d.categories.map((c) => c.highlight ? ink.accent : ink.bar) : [ink.accent]) : [ink.accent, ink.ink, ink.bar].slice(0, m);
      const symbol = d.series[0]!.unit && !/[\p{L}\p{N}]/u.test(d.series[0]!.unit) && d.series[0]!.unit.length <= 2 ? d.series[0]!.unit : "";
      // Lines too close to label each point are read from the axis (the screen labels their ends).
      const crowded = d.kind === "line" && m > 1 && d.categories.length * m > 12;
      const kind = d.kind === "line" ? pptx.ChartType.line : pptx.ChartType.bar;
      slide.addChart(kind, data, {
        x: inch(80), y: inch(y), w: inch(1120), h: inch(bodyBottom - y - afterH),
        barDir: d.kind === "bar" ? "bar" : "col", chartColors: colours, showValue: !crowded, dataLabelFontSize: pt(20), dataLabelFontBold: true, dataLabelColor: ink.ink,
        dataLabelFormatCode: `General${symbol ? `"${symbol}"` : ""}`, dataLabelPosition: d.kind === "line" ? "t" : "outEnd",
        catAxisLabelFontSize: pt(20), catAxisLabelColor: ink.ink, catAxisOrientation: d.kind === "bar" ? "maxMin" : "minMax", catAxisLineShow: true, valAxisHidden: !crowded,
        valGridLine: crowded ? { color: ink.rule, size: 0.75 } : { style: "none" }, catGridLine: { style: "none" }, valAxisLineShow: false, valAxisLabelFontSize: pt(18), valAxisLabelColor: ink.muted,
        showLegend: m > 1, legendPos: "t", legendFontSize: pt(20), legendColor: ink.ink, legendFontFace: SANS,
        barGapWidthPct: 60, lineSize: 4, lineDataSymbolSize: 9, catAxisLabelFontFace: SANS, dataLabelFontFace: SANS,
        plotArea: { fill: { color: ink.bg } },
      } as never);
      if (d.after) after(d.after, bodyBottom - afterH + 20, true);
      break;
    }
    case "table": {
      const cols = d.head.length;
      const longest = Math.max(...d.rows.flatMap((r) => r.cells).map((c) => countUnits(c)), ...d.head.map((c) => countUnits(c)));
      const dense = d.rows.length * cols;
      const size = dense > 20 || longest > 36 ? 20 : dense > 12 || longest > 24 ? 22 : dense <= 9 && longest <= 18 ? 28 : 24;
      const widths = d.head.map((_, c) => Math.max(...[d.head[c]!, ...d.rows.map((r) => r.cells[c]!)].map((cell) => Math.min(countUnits(cell), 40)), 4));
      const total = widths.reduce((a, b) => a + b, 0);
      const colW = widths.map((w) => Math.max(110, (w / total) * 1120));
      const scale = 1120 / colW.reduce((a, b) => a + b, 0);
      const cw = colW.map((w) => inch(w * scale));
      const afterH = d.after ? heightOf(d.after, 24, 1096, 1.38) + 20 : 0;
      const headH = 44;
      const rowH = Math.min(d.rows.length <= 5 ? 92 : 56, (bodyBottom - top - headH - afterH) / d.rows.length);
      const side = (c: number) => ({ align: (d.align[c] ?? "left") as "left" | "right" | "center" });
      const rows = [
        d.head.map((cell, c) => ({ text: runs(cell, { size: 18, color: ink.muted, bold: true }, ink) as never, options: { ...side(c), valign: "bottom", border: [{ type: "none" }, { type: "none" }, { pt: 1.5, color: ink.ink }, { type: "none" }], margin: [0, inch(10), inch(6), 0] } })),
        ...d.rows.map((row) => row.cells.map((cell, c) => ({ text: runs(cell, { size, color: ink.ink, bold: row.highlight || c === 0 }, ink) as never, options: { ...side(c), valign: "middle", fill: row.highlight ? { color: ink.soft } : undefined, border: [{ type: "none" }, { type: "none" }, { pt: row.highlight ? 1.5 : 0.75, color: row.highlight ? ink.ink : ink.rule }, { type: "none" }], margin: [0, inch(10), 0, inch(c === 0 && row.highlight ? 12 : 0)] } }))),
      ];
      slide.addTable(rows as never, { x: inch(80), y: inch(top), w: inch(1120), colW: cw, rowH: [inch(headH), ...d.rows.map(() => inch(rowH))], fontFace: SANS, autoPage: false });
      if (d.after) after(d.after, bodyBottom - afterH + 20, true);
      break;
    }
    case "compare": {
      const afterH = d.after ? heightOf(d.after, 24, 1096, 1.38) + 20 : 0;
      const most = Math.max(...d.sides.map((s) => s.items.length));
      const totalUnits = d.sides.reduce((sum, s) => sum + s.items.reduce((a, i) => a + countUnits(i), 0), 0);
      const mode = most >= 4 ? "dense" : most <= 2 && totalUnits <= 400 ? "roomy" : "normal";
      const size = mode === "dense" ? 22 : mode === "roomy" ? (totalUnits > 200 ? 28 : 32) : 24;
      const avail = bodyBottom - top - afterH;
      slide.addShape("line", { x: inch(640), y: inch(top), w: 0, h: inch(avail), line: { color: ink.rule, width: 0.75 } });
      d.sides.forEach((side, at) => {
        const x = at === 0 ? 80 : 688;
        const w = 512;
        text(slide, runs(visible(side.name).toUpperCase(), { size: 20, color: ink.ink, bold: true }, ink), x, top, w, 28);
        box(slide, x, top + 38, w, 4, at === 0 ? ink.accent : ink.ink);
        let y = top + 58;
        const row = mode === "roomy" ? Math.min(360, avail - 58) / side.items.length : 0;
        side.items.forEach((item) => {
          const h = row || heightOf(item, size, w - 26, 1.34) + (mode === "dense" ? 12 : 20);
          box(slide, x, y + (row ? row / 2 : (h - 10) / 2) - 5, 10, 10, at === 0 ? ink.accent : ink.ink);
          text(slide, runs(item, { size, color: ink.ink }, ink), x + 26, y, w - 26, h, { valign: "middle" });
          if (row) slide.addShape("line", { x: inch(x), y: inch(y + h), w: inch(w), h: 0, line: { color: ink.rule, width: 0.75 } });
          y += h;
        });
      });
      if (d.after) after(d.after, bodyBottom - afterH + 20, true);
      break;
    }
    case "process": {
      const n = d.items.length;
      const roomy = n <= 4 && d.items.reduce((s, i) => s + countUnits(i.lead ?? "") + countUnits(i.detail), 0) <= 220;
      const gap = n >= 5 ? 20 : 28;
      const w = (1120 - gap * (n - 1)) / n;
      const circle = roomy ? 76 : n >= 5 ? 52 : 64;
      const strong = roomy ? 34 : n >= 5 ? 24 : 28;
      const detail = roomy ? 26 : n >= 5 ? 20 : n === 4 ? 22 : 24;
      const afterH = d.after ? heightOf(d.after, 24, 1120, 1.38) + 42 : 0;
      const room = height - afterH;
      const bodyH = Math.max(...d.items.map((i) => (i.lead ? heightOf(i.lead, strong, w - (roomy ? 52 : 0), 1.2, 700) + 8 : 0) + heightOf(i.detail, detail, w - (roomy ? 52 : 0), 1.36))) + (roomy ? 56 : 0);
      const blockH = circle + (roomy ? 30 : 22) + bodyH;
      const y0 = top + Math.max(0, (room - Math.min(blockH, roomy ? 420 : blockH)) / 2);
      slide.addShape("line", { x: inch(80 + circle / 2), y: inch(y0 + circle / 2), w: inch(1120 - circle), h: 0, line: { color: ink.rule, width: 2.25 } });
      d.items.forEach((item, at) => {
        const x = 80 + at * (w + gap);
        slide.addShape("ellipse", { x: inch(x), y: inch(y0), w: inch(circle), h: inch(circle), fill: { color: ink.accent }, line: { type: "none" } });
        text(slide, runs(String(at + 1), { size: circle > 60 ? 28 : 24, color: ink.onAccent, bold: true }, ink), x, y0, circle, circle, { align: "center", valign: "middle" });
        const cy = y0 + circle + (roomy ? 30 : 22);
        if (roomy) box(slide, x, cy, w, Math.min(420, room) - circle - 30, ink.soft);
        const pad = roomy ? 26 : 0;
        const parts = [...(item.lead ? runs(item.lead, { size: strong, color: ink.ink, bold: true }, ink, false, { paraSpaceAfter: 6 }) : []), ...runs(item.detail, { size: detail, color: ink.muted }, ink)];
        text(slide, parts, x + pad, cy + pad, w - 2 * pad, bodyH);
      });
      if (d.after) after(d.after, top + room + 24);
      break;
    }
    case "timeline": {
      const n = d.items.length;
      const roomy = n <= 4 && d.items.reduce((s, i) => s + countUnits(i.event), 0) <= 160;
      const gap = 24;
      const w = (1120 - gap * (n - 1)) / n;
      const dateSize = roomy ? 44 : n === 6 ? 28 : n <= 4 ? 40 : 32;
      const eventSize = roomy ? 28 : n === 6 ? 20 : n <= 4 ? 24 : 22;
      const afterH = d.after ? heightOf(d.after, 24, 1120, 1.38) + 42 : 0;
      const room = height - afterH;
      const eventH = Math.max(...d.items.map((i) => heightOf(i.event, eventSize, w - 8, 1.36)));
      const blockH = dateSize * 1.1 + (roomy ? 24 : 18) + 22 + (roomy ? 26 : 20) + eventH;
      const y0 = top + (room - blockH) / 2;
      const axis = y0 + dateSize * 1.1 + (roomy ? 24 : 18) + 11;
      slide.addShape("line", { x: inch(80), y: inch(axis), w: inch(1120), h: 0, line: { color: ink.ink, width: 3 } });
      d.items.forEach((item, at) => {
        const x = 80 + at * (w + gap);
        text(slide, runs(item.when, { size: dateSize, color: ink.accent, bold: true }, ink), x, y0, w, dateSize * 1.1, { wrap: false });
        slide.addShape("ellipse", { x: inch(x), y: inch(axis - 11), w: inch(22), h: inch(22), fill: { color: ink.bg }, line: { color: ink.accent, width: 3.75 } });
        text(slide, runs(item.event, { size: eventSize, color: ink.ink }, ink), x, axis + 11 + (roomy ? 26 : 20), w - 8, eventH);
      });
      if (d.after) after(d.after, top + room + 24);
      break;
    }
    case "quote": {
      const units = countUnits(d.quote);
      const size = units <= 90 ? 54 : units <= 150 ? 44 : 36;
      const qH = heightOf(d.quote, size, 1020, 1.22, 500);
      const aH = d.attribution ? 30 + 30 : 0;
      const afterH = d.after ? heightOf(d.after, 24, 1120, 1.38) + 42 : 0;
      const room = height - afterH;
      const total = 88 + qH + aH;
      let y = top + (room - total) / 2;
      text(slide, runs("“", { size: 160, color: ink.accent, bold: true, font: SERIF }, ink), 80, y - 10, 200, 110);
      y += 72;
      text(slide, runs(d.quote, { size, color: ink.ink, font: ink.titleFont }, ink), 80, y, 1020, qH, { lineSpacingMultiple: 0.96 });
      if (d.attribution) {
        box(slide, 80, y + qH + 30 + 14, 40, 3, ink.ink);
        text(slide, runs(d.attribution, { size: 22, color: ink.muted }, ink), 136, y + qH + 30, 1000, 30);
      }
      if (d.after) after(d.after, top + room + 24);
      break;
    }
    case "figure": {
      const takeH = d.takeaway ? heightOf(d.takeaway, 24, 1096, 1.38) + 20 : 0;
      const capH = d.alt ? 28 : 0;
      const pictureH = bodyBottom - top - takeH - capH;
      box(slide, 80, top, 1120, pictureH, ink.soft);
      const image = options.resolveImage ? await options.resolveImage(d.assetId) : null;
      if (image) slide.addImage({ data: image, x: inch(92), y: inch(top + 12), w: inch(1096), h: inch(pictureH - 24), sizing: { type: "contain", w: inch(1096), h: inch(pictureH - 24) } } as never);
      else text(slide, runs(d.alt || "The figure could not be loaded.", { size: 22, color: ink.muted }, ink), 120, top, 1040, pictureH, { align: "center", valign: "middle" });
      if (d.alt) text(slide, runs(d.alt, { size: 18, color: ink.muted }, ink), 80, top + pictureH + 8, 1120, 24);
      if (d.takeaway) { box(slide, 80, bodyBottom - takeH + 20, 4, takeH - 20, ink.accent); text(slide, runs(d.takeaway, { size: 24, color: ink.ink }, ink), 104, bodyBottom - takeH + 20, 1096, takeH - 20); }
      break;
    }
    case "statement": {
      const units = countUnits(d.statement);
      const size = units <= 70 ? 56 : units <= 120 ? 50 : 46;
      const sH = heightOf(d.statement, size, 1080, 1.16, 650);
      const itemsH = d.items.length ? 48 + Math.max(...d.items.map((i) => heightOf(i, 24, (1120 - 40 * (d.items.length - 1)) / d.items.length - 8, 1.34))) + 18 : 0;
      const afterH = d.after ? heightOf(d.after, 24, 1120, 1.38) + 42 : 0;
      const room = height - afterH;
      const total = sH + itemsH;
      let y = top + (room - total) / 2;
      text(slide, runs(d.statement, { size, color: ink.ink, bold: true, font: ink.titleFont }, ink), 80, y, 1080, sH, { lineSpacingMultiple: 0.95 });
      y += sH + 48;
      const w = (1120 - 40 * (d.items.length - 1)) / Math.max(1, d.items.length);
      d.items.forEach((item, at) => {
        const x = 80 + at * (w + 40);
        box(slide, x, y, w, 2, ink.ink);
        text(slide, runs(item, { size: 24, color: ink.ink }, ink), x, y + 16, w, itemsH - 48);
      });
      if (d.after) after(d.after, top + room + 24);
      break;
    }
    case "points": {
      const n = d.items.length;
      const roomy = d.items.reduce((s, i) => s + countUnits(i.lead ?? "") + countUnits(i.detail), 0) <= 240;
      const cols = n === 3 ? 3 : 2;
      const gap = n === 3 ? 40 : 48;
      const w = (1120 - gap * (cols - 1)) / cols;
      const strong = roomy ? 36 : n === 4 ? 26 : 28;
      const detail = roomy ? 28 : 24;
      const afterH = d.after ? heightOf(d.after, 24, 1120, 1.38) + 42 : 0;
      const room = height - afterH;
      const cell = (i: { lead: string | null; detail: string }) => (i.lead ? heightOf(i.lead, strong, w - 56, 1.2, 700) + 12 : 0) + heightOf(i.detail, detail, w - 56, 1.38) + (roomy && n === 3 ? 60 : 34);
      const rows = Math.ceil(n / cols);
      const ch = Math.max(...d.items.map(cell));
      const rowGap = n === 4 ? 40 : 0;
      const y0 = top + Math.max(0, (room - rows * ch - (rows - 1) * rowGap) / 2);
      d.items.forEach((item, at) => {
        const x = 80 + (at % cols) * (w + gap);
        const y = y0 + Math.floor(at / cols) * (ch + rowGap);
        const card = roomy && n === 3;
        if (card) box(slide, x, y, w, ch, ink.soft);
        box(slide, x, y, w, card ? 6 : 4, at === 0 ? ink.accent : ink.ink);
        const pad = card ? 28 : 0;
        const parts = [...(item.lead ? runs(item.lead, { size: strong, color: ink.ink, bold: true }, ink, false, { paraSpaceAfter: 8 }) : []), ...runs(item.detail, { size: item.lead ? detail : detail + (roomy ? 0 : 4), color: item.lead ? ink.muted : ink.ink }, ink)];
        text(slide, parts, x + pad, y + (card ? 28 : 20), w - 2 * pad, ch - 20);
      });
      if (d.after) after(d.after, top + room + 24);
      break;
    }
    case "actions": {
      const n = d.items.length;
      const roomy = d.items.reduce((s, i) => s + countUnits(i.lead ?? "") + countUnits(i.detail), 0) <= 200;
      const afterH = d.after ? heightOf(d.after, 24, 1120, 1.38) + 42 : 0;
      const room = height - afterH;
      const gap = n === 4 ? 10 : 14;
      const rh = Math.min(roomy ? 168 : 150, (room - gap * (n - 1)) / n);
      const y0 = top + (room - rh * n - gap * (n - 1)) / 2;
      d.items.forEach((item, at) => {
        const y = y0 + at * (rh + gap);
        box(slide, 80, y, 1120, rh, ink.soft);
        const b = roomy ? 64 : 56;
        box(slide, 104, y + (rh - b) / 2, b, b, ink.accent);
        text(slide, runs(String(at + 1), { size: roomy ? 32 : 28, color: ink.onAccent, bold: true }, ink), 104, y + (rh - b) / 2, b, b, { align: "center", valign: "middle" });
        const parts = roomy
          ? [...(item.lead ? runs(item.lead, { size: 34, color: ink.ink, bold: true }, ink, false) : []), ...(item.detail ? runs(item.detail, { size: 26, color: ink.muted }, ink) : [])]
          : [...(item.lead ? runs(`${visible(item.lead) === item.lead ? item.lead : item.lead} `, { size: 28, color: ink.ink, bold: true }, ink, !item.detail).map((r, i, all) => i === all.length - 1 && item.detail ? { ...r, options: { ...r.options, breakLine: false } } : r) : []), ...(item.detail ? runs(item.detail, { size: 24, color: ink.muted }, ink) : [])];
        text(slide, parts, 104 + b + 28, y, 1120 - b - 28 - 32 - 24, rh, { valign: "middle" });
      });
      if (d.after) after(d.after, top + room + 24);
      break;
    }
    case "text": {
      const parts = d.markdown.split("\n").filter(Boolean).flatMap((line, at, all) => runs(line.replace(/^\s*(?:[-*+•]|\d+[.)])\s+/, "• "), { size: 24, color: ink.ink }, ink, at === all.length - 1, { paraSpaceAfter: 10 }));
      text(slide, parts, 80, top, 1120, height);
      break;
    }
  }
}

function cover(slide: Slide, d: Extract<DeckData, { layout: "title" | "section" }>, ink: Ink) {
  const meta = d.layout === "title" ? d.meta : null;
  const section = d.layout === "section";
  const x = section ? 120 : 80;
  const long = countUnits(d.title) > 60;
  const size = section ? (long ? 54 : 64) : long ? 58 : 72;
  const width = section ? 1000 : 1040;
  const tH = heightOf(d.title, size, width, 1.06, 700);
  const sH = d.subtitle ? heightOf(d.subtitle, 28, 900, 1.36) : 0;
  const block = 42 + tH + (d.subtitle ? 28 + sH : 0);
  const region = section ? [0, 720] : [96, 720 - 76 - (meta ? 52 : 0)];
  let y = region[0]! + (region[1]! - region[0]! - block) / 2;
  if (section) box(slide, 0, 0, 24, 720, ink.accent);
  box(slide, x, y, 72, 6, ink.accent);
  y += 42;
  text(slide, runs(d.title, { size, color: ink.ink, bold: true, font: ink.titleFont }, ink), x, y, width, tH, { lineSpacingMultiple: 0.93 });
  if (d.subtitle) text(slide, runs(d.subtitle, { size: 28, color: ink.muted }, ink), x, y + tH + 28, 900, sH);
  if (meta) {
    slide.addShape("line", { x: inch(x), y: inch(620), w: inch(1040), h: 0, line: { color: ink.rule, width: 0.75 } });
    text(slide, runs(meta, { size: 20, color: ink.muted }, ink), x, 632, 1040, 28);
  }
}
