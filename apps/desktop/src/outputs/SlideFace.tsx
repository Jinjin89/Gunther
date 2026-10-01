import { type CSSProperties, useLayoutEffect, useRef } from "react";
import { AnswerBody } from "../pages/AnswerBody";
import { fitFor, rememberFit, useFit } from "./slideFit";

/**
 * What one slide shows. The deck, its thumbnails and the printed page draw the same face,
 * 1280 × 720 each, and a slide whose text is too long for it is drawn smaller (see slideFit).
 */
export function SlideFace({ body, title, numbers, onCite }: {
  body: string;
  title: boolean;
  numbers: number[];
  onCite?: ((index: number) => void) | undefined;
}) {
  const key = `${title ? "title" : "slide"}\n${body}`;
  const fit = useFit(key);
  const face = useRef<HTMLDivElement>(null);

  // Look at how much of the slide the text takes, when the slide is laid out and whenever its
  // text changes size (a slide out of view has no size until it is shown; fonts load late).
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
      if (next < fit) rememberFit(key, next);
    };
    look();
    if (typeof ResizeObserver === "undefined") return undefined;
    const watcher = new ResizeObserver(look);
    watcher.observe(element);
    watcher.observe(text);
    return () => watcher.disconnect();
  }, [key, fit]);

  return <div ref={face} className={`outputs-slide-face ${title ? "is-title" : ""}`} style={{ "--outputs-fit": fit } as CSSProperties}>
    <AnswerBody content={body} numbers={numbers} onCitation={onCite} />
  </div>;
}
