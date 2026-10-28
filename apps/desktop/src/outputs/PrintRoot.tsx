import type { Artifact } from "@gunther/contracts";
import { lazy, Suspense } from "react";
import { createPortal } from "react-dom";
import { AnswerBody, citationNumbers } from "../pages/AnswerBody";
import { splitDocument, withoutTitle } from "./sections";
import { isReportV2 } from "./deck/detect";
import { ReportV2Section } from "./deck/ReportV2";

// The designed slides and their charts are fetched when a deck is open, not when the app starts.
const PrintSlides = lazy(() => import("./PrintSlides").then((module) => ({ default: module.PrintSlides })));

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
export function PrintRoot({ artifact, from = 0, to = Number.POSITIVE_INFINITY }: { artifact: Artifact; from?: number; to?: number }) {
  const numbers = citationNumbers(artifact.citations);
  const date = new Date(artifact.createdAt).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
  const deck = artifact.kind === "slides";
  const report = splitDocument(artifact.content, "report");
  const deckSlides = deck ? splitDocument(artifact.content, "slides").sections : [];
  const end = Math.min(to, deckSlides.length);
  const v2 = isReportV2(artifact.content);
  const brief = withoutTitle(report.preamble).replace(/^\s*<!--\s*report:\s*v2\s*-->\s*/, "");
  return createPortal(
    <div id="print-root" aria-hidden="true" data-kind={artifact.kind}>
      <style>{deck ? DECK_PAGE : REPORT_PAGE}</style>
      {deck
        ? <div className="outputs-print-deck">
          <Suspense fallback={null}><PrintSlides artifact={artifact} from={from} to={end} /></Suspense>
          {artifact.citations.length > 0 && end >= deckSlides.length && <section className="outputs-print-slide outputs-print-slide-sources"><Sources artifact={artifact} /></section>}
        </div>
        : <article className={`outputs-print-report${v2 ? " is-studio" : ""}`}>
          <header>
            <h1>{artifact.title}</h1>
            {brief && <div className="outputs-print-brief"><AnswerBody content={brief} numbers={[]} /></div>}
            <p className="outputs-print-meta">Gunther · version {artifact.versionNumber} · {date}</p>
          </header>
          {artifact.origin === "legacy" || report.sections.length === 0
            ? <section><AnswerBody content={withoutTitle(artifact.content)} numbers={artifact.origin === "legacy" ? [] : numbers} /></section>
            : report.sections.map((text, index) => <section key={index}>{v2 ? <ReportV2Section text={text} numbers={numbers} /> : <AnswerBody content={text} numbers={numbers} />}</section>)}
          <Sources artifact={artifact} />
        </article>}
    </div>,
    document.body,
  );
}
