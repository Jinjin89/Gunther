/**
 * Designed slides for the PowerPoint export (browser only): each slide is drawn off-screen on its 1280 x 720 stage exactly as
 * the viewer draws it (DesignedSlide, fonts and chart ready), its text is collected into boxes (designedText.ts), then the
 * slide is rasterised at 2x with every word made transparent (html-to-image), so the picture carries the design and the
 * text boxes carry the words. Text inside charts and drawings stays in the picture.
 */
import { createRoot } from "react-dom/client";
import type { DeckStyle } from "../sections";
import { waitForSlides } from "../slideMeasurements";
import type { BrandTheme } from "./brand";
import { DesignedSlide } from "./DesignedSlide";
import type { DesignedCapture } from "./designedPptx";
import { collectTextBlocks } from "./designedText";
import type { DeckData } from "./parseDeck";
import { sanitizeDesign } from "./sanitizeDesign";

export interface DesignedJob {
  /** The slide's HTML as stored (cleaned here). */
  html: string;
  style: DeckStyle;
  brand: BrandTheme | null;
  chart: Extract<DeckData, { layout: "chart" }> | null;
  numbers: number[];
  sources: string[];
  lang?: string | undefined;
  layout?: string | null | undefined;
}

const SIDES = ["Top", "Right", "Bottom", "Left"] as const;
const isOutside = (element: Element) => !!element.closest("svg,gunther-chart");

/** Makes the stage's own words invisible and returns nothing; borders and drawings that took their colour from the text keep it. */
function hideText(stage: HTMLElement): void {
  const owners: HTMLElement[] = [];
  const keepers: Array<[HTMLElement, string]> = [];
  for (const element of stage.querySelectorAll<HTMLElement>("*")) {
    const tag = element.tagName.toLowerCase();
    if (tag === "svg" || tag === "gunther-chart") keepers.push([element, getComputedStyle(element).color]);
    else if (!isOutside(element) && tag !== "style" && [...element.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && /\S/.test(node.textContent ?? ""))) owners.push(element);
  }
  for (const [element, colour] of keepers) element.style.setProperty("color", colour);
  for (const element of owners) {
    const look = getComputedStyle(element);
    // A border, outline or underline that follows the text colour must not vanish with the text.
    for (const side of SIDES) element.style.setProperty(`border-${side.toLowerCase()}-color`, look.getPropertyValue(`border-${side.toLowerCase()}-color`));
    element.style.setProperty("outline-color", look.outlineColor);
    element.style.setProperty("color", "transparent", "important");
    element.style.setProperty("-webkit-text-fill-color", "transparent", "important");
    element.style.setProperty("text-shadow", "none", "important");
    element.style.setProperty("text-decoration-color", "transparent");
  }
}

/** Drawings paint with `fill="var(--accent)"` attributes; the copy html-to-image rasterises has no style variables, so the resolved colours are written in. */
function pinPaint(stage: HTMLElement): void {
  for (const element of stage.querySelectorAll<SVGElement>("svg, svg *")) {
    const look = getComputedStyle(element);
    for (const property of ["fill", "stroke", "stop-color", "fill-opacity", "stroke-opacity", "stop-opacity", "stroke-width"]) element.style.setProperty(property, look.getPropertyValue(property));
  }
}

/** One slide, or null when its design is empty or could not be drawn. */
export async function captureDesignedSlide(job: DesignedJob): Promise<DesignedCapture | null> {
  const html = sanitizeDesign(job.html);
  if (!html) return null;
  const { toPng, getFontEmbedCSS } = await import("html-to-image");
  const holder = document.createElement("div");
  holder.id = "export-designed";
  holder.style.cssText = "position:fixed;left:-20000px;top:0;width:1280px;height:720px;overflow:hidden;pointer-events:none;";
  document.body.append(holder);
  const root = createRoot(holder);
  try {
    root.render(<DesignedSlide html={html} style={job.style} brand={job.brand} chart={job.chart} numbers={job.numbers} sources={job.sources} lang={job.lang} layout={job.layout} />);
    await waitForSlides("#export-designed .dk-designed", 1, 15000);
    const host = holder.querySelector<HTMLElement>(".dk-designed")!;
    const stage = host.shadowRoot?.querySelector<HTMLElement>(".stage");
    if (!stage) return null;
    const blocks = collectTextBlocks(stage);
    const fontEmbedCSS = await getFontEmbedCSS(stage).catch(() => "");
    pinPaint(stage);
    hideText(stage);
    const image = await toPng(stage, { width: 1280, height: 720, pixelRatio: 2, cacheBust: false, fontEmbedCSS, style: { transform: "none", left: "0", top: "0" } });
    return { image, blocks };
  } finally {
    root.unmount();
    holder.remove();
  }
}

/** The designed slides one after the other (they share fonts, and the page stays responsive between them). */
export async function captureDesignedSlides(jobs: DesignedJob[]): Promise<Array<DesignedCapture | null>> {
  const out: Array<DesignedCapture | null> = [];
  for (const job of jobs) out.push(await captureDesignedSlide(job).catch(() => null));
  return out;
}
