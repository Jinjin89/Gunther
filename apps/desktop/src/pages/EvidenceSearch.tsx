import type { ConversationCitation, SearchedHit, UsedKeyResult } from "@gunther/contracts";
import { isWebCitation } from "../components/evidence/citationKind";
import { WebFindActions } from "../components/evidence/WebFindActions";
import { webFindKey, webFindOf, type WebFind, type WebFindImports } from "./useImportWebFind";

type OpenPassage = (citations: ConversationCitation[], index: number) => void;

export function CitedHeader({ citations, imports }: { citations: ConversationCitation[]; imports?: WebFindImports | undefined }) {
  const pending = new Map<string, WebFind>();
  if (imports) for (const citation of citations) {
    if (!isWebCitation(citation)) continue;
    const find = webFindOf(citation);
    const key = webFindKey(find.url);
    if (key && imports.status(find).status !== "imported" && !pending.has(key)) pending.set(key, find);
  }
  return <header className="evidence-cited-header">
    <h3>Cited ({citations.length})</h3>
    {imports && pending.size > 0 && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm"
      disabled={imports.busy} onClick={() => void imports.importAll([...pending.values()])}>Add all cited ({pending.size})</button>}
  </header>;
}

export function SearchEvidence({ searched = [], keyResultsUsed = [], citations, imports, onOpen }: {
  searched?: SearchedHit[] | undefined;
  keyResultsUsed?: UsedKeyResult[] | undefined;
  citations: ConversationCitation[];
  imports?: WebFindImports | undefined;
  onOpen: OpenPassage;
}) {
  const web = searched.filter((hit) => hit.kind === "web");
  // Saved hits carry a source and a passage excerpt. Their order identifies each hit in this run.
  const library: ConversationCitation[] = searched.filter((hit) => hit.kind === "library").map((hit, index) => ({
    id: `searched:${index}`, sourceId: hit.sourceId ?? "", kind: "library", sourceTitle: hit.title,
    locator: hit.locator, quote: hit.snippet, assertionId: null, status: "provisional", confidence: 0,
  }));
  return <>
    {keyResultsUsed.length > 0 && <details className="evidence-disclosure">
      <summary>Key results used ({keyResultsUsed.length})</summary>
      <ul>{keyResultsUsed.map((result) => <li key={result.id}>
        <p>{result.text}</p>
        <div className="evidence-chips">{result.refs.filter((ref) => citations[ref - 1]).map((ref) => <button type="button"
          key={ref} aria-label={`Open citation ${ref} for this key result`} onClick={() => onOpen(citations, ref - 1)}>[{ref}]</button>)}</div>
      </li>)}</ul>
    </details>}
    {searched.length > 0 && <details className="evidence-disclosure">
      <summary>Also searched, not cited ({searched.length}) <small>{searched.length} more ({web.length} online)</small></summary>
      {web.map((hit, index) => <article className="evidence-card evidence-source-group" key={`searched:web:${index}`}>
        <strong>{hit.title}</strong>{hit.locator && <small>{hit.locator}</small>}
        {hit.snippet && <span className="evidence-card-quote">{hit.snippet}</span>}
        <WebFindActions find={hit} imports={imports} />
      </article>)}
      {library.map((hit, index) => <button type="button" className="evidence-card" key={hit.id} disabled={!hit.sourceId}
        onClick={() => onOpen(library, index)}>
        <strong>{hit.sourceTitle}</strong>{hit.locator && <small>{hit.locator}</small>}
        {hit.quote && <span className="evidence-card-quote">{hit.quote}</span>}
      </button>)}
    </details>}
  </>;
}
