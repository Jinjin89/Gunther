import type { Artifact, ConversationCitation } from "@gunther/contracts";
import { Deck, Slide } from "@revealjs/react";
import type { RevealApi } from "reveal.js";
import { Maximize2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { citationNumbers } from "../pages/AnswerBody";
import { SectionStatus } from "./ReportView";
import { SlideFace } from "./SlideFace";
import { parseSlides } from "./sections";
import "reveal.js/reveal.css";

/** Reveal's settings: a 16:9 deck inside its box, moved by keys only while it has focus. */
const DECK = {
  embedded: true,
  hash: false,
  history: false,
  width: 1280,
  height: 720,
  margin: 0,
  center: false,
  controls: true,
  // Arrows at the two edges, where the slide's text never runs.
  controlsLayout: "edges",
  progress: true,
  slideNumber: "c/t",
  transition: "fade",
  mouseWheel: false,
  help: false,
} as const;

const indexOf = (event: Event) => (event as Event & { indexh?: number }).indexh ?? 0;

/**
 * Slides, shown in a reveal.js deck: arrows and progress inside the page, a strip of
 * thumbnails to jump by, the presenter's notes under the deck, and Present.
 *
 * Present fills the app's window with the deck, starting at the slide in view; Esc or ✕
 * leaves. It is a layer of the page (not a pop-up window, which the app does not allow, and
 * not the screen's full-screen mode: the window keeps its size, and a person who wants the
 * whole screen can use the window's own button).
 */
export function SlidesView({ artifact, onCite, compact = false }: {
  artifact: Artifact;
  onCite: (citation: ConversationCitation, index: number) => void;
  /** A small preview (beside the editor): no arrows over the slide, the thumbnails move it. */
  compact?: boolean;
}) {
  const deck = useRef<RevealApi | null>(null);
  const stage = useRef<HTMLDivElement>(null);
  const layer = useRef<HTMLDivElement>(null);
  const [current, setCurrent] = useState(0);
  const [presenting, setPresenting] = useState(false);
  const numbers = useMemo(() => citationNumbers(artifact.citations), [artifact.citations]);
  const slides = useMemo(() => parseSlides(artifact.content), [artifact.content]);
  const cite = useCallback((index: number) => {
    const citation = artifact.citations[index];
    if (citation) onCite(citation, index);
  }, [artifact.citations, onCite]);

  // A new version is a new deck, from its first slide.
  useEffect(() => setCurrent(0), [artifact.id]);

  const go = (index: number) => {
    setCurrent(index);
    deck.current?.slide(index);
  };

  const leave = useCallback(() => setPresenting(false), []);
  useEffect(() => {
    if (!presenting) return undefined;
    const opener = document.activeElement as HTMLElement | null;
    layer.current?.focus();
    // Reveal takes Esc for its overview and cancels the event, so Esc has to be taken before it gets there.
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      leave();
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      opener?.focus?.();
    };
  }, [presenting, leave]);

  // Reveal scales the deck to its box when it starts and when the window changes, but not when
  // only the box does (the sidebar closing, another page width), so it is told.
  const hasSlides = slides.length > 0;
  useEffect(() => {
    const box = stage.current;
    if (!box || typeof ResizeObserver === "undefined") return undefined;
    const watcher = new ResizeObserver(() => deck.current?.layout());
    watcher.observe(box);
    return () => watcher.disconnect();
  }, [hasSlides]);

  if (!hasSlides) return <p className="outputs-noslides">This deck has no slides yet.</p>;
  const here = slides[Math.min(current, slides.length - 1)];
  return <div className="outputs-slides">
    <div className="outputs-slides-bar">
      <span aria-live="polite">Slide {Math.min(current, slides.length - 1) + 1} of {slides.length}</span>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setPresenting(true)}><Maximize2 size={13} />Present</button>
    </div>
    <div ref={stage} className="outputs-stage">
      <Deck
        key={artifact.id}
        deckRef={deck}
        className="outputs-reveal"
        config={{ ...DECK, controls: !compact, keyboardCondition: "focused" }}
        onSlideChange={(event: Event) => setCurrent(indexOf(event))}
      >
        {slides.map((slide, index) => <Slide key={index}>
          <SlideFace body={slide.body} title={index === 0} numbers={numbers} onCite={cite} />
        </Slide>)}
      </Deck>
    </div>
    {/* On the document body, not here: the page's entrance animation leaves a transform on it, and a
        fixed layer inside a transformed element fills that element, not the window. */}
    {presenting && createPortal(
      <div ref={layer} className="outputs-stage is-presenting" role="dialog" aria-modal="true" aria-label="Presenting" tabIndex={-1}>
        <button type="button" className="outputs-leave gx-icon-button" onClick={leave} aria-label="Leave Present"><X size={16} /></button>
        <Deck
          className="outputs-reveal"
          config={{ ...DECK, keyboardCondition: null }}
          onReady={(shown) => shown.slide(current)}
          onSlideChange={(event: Event) => go(indexOf(event))}
        >
          {slides.map((slide, index) => <Slide key={index}>
            <SlideFace body={slide.body} title={index === 0} numbers={numbers} />
          </Slide>)}
        </Deck>
      </div>,
      document.body,
    )}
    <ol className="outputs-thumbs" aria-label="Slides">
      {slides.map((slide, index) => <li key={index}>
        <button type="button" className={index === current ? "is-active" : ""} aria-label={`Go to slide ${index + 1}`} aria-current={index === current ? "true" : undefined} onClick={() => go(index)}>
          <span className="outputs-thumb-frame"><span className="outputs-thumb-scale"><SlideFace body={slide.body} title={index === 0} numbers={[]} /></span></span>
          <small>{index + 1}</small>
        </button>
      </li>)}
    </ol>
    <section className="outputs-notes" aria-label="Speaker notes">
      <h3>Notes</h3>
      {here?.notes ? <p>{here.notes}</p> : <p className="is-empty">No notes for this slide.</p>}
    </section>
    <SectionStatus info={artifact.sections[Math.min(current, slides.length - 1)]} />
  </div>;
}
