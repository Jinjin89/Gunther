import type { Artifact, ConversationCitation } from "@gunther/contracts";
import { Deck, Slide } from "@revealjs/react";
import type { RevealApi } from "reveal.js";
import { Maximize2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { citationNumbers } from "../pages/AnswerBody";
import { useEscape } from "../shortcuts/shortcuts";
import { SectionStatus } from "./ReportView";
import { SlideFace } from "./SlideFace";
import { parseSlides } from "./sections";
import "reveal.js/reveal.css";

/** Reveal's settings: a 16:9 deck inside the page, moved by keys only while it has focus. */
const DECK = {
  embedded: true,
  hash: false,
  history: false,
  width: 1280,
  height: 720,
  margin: 0,
  center: false,
  controls: true,
  progress: true,
  slideNumber: "c/t",
  transition: "fade",
  mouseWheel: false,
  help: false,
} as const;

const native = () => "__TAURI_INTERNALS__" in window;

/** The window (or, in a browser, the page) goes full screen, or comes back. */
async function setFullscreen(on: boolean) {
  try {
    if (native()) {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      await getCurrentWindow().setFullscreen(on);
    } else if (on) {
      await document.documentElement.requestFullscreen?.();
    } else if (document.fullscreenElement) {
      await document.exitFullscreen?.();
    }
  } catch {
    // The deck still fills the window.
  }
}

/**
 * Slides, shown in a reveal.js deck: arrows and progress inside the page, a strip of
 * thumbnails to jump by, the presenter's notes under the deck, and a full-screen Present.
 * Presenting uses the page's own layer, not a pop-up window, which the app does not allow.
 */
export function SlidesView({ artifact, onCite }: {
  artifact: Artifact;
  onCite: (citation: ConversationCitation, index: number) => void;
}) {
  const deck = useRef<RevealApi | null>(null);
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

  const present = () => {
    setPresenting(true);
    void setFullscreen(true);
  };
  const leave = useCallback(() => {
    if (deck.current?.isOverview()) deck.current.toggleOverview(false);
    setPresenting(false);
    void setFullscreen(false);
  }, []);
  useEscape(leave, presenting);

  // In a browser, Esc leaves full screen by itself; Present follows.
  useEffect(() => {
    if (!presenting || native()) return undefined;
    const onChange = () => { if (!document.fullscreenElement) setPresenting(false); };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, [presenting]);

  // The deck is measured by its box, so it needs to look again when the box changes.
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => deck.current?.layout());
    return () => window.cancelAnimationFrame(frame);
  }, [presenting]);

  if (slides.length === 0) return <p className="outputs-noslides">This deck has no slides yet.</p>;
  const here = slides[Math.min(current, slides.length - 1)];
  return <div className="outputs-slides">
    <div className="outputs-slides-bar">
      <span aria-live="polite">Slide {Math.min(current, slides.length - 1) + 1} of {slides.length}</span>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={present}><Maximize2 size={13} />Present</button>
    </div>
    <div className={`outputs-stage ${presenting ? "is-presenting" : ""}`}>
      {presenting && <button type="button" className="outputs-leave gx-icon-button" onClick={leave} aria-label="Leave full screen"><X size={16} /></button>}
      <Deck
        key={artifact.id}
        deckRef={deck}
        className="outputs-reveal"
        config={{ ...DECK, keyboardCondition: presenting ? null : "focused" }}
        onSlideChange={(event: Event) => setCurrent((event as Event & { indexh?: number }).indexh ?? 0)}
      >
        {slides.map((slide, index) => <Slide key={index}>
          <SlideFace body={slide.body} title={index === 0} numbers={numbers} onCite={cite} />
        </Slide>)}
      </Deck>
    </div>
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
