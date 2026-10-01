import type { Artifact } from "@gunther/contracts";
import { createPortal } from "react-dom";
import { AnswerBody, citationNumbers } from "../pages/AnswerBody";
import { parseSlides, splitDocument, withoutTitle } from "./sections";
import { SlideFace } from "./SlideFace";

/** A4 with room to bind; a deck is a 13⅓ × 7½ in page, which is exactly the 1280 × 720 px the slides are drawn on. */
const REPORT_PAGE = "@page { size: A4; margin: 18mm 16mm; }";
const DECK_PAGE = "@page { size: 13.333in 7.5in; margin: 0; }";

const short = (text: string, limit = 220) => {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit - 1).trimEnd()}…` : flat;
};

function Sources({ artifact }: { artifact: Artifact }) {
  if (artifact.citations.length === 0) return null;
  return <section className="outputs-print-sources">
    <h2>Sources</h2>
    <ol>
      {artifact.citations.map((citation, index) => <li key={index}>
        <strong>{citation.sourceTitle}</strong>
        {citation.locator && <span> · {citation.locator}</span>}
        <q>{short(citation.quote)}</q>
      </li>)}
    </ol>
  </section>;
}

/**
 * The open version laid out for paper. It is always in the page and hidden on screen; printing
 * hides everything else (see outputs.css). A report is typeset for A4 with its numbers as
 * superscripts and a list of sources at the end; a deck is one slide per page, without notes,
 * followed by a page of sources.
 */
export function PrintRoot({ artifact }: { artifact: Artifact }) {
  const numbers = citationNumbers(artifact.citations);
  const date = new Date(artifact.createdAt).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
  const deck = artifact.kind === "slides";
  const report = splitDocument(artifact.content, "report");
  const brief = withoutTitle(report.preamble);
  return createPortal(
    <div id="print-root" aria-hidden="true" data-kind={artifact.kind}>
      <style>{deck ? DECK_PAGE : REPORT_PAGE}</style>
      {deck
        ? <div className="outputs-print-deck">
          {parseSlides(artifact.content).map((slide, index) => <section key={index} className="outputs-print-slide">
            <SlideFace body={slide.body} title={index === 0} numbers={numbers} />
          </section>)}
          {artifact.citations.length > 0 && <section className="outputs-print-slide outputs-print-slide-sources"><Sources artifact={artifact} /></section>}
        </div>
        : <article className="outputs-print-report">
          <header>
            <h1>{artifact.title}</h1>
            {brief && <div className="outputs-print-brief"><AnswerBody content={brief} numbers={[]} /></div>}
            <p className="outputs-print-meta">Gunther · version {artifact.versionNumber} · {date}</p>
          </header>
          {artifact.origin === "legacy" || report.sections.length === 0
            ? <section><AnswerBody content={withoutTitle(artifact.content)} numbers={artifact.origin === "legacy" ? [] : numbers} /></section>
            : report.sections.map((text, index) => <section key={index}><AnswerBody content={text} numbers={numbers} /></section>)}
          <Sources artifact={artifact} />
        </article>}
    </div>,
    document.body,
  );
}
