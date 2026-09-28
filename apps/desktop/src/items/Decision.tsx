import type { Assertion, InboxKnowledgeBaseRef } from "@gunther/contracts";
import { ArrowRight, Check, CheckCheck, CircleDashed, FolderInput, LoaderCircle, ShieldAlert, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { KnowledgeBase } from "../atlas";
import { LibraryGlyph } from "../design/LibraryGlyph";
import { comboKeys, useShortcut } from "../shortcuts/shortcuts";
import { LibraryPicker } from "./LibraryPicker";

export type DecisionTone = "waiting" | "review" | "held" | "settled";

function Keys({ combo }: { combo: string }) {
  return <span className="gx-keys" aria-hidden="true">{comboKeys(combo).map((key) => <kbd key={key}>{key}</kbd>)}</span>;
}

export function DecisionCard({ tone, status, title, children, note }: { tone: DecisionTone; status: string; title: string; children?: ReactNode; note?: ReactNode }) {
  return (
    <section className={`gx-decision tone-${tone}`} aria-label={title}>
      <p className="gx-decision-status"><i />{status}</p>
      <h2>{title}</h2>
      {children}
      {note && <p className="gx-decision-note">{note}</p>}
    </section>
  );
}

export function LibraryLink({ base, known, onOpen }: { base: InboxKnowledgeBaseRef; known?: KnowledgeBase | undefined; onOpen: (id: string) => void }) {
  return (
    <button type="button" className="gx-library-link" onClick={() => onOpen(base.id)}>
      {known && <LibraryGlyph base={known} size="xs" />}
      <span>{base.title}</span>
      <ArrowRight size={12} className="gx-nudge" />
    </button>
  );
}

interface FileDecisionProps {
  bases: KnowledgeBase[];
  defaultBaseId?: string | null;
  what: "source" | "note";
  disabledReason?: string | null;
  onFile: (baseId: string) => Promise<void>;
  onCreateBase: () => void;
}

/** Unfiled captures: choose a library, then file. ⌘↵ files. */
export function FileDecision({ bases, defaultBaseId, what, disabledReason, onFile, onCreateBase }: FileDecisionProps) {
  const [target, setTarget] = useState(defaultBaseId && bases.some((base) => base.id === defaultBaseId) ? defaultBaseId : bases[0]?.id ?? "");
  const [working, setWorking] = useState(false);
  const chosen = bases.some((base) => base.id === target) ? target : bases[0]?.id ?? "";
  const blocked = Boolean(disabledReason) || !chosen || working;
  const file = async () => {
    if (blocked) return;
    setWorking(true);
    try {
      await onFile(chosen);
    } finally {
      setWorking(false);
    }
  };
  useShortcut("mod+enter", () => void file(), { enabled: !blocked, allowInInputs: false });
  return (
    <DecisionCard
      tone="waiting"
      status="Waiting in Inbox"
      title="Choose a home"
      note={what === "note"
        ? "Filing keeps this note and adds a read-only copy to the library. Claims stay reviewable."
        : "The original stays exactly as captured. AI suggestions wait for your review."}
    >
      {bases.length > 0 ? (
        <div className="gx-decision-controls">
          <LibraryPicker bases={bases} value={chosen} onChange={setTarget} onCreate={onCreateBase} label="File to library" />
          <button type="button" className="gx-btn gx-btn-primary gx-decision-primary" disabled={blocked} onClick={() => void file()} title={disabledReason ?? undefined}>
            {working ? <LoaderCircle className="spin" size={14} /> : <FolderInput size={14} />}
            <span>File to library</span>
            <Keys combo="mod+enter" />
          </button>
          {disabledReason && <p className="gx-decision-hint">{disabledReason}</p>}
        </div>
      ) : (
        <div className="gx-decision-controls">
          <p className="gx-decision-hint">You don’t have a library yet. Create one when a subject becomes clear — this can wait in Inbox until then.</p>
          <button type="button" className="gx-btn gx-btn-quiet" onClick={onCreateBase}>New library</button>
        </div>
      )}
    </DecisionCard>
  );
}

/** Filed sources with provisional claims: accept or dispute them together. */
export function ReviewDecision({ base, known, pending, onOpenBase, onDecide }: {
  base: InboxKnowledgeBaseRef | null;
  known?: KnowledgeBase | undefined;
  pending: number;
  onOpenBase: (id: string) => void;
  onDecide: (status: "verified" | "disputed") => Promise<void>;
}) {
  const [working, setWorking] = useState<"verified" | "disputed" | null>(null);
  const decide = async (status: "verified" | "disputed") => {
    if (working) return;
    setWorking(status);
    try {
      await onDecide(status);
    } finally {
      setWorking(null);
    }
  };
  useShortcut("mod+enter", () => void decide("verified"), { enabled: !working && pending > 0, allowInInputs: false });
  return (
    <DecisionCard
      tone="review"
      status={`${pending} ${pending === 1 ? "claim" : "claims"} to review`}
      title="Review what Gunther found"
      note="Nothing becomes trusted knowledge until you accept it. Disputed claims are kept as evidence."
    >
      {base && <p className="gx-decision-place">Filed in <LibraryLink base={base} known={known} onOpen={onOpenBase} /></p>}
      <div className="gx-decision-row">
        <button type="button" className="gx-btn gx-btn-primary gx-decision-primary" disabled={Boolean(working)} onClick={() => void decide("verified")}>
          {working === "verified" ? <LoaderCircle className="spin" size={14} /> : <CheckCheck size={14} />}
          <span>Accept all</span>
          <Keys combo="mod+enter" />
        </button>
        <button type="button" className="gx-btn gx-btn-quiet" disabled={Boolean(working)} onClick={() => void decide("disputed")}>
          {working === "disputed" ? <LoaderCircle className="spin" size={14} /> : <X size={14} />}Dispute all
        </button>
      </div>
    </DecisionCard>
  );
}

export function SettledDecision({ bases, knowledgeBases, onOpenBase, children }: { bases: KnowledgeBase[]; knowledgeBases: InboxKnowledgeBaseRef[]; onOpenBase: (id: string) => void; children?: ReactNode }) {
  return (
    <DecisionCard tone="settled" status="Filed" title={knowledgeBases.length > 1 ? "In your libraries" : "In your library"}>
      <div className="gx-decision-places">
        {knowledgeBases.map((base) => <LibraryLink key={base.id} base={base} known={bases.find((item) => item.id === base.id)} onOpen={onOpenBase} />)}
      </div>
      {children}
    </DecisionCard>
  );
}

const STATUS_ICON = { provisional: CircleDashed, verified: Check, disputed: ShieldAlert } as const;
const STATUS_LABEL = { provisional: "To review", verified: "Trusted", disputed: "Disputed" } as const;

/** Candidate claims extracted from a source, each decided on its own. */
export function ClaimsList({ assertions, onDecide }: { assertions: Assertion[]; onDecide: (id: string, status: "verified" | "disputed") => Promise<void> }) {
  const [working, setWorking] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const ordered = [...assertions].sort((left, right) => Number(right.status === "provisional") - Number(left.status === "provisional"));
  const visible = expanded ? ordered : ordered.slice(0, 6);
  const decide = async (id: string, status: "verified" | "disputed") => {
    setWorking(id);
    try {
      await onDecide(id, status);
    } finally {
      setWorking(null);
    }
  };
  return (
    <div className="gx-claims">
      {visible.map((assertion) => {
        const StatusIcon = STATUS_ICON[assertion.status];
        const quote = assertion.evidence[0]?.quote;
        return (
          <article key={assertion.id} className={`gx-claim is-${assertion.status}`}>
            <p className="gx-claim-text">
              <strong>{assertion.subject.label}</strong> <span>{assertion.predicate.replaceAll("_", " ")}</span> <strong>{assertion.object.label}</strong>
            </p>
            {quote && <blockquote title={quote}>{quote}</blockquote>}
            <footer>
              <span className="gx-claim-status"><StatusIcon size={12} />{STATUS_LABEL[assertion.status]}</span>
              <span className="gx-claim-confidence" title="Extraction confidence">{Math.round(assertion.confidence * 100)}%</span>
              {assertion.status === "provisional" && (
                <span className="gx-claim-actions">
                  <button type="button" className="gx-icon-button" disabled={working === assertion.id} onClick={() => void decide(assertion.id, "disputed")} aria-label={`Dispute: ${assertion.subject.label} ${assertion.predicate} ${assertion.object.label}`} title="Dispute"><X size={14} /></button>
                  <button type="button" className="gx-icon-button is-accept" disabled={working === assertion.id} onClick={() => void decide(assertion.id, "verified")} aria-label={`Accept: ${assertion.subject.label} ${assertion.predicate} ${assertion.object.label}`} title="Accept">{working === assertion.id ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}</button>
                </span>
              )}
            </footer>
          </article>
        );
      })}
      {ordered.length > 6 && <button type="button" className="gx-link gx-claims-more" onClick={() => setExpanded((value) => !value)}>{expanded ? "Show fewer" : `Show all ${ordered.length}`}</button>}
    </div>
  );
}
