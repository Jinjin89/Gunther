import type { ConversationCitation, ContentBlock, SourceDetail } from "@gunther/contracts";
import { ArrowUpRight, FileText, Globe2, X } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { knowledgeApi, sourceAssetUrl } from "../../api";
import { useEscape } from "../../shortcuts/shortcuts";
import "../../design/evidence.css";

const PdfViewer = lazy(() => import("./PdfViewer"));

const CONTEXT_BEFORE = 2;
const CONTEXT_AFTER = 3;

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
  useEffect(() => {
    let active = true;
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
  const context = at >= 0
    ? blocks.slice(Math.max(0, at - CONTEXT_BEFORE), at + CONTEXT_AFTER + 1).filter((block) => block.headings[0] !== "Your context")
    : [];
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
          <h3>{citation.locator || "Passage"}</h3>
          {context.length > 1
            ? <div className="gx-evidence-context">{context.map((block) => <p key={block.id} className={block.id === citation.blockId ? "is-cited" : ""}>{block.content}</p>)}</div>
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
  useEscape(onClose);
  useEffect(() => { close.current?.focus(); }, [citation.id]);
  const web = citation.kind === "web";
  return createPortal(
    <aside className="gx-evidence" role="complementary" aria-label="Evidence">
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
