import { type CSSProperties, type ComponentProps, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AnswerBody } from "../pages/AnswerBody";
import { fitFor, overflowsFace, rememberFit, settleFit, useFit } from "./slideFit";
import { compositionFor, type DeckStyle, type DeckTheme } from "./sections";
import { DeckSlide } from "./deck/DeckSlide";
import { themeAccentColour } from "./slides/accent";
import { SLIDE_THEMES, slideThemeStyle } from "./slides/themes";
import { Drawing } from "./slides/drawings";
import { parseSlide } from "./slides/parseSlide";
import { SlideContext } from "./slides/Rich";
import { ComposedSlide } from "./slides/ComposedSlide";
import { splitBlocks, validateScene, verifySceneEnvelope, type SceneEnvelope } from "./slides/scene";
import "./slides/design.css";

/**
 * What one slide shows. The deck, its thumbnails and the printed page draw the same face,
 * 1280 × 720 each, and a slide whose content is too long for it is drawn smaller (see slideFit).
 *
 * `layout` is how the slide is meant to be drawn (its marker's, else its outline's, see
 * slideLayouts) and `accent` the colour of the deck (its title slide's). A slide whose body takes
 * its layout's form is drawn by that layout's component (outputs/slides/); one that does not, or
 * has no layout, is its text as Markdown, as every slide was before layouts.
 */
function LegacySlideFace({ body, title, layout, accent, theme = "classic", designVersion = 0, composition = "standard", scene: sceneMeta, numbers, sources, onCite, onFitChange, onSceneIssue }: {
  body: string;
  title: boolean;
  layout?: string | null | undefined;
  accent?: string | null | undefined;
  theme?: string | null | undefined;
  designVersion?: number | undefined;
  composition?: string | null | undefined;
  scene?: { scene: SceneEnvelope | null; issue: string | null } | undefined;
  numbers: number[];
  /** What each citation names (its source's title and place), for the tooltip of a chart's mark. */
  sources?: string[] | undefined;
  onCite?: ((index: number) => void) | undefined;
  onFitChange?: ((fit: number, overflow: boolean) => void) | undefined;
  onSceneIssue?: ((issue: string | null) => void) | undefined;
}) {
  const composed = layout === "composed";
  const blocks = useMemo(() => splitBlocks(body), [body]);
  const blockIssue = useMemo(() => composed ? validateScene(sceneMeta?.scene?.root, blocks.blocks, sceneMeta?.scene) : null, [composed, sceneMeta, blocks]);
  const verificationKey = `${body}\n${sceneMeta?.scene?.version ?? ""}\n${sceneMeta?.scene?.sourceHash ?? ""}\n${sceneMeta?.scene?.version === 2 ? sceneMeta.scene.graphicsHash : ""}\n${JSON.stringify(sceneMeta?.scene?.version === 2 ? sceneMeta.scene.graphics : {})}`;
  const sceneLayoutKey = `${verificationKey}\n${JSON.stringify(sceneMeta?.scene?.root ?? null)}\n${theme}\n${accent ?? ""}\n${designVersion}`;
  const [verified, setVerified] = useState<{ key: string; sourceValid: boolean; graphicsValid: boolean } | null>(null);
  const [overflowFallback, setOverflowFallback] = useState<string | null>(null);
  const [graphicIssues, setGraphicIssues] = useState<{ key: string; items: Record<string, string> }>({ key: "", items: {} });
  useEffect(() => {
    if (!composed || !sceneMeta?.scene || blockIssue) { setVerified(null); return; }
    let alive = true;
    void verifySceneEnvelope(sceneMeta.scene, body).then((result) => { if (alive) setVerified({ key: verificationKey, ...result }); });
    return () => { alive = false; };
  }, [body, composed, sceneMeta, blockIssue, verificationKey]);
  const verifiedScene = composed && sceneMeta?.scene && !blockIssue && verified?.key === verificationKey && verified.sourceValid && overflowFallback !== sceneLayoutKey ? sceneMeta.scene : null;
  const fullSceneIssue = !composed ? null : overflowFallback === sceneLayoutKey ? "The composed elements do not fit this page at a readable size." : sceneMeta?.issue ?? (!sceneMeta?.scene ? "This composed slide has no usable scene metadata." : blockIssue ?? (verified?.key === verificationKey && !verified.sourceValid ? "This scene is out of date for the current slide text." : null));
  const graphicIssueItems = graphicIssues.key === sceneLayoutKey ? graphicIssues.items : {};
  const graphicIssueSummary = Object.keys(graphicIssueItems).length ? `Graphic elements fell back to their source content: ${Object.entries(graphicIssueItems).map(([id, issue]) => `${id} (${issue})`).join("; ")}.` : null;
  const sceneIssue = fullSceneIssue ?? graphicIssueSummary;
  useEffect(() => { onSceneIssue?.(sceneIssue); }, [onSceneIssue, sceneIssue]);
  const reportGraphicIssue = useCallback((blockId: string, issue: string | null) => setGraphicIssues((previous) => {
    const items = previous.key === sceneLayoutKey ? { ...previous.items } : {};
    if (issue) items[blockId] = issue; else delete items[blockId];
    return { key: sceneLayoutKey, items };
  }), [sceneLayoutKey]);
  const shape = useMemo(() => composed ? null : parseSlide(layout, body), [composed, layout, body]);
  const chosenTheme = theme === "briefing" || theme === "editorial" || theme === "keynote" ? theme : "classic";
  const chosenComposition = compositionFor(layout, composition);
  const key = `${chosenTheme}\n${designVersion}\n${chosenComposition}\n${shape ? `${shape.layout}\n` : ""}${verifiedScene ? `${verificationKey}\n${verified?.graphicsValid}\n${JSON.stringify(verifiedScene.root)}` : ""}\n${title ? "title" : "slide"}\n${body}`;
  const fit = useFit(key);
  const face = useRef<HTMLDivElement>(null);

  // Look at how much of the slide the content takes, when the slide is laid out and whenever its
  // content changes size (a slide out of view has no size until it is shown; fonts load late).
  useLayoutEffect(() => {
    const element = face.current;
    const text = element?.firstElementChild;
    if (!element || !text) return undefined;
    const look = () => {
      const frame = element.getBoundingClientRect().height;
      if (frame === 0) return;
      if (element.querySelector('[data-graphic-fallback="pending"]')) {
        if (fit !== 1) rememberFit(key, 1);
        return;
      }
      const style = getComputedStyle(element);
      const room = 1 - (parseFloat(style.paddingTop) + parseFloat(style.paddingBottom)) / parseFloat(style.height);
      const measure = () => {
        const faceRect = element.getBoundingClientRect();
        const textRect = text.getBoundingClientRect();
        const cssWidth = parseFloat(style.width) || faceRect.width;
        const cssHeight = parseFloat(style.height) || faceRect.height;
        const visibleElements = [...element.querySelectorAll<HTMLElement>("*")]
          .filter((child) => !child.closest(".outputs-sr-only,[aria-hidden='true']") && getComputedStyle(child).display !== "none" && getComputedStyle(child).visibility !== "hidden");
        const descendants = visibleElements.map((child) => child.getBoundingClientRect());
        // Text can overflow without its parent box growing (e.g. a long unbroken token).
        // Measure it against the slide, not the font's line box: glyph ink and superscripts
        // legitimately extend beyond a tight line-height without being clipped.
        for (const node of visibleElements) {
          for (const child of node.childNodes) {
            if (child.nodeType !== Node.TEXT_NODE || !child.textContent?.trim()) continue;
            const range = document.createRange();
            range.selectNodeContents(child);
            if (typeof range.getClientRects === "function") descendants.push(...range.getClientRects());
          }
        }
        const intrinsic = visibleElements.some((node) => {
          const childStyle = getComputedStyle(node);
          return childStyle.overflowX !== "visible" && childStyle.overflowX !== "" && node.clientWidth > 0 && node.scrollWidth > node.clientWidth + 2
            || childStyle.overflowY !== "visible" && childStyle.overflowY !== "" && node.clientHeight > 0 && node.scrollHeight > node.clientHeight + 2;
        });
        const isOverflowing = overflowsFace(faceRect, textRect, {
          left: parseFloat(style.paddingLeft) || 0,
          right: parseFloat(style.paddingRight) || 0,
          bottom: parseFloat(style.paddingBottom) || 0,
          width: cssWidth,
          height: cssHeight,
        }, descendants, intrinsic);
        return { vertical: textRect.bottom > faceRect.bottom - ((parseFloat(style.paddingBottom) || 0) * faceRect.height / cssHeight) + 1, horizontal: isOverflowing };
      };
        const currentMeasure = measure();
        if (verifiedScene && currentMeasure.horizontal) {
          let low = 0.55; let high = fit; let best: number | null = null;
          for (let step = 0; step < 8 && high - low > 0.011; step += 1) {
            const middle = Math.round(((low + high) / 2) * 100) / 100;
            if (middle <= low || middle >= high) break;
            element.style.setProperty("--outputs-fit", String(middle));
            if (measure().horizontal) high = middle; else { best = middle; low = middle; }
          }
          element.style.setProperty("--outputs-fit", String(fit));
          if (best !== null) {
            rememberFit(key, best);
            onFitChange?.(best, false);
          } else {
            setOverflowFallback(sceneLayoutKey);
            onFitChange?.(1, true);
          }
          return;
        }
        if (verifiedScene) {
          onFitChange?.(fit, false);
          return;
        }
      const next = fitFor(text.getBoundingClientRect().height / frame, room, fit);
      if (next >= fit) { onFitChange?.(fit, currentMeasure.vertical || currentMeasure.horizontal); return; }
      // A designed slide sets its words in columns and cells, which get shorter faster than the type
      // is scaled: the proportional size would be too small, so look for the largest that fits,
      // drawing the slide at each size to see. Nothing shows: this is before the browser paints.
      if (shape || verifiedScene) {
        const largest = settleFit((size) => {
          element.style.setProperty("--outputs-fit", String(size));
          return text.getBoundingClientRect().height / frame;
        }, room, next, fit);
        element.style.setProperty("--outputs-fit", String(fit));
        rememberFit(key, largest);
        const overflow = measure();
        onFitChange?.(largest, overflow.vertical || overflow.horizontal);
      } else {
        rememberFit(key, next);
        const overflow = measure();
        onFitChange?.(next, overflow.vertical || overflow.horizontal);
      }
    };
    look();
    const watcher = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(look);
    watcher?.observe(element);
    watcher?.observe(text);
    const mutations = typeof MutationObserver === "undefined" ? null : new MutationObserver(look);
    mutations?.observe(text, { subtree: true, attributes: true, attributeFilter: ["data-graphic-fallback"] });
    return () => { watcher?.disconnect(); mutations?.disconnect(); };
  }, [key, fit, shape, verifiedScene, onFitChange, verificationKey, sceneLayoutKey, composed]);

  const context = useMemo(() => ({ numbers, sources: sources ?? [], onCite }), [numbers, sources, onCite]);
  const style = {
    "--outputs-fit": fit,
    ...slideThemeStyle(chosenTheme),
    ...(shape || composed ? { "--outputs-accent": designVersion === 1 && accent == null ? SLIDE_THEMES[chosenTheme].accent : themeAccentColour(accent, chosenTheme) } : {}),
  } as CSSProperties;
  return <div ref={face} className={`outputs-slide-face theme-${chosenTheme}${designVersion === 1 ? " design-v1" : ""} is-${chosenComposition} ${title ? "is-title" : ""}${shape || verifiedScene ? " is-designed" : ""}${verifiedScene ? " is-composed" : ""}`} data-scene-ready={!composed || !sceneMeta?.scene || !!blockIssue || verified?.key === verificationKey ? "true" : "false"} data-scene-issue={sceneIssue ?? undefined} data-design-version={designVersion === 1 ? 1 : 0} data-theme={chosenTheme} data-composition={chosenComposition} data-layout={layout ?? undefined} data-drawn={shape?.layout ?? (verifiedScene ? "composed" : undefined)} style={style}>
    {verifiedScene
      ? <SlideContext.Provider value={context}><ComposedSlide scene={verifiedScene} graphicsValid={verified?.key === verificationKey && verified.graphicsValid} heading={blocks.heading} title={title} blocks={blocks.blocks} onGraphicIssue={reportGraphicIssue} numbers={numbers} /></SlideContext.Provider>
      : shape
        ? <SlideContext.Provider value={context}><Drawing shape={shape} /></SlideContext.Provider>
      : <AnswerBody content={body} numbers={numbers} onCitation={onCite} />}
  </div>;
}

/**
 * What one slide shows. A deck whose title slide says `design: v2` is drawn by the studio renderer
 * (outputs/deck/); every other deck is drawn as it always was.
 */
export function SlideFace(props: ComponentProps<typeof LegacySlideFace> & {
  /** The marker's other options (`chart: column`). Only the studio design reads them. */
  attrs?: Record<string, string> | undefined;
  /** The studio design's theme and the slide's place in the deck. */
  deckTheme?: DeckTheme | undefined;
  first?: boolean | undefined;
  page?: number | undefined;
  /** A designed slide's HTML and the deck's look: with both, the slide is drawn from its design, else by the template. */
  design?: string | null | undefined;
  deckStyle?: DeckStyle | null | undefined;
}) {
  const { attrs, deckTheme, first, page, design, deckStyle, ...legacy } = props;
  if (props.designVersion === 2) {
    return <DeckSlide body={props.body} layout={props.layout} attrs={attrs} theme={deckTheme ?? "paper"} accent={props.accent} first={first ?? props.title} design={design} style={deckStyle} numbers={props.numbers} sources={props.sources} page={page} onCite={props.onCite} onFitChange={props.onFitChange} />;
  }
  return <LegacySlideFace {...legacy} />;
}
