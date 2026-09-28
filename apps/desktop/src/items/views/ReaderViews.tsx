import { ArrowUpRight, Globe, MessageSquareQuote, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { MarkdownView, safeHref } from "../../components/markdown/MarkdownView";
import { formatDate } from "../ItemLayout";
import { hostOf, pathOf, type ResearchContent, type WebContent } from "../sourceContent";

/** "Why you saved it": the capturer's own words, set apart from the source. */
export function ContextNote({ text, label = "Your note" }: { text: string | null; label?: string }) {
  if (!text?.trim()) return null;
  return (
    <aside className="gx-context-note" aria-label={label}>
      <span className="gx-context-label"><MessageSquareQuote size={13} />{label}</span>
      <MarkdownView source={text} className="is-compact" />
    </aside>
  );
}

const PREVIEW_PARAGRAPHS = 60;

/**
 * Extracted text (web pages, documents without structure) is shown as plain
 * paragraphs: it is never re-interpreted as Markdown, so it reads exactly as captured.
 */
export function PlainText({ text, emptyLabel = "No readable text was captured." }: { text: string; emptyLabel?: string }) {
  const [expanded, setExpanded] = useState(false);
  const paragraphs = useMemo(() => text.split(/\n+/).map((line) => line.trim()).filter(Boolean), [text]);
  if (!paragraphs.length) return <p className="gx-item-empty-line">{emptyLabel}</p>;
  const visible = expanded ? paragraphs : paragraphs.slice(0, PREVIEW_PARAGRAPHS);
  return (
    <div className="gx-prose gx-plain-text">
      {visible.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
      {paragraphs.length > PREVIEW_PARAGRAPHS && (
        <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm gx-continue" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Show less" : `Continue reading · ${paragraphs.length - PREVIEW_PARAGRAPHS} more paragraphs`}
        </button>
      )}
    </div>
  );
}

export function SiteCard({ url, capturedAt, status }: { url: string | null; capturedAt?: string | null; status?: string | null }) {
  const href = safeHref(url);
  const host = hostOf(url) ?? "Web page";
  const path = pathOf(url);
  return (
    <div className="gx-site-card">
      <span className="gx-site-mark" aria-hidden="true">{host.slice(0, 1).toUpperCase()}</span>
      <span className="gx-site-body">
        <strong>{host}</strong>
        {path && <small title={url ?? undefined}>{path}</small>}
        <em>
          <Globe size={12} />
          {capturedAt ? `Snapshot saved ${formatDate(capturedAt)}` : "Snapshot saved"}
          {status && status !== "200" ? ` · HTTP ${status}` : ""}
        </em>
      </span>
      {href && <a className="gx-btn gx-btn-quiet gx-btn-sm" href={href} target="_blank" rel="noreferrer noopener">Open page<ArrowUpRight size={13} /></a>}
    </div>
  );
}

export function WebBody({ web }: { web: WebContent }) {
  return (
    <div className="gx-reader">
      <SiteCard url={web.finalUrl ?? web.originalUrl} capturedAt={web.capturedAt} status={web.httpStatus} />
      <ContextNote text={web.context} label="Why you saved it" />
      <PlainText text={web.body} emptyLabel="The page had no readable text. The preserved snapshot is still available to download." />
    </div>
  );
}

export function ResearchBody({ research }: { research: ResearchContent }) {
  return (
    <div className="gx-reader">
      <div className="gx-research-query"><Search size={14} /><span>“{research.query}”</span></div>
      <MarkdownView source={research.answer} empty={<p className="gx-item-empty-line">No answer was captured.</p>} />
      {research.references.length > 0 && (
        <section className="gx-references">
          <h2 className="gx-section-title">Referenced pages</h2>
          <ol>
            {research.references.map((reference, index) => {
              const href = safeHref(reference.url);
              const body = <><span className="gx-reference-index">{index + 1}</span><span><strong>{reference.title}</strong>{reference.url && <small>{hostOf(reference.url) ?? reference.url}</small>}{reference.snippet && <em>{reference.snippet}</em>}</span>{href && <ArrowUpRight size={14} className="gx-reference-arrow" />}</>;
              return <li key={`${reference.title}-${index}`}>{href ? <a href={href} target="_blank" rel="noreferrer noopener">{body}</a> : <div>{body}</div>}</li>;
            })}
          </ol>
        </section>
      )}
    </div>
  );
}

export function TextBody({ content }: { content: string }) {
  return <div className="gx-reader"><MarkdownView source={content} empty={<p className="gx-item-empty-line">This source has no text.</p>} /></div>;
}
