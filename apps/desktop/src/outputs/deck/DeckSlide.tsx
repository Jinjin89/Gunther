import { useLayoutEffect, useMemo, useRef, type CSSProperties } from "react";
import type { DeckStyle, DeckTheme } from "../sections";
import { rememberFit, useFit } from "../slideFit";
import { SlideContext } from "../slides/Rich";
import { Inline } from "./Inline";
import { sourceDetail, sourceLine } from "./inlineTokens";
import { Layout } from "./layouts";
import { parseDeckSlide } from "./parseDeck";
import { useBrandTheme } from "./brandStore";
import { deckStyle, resolveTheme } from "./theme";
import { DesignedSlide, useCleanDesign } from "./DesignedSlide";
import "./deck.css";

/** The smallest a slide's type is scaled to when text typed over the budget does not fit. */
export const SMALLEST_FIT = 0.85;
const STEPS = [1, 0.95, 0.9, SMALLEST_FIT];

interface DeckSlideProps {
  body: string;
  layout: string | null | undefined;
  attrs?: Record<string, string> | undefined;
  theme: DeckTheme;
  accent?: string | null | undefined;
  /** This is the deck's first slide: it is the title slide even without a marker. */
  first?: boolean;
  numbers: number[];
  sources?: string[] | undefined;
  page?: number | undefined;
  onCite?: ((index: number) => void) | undefined;
  onFitChange?: ((fit: number, overflow: boolean) => void) | undefined;
  /** The slide's HTML (its `:::design` block); with a `style` the slide is drawn from it, else the template draws it. */
  design?: string | null | undefined;
  /** The look of a designed deck (the title marker's `style:`). */
  style?: DeckStyle | null | undefined;
}

/**
 * One slide of a studio deck (design v2). A designed slide (it has HTML and its deck has a style) is drawn from that HTML
 * (DesignedSlide); every other slide is drawn by the template (TemplateSlide), so old decks look as they always did.
 */
export function DeckSlide(props: DeckSlideProps) {
  const { design, style, body, layout, attrs, first = false, theme, numbers, sources, onCite, onFitChange } = props;
  const clean = useCleanDesign(design);
  const brand = useBrandTheme();
  const chart = useMemo(() => {
    if (!clean || !style) return null;
    const data = parseDeckSlide(layout ?? (first ? "title" : null), body, attrs);
    return data.layout === "chart" ? data : null;
  }, [clean, style, layout, first, body, attrs]);
  if (clean && style) {
    return <DesignedSlide html={clean} style={style} brand={theme === "brand" ? brand : null} chart={chart} numbers={numbers} sources={sources} onCite={onCite} onFitChange={onFitChange} layout={layout ?? (first ? "title" : null)} lang={/[\u3400-\u9fff]/.test(body) ? "zh-CN" : undefined} />;
  }
  return <TemplateSlide {...props} />;
}

/**
 * One slide of a studio deck (design v2), drawn on a 1280 × 720 face: an action title, the layout's
 * content, and at the foot the sources the slide cites. Nothing is scaled unless the text is over
 * its budget; then the canvas is drawn down to 85% at most and `onFitChange` says whether it still
 * overflows.
 */
function TemplateSlide({ body, layout, attrs, theme, accent, first = false, numbers, sources, page, onCite, onFitChange }: DeckSlideProps) {
  const brand = useBrandTheme();
  const drawn = resolveTheme(theme, brand);
  const data = useMemo(() => parseDeckSlide(layout ?? (first ? "title" : null), body, attrs), [layout, first, body, attrs]);
  const cover = data.layout === "title" || data.layout === "section";
  const key = `v2\n${theme}\n${drawn.brand ? drawn.brand.name : ""}\n${data.layout}\n${body}`;
  const fit = useFit(key);
  const face = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const bodyBox = useRef<HTMLDivElement>(null);
  const titleBox = useRef<HTMLHeadingElement>(null);

  const foot = useMemo(() => sourceLine(body, numbers, sources), [body, sources, numbers]);
  const footDetail = useMemo(() => sourceDetail(body, numbers, sources), [body, sources, numbers]);

  useLayoutEffect(() => {
    const root = face.current;
    const inner = canvas.current;
    if (!root || !inner) return undefined;
    /** Why the slide does not fit, or null when it does. */
    const overflows = (): string | null => {
      const limit = bodyBox.current?.getBoundingClientRect();
      const frame = inner.getBoundingClientRect();
      const px = frame.width / inner.offsetWidth || 1;
      const slack = 1.5 * px;
      const name = (el: Element) => `${el.tagName.toLowerCase()}.${(el.getAttribute("class") ?? "").split(" ")[0]} "${(el.textContent ?? "").trim().slice(0, 30)}"`;
      const title = titleBox.current;
      if (title && title.getBoundingClientRect().height / (parseFloat(getComputedStyle(title).lineHeight) * px) > 2.05) return "title is longer than two lines";
      const content = bodyBox.current;
      if (!content || !limit) return null;
      // A block whose content is taller or wider than the room its layout gave it would be drawn over its neighbour.
      for (const el of content.querySelectorAll<HTMLElement>("div,ul,ol,section,li,table,figure,p,h3,blockquote")) {
        if (el.closest("svg")) continue;
        const text = /^(P|H3|BLOCKQUOTE|STRONG)$/.test(el.tagName);
        if (el.scrollHeight > el.clientHeight + (text ? 10 : 2) || (text && el.scrollWidth > el.clientWidth + 2)) return `${name(el)} spills (${el.scrollWidth}x${el.scrollHeight} in ${el.clientWidth}x${el.clientHeight})`;
      }
      for (const el of content.querySelectorAll("*")) {
        if (el.closest("svg") && el.tagName !== "svg") continue;
        // A panel crop scales its image up and clips it: the hidden part is not a spill.
        if (el.closest(".dk-crop") && !el.classList.contains("dk-crop")) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && (r.top < limit.top - slack || r.bottom > limit.bottom + slack || r.right > limit.right + slack || r.left < limit.left - slack)) return `${name(el)} outside the body`;
      }
      return null;
    };
    const look = () => {
      if (root.getBoundingClientRect().height === 0) return;
      const apply = (s: number) => { inner.style.setProperty("--dk-fit", String(s)); };
      let best = SMALLEST_FIT;
      let over = true;
      let why: string | null = null;
      for (const s of STEPS) { apply(s); why = overflows(); if (!why) { best = s; over = false; break; } }
      const next = over ? SMALLEST_FIT : best;
      if (STEPS[0] !== undefined) { apply(1); const first = overflows(); if (first) root.dataset.overflowWhy = first; else delete root.dataset.overflowWhy; }
      inner.style.setProperty("--dk-fit", String(fit));
      if (next !== fit) rememberFit(key, next);
      onFitChange?.(next, over);
    };
    look();
    const fonts = document.fonts?.ready;
    let alive = true;
    void fonts?.then(() => { if (alive) look(); });
    return () => { alive = false; };
  }, [key, fit, cover, onFitChange]);

  const context = useMemo(() => ({ numbers, sources: sources ?? [], onCite }), [numbers, sources, onCite]);
  const style = { ...deckStyle(theme, accent, brand), "--dk-fit": fit } as CSSProperties;
  return <div ref={face} className={`dk-face dk-theme-${drawn.theme}${drawn.brand ? " dk-brand" : ""} dk-l-${data.layout}`} lang={/[\u3400-\u9fff]/.test(body) ? "zh-CN" : undefined} data-design-version="2" data-theme={drawn.brand ? "brand" : drawn.theme} data-layout={data.layout} data-fit={fit} style={style}>
    <div ref={canvas} className="dk-canvas">
      <SlideContext.Provider value={context}>
        {!cover && data.layout !== "text" || data.layout === "text" && data.title
          ? <h2 ref={titleBox} className="dk-title"><Inline text={data.title} /></h2>
          : null}
        <div ref={bodyBox} className="dk-body"><Layout data={data} /></div>
      </SlideContext.Provider>
      {drawn.brand?.logo && <img className="dk-logo" src={drawn.brand.logo} alt="" style={{ "--dk-logo-ratio": drawn.brand.logoRatio ?? 3 } as CSSProperties} />}
      <footer className="dk-foot">
        <span className="dk-source" title={footDetail}>{foot}</span>
        {page !== undefined && !cover && <span className="dk-page">{page}</span>}
      </footer>
    </div>
  </div>;
}
