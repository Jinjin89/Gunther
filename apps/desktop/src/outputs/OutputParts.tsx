import type { Artifact, ConversationCitation, OutputIssue, OutputSection, OutputType } from "@gunther/contracts";
import { MessageSquarePlus } from "lucide-react";
import { citationNumbers, verificationNotice } from "../pages/AnswerBody";

export type OutputPresentation = "read" | "preview" | "print";

export type OutputContentProps = {
  artifact: Artifact;
  type?: OutputType | undefined;
  presentation?: OutputPresentation | undefined;
  onCite?: ((citation: ConversationCitation, index: number) => void) | undefined;
  onAsk?: ((index: number, quote?: string) => void) | undefined;
  onAskChat?: ((question: string) => void) | undefined;
  onPart?: ((index: number | null) => void) | undefined;
  goTo?: { index: number; nonce: number } | null | undefined;
  changed?: readonly number[] | undefined;
};

export function outputCitations({ artifact, onCite, presentation }: Pick<OutputContentProps, "artifact" | "onCite" | "presentation">) {
  const citations = artifact.citations;
  return {
    numbers: citationNumbers(citations),
    onCitation: onCite && presentation !== "print" ? (index: number) => {
      const citation = citations[index];
      if (citation) onCite(citation, index);
    } : undefined,
  };
}

export function OutputPartTools({ position, onAsk, changed, label = "Ask about this section" }: {
  position: number;
  label?: string;
} & Pick<OutputContentProps, "onAsk" | "changed">) {
  const revised = changed?.includes(position);
  if (!onAsk && !revised) return null;
  return <div className="outputs-section-tools">
    {revised && <em className="outputs-changed">Changed</em>}
    {onAsk && <button type="button" className="outputs-ask-section" onClick={(event) => {
      event.currentTarget.focus({ preventScroll: true });
      onAsk(position);
    }}>
      <MessageSquarePlus size={16} aria-hidden="true" /> {label}
    </button>}
  </div>;
}

const VERDICT: Record<OutputIssue["verdict"], { label: string; severe?: true }> = {
  contradicted: { label: "Contradicted", severe: true },
  unsupported: { label: "Unsupported", severe: true },
  unverified: { label: "Unverified" },
  unsourced: { label: "Unsourced" },
  unchecked: { label: "Unchecked" },
};

function sectionCheckNotice(info: OutputSection | undefined): string | undefined {
  if (!info) return undefined;
  if (info.coverage?.status === "stale") return "Source check is out of date";
  return verificationNotice(info.coverage) ?? (info.checked ? undefined : "Not re-checked");
}

export function sectionCheckSummary(info: OutputSection | undefined): { label: string; severe: boolean } | undefined {
  if (!info) return undefined;
  const labels: string[] = [];
  let severe = false;
  for (const [verdict, presentation] of Object.entries(VERDICT)) {
    const count = info.issues.filter((issue) => issue.verdict === verdict).length;
    if (!count) continue;
    labels.push(`${count} ${presentation.label.toLowerCase()}`);
    severe ||= presentation.severe === true;
  }
  const notice = sectionCheckNotice(info);
  if (notice) labels.push(notice);
  return labels.length ? { label: labels.join(" · "), severe } : undefined;
}

/** What the Checker found in one section, and whether it has looked at the text as it is now. */
export function SectionStatus({ info }: { info: OutputSection | undefined }) {
  const notice = sectionCheckNotice(info);
  if (!info || (!notice && info.issues.length === 0)) return null;
  return <div className="outputs-status">
    {notice && <span className="outputs-flag" role="status">{notice}</span>}
    {info.issues.length > 0 && <ul className="outputs-issues" aria-label="Found when checking">
      {info.issues.map((issue, index) => {
        const verdict = VERDICT[issue.verdict];
        return <li key={index} className={verdict.severe ? "is-severe" : undefined}>
          <div><strong>{verdict.label}</strong> <q>{issue.claim}</q></div>
          {issue.note && <details className="outputs-check-details">
            <summary>Check details</summary>
            <p>{issue.note}</p>
          </details>}
        </li>;
      })}
    </ul>}
  </div>;
}
