import type { ConversationCitation, ContentBlock, SourceDetail } from "@gunther/contracts";
import { ArrowLeft, ArrowUpRight, ChevronLeft, ChevronRight, FileText, Globe2 } from "lucide-react";
import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { knowledgeApi, sourceAssetUrl } from "../../api";
import { webFindOf, type WebFindImports } from "../../pages/useImportWebFind";
import { isWebCitation } from "./citationKind";
import { WebFindActions } from "./WebFindActions";
import "../../design/evidence.css";

const PdfViewer = lazy(() => import("./PdfViewer"));
const CONTEXT_BEFORE = 3;
const CONTEXT_AFTER = 4;
const CONTEXT_STEP = 6;

/** The quote marked inside the block that holds it, when it is a piece of it. */
function Marked({ text, quote }: { text: string; quote: string }) {
  const needle = quote.trim();
  const at = needle ? text.indexOf(needle) : -1;
  if (at < 0 || needle.length >= text.length) return <>{text}</>;
  return <>{text.slice(0, at)}<mark>{needle}</mark>{text.slice(at + needle.length)}</>;
}

export const hostOf = (url: string | null | undefined): string => {
  try {
    return new URL(url ?? "").hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
};

const isPdf = (source: SourceDetail) => source.asset?.mediaType === "application/pdf" || /\.pdf$/i.test(source.asset?.originalName ?? "");
const isImage = (source: SourceDetail) => Boolean(source.asset?.mediaType.startsWith("image/"));

function WebEvidence({ citation, imports }: { citation: ConversationCitation; imports: WebFindImports | undefined }) {
  const host = citation.locator || hostOf(citation.url);
  return <>
    <div className="gx-evidence-body">
      <section aria-label="Passage from the page">
        <h3>What the page says</h3>
        <blockquote className="gx-evidence-quote">{citation.supportText || citation.quote}</blockquote>
        <p className="gx-evidence-note">The part of this page the search matched to your question. Open the page for the rest.</p>
      </section>
    </div>
    <footer className="gx-evidence-foot">
      <WebFindActions find={webFindOf(citation)} imports={imports} outsideLabel={`Open ${host || "the page"}`} />
      {!imports && <small><Globe2 size={11} />Outside your library</small>}
    </footer>
  </>;
}

function LibraryEvidence({ citation, onOpenSource, onOriginalPdfChange }: {
  citation: ConversationCitation;
  onOpenSource?: ((sourceId: string) => void) | undefined;
  onOriginalPdfChange?: ((original: boolean) => void) | undefined;
}) {
  const [source, setSource] = useState<SourceDetail | null>(null);
  const [blocks, setBlocks] = useState<ContentBlock[]>([]);
  const [failed, setFailed] = useState(false);
  const [tab, setTab] = useState<"passage" | "original">("passage");
  const [before, setBefore] = useState(CONTEXT_BEFORE);
  const [after, setAfter] = useState(CONTEXT_AFTER);
  const cited = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    let active = true;
    setBefore(CONTEXT_BEFORE);
    setAfter(CONTEXT_AFTER);
    setSource(null);
    setBlocks([]);
    setFailed(false);
    setTab(citation.quote ? "passage" : "original");
    void knowledgeApi.source(citation.sourceId).then((value) => { if (active) setSource(value); }).catch(() => { if (active) setFailed(true); });
    if (citation.blockId) {
      void knowledgeApi.sourceStructure(citation.sourceId, 0, citation.sourceRevisionId, null, 500)
        .then((structure) => { if (active) setBlocks(structure.blocks); })
        .catch(() => undefined);
    }
    return () => { active = false; };
  }, [citation.id, citation.sourceId, citation.blockId, citation.sourceRevisionId, citation.quote]);

  const at = citation.blockId ? blocks.findIndex((block) => block.id === citation.blockId) : -1;
  const from = Math.max(0, at - before);
  const to = at + after + 1;
  const context = at >= 0 ? blocks.slice(from, to).filter((block) => block.headings[0] !== "Your context") : [];
  const trail = at >= 0 ? (blocks[at]?.headings ?? []).filter((heading) => heading !== "Your context").join(" › ") : "";
  // Bring the cited passage into view once its neighbours are in, without moving the page.
  useLayoutEffect(() => {
    const node = cited.current;
    const body = node?.closest<HTMLElement>(".gx-evidence-body");
    if (node && body) body.scrollTop = Math.max(0, node.offsetTop - body.offsetTop - 72);
  }, [citation.id, at, tab]);
  const page = citation.anchor?.page ?? (at >= 0 ? blocks[at]?.anchor.page : undefined);
  const viewable = source ? (isPdf(source) ? "pdf" : isImage(source) ? "image" : null) : null;
  const assetUrl = source?.asset ? sourceAssetUrl(source.asset.id) : null;
  useEffect(() => { onOriginalPdfChange?.(tab === "original" && viewable === "pdf"); }, [onOriginalPdfChange, tab, viewable]);
  useEffect(() => () => onOriginalPdfChange?.(false), [onOriginalPdfChange]);

  return <>
    {viewable && <nav className="gx-evidence-tabs" aria-label="View">
      <button type="button" className={tab === "passage" ? "is-active" : ""} onClick={() => setTab("passage")} disabled={!citation.quote}>Passage</button>
      <button type="button" className={tab === "original" ? "is-active" : ""} onClick={() => setTab("original")}>Original{page && viewable === "pdf" ? ` · p. ${page}` : ""}</button>
    </nav>}
    <div className={`gx-evidence-body ${tab === "original" && viewable ? "is-viewer" : ""}`}>
      {tab === "original" && viewable && assetUrl
        ? viewable === "pdf"
          ? <Suspense fallback={<div className="gx-pdf-loading" />}><PdfViewer url={assetUrl} page={page} quote={citation.quote} title={citation.sourceTitle} /></Suspense>
          : <div className="gx-evidence-image"><img src={assetUrl} alt={citation.sourceTitle} /></div>
        : <section aria-label="Passage from the source">
          <h3>{[citation.locator || "Passage", trail].filter(Boolean).join(" · ")}</h3>
          {citation.supportText
            ? <blockquote className="gx-evidence-quote">{citation.supportText}</blockquote>
            : context.length > 1
            ? <div className="gx-evidence-context">
              {from > 0 && <button type="button" className="gx-evidence-more" onClick={() => setBefore((value) => value + CONTEXT_STEP)}>Show earlier</button>}
              {context.map((block) => block.id === citation.blockId
                ? <p key={block.id} ref={cited} className="is-cited"><Marked text={block.content} quote={citation.quote} /></p>
                : block.kind === "heading" ? <h4 key={block.id}>{block.content}</h4> : <p key={block.id}>{block.content}</p>)}
              {to < blocks.length && <button type="button" className="gx-evidence-more" onClick={() => setAfter((value) => value + CONTEXT_STEP)}>Show later</button>}
            </div>
            : citation.quote ? <blockquote className="gx-evidence-quote">{citation.quote}</blockquote> : <p className="gx-evidence-note">This source was opened without a specific passage.</p>}
          {failed && <p className="gx-evidence-note" role="alert">The source itself could not be loaded, only the quote is shown.</p>}
        </section>}
    </div>
    <footer className="gx-evidence-foot">
      {onOpenSource && <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" onClick={() => onOpenSource(citation.sourceId)}>Open source<ArrowUpRight size={13} /></button>}
      <small>{source ? [source.kind, citation.status === "verified" ? "Trusted" : null].filter(Boolean).join(" · ") : ""}</small>
    </footer>
  </>;
}

/** A citation's shared passage or original viewer, optionally within a source list. */
export function EvidenceDetail({ citation, index, passages, onPassage, onOpenSource, headerAction, onBack,
  position, total, onPrevious, onNext, onOriginalPdfChange, webImports }: {
  citation: ConversationCitation;
  index?: number | undefined;
  passages?: readonly ConversationCitation[] | undefined;
  onPassage?: ((index: number) => void) | undefined;
  onOpenSource?: ((sourceId: string) => void) | undefined;
  headerAction?: ReactNode;
  onBack?: (() => void) | undefined;
  position?: number | undefined;
  total?: number | undefined;
  onPrevious?: (() => void) | undefined;
  onNext?: (() => void) | undefined;
  onOriginalPdfChange?: ((original: boolean) => void) | undefined;
  webImports?: WebFindImports | undefined;
}) {
  const web = isWebCitation(citation);
  return <div className="gx-evidence-detail">
    {onBack && <nav className="gx-evidence-navigation" aria-label="Evidence navigation">
      <button type="button" onClick={onBack}><ArrowLeft size={14} />All {total ?? 0} {total === 1 ? "source" : "sources"}</button>
      {position !== undefined && total !== undefined && <span>[{position + 1} / {total}]</span>}
      <button type="button" aria-label="Previous source" disabled={!onPrevious} onClick={onPrevious}><ChevronLeft size={15} /></button>
      <button type="button" aria-label="Next source" disabled={!onNext} onClick={onNext}><ChevronRight size={15} /></button>
    </nav>}
    <header>
      {index !== undefined && <i>{index + 1}</i>}
      <span>
        <small>{web ? <><Globe2 size={11} />{citation.sourceOrigin === "web_supplement" ? "Web supplement" : "Web"}</> : <><FileText size={11} />{citation.sourceOrigin === "library_supplement" ? "Library supplement" : citation.sourceOrigin === "selected" ? "Selected material" : "Your library"}</>}</small>
        <strong title={citation.sourceTitle}>{citation.sourceTitle}</strong>
      </span>
      {headerAction}
    </header>
    {passages && passages.length > 1 && onPassage && <label className="gx-evidence-passages">Passage
      <select value={citation.id} onChange={(event) => onPassage(passages.findIndex((item) => item.id === event.target.value))}>
        {passages.map((passage, at) => <option key={passage.id} value={passage.id}>
          {at + 1}. {passage.sourceTitle}{passage.locator ? ` · ${passage.locator}` : ""}
        </option>)}
      </select>
    </label>}
    {web ? <WebEvidence citation={citation} imports={webImports} /> : <LibraryEvidence citation={citation} onOpenSource={onOpenSource} onOriginalPdfChange={onOriginalPdfChange} />}
  </div>;
}

