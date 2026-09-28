import { ArrowLeft, Check, ChevronDown, ChevronUp, Copy, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { withShortcut } from "../shortcuts/shortcuts";

export type IdentityTone = "clay" | "blue" | "amber" | "violet" | "rose" | "green" | "brand" | "neutral";

export interface ItemNavigation {
  backLabel: string;
  onBack: () => void;
  position: { index: number; total: number } | null;
  onPrevious: (() => void) | null;
  onNext: (() => void) | null;
}

/** Back, previous / next and the item's own actions. Gains a hairline once the page scrolls. */
export function ItemToolbar({ nav, actions }: { nav: ItemNavigation; actions?: ReactNode }) {
  const sentinel = useRef<HTMLSpanElement>(null);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const target = sentinel.current;
    if (!target || typeof IntersectionObserver === "undefined") return undefined;
    const observer = new IntersectionObserver(([entry]) => setScrolled(!entry?.isIntersecting), { threshold: 1 });
    observer.observe(target);
    return () => observer.disconnect();
  }, []);
  return (
    <>
      <span ref={sentinel} className="gx-item-sentinel" aria-hidden="true" />
      <div className={`gx-item-toolbar ${scrolled ? "is-scrolled" : ""}`} role="toolbar" aria-label="Item">
        <button type="button" className="gx-item-back" onClick={nav.onBack} title={withShortcut(`Back to ${nav.backLabel}`, "close")}>
          <ArrowLeft size={15} className="gx-item-back-arrow" />
          <span>{nav.backLabel}</span>
        </button>
        {nav.position && nav.position.total > 1 && (
          <div className="gx-item-stepper">
            <button type="button" className="gx-icon-button" onClick={nav.onPrevious ?? undefined} disabled={!nav.onPrevious} aria-label="Previous item" title={withShortcut("Previous", "item-previous")}><ChevronUp size={16} /></button>
            <button type="button" className="gx-icon-button" onClick={nav.onNext ?? undefined} disabled={!nav.onNext} aria-label="Next item" title={withShortcut("Next", "item-next")}><ChevronDown size={16} /></button>
            <span aria-live="polite">{nav.position.index + 1} of {nav.position.total}</span>
          </div>
        )}
        <span className="gx-item-toolbar-fill" />
        {actions && <div className="gx-item-actions">{actions}</div>}
      </div>
    </>
  );
}

export function KindTile({ icon: Icon, tone, size = "md" }: { icon: LucideIcon; tone: IdentityTone; size?: "md" | "lg" }) {
  return <span className={`gx-kind-tile tone-${tone} is-${size}`} aria-hidden="true"><Icon size={size === "lg" ? 20 : 15} /></span>;
}

export interface ItemHeaderProps {
  icon: LucideIcon;
  tone: IdentityTone;
  kicker: string;
  title: ReactNode;
  meta: Array<ReactNode | null | false | undefined>;
  /** Replaces the title, e.g. with an editable field. */
  titleSlot?: ReactNode;
  trailing?: ReactNode;
}

export function ItemHeader({ icon, tone, kicker, title, meta, titleSlot, trailing }: ItemHeaderProps) {
  const visibleMeta = meta.filter(Boolean);
  return (
    <header className="gx-item-header">
      <div className="gx-item-kicker">
        <KindTile icon={icon} tone={tone} />
        <span>{kicker}</span>
        {trailing && <span className="gx-item-kicker-trailing">{trailing}</span>}
      </div>
      {titleSlot ?? <h1 className="gx-item-title">{title}</h1>}
      {visibleMeta.length > 0 && (
        <p className="gx-item-meta">
          {visibleMeta.map((item, index) => <span key={index}>{item}</span>)}
        </p>
      )}
    </header>
  );
}

export function ItemLayout({ nav, actions, header, aside, children, wide = false }: { nav: ItemNavigation; actions?: ReactNode; header: ReactNode; aside?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`gx-item ${wide ? "is-wide" : ""}`}>
      <ItemToolbar nav={nav} actions={actions} />
      <div className={`gx-item-grid ${aside ? "" : "has-no-aside"}`}>
        {header}
        {aside && <aside className="gx-item-aside" aria-label="About this item">{aside}</aside>}
        <div className="gx-item-main">{children}</div>
      </div>
    </div>
  );
}

export function AsideSection({ title, action, children, className = "" }: { title: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`gx-aside-section ${className}`.trim()}>
      <header><h2>{title}</h2>{action}</header>
      {children}
    </section>
  );
}

export interface DetailRow {
  label: string;
  value: ReactNode;
  /** When set, a copy button copies this text. */
  copy?: string;
  title?: string;
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="gx-copy"
      aria-label={copied ? "Copied" : `Copy ${label}`}
      title={copied ? "Copied" : `Copy ${label}`}
      onClick={() => void navigator.clipboard.writeText(text).then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1_400);
      }).catch(() => undefined)}
    >
      {copied ? <Check size={12} /> : <Copy size={12} />}
    </button>
  );
}

export function DetailsList({ rows }: { rows: Array<DetailRow | null | false | undefined> }) {
  const visible = rows.filter((row): row is DetailRow => Boolean(row));
  if (!visible.length) return null;
  return (
    <dl className="gx-details">
      {visible.map((row) => (
        <div key={row.label}>
          <dt>{row.label}</dt>
          <dd title={row.title}>
            <span>{row.value}</span>
            {row.copy && <CopyButton text={row.copy} label={row.label.toLowerCase()} />}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Shorten a fingerprint for display while keeping it recognisable. */
export const shortHash = (hash: string) => hash.length > 16 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash;

export const formatDate = (value: string | null | undefined, withTime = false) => {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, withTime
    ? { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }
    : { month: "short", day: "numeric", year: "numeric" }).format(date);
};

export function ItemSkeleton({ nav }: { nav: ItemNavigation }) {
  return (
    <div className="gx-item" aria-busy="true" aria-label="Opening item">
      <ItemToolbar nav={nav} />
      <div className="gx-item-grid">
        <header className="gx-item-header is-skeleton" aria-hidden="true"><i /><b /><b /></header>
        <aside className="gx-item-aside is-skeleton" aria-hidden="true"><b /><b /><b /></aside>
        <div className="gx-item-main is-skeleton" aria-hidden="true">{[0, 1, 2, 3, 4].map((index) => <b key={index} />)}</div>
      </div>
    </div>
  );
}
