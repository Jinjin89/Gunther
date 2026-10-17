import { type CSSProperties, useLayoutEffect, useMemo, useRef } from "react";
import { AnswerBody } from "../pages/AnswerBody";
import { fitFor, rememberFit, settleFit, useFit } from "./slideFit";
import { accentColour } from "./slides/accent";
import { Drawing } from "./slides/drawings";
import { parseSlide } from "./slides/parseSlide";
import { SlideContext } from "./slides/Rich";

/**
 * What one slide shows. The deck, its thumbnails and the printed page draw the same face,
 * 1280 × 720 each, and a slide whose content is too long for it is drawn smaller (see slideFit).
 *
 * `layout` is how the slide is meant to be drawn (its marker's, else its outline's, see
 * slideLayouts) and `accent` the colour of the deck (its title slide's). A slide whose body takes
 * its layout's form is drawn by that layout's component (outputs/slides/); one that does not, or
 * has no layout, is its text as Markdown, as every slide was before layouts.
 */
export function SlideFace({ body, title, layout, accent, numbers, sources, onCite }: {
  body: string;
  title: boolean;
  layout?: string | null | undefined;
  accent?: string | null | undefined;
  numbers: number[];
  /** What each citation names (its source's title and place), for the tooltip of a chart's mark. */
  sources?: string[] | undefined;
  onCite?: ((index: number) => void) | undefined;
}) {
  const shape = useMemo(() => parseSlide(layout, body), [layout, body]);
  const key = `${shape ? `${shape.layout}\n` : ""}${title ? "title" : "slide"}\n${body}`;
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
      const style = getComputedStyle(element);
      const room = 1 - (parseFloat(style.paddingTop) + parseFloat(style.paddingBottom)) / parseFloat(style.height);
      const next = fitFor(text.getBoundingClientRect().height / frame, room, fit);
      if (next >= fit) return;
      // A designed slide sets its words in columns and cells, which get shorter faster than the type
      // is scaled: the proportional size would be too small, so look for the largest that fits,
      // drawing the slide at each size to see. Nothing shows: this is before the browser paints.
      if (shape) {
        const largest = settleFit((size) => {
          element.style.setProperty("--outputs-fit", String(size));
          return text.getBoundingClientRect().height / frame;
        }, room, next, fit);
        element.style.setProperty("--outputs-fit", String(fit));
        rememberFit(key, largest);
      } else rememberFit(key, next);
    };
    look();
    if (typeof ResizeObserver === "undefined") return undefined;
    const watcher = new ResizeObserver(look);
    watcher.observe(element);
    watcher.observe(text);
    return () => watcher.disconnect();
  }, [key, fit, shape]);

  const context = useMemo(() => ({ numbers, sources: sources ?? [], onCite }), [numbers, sources, onCite]);
  const style = { "--outputs-fit": fit, ...(shape ? { "--outputs-accent": accentColour(accent) } : {}) } as CSSProperties;
  return <div ref={face} className={`outputs-slide-face ${title ? "is-title" : ""}${shape ? " is-designed" : ""}`} data-layout={layout ?? undefined} data-drawn={shape?.layout} style={style}>
    {shape
      ? <SlideContext.Provider value={context}><Drawing shape={shape} /></SlideContext.Provider>
      : <AnswerBody content={body} numbers={numbers} onCitation={onCite} />}
  </div>;
}
