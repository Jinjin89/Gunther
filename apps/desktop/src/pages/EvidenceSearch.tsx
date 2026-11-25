import type { ConversationCitation, SearchedHit, UsedKeyResult } from "@gunther/contracts";
import { isWebCitation } from "../components/evidence/citationKind";
import { WebFindActions } from "../components/evidence/WebFindActions";
import { EvidenceDisclosure, evidenceLocator } from "./EvidencePrimitives";
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
    <h3>Cited <span className="evidence-count">({citations.length})</span></h3>
    {imports && pending.size > 0 && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm"
      disabled={imports.busy} onClick={() => void imports.importAll([...pending.values()])}>Add all cited ({pending.size})</button>}
  </header>;
}

function SearchedPassage({ hit }: { hit: SearchedHit }) {
  const locator = evidenceLocator(hit.locator, hit.kind === "web" ? hit.url : null);
  const query = hit.query?.trim();
  const filtered = hit.kept === false;
  return <>
    <strong>{hit.title}</strong>
    {locator && <small>{locator}</small>}
    {hit.snippet && <span className="evidence-card-quote">{hit.snippet}</span>}
    {(query || filtered) && <span className="evidence-search-detail">
      {query && <small>Query: {query}</small>}
      {filtered && <span className="evidence-pill">Filtered</span>}
    </span>}
  </>;
}

export function SearchEvidence({ searched = [], keyResultsUsed = [], citations, imports, onOpen }: {
  searched?: SearchedHit[] | undefined;
  keyResultsUsed?: UsedKeyResult[] | undefined;
  citations: ConversationCitation[];
  imports?: WebFindImports | undefined;
  onOpen: OpenPassage;
}) {
  const web = searched.filter((hit) => hit.kind === "web");
  const libraryHits = searched.filter((hit) => hit.kind === "library");
  // Saved hits carry a source and a passage excerpt. Their order identifies each hit in this run.
  const library: ConversationCitation[] = libraryHits.map((hit, index) => ({
    id: `searched:${index}`, sourceId: hit.sourceId ?? "", kind: "library", sourceTitle: hit.title,
    locator: hit.locator, quote: hit.snippet, assertionId: null, status: "provisional", confidence: 0,
  }));
  return <>
    {keyResultsUsed.length > 0 && <EvidenceDisclosure title="Key results used" count={keyResultsUsed.length}>
      <ul>{keyResultsUsed.map((result) => <li key={result.id}>
        <p>{result.text}</p>
        <div className="evidence-chips">{result.refs.filter((ref) => citations[ref - 1]).map((ref) => <button type="button"
          key={ref} aria-label={`Open citation ${ref} for this key result`} onClick={() => onOpen(citations, ref - 1)}>[{ref}]</button>)}</div>
      </li>)}</ul>
    </EvidenceDisclosure>}
    {searched.length > 0 && <EvidenceDisclosure title="Also searched" count={searched.length} subtitle={`${web.length} web · ${library.length} library`}>
      {web.map((hit, index) => <article className="evidence-card evidence-source-group" key={`searched:web:${index}`}>
        <SearchedPassage hit={hit} />
        <WebFindActions find={hit} imports={imports} />
      </article>)}
      {libraryHits.map((hit, index) => <button type="button" className="evidence-card" key={index} disabled={!hit.sourceId}
        onClick={() => onOpen(library, index)}><SearchedPassage hit={hit} />
      </button>)}
    </EvidenceDisclosure>}
  </>;
}
