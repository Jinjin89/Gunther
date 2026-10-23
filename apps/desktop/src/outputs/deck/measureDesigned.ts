/**
 * What a designed slide measures, in stage pixels (the 1280 x 720 the slide is drawn on, whatever size it is shown at):
 * - `overflow`: elements whose box goes outside the stage, and text containers whose content is bigger than the box
 *   they were given (scrollHeight/scrollWidth over clientHeight/clientWidth);
 * - `smallest`: the smallest visible text, with its size in stage pixels. Citation marks (`sup.dk-cite`) are small by
 *   design and are not counted.
 * P3 reads both through the renderer's measure.json (`overflowWhy`, `designed`, `smallText`).
 */
export interface DesignedMeasure {
  overflow: Array<{ kind: "outside" | "spill"; element: string; text: string; bounds: { x: number; y: number; width: number; height: number } }>;
  smallest: { size: number; text: string } | null;
  typography: Array<{ role: "heading" | "body" | "caption" | "label"; size: number; minimum: number; family: string; text: string; bounds: { x: number; y: number; width: number; height: number } }>;
}

/** Nothing is set smaller than this on a designed slide (the design prompt's own floor), or 20 for a heading. */
export const DESIGNED_MINIMUM = 18;
const SLACK = 2;

const name = (element: Element) => `${element.tagName.toLowerCase()}${element.className && typeof element.className === "string" ? `.${element.className.split(" ")[0]}` : ""}`;

export function measureDesigned(host: HTMLElement): DesignedMeasure {
  const stage = host.shadowRoot?.querySelector<HTMLElement>(".stage");
  const empty: DesignedMeasure = { overflow: [], smallest: null, typography: [] };
  if (!stage) return empty;
  const frame = stage.getBoundingClientRect();
  const factor = frame.width / 1280 || 1;
  const bounds = (box: DOMRect) => ({ x: (box.x - frame.x) / factor, y: (box.y - frame.y) / factor, width: box.width / factor, height: box.height / factor });
  const result: DesignedMeasure = { overflow: [], smallest: null, typography: [] };
  const hidden = (element: Element) => { const style = getComputedStyle(element); return style.display === "none" || style.visibility === "hidden" || style.opacity === "0"; };
  for (const element of stage.querySelectorAll("*")) {
    if (element.closest("svg") && element.tagName.toLowerCase() !== "svg" && element.tagName.toLowerCase() !== "text" && !element.closest("text")) continue;
    if (element.tagName === "STYLE" || hidden(element)) continue;
    const box = element.getBoundingClientRect();
    // The longest run of the element's own text: a citation mark in the middle splits the words, and the reported text must read the same in the printed PDF.
    const own = [...element.childNodes].filter((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim()).map((node) => node.textContent!.trim()).sort((a, b) => b.length - a.length)[0] ?? "";
    const text = (own || element.textContent?.trim() || "").slice(0, 80);
    if (box.width > 0 && box.height > 0 && (box.left < frame.left - SLACK || box.right > frame.right + SLACK || box.top < frame.top - SLACK || box.bottom > frame.bottom + SLACK)) {
      result.overflow.push({ kind: "outside", element: name(element), text, bounds: bounds(box) });
    }
    if (!own) continue;
    const html = element as HTMLElement;
    // Type taller than its line box (a serif heading) reads a few pixels over; a line that really spills is a whole line over.
    const room = Math.max(SLACK, parseFloat(getComputedStyle(html).fontSize) * 0.3);
    if (!(element instanceof SVGElement) && (html.scrollHeight > html.clientHeight + room && html.clientHeight > 0 || html.scrollWidth > html.clientWidth + SLACK && html.clientWidth > 0)) {
      result.overflow.push({ kind: "spill", element: name(element), text, bounds: bounds(box) });
    }
    if (element.closest("sup.dk-cite") || box.width === 0 || box.height === 0) continue;
    const style = getComputedStyle(element);
    let size = parseFloat(style.fontSize);
    if (element instanceof SVGElement) {
      // SVG text is drawn at its font size times the picture's scale.
      const matrix = (element as SVGGraphicsElement).getScreenCTM?.();
      size = matrix ? size * Math.hypot(matrix.a, matrix.b) / factor : size;
    }
    if (!Number.isFinite(size)) continue;
    const role = /^H[1-3]$/.test(element.tagName) || size >= 28 ? "heading" : element instanceof SVGElement ? "label" : size < 20 ? "caption" : "body";
    result.typography.push({ role, size, minimum: role === "heading" ? 20 : DESIGNED_MINIMUM, family: style.fontFamily, text, bounds: bounds(box) });
    if (!result.smallest || size < result.smallest.size) result.smallest = { size, text };
  }
  return result;
}
