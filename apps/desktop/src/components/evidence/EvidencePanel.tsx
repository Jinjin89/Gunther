import type { ConversationCitation, ContentBlock, SourceDetail } from "@gunther/contracts";
import { ArrowUpRight, FileText, Globe2, X } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import { knowledgeApi, sourceAssetUrl } from "../../api";
import { useEscape } from "../../shortcuts/shortcuts";
import "../../design/evidence.css";

const PdfViewer = lazy(() => import("./PdfViewer"));

const CONTEXT_BEFORE = 3;
const CONTEXT_AFTER = 4;
const CONTEXT_STEP = 6;
const WIDTH_KEY = "gunther:evidence-width";
const MIN_WIDTH = 340;
const DEFAULT_WIDTH = 460;

const maxWidth = () => Math.max(MIN_WIDTH, Math.min(960, Math.round(window.innerWidth * 0.8)));
const clampWidth = (value: number) => Math.min(Math.max(value, MIN_WIDTH), maxWidth());
const storedWidth = () => {
  const value = Number(window.localStorage.getItem(WIDTH_KEY));
  return Number.isFinite(value) && value > 0 ? clampWidth(value) : DEFAULT_WIDTH;
};

/** The panel's width: dragged by its left edge, moved by the arrow keys, and remembered. */
function usePanelWidth() {
  const [width, setWidth] = useState(storedWidth);
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);
  const commit = useCallback((next: number) => {
    const value = clampWidth(next);
    setWidth(value);
    window.localStorage.setItem(WIDTH_KEY, String(value));
  }, []);
  const handle = {
    onPointerDown: (event: PointerEvent<HTMLDivElement>) => {
      drag.current = { startX: event.clientX, startWidth: width };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    onPointerMove: (event: PointerEvent<HTMLDivElement>) => {
      if (drag.current) setWidth(clampWidth(drag.current.startWidth + drag.current.startX - event.clientX));
    },
    onPointerUp: (event: PointerEvent<HTMLDivElement>) => {
      if (!drag.current) return;
      commit(drag.current.startWidth + drag.current.startX - event.clientX);
      drag.current = null;
    },
    onDoubleClick: () => commit(DEFAULT_WIDTH),
    onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "ArrowLeft") { event.preventDefault(); commit(width + 24); }
      if (event.key === "ArrowRight") { event.preventDefault(); commit(width - 24); }
    },
  };
  return { width, handle };
}

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

function WebEvidence({ citation }: { citation: ConversationCitation }) {
  const host = citation.locator || hostOf(citation.url);
  return <>
    <div className="gx-evidence-body">
      <section aria-label="Passage from the page">
        <h3>What the page says</h3>
        <blockquote className="gx-evidence-quote">{citation.quote}</blockquote>
        <p className="gx-evidence-note">The part of this page the search matched to your question. Open the page for the rest.</p>
      </section>
    </div>
    <footer className="gx-evidence-foot">
      {citation.url && <a className="gx-btn gx-btn-primary gx-btn-sm" href={citation.url} target="_blank" rel="noreferrer noopener">Open {host || "the page"}<ArrowUpRight size={13} /></a>}
      <small><Globe2 size={11} />Outside your library</small>
    </footer>
  </>;
}

function LibraryEvidence({ citation, onOpenSource }: { citation: ConversationCitation; onOpenSource?: ((sourceId: string) => void) | undefined }) {
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
          {context.length > 1
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

/** The evidence behind one citation, at the right of the screen: the passage, then the original. */
export function EvidencePanel({ citation, index, onClose, onOpenSource }: {
  citation: ConversationCitation;
  index?: number | undefined;
  onClose: () => void;
  onOpenSource?: ((sourceId: string) => void) | undefined;
}) {
  const close = useRef<HTMLButtonElement>(null);
  const { width, handle } = usePanelWidth();
  useEscape(onClose);
  useEffect(() => { close.current?.focus(); }, [citation.id]);
  const web = citation.kind === "web";
  return createPortal(
    <aside className="gx-evidence" role="complementary" aria-label="Evidence" style={{ width: `min(${width}px, 100vw)` }}>
      <div className="gx-evidence-resize" role="separator" aria-orientation="vertical" aria-label="Resize evidence" aria-valuenow={width} aria-valuemin={MIN_WIDTH} aria-valuemax={maxWidth()} tabIndex={0} title="Drag to resize · double-click to reset" {...handle} />
      <header>
        {index !== undefined && <i>{index + 1}</i>}
        <span>
          <small>{web ? <><Globe2 size={11} />Web</> : <><FileText size={11} />Your library</>}</small>
          <strong title={citation.sourceTitle}>{citation.sourceTitle}</strong>
        </span>
        <button ref={close} type="button" className="gx-icon-button" onClick={onClose} aria-label="Close evidence" title="Close  Esc"><X size={15} /></button>
      </header>
      {web ? <WebEvidence citation={citation} /> : <LibraryEvidence citation={citation} onOpenSource={onOpenSource} />}
    </aside>,
    document.body,
  );
}
