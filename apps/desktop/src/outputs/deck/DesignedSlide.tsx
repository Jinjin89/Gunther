import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/latin-700.css";
import "@fontsource/inter/latin-800.css";
import "@fontsource/source-serif-4/latin-400.css";
import "@fontsource/source-serif-4/latin-600.css";
import "@fontsource/source-serif-4/latin-700.css";
import type { DeckStyle } from "../sections";
import type { BrandTheme } from "./brand";
import { Chart } from "./Charts";
import { designCss } from "./designStyles";
import { measureDesigned } from "./measureDesigned";
import type { DeckData } from "./parseDeck";
import { sanitizeDesign } from "./sanitizeDesign";

type ChartData = Extract<DeckData, { layout: "chart" }>;

/** The faces the styles use (latin; Chinese text falls back to the system's CJK fonts, as the style CSS lists them). */
const FACES = ['400 20px Inter', '500 20px Inter', '600 20px Inter', '700 20px Inter', '800 20px Inter', '400 20px "Source Serif 4"', '600 20px "Source Serif 4"', '700 20px "Source Serif 4"'];

/** A citation mark, or a run of them (`[3]`, `[1][6]`, `[1, 2]`, `[?]`), in a slide's text. Checker marks like `[i:3]` count too. */
const CITE_RUN = /(?:[ \t ]*(?:\[(?:[ipd]:)?\d+(?:\s*[,，、]\s*\d+)*\]|\[\?\]))+/g;
const CITE_ONE = /\[(?:[ipd]:)?(\d+(?:\s*[,，、]\s*\d+)*)\]|\[\?\]/g;

/**
 * Citation marks in the slide's text become the small raised marks the template slides draw (outputs/deck/Inline.tsx): a
 * button per source number that opens its evidence, and a "?" for the model's own knowledge. `sources` is titled on hover.
 */
export function markCitations(root: ParentNode, numbers: number[], sources: string[] | undefined, interactive: boolean): void {
  const walker = document.createTreeWalker(root as Node, NodeFilter.SHOW_TEXT);
  const found: Text[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node as Text;
    if (text.parentElement?.closest("svg,script,style")) continue;
    if (CITE_RUN.test(text.data)) found.push(text);
    CITE_RUN.lastIndex = 0;
  }
  for (const text of found) {
    const parts: Array<string | HTMLElement> = [];
    let last = 0;
    for (const run of text.data.matchAll(CITE_RUN)) {
      const at = run.index ?? 0;
      if (at > last) parts.push(text.data.slice(last, at));
      // A space before a mark would hang between the claim and the mark.
      let verified: HTMLElement | null = null;
      for (const one of run[0].matchAll(CITE_ONE)) {
        if (one[1] === undefined) {
          const mark = document.createElement("sup");
          mark.className = "dk-cite is-unverified";
          mark.title = "From the model's own knowledge; no source was found for it";
          mark.textContent = "?";
          parts.push(mark);
          verified = null;
          continue;
        }
        if (!verified) { verified = document.createElement("sup"); verified.className = "dk-cite"; parts.push(verified); } else verified.append(",");
        one[1].split(/[,，、]/).forEach((part, index) => {
          if (index > 0) verified!.append(",");
          const n = Number(part.trim());
          const place = numbers.indexOf(n);
          const label = document.createElement(interactive ? "button" : "span");
          label.textContent = String(n);
          const source = sources?.[place];
          if (source) label.title = source;
          if (interactive) { label.setAttribute("type", "button"); label.setAttribute("aria-label", `Inspect citation ${n}`); label.dataset.cite = String(place); }
          verified!.append(label);
        });
      }
      last = at + run[0].length;
    }
    if (last < text.data.length) parts.push(text.data.slice(last));
    text.replaceWith(...parts);
  }
}

/** The whole slide's HTML ready to draw, or null when nothing of it is left after cleaning. */
export function useCleanDesign(html: string | null | undefined): string | null {
  return useMemo(() => (html ? sanitizeDesign(html) || null : null), [html]);
}

/**
 * A designed slide: the model's HTML for it, cleaned, drawn in a shadow root on a fixed 1280 x 720 stage (scaled to fit its
 * box, which is the stage itself in the deck) with the base CSS and the deck's style. The slide's chart table is drawn in
 * the `<gunther-chart>` it leaves; citation marks behave as the template slides'. `data-ready` turns "true" when fonts and
 * the chart are drawn, which is what the headless renderer waits for.
 */
export function DesignedSlide({ html, style, brand = null, chart, numbers, sources, onCite, onFitChange, layout, lang }: {
  /** Already cleaned (useCleanDesign). */
  html: string;
  style: DeckStyle;
  /** The imported template, drawn over the style, or null. */
  brand?: BrandTheme | null | undefined;
  chart: ChartData | null;
  numbers: number[];
  sources?: string[] | undefined;
  onCite?: ((index: number) => void) | undefined;
  /** Said once the slide is drawn: always 1 (a designed slide is never scaled down), and whether something spills out of the stage. */
  onFitChange?: ((fit: number, overflow: boolean) => void) | undefined;
  layout?: string | null | undefined;
  lang?: string | undefined;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const [fonts, setFonts] = useState(false);
  const cite = useRef(onCite);
  cite.current = onCite;
  const css = useMemo(() => designCss(style, brand), [style, brand]);

  useLayoutEffect(() => {
    const element = host.current;
    if (!element) return undefined;
    const root = element.shadowRoot ?? element.attachShadow({ mode: "open" });
    const sheet = document.createElement("style");
    sheet.textContent = css;
    const frame = document.createElement("div");
    frame.className = "stage";
    const template = document.createElement("template");
    template.innerHTML = html;
    frame.append(template.content);
    const slide = frame.querySelector<HTMLElement>(".slide") ?? frame.firstElementChild as HTMLElement | null;
    if (slide && lang) slide.setAttribute("lang", lang);
    // The chart goes where the first placeholder is; any other is dropped.
    const places = frame.querySelectorAll("gunther-chart");
    places.forEach((place, at) => { if (at > 0) place.remove(); });
    markCitations(frame, numbers, sources, !!cite.current);
    root.replaceChildren(sheet, frame);
    setSlot(frame.querySelector<HTMLElement>("gunther-chart"));
    const click = (event: Event) => {
      const button = (event.target as Element | null)?.closest?.("button[data-cite]");
      if (!button) return;
      event.stopPropagation();
      cite.current?.(Number((button as HTMLElement).dataset.cite));
    };
    root.addEventListener("click", click);
    return () => root.removeEventListener("click", click);
    // `onCite` is read through a ref; whether there is one decides button or plain mark.
  }, [html, css, numbers, sources, lang, !!onCite]);

  // Scaled to fit its box: the stage is 1280 x 720 whatever the box is.
  useLayoutEffect(() => {
    const element = host.current;
    if (!element) return undefined;
    const fit = () => {
      const scale = Math.min(element.clientWidth / 1280, element.clientHeight / 720) || 1;
      element.style.setProperty("--stage-scale", String(scale));
    };
    fit();
    if (typeof ResizeObserver === "undefined") return undefined;
    const watcher = new ResizeObserver(fit);
    watcher.observe(element);
    return () => watcher.disconnect();
  }, [html, css]);

  useEffect(() => {
    let alive = true;
    const loading = typeof document.fonts?.load === "function" ? Promise.all(FACES.map((face) => document.fonts.load(face).catch(() => []))) : Promise.resolve([]);
    void loading.then(() => document.fonts?.ready).then(() => { if (alive) setFonts(true); });
    return () => { alive = false; };
  }, []);

  // Once fonts and the chart are in, look at the slide and say whether it spills (the Slides view marks such slides).
  useEffect(() => {
    if (!fonts || !onFitChange || !host.current) return undefined;
    const element = host.current;
    const frames = [0, 0];
    frames[0] = requestAnimationFrame(() => { frames[1] = requestAnimationFrame(() => onFitChange(1, measureDesigned(element).overflow.length > 0)); });
    return () => { cancelAnimationFrame(frames[0]!); cancelAnimationFrame(frames[1]!); };
  }, [fonts, html, css, chart, onFitChange]);

  const highlighted = !!chart?.categories.some((category) => category.highlight) && chart.series.length === 1;
  useLayoutEffect(() => { if (slot) slot.dataset.highlight = String(highlighted); }, [slot, highlighted]);
  return <div ref={host} className="dk-designed" data-design-style={style} data-layout={layout ?? undefined} data-drawn="designed" data-ready={fonts ? "true" : "false"} data-designed="true">
    {slot && chart ? createPortal(<Chart data={chart} />, slot) : null}
  </div>;
}
