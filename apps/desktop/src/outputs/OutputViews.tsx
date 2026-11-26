import type { Artifact, OutputExport, OutputKind, OutputVoices } from "@gunther/contracts";
import { lazy, Suspense, type ComponentProps, type ComponentType, type ReactNode } from "react";
import { audioFile, AudioOverview, AudioScript } from "./AudioOverview";
import { CardsList, CardsView } from "./CardsView";
import { LazySlidesView } from "./LazySlides";
import { MindMapOutline, MindMapView } from "./MindMapView";
import type { OutputContentProps } from "./OutputParts";
import { ReportView } from "./ReportView";
import { SlideReviewPanel } from "./SlideReviewPanel";
import { isDeckV2, isReportV2 } from "./deck/detect";
import { downloadDocx, downloadPptx } from "./deck/download";
import { splitDocument } from "./sections";
import type { OutputListener } from "./useStudio";
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/source-serif-4/latin-400.css";
import "@fontsource/source-serif-4/latin-600.css";

// Load the slide renderer and its charts only for a deck.
const PrintSlides = lazy(() => import("./PrintSlides").then((module) => ({ default: module.PrintSlides })));

type SlideDesignControls = Pick<ComponentProps<typeof LazySlidesView>,
  "onAccent" | "onTheme" | "onDeckTheme" | "onDeckStyle" | "accentDisabled" | "deckStyleDisabled" | "pendingDeckStyle">;

export type ReaderProps = OutputContentProps & {
  onCite: NonNullable<OutputContentProps["onCite"]>;
  onNotice?: ((text: string) => void) | undefined;
  design?: SlideDesignControls | undefined;
  work?: { working: boolean; disabled: boolean; run: (title: string, listen: OutputListener) => void; voices?: OutputVoices | null | undefined };
};

type PrintProps = Pick<OutputContentProps, "artifact" | "type"> & { presentation: "print"; from: number; to: number };

type OutputView = {
  Reader: ComponentType<ReaderProps>;
  Print: ComponentType<PrintProps>;
  wide?: boolean;
  pageRule?: string;
  printLabel?: string;
  savedStage?: string;
  file?: {
    format: OutputExport;
    label: string;
    available: (artifact: Artifact, working: boolean) => boolean;
    download: (artifact: Artifact) => Promise<string>;
  };
  Review?: typeof SlideReviewPanel;
};

function SlideReader({ onPart, design, presentation, ...props }: ReaderProps) {
  return <LazySlidesView {...props} {...design} onSlide={onPart} compact={presentation === "preview"} />;
}

function CardReader(props: ReaderProps) {
  return props.presentation === "preview" ? <CardsList {...props} /> : <CardsView key={props.artifact.id} {...props} />;
}

function MapReader(props: ReaderProps) {
  return props.presentation === "preview" ? <MindMapOutline {...props} /> : <MindMapView key={props.artifact.id} {...props} />;
}

function PrintSources({ artifact }: { artifact: Artifact }) {
  if (artifact.citations.length === 0) return null;
  return <section className="outputs-print-sources">
    <h2>Sources</h2>
    <ol>
      {artifact.citations.map((citation, index) => (
        <li key={index}>
          <strong>{citation.sourceTitle}</strong>
          {citation.locator && <span> · {citation.locator}</span>}
          <q>{citation.quote}</q>
        </li>
      ))}
    </ol>
  </section>;
}

function PrintDocument({ artifact, children }: { artifact: Artifact; children: ReactNode }) {
  const date = new Date(artifact.createdAt).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
  return <article className="outputs-print-report">
    <header>
      <h1>{artifact.title}</h1>
      <p className="outputs-print-meta">Gunther · version {artifact.versionNumber} · {date}</p>
    </header>
    {children}
    <PrintSources artifact={artifact} />
  </article>;
}

function PrintReport(props: PrintProps) {
  return <PrintDocument artifact={props.artifact}><ReportView {...props} /></PrintDocument>;
}

function PrintDeck({ artifact, from, to }: { artifact: Artifact; from: number; to: number }) {
  const { sections } = splitDocument(artifact.content, "slides");
  const count = sections.length;
  const end = Math.min(to, count);
  return <div className="outputs-print-deck" data-slide-count={sections.slice(from, end).length}>
    <Suspense fallback={null}><PrintSlides artifact={artifact} from={from} to={end} /></Suspense>
    {artifact.citations.length > 0 && end >= count && <section className="outputs-print-slide outputs-print-slide-sources"><PrintSources artifact={artifact} /></section>}
  </div>;
}

function PrintCards(props: PrintProps) {
  return <PrintDocument artifact={props.artifact}><CardsList {...props} /></PrintDocument>;
}

function PrintMap(props: PrintProps) {
  return <PrintDocument artifact={props.artifact}><MindMapOutline {...props} /></PrintDocument>;
}

function PrintAudio(props: PrintProps) {
  return <PrintDocument artifact={props.artifact}><AudioScript {...props} /></PrintDocument>;
}

export const outputViews: Record<OutputKind, OutputView> = {
  report: { Reader: ReportView, Print: PrintReport, file: {
    format: "docx", label: "Download Word", available: (artifact) => isReportV2(artifact.content), download: downloadDocx,
  } },
  slides: { Reader: SlideReader, Print: PrintDeck, wide: true, Review: SlideReviewPanel, pageRule: "@page { size: 13.333in 7.5in; margin: 0; }",
    file: { format: "pptx", label: "Download PowerPoint", available: (artifact) => isDeckV2(artifact.content), download: downloadPptx } },
  cards: { Reader: CardReader, Print: PrintCards },
  map: { Reader: MapReader, Print: PrintMap, wide: true },
  audio: { Reader: AudioOverview, Print: PrintAudio, printLabel: "Print script", savedStage: "audio", file: audioFile },
};
