import type { Artifact, ConversationCitation, OutputIssue, OutputSection } from "@gunther/contracts";
import { MessageSquarePlus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnswerBody, citationNumbers } from "../pages/AnswerBody";
import { splitDocument, withoutTitle } from "./sections";

const VERDICT: Record<OutputIssue["verdict"], string> = {
  unsupported: "Its sources don’t state this",
  unverified: "Not backed by a source",
  unsourced: "No source is cited",
  contradicted: "Left out: the sources disagree",
  unchecked: "The source check is incomplete",
};

/** What the Checker found in one section, and whether it has looked at the text as it is now. */
export function SectionStatus({ info }: { info: OutputSection | undefined }) {
  if (!info || (info.checked && info.issues.length === 0)) return null;
  return <div className="outputs-status">
    {!info.checked && <span className="outputs-flag" title="This text changed after it was last checked against the sources.">Not re-checked</span>}
    {info.issues.length > 0 && <ul className="outputs-issues" aria-label="Found when checking">
      {info.issues.map((issue, index) => <li key={index} className={`is-${issue.verdict}`}>
        <strong>{VERDICT[issue.verdict]}</strong>
        <q>{issue.claim}</q>
        {issue.note && <small>{issue.note}</small>}
      </li>)}
    </ul>}
  </div>;
}

/** Text selected inside one section: what it says and where on screen it is. */
interface Picked { section: number; quote: string; x: number; y: number }

/** The words most worth quoting back to the agent: at most this many characters. */
const QUOTE_LIMIT = 500;

/**
 * A report, read on the page: its sections, each with what the Checker found in it.
 *
 * Pointing: a section shows "Ask about this section" when hovered, and text selected inside one
 * section shows a small "Ask about this" beside the selection. Both tell the page what the next
 * message is about.
 */
export function ReportView({ artifact, onCite, onAsk, goTo, changed }: {
  artifact: Artifact;
  onCite: (citation: ConversationCitation, index: number) => void;
  /** Point at a section (counted from 0), and at the words selected in it when there are some. Without it nothing can be pointed at. */
  onAsk?: ((section: number, quote?: string) => void) | undefined;
  /** Scroll to this section (counted from 0). A new `nonce` scrolls again to the same section. */
  goTo?: { index: number; nonce: number } | null | undefined;
  /** The sections (counted from 0) this version changed. */
  changed?: readonly number[] | undefined;
}) {
  const numbers = useMemo(() => citationNumbers(artifact.citations), [artifact.citations]);
  const parts = useMemo(() => splitDocument(artifact.content, "report"), [artifact.content]);
  const body = useRef<HTMLDivElement>(null);
  const [picked, setPicked] = useState<Picked | null>(null);
  const cite = (index: number) => {
    const citation = artifact.citations[index];
    if (citation) onCite(citation, index);
  };

  useEffect(() => {
    if (!onAsk) return undefined;
    const look = () => {
      const selection = window.getSelection();
      const box = body.current;
      if (!selection || selection.isCollapsed || selection.rangeCount === 0 || !box) { setPicked(null); return; }
      const owner = (node: Node | null) => (node instanceof Element ? node : node?.parentElement)?.closest<HTMLElement>("[data-section]") ?? null;
      const first = owner(selection.anchorNode);
      const last = owner(selection.focusNode);
      const quote = selection.toString().replace(/\s+/g, " ").trim();
      // Only words inside one section can be pointed at.
      if (!first || first !== last || !box.contains(first) || !quote) { setPicked(null); return; }
      const rect = selection.getRangeAt(0).getBoundingClientRect?.();
      setPicked({ section: Number(first.dataset.section), quote: quote.slice(0, QUOTE_LIMIT), x: rect?.right ?? 0, y: rect?.bottom ?? 0 });
    };
    document.addEventListener("selectionchange", look);
    return () => document.removeEventListener("selectionchange", look);
  }, [onAsk]);

  useEffect(() => {
    if (goTo) body.current?.querySelector<HTMLElement>(`[data-section="${goTo.index}"]`)?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }, [goTo?.nonce]);

  // A version from the old builder has no citations; its Markdown is shown as it is.
  if (artifact.origin === "legacy") {
    return <div className="outputs-body"><AnswerBody content={withoutTitle(artifact.content)} numbers={[]} /></div>;
  }
  const brief = withoutTitle(parts.preamble);
  return <div ref={body} className="outputs-body">
    {brief && <div className="outputs-brief"><AnswerBody content={brief} numbers={[]} /></div>}
    {parts.sections.length === 0 && <section className="outputs-section"><AnswerBody content={withoutTitle(artifact.content)} numbers={numbers} onCitation={cite} /></section>}
    {parts.sections.map((text, index) => <section key={index} className={`outputs-section${changed?.includes(index) ? " is-changed" : ""}`} data-section={index}>
      {(onAsk || changed?.includes(index)) && <div className="outputs-section-tools">
        {changed?.includes(index) && <em className="outputs-changed">Changed</em>}
        {onAsk && <button type="button" className="outputs-ask-section" onClick={() => onAsk(index)}><MessageSquarePlus size={12} />Ask about this section</button>}
      </div>}
      <AnswerBody content={text} numbers={numbers} onCitation={cite} />
      <SectionStatus info={artifact.sections[index]} />
    </section>)}
    {/* On the document body: a fixed layer inside the page's entrance animation fills the page, not the window. */}
    {picked && onAsk && createPortal(
      <button type="button" className="outputs-ask-selection" style={{ left: Math.max(8, Math.min(picked.x, window.innerWidth - 120)), top: Math.min(picked.y + 6, window.innerHeight - 40) }}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => { onAsk(picked.section, picked.quote); window.getSelection()?.removeAllRanges(); setPicked(null); }}>
        <MessageSquarePlus size={12} />Ask about this
      </button>,
      document.body,
    )}
  </div>;
}
