import type { Artifact, ConversationCitation, OutputIssue, OutputSection } from "@gunther/contracts";
import { useMemo } from "react";
import { AnswerBody, citationNumbers } from "../pages/AnswerBody";
import { splitDocument, withoutTitle } from "./sections";

const VERDICT: Record<OutputIssue["verdict"], string> = {
  unsupported: "Its sources don’t state this",
  unverified: "Not backed by a source",
  unsourced: "No source is cited",
  contradicted: "Left out: the sources disagree",
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

/** A report, read on the page: its sections, each with what the Checker found in it. */
export function ReportView({ artifact, onCite }: {
  artifact: Artifact;
  onCite: (citation: ConversationCitation, index: number) => void;
}) {
  const numbers = useMemo(() => citationNumbers(artifact.citations), [artifact.citations]);
  const parts = useMemo(() => splitDocument(artifact.content, "report"), [artifact.content]);
  const cite = (index: number) => {
    const citation = artifact.citations[index];
    if (citation) onCite(citation, index);
  };
  // A version from the old builder has no citations; its Markdown is shown as it is.
  if (artifact.origin === "legacy") {
    return <div className="outputs-body"><AnswerBody content={withoutTitle(artifact.content)} numbers={[]} /></div>;
  }
  const brief = withoutTitle(parts.preamble);
  return <div className="outputs-body">
    {brief && <div className="outputs-brief"><AnswerBody content={brief} numbers={[]} /></div>}
    {parts.sections.length === 0 && <section className="outputs-section"><AnswerBody content={withoutTitle(artifact.content)} numbers={numbers} onCitation={cite} /></section>}
    {parts.sections.map((text, index) => <section key={index} className="outputs-section">
      <AnswerBody content={text} numbers={numbers} onCitation={cite} />
      <SectionStatus info={artifact.sections[index]} />
    </section>)}
  </div>;
}
