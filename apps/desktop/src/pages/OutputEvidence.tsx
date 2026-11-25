import type { Artifact, ConversationCitation } from "@gunther/contracts";
import { useMemo, useState } from "react";
import { hostOf } from "../components/evidence/EvidenceDetail";
import { isWebCitation } from "../components/evidence/citationKind";
import { WebFindActions } from "../components/evidence/WebFindActions";
import { scopeLabel } from "../outputs/artifactConversations";
import { splitDesign, splitDocument } from "../outputs/sections";
import { verificationNotice } from "./AnswerBody";
import { CitedHeader, SearchEvidence } from "./EvidenceSearch";
import { webFindOf, type WebFindImports } from "./useImportWebFind";

export function OutputEvidence({ artifact, partInView, imports, onOpen }: {
  artifact: Artifact;
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
  const inPart = new Set([...text.matchAll(/\[(?:[ipd]:)?(\d+(?:\s*[,，、]\s*\d+)*)\]/g)]
    .flatMap((match) => match[1]!.split(/[,，、]/).map((number) => Number(number.trim()) - 1)));
  const groups = new Map<string, { citation: ConversationCitation; indices: number[] }>();
  artifact.citations.forEach((citation, index) => {
    if (canFilter && onlyPart && !inPart.has(index)) return;
    const key = isWebCitation(citation) ? `url:${citation.url || citation.id}` : `source:${citation.sourceId || citation.id}`;
    const group = groups.get(key);
    if (group) group.indices.push(index);
    else groups.set(key, { citation, indices: [index] });
  });
  const checked = artifact.sections.length > 0 && artifact.sections.every((section) => section.checked);
  const status = artifact.sections.map((section) => verificationNotice(section.coverage)).find(Boolean)
    ?? (checked ? "Sources checked" : artifact.sections.some((section) => section.checked) ? "Source check incomplete" : "Not checked");
  const approach = artifact.provenance.approach;
  const memo = approach?.memo?.answer?.trim();
  const gaps = [...new Set([...(approach?.gaps ?? []), ...(approach?.studyMap?.limitations ?? [])].filter((item) => item.trim()))];
  return <div className="evidence-overview" aria-label="Evidence overview">
    {(memo || gaps.length > 0) && <details className="evidence-disclosure evidence-research-notes">
      <summary>Research notes</summary>
      {memo && <p>{memo}</p>}
      {gaps.length > 0 && <ul>{gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul>}
    </details>}
    <section className="evidence-method" aria-label="Output details">
      <h3>Output details</h3>
      <p>Scope: {artifact.scope ? scopeLabel(artifact.scope) : "not recorded"} · {status} · Version {artifact.versionNumber}</p>
    </section>
    <section className="evidence-cited" aria-label="Cited sources">
      <CitedHeader citations={artifact.citations} imports={imports} />
      {canFilter && <div className="evidence-filter" role="group" aria-label="Citation scope">
        <button type="button" aria-pressed={!onlyPart} onClick={() => setOnlyPart(false)}>All</button>
        <button type="button" aria-pressed={onlyPart} onClick={() => setOnlyPart(true)}>This section</button>
      </div>}
      {groups.size === 0 && <p>{artifact.citations.length ? "Nothing cited in this section" : "Nothing cited"}</p>}
      {[...groups].map(([key, { citation, indices }]) => {
        const shown = expanded.has(key) ? indices : indices.slice(0, 4);
        return <article className="evidence-card evidence-source-group" key={key}>
          <strong>{citation.sourceTitle}</strong>
          {citation.url && isWebCitation(citation) && <small>{hostOf(citation.url)}</small>}
          <div className="evidence-chips">{shown.map((index) => <button type="button" key={index}
            aria-label={`Inspect citation ${index + 1}`} title={artifact.citations[index]?.locator}
            onClick={() => onOpen(artifact.citations, index)}>[{index + 1}]</button>)}
            {shown.length < indices.length && <button type="button" onClick={() => setExpanded((current) => new Set(current).add(key))}>+{indices.length - shown.length} more</button>}
          </div>
          {(citation.quote || citation.supportText) && <span className="evidence-card-quote">{citation.quote || citation.supportText}</span>}
          {isWebCitation(citation) && <WebFindActions find={webFindOf(citation)} imports={imports} />}
        </article>;
      })}
    </section>
    <SearchEvidence searched={artifact.provenance.searched} keyResultsUsed={artifact.provenance.keyResultsUsed}
      citations={artifact.citations} imports={imports} onOpen={onOpen} />
  </div>;
}
