import type { Artifact, ContentsSource, ConversationCitation } from "@gunther/contracts";
import { useMemo, useState } from "react";
import { isWebCitation } from "../components/evidence/citationKind";
import { proseCitationNumbers } from "../components/evidence/citationMarks";
import { WebFindActions } from "../components/evidence/WebFindActions";
import { scopeLabel } from "../outputs/artifactConversations";
import { splitDesign, splitDocument } from "../outputs/sections";
import { citationMeta, CoverageMeter, EvidenceDisclosure, SourceKindMark } from "./EvidencePrimitives";
import { CitedHeader, SearchEvidence } from "./EvidenceSearch";
import { webFindOf, type WebFindImports } from "./useImportWebFind";

export function OutputEvidence({ artifact, sources, partInView, imports, onOpen }: {
  artifact: Artifact;
  sources: readonly ContentsSource[];
  partInView: number | null;
  imports?: WebFindImports | undefined;
  onOpen: (citations: ConversationCitation[], index: number) => void;
}) {
  const [onlyPart, setOnlyPart] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const parts = useMemo(() => artifact.kind === "report" || artifact.kind === "slides"
    ? splitDocument(artifact.content, artifact.kind).sections : [], [artifact.content, artifact.kind]);
  const canFilter = artifact.citations.length > 12 && parts.length >= 2;
  // Citation marks are part of the output's Markdown format, including checker annotations.
  const part = parts[partInView ?? 0] ?? "";
  const text = artifact.kind === "slides" ? splitDesign(part).text : part;
  const inPart = useMemo(() => canFilter && onlyPart ? new Set(proseCitationNumbers(text)) : null, [canFilter, onlyPart, text]);
  const groups = new Map<string, { citation: ConversationCitation; indices: number[] }>();
  artifact.citations.forEach((citation, index) => {
    if (inPart && !inPart.has(index + 1)) return;
    const key = isWebCitation(citation) ? `url:${citation.url || citation.id}` : `source:${citation.sourceId || citation.id}`;
    const group = groups.get(key);
    if (group) group.indices.push(index);
    else groups.set(key, { citation, indices: [index] });
  });
  const total = artifact.sections.length;
  const checked = artifact.sections.filter((section) => section.checked).length;
  const clean = artifact.sections.filter((section) => section.checked && section.issues.length === 0).length;
  const issues = artifact.sections.reduce((count, section) => count + section.issues.filter((issue) => issue.verdict !== "unchecked").length, 0);
  const checkedLabel = checked < total ? `${checked} of ${total}` : total > 1 ? `All ${total}` : "1";
  const coverage = total ? `${checkedLabel} ${total === 1 ? "section" : "sections"} checked` : "Not checked";
  const approach = artifact.provenance.approach;
  const memo = approach?.memo?.answer?.trim();
  const gaps = [...new Set([...(approach?.gaps ?? []), ...(approach?.studyMap?.limitations ?? [])].filter((item) => item.trim()))];
  const partLabel = artifact.kind === "slides" ? "slide" : "section";
  const researchSummary = [memo && "memo", gaps.length > 0 && `${gaps.length} ${gaps.length === 1 ? "gap" : "gaps"}`].filter(Boolean).join(" · ");
  return <div className="evidence-overview evidence-bands" aria-label="Evidence overview">
    <section className="evidence-context" aria-label="Output details">
      <header><h3>Output</h3><span className="evidence-pill">Version {artifact.versionNumber}</span></header>
      <p>Scope: {artifact.scope ? scopeLabel(artifact.scope) : "not recorded"}</p>
      <CoverageMeter summary={issues ? `${coverage} · ${issues} ${issues === 1 ? "issue" : "issues"}` : coverage} segments={[
        { tone: "verified", count: clean },
        { tone: "warning", count: checked - clean },
        { tone: "warning", count: total - checked },
      ]} />
    </section>
    <section className="evidence-cited" aria-label="Cited sources">
      <CitedHeader citations={artifact.citations} imports={imports} />
      {canFilter && <div className="evidence-filter" role="group" aria-label="Citation scope">
        <button type="button" aria-pressed={!onlyPart} onClick={() => setOnlyPart(false)}>All</button>
        <button type="button" aria-pressed={onlyPart} onClick={() => setOnlyPart(true)}>This {partLabel}</button>
      </div>}
      {groups.size === 0 && <p>{artifact.citations.length ? `Nothing cited in this ${partLabel}` : "Nothing cited"}</p>}
      {[...groups].map(([key, { citation, indices }]) => {
        const shown = expanded.has(key) ? indices : indices.slice(0, 4);
        const meta = citationMeta(citation, sources);
        return <article className="evidence-card evidence-source-group evidence-output-source" key={key}>
          <SourceKindMark {...meta} />
          <div>
            <strong>{citation.sourceTitle}</strong>
            <span className="evidence-meta">{[meta.kindLabel, meta.locator].filter(Boolean).join(" · ")}</span>
            <div className="evidence-chips">{shown.map((index) => <button type="button" key={index}
              aria-label={`Inspect citation ${index + 1}`} title={citationMeta(artifact.citations[index]!, sources).locator || undefined}
              onClick={() => onOpen(artifact.citations, index)}>[{index + 1}]</button>)}
              {shown.length < indices.length && <button type="button" onClick={() => setExpanded((current) => new Set(current).add(key))}>+{indices.length - shown.length} more</button>}
            </div>
            {(citation.quote || citation.supportText) && <span className="evidence-card-quote">{citation.quote || citation.supportText}</span>}
            {isWebCitation(citation) && <WebFindActions find={webFindOf(citation)} imports={imports} />}
          </div>
        </article>;
      })}
    </section>
    {researchSummary && <EvidenceDisclosure title="Research notes" subtitle={researchSummary} className="evidence-research-notes">
      {memo && <p>{memo}</p>}
      {gaps.length > 0 && <ul>{gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul>}
    </EvidenceDisclosure>}
    <SearchEvidence searched={artifact.provenance.searched} keyResultsUsed={artifact.provenance.keyResultsUsed}
      citations={artifact.citations} imports={imports} onOpen={onOpen} />
  </div>;
}
