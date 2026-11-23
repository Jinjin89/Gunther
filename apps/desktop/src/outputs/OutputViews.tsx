import type { Artifact, ConversationCitation, OutputKind } from "@gunther/contracts";
import { lazy, Suspense, type ComponentProps, type ComponentType, type ReactNode } from "react";
import { AnswerBody, citationNumbers } from "../pages/AnswerBody";
import { CardsList, CardsView } from "./CardsView";
import { LazySlidesView } from "./LazySlides";
import { MindMapOutline, MindMapView } from "./MindMapView";
import { ReportView } from "./ReportView";
import { SlideReviewPanel } from "./SlideReviewPanel";
import { isReportV2 } from "./deck/detect";
import { ReportV2Section } from "./deck/ReportV2";
import { splitDocument, withoutTitle } from "./sections";

// Load the slide renderer and its charts only for a deck.
const PrintSlides = lazy(() => import("./PrintSlides").then((module) => ({ default: module.PrintSlides })));

type SlideDesignControls = Pick<ComponentProps<typeof LazySlidesView>,
  "onAccent" | "onTheme" | "onDeckTheme" | "onDeckStyle" | "accentDisabled" | "deckStyleDisabled" | "pendingDeckStyle">;

type ReaderProps = {
  artifact: Artifact;
  onCite: (citation: ConversationCitation, index: number) => void;
  onAsk?: ((index: number, quote?: string) => void) | undefined;
  onAskChat?: ((question: string) => void) | undefined;
  onPart?: ((index: number | null) => void) | undefined;
  goTo?: { index: number; nonce: number } | null | undefined;
  changed?: readonly number[] | undefined;
  onNotice?: ((text: string) => void) | undefined;
  design?: SlideDesignControls | undefined;
  compact?: boolean;
};

type OutputView = {
  Reader: ComponentType<ReaderProps>;
  Print: ComponentType<{ artifact: Artifact; from: number; to: number }>;
  wide?: boolean;
  pageRule?: string;
  Review?: typeof SlideReviewPanel;
};

function SlideReader({ onPart, design, ...props }: ReaderProps) {
  return <LazySlidesView {...props} {...design} onSlide={onPart} />;
}

function CardReader({ compact, ...props }: ReaderProps) {
  return compact ? <CardsList {...props} /> : <CardsView key={props.artifact.id} {...props} />;
}

function MapReader({ compact, ...props }: ReaderProps) {
  return compact ? <MindMapOutline {...props} /> : <MindMapView key={props.artifact.id} {...props} />;
}

function PrintSources({ artifact }: { artifact: Artifact }) {
  if (artifact.citations.length === 0) return null;
  return <section className="outputs-print-sources">
    <h2>Sources</h2>
    <ol>
      {artifact.citations.map((citation, index) => {
        const quote = citation.quote.replace(/\s+/g, " ").trim();
        return <li key={index}>
          <strong>{citation.sourceTitle}</strong>
          {citation.locator && <span> · {citation.locator}</span>}
          <q>{quote.length > 220 ? `${quote.slice(0, 219).trimEnd()}…` : quote}</q>
        </li>;
      })}
    </ol>
  </section>;
}

function PrintDocument({ artifact, brief, styled = false, children }: {
  artifact: Artifact; brief?: string; styled?: boolean; children: ReactNode;
}) {
  const date = new Date(artifact.createdAt).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
  return <article className={`outputs-print-report${styled ? " is-studio" : ""}`}>
    <header>
      <h1>{artifact.title}</h1>
      {brief && <div className="outputs-print-brief"><AnswerBody content={brief} numbers={[]} /></div>}
      <p className="outputs-print-meta">Gunther · version {artifact.versionNumber} · {date}</p>
    </header>
    {children}
    <PrintSources artifact={artifact} />
  </article>;
}

function PrintReport({ artifact }: { artifact: Artifact }) {
  const numbers = citationNumbers(artifact.citations);
  const { preamble, sections } = splitDocument(artifact.content, "report");
  const v2 = isReportV2(artifact.content);
  const brief = withoutTitle(preamble).replace(/^\s*<!--\s*report:\s*v2\s*-->\s*/, "");
  return <PrintDocument artifact={artifact} brief={brief} styled={v2}>
    {artifact.origin === "legacy" || sections.length === 0
      ? <section><AnswerBody content={withoutTitle(artifact.content)} numbers={artifact.origin === "legacy" ? [] : numbers} /></section>
      : sections.map((text, index) => <section key={index}>{v2 ? <ReportV2Section text={text} numbers={numbers} /> : <AnswerBody content={text} numbers={numbers} />}</section>)}
  </PrintDocument>;
}

function PrintDeck({ artifact, from, to }: { artifact: Artifact; from: number; to: number }) {
  const count = splitDocument(artifact.content, "slides").sections.length;
  const end = Math.min(to, count);
  return <div className="outputs-print-deck">
    <Suspense fallback={null}><PrintSlides artifact={artifact} from={from} to={end} /></Suspense>
    {artifact.citations.length > 0 && end >= count && <section className="outputs-print-slide outputs-print-slide-sources"><PrintSources artifact={artifact} /></section>}
  </div>;
}

function PrintCards({ artifact }: { artifact: Artifact }) {
  return <PrintDocument artifact={artifact}><CardsList artifact={artifact} /></PrintDocument>;
}

function PrintMap({ artifact }: { artifact: Artifact }) {
  return <PrintDocument artifact={artifact}><MindMapOutline artifact={artifact} /></PrintDocument>;
}

export const outputViews: Record<OutputKind, OutputView> = {
  report: { Reader: ReportView, Print: PrintReport },
  slides: { Reader: SlideReader, Print: PrintDeck, wide: true, Review: SlideReviewPanel, pageRule: "@page { size: 13.333in 7.5in; margin: 0; }" },
  cards: { Reader: CardReader, Print: PrintCards },
  map: { Reader: MapReader, Print: PrintMap, wide: true },
};
