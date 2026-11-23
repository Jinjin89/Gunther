import type { ConversationCitation } from "@gunther/contracts";
import { createContext, useContext, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import "./citationPreview.css";

export const CitationDetails = createContext<readonly ConversationCitation[]>([]);
const QUOTE_LIMIT = 240;

/** The same numbered control in Markdown, laid-out reports and rendered slides. */
export function CitationButton({ number, index, onCite, title, note, className, children }: {
  number: number | string;
  index: number;
  onCite: (index: number) => void;
  title?: string | undefined;
  note?: string | undefined;
  className?: string | undefined;
  children: ReactNode;
}) {
  const citation = useContext(CitationDetails)[index];
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const open = Boolean(citation) && !dismissed && (hovered || focused);
  const button = useRef<HTMLButtonElement>(null);
  const popover = useRef<HTMLDivElement>(null);
  const id = useId();

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = button.current?.getBoundingClientRect();
      const tip = popover.current;
      if (!anchor || !tip) return;
      const { width, height } = tip.getBoundingClientRect();
      const left = Math.max(8, Math.min(anchor.left, window.innerWidth - width - 8));
      const below = anchor.bottom + 6;
      const top = below + height <= window.innerHeight - 8 ? below : Math.max(8, anchor.top - height - 6);
      Object.assign(tip.style, { left: `${left}px`, top: `${top}px` });
    };
    const dismiss = () => setDismissed(true);
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        dismiss();
        event.stopPropagation();
      }
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("keydown", escape, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("keydown", escape, true);
    };
  }, [open, citation]);

  const quote = citation?.quote.trim();
  return <>
    <button ref={button} type="button" className={className} title={citation ? undefined : note ?? title}
      aria-label={`Inspect citation ${number}`} aria-describedby={open ? id : undefined} data-opens-evidence
      onPointerEnter={() => { setHovered(true); setDismissed(false); }}
      onPointerLeave={() => setHovered(false)}
      onFocus={() => { setFocused(true); setDismissed(false); }}
      onBlur={() => setFocused(false)}
      onClick={(event) => { event.stopPropagation(); setDismissed(true); onCite(index); }}>
      {children}
    </button>
    {open && citation && createPortal(<div ref={popover} id={id} className="gx-citation-preview" role="tooltip">
      <strong>{citation.sourceTitle}</strong>
      {citation.locator && <small>{citation.locator}</small>}
      {quote && <p>{quote.length > QUOTE_LIMIT ? `${quote.slice(0, QUOTE_LIMIT).trimEnd()}…` : quote}</p>}
      {note && <small>{note}</small>}
    </div>, document.body)}
  </>;
}
