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
  const notice = verificationNotice(info.coverage);
  return <div className="outputs-status">
    {!info.checked && <span className="outputs-flag" role="status">{notice ?? "Not re-checked"}</span>}
    {info.issues.length > 0 && <ul className="outputs-issues" aria-label="Found when checking">
      {info.issues.map((issue, index) => <li key={index} className={`is-${issue.verdict}`}>
        <strong>{VERDICT[issue.verdict]}</strong>
        <q>{issue.claim}</q>
        {issue.note && <small>{issue.note}</small>}
      </li>)}
    </ul>}
  </div>;
}
