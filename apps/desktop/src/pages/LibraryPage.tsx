import {
  ArrowRight,
  BookOpenCheck,
  Check,
  ChevronDown,
  Clock3,
  FileText,
  GitBranch,
  MoreHorizontal,
  Pencil,
  Plus,
  Quote,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useMemo, useState } from "react";
import { unitsForDomain, type DomainId, type KnowledgeUnit } from "../prototype";

interface LibraryPageProps {
  domain: DomainId;
  selectedUnit: KnowledgeUnit;
  onSelectUnit: (unit: KnowledgeUnit) => void;
  onCapture: () => void;
  onNotify: (message: string) => void;
}

const statusLabel = { trusted: "Trusted", provisional: "Provisional", disputed: "Disputed" };

export function LibraryPage({ domain, selectedUnit, onSelectUnit, onCapture, onNotify }: LibraryPageProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"units" | "sources">("units");
  const visibleUnits = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return unitsForDomain(domain).filter((unit) => !normalized || `${unit.title} ${unit.summary} ${unit.domains.join(" ")}`.toLowerCase().includes(normalized));
  }, [domain, query]);

  return (
    <div className="page-stack library-page">
      <section className="library-heading">
        <div><span className="eyebrow">Curated understanding</span><h1>Knowledge library</h1><p>Claims become useful when they are shaped into explanations you can revise and reuse.</p></div>
        <div><button className="button button-secondary" onClick={() => onNotify("A new collection is ready to name.")}><Plus size={14} />New collection</button><button className="button button-primary" onClick={onCapture}><Sparkles size={14} />Add knowledge</button></div>
      </section>

      <section className="knowledge-workspace">
        <aside className="panel unit-browser">
          <div className="unit-browser-tabs">
            <button className={filter === "units" ? "is-active" : ""} onClick={() => setFilter("units")}>Units <span>{visibleUnits.length}</span></button>
            <button className={filter === "sources" ? "is-active" : ""} onClick={() => setFilter("sources")}>Sources <span>12</span></button>
          </div>
          <label className="unit-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find knowledge…" /></label>
          <div className="unit-sort-row"><span>{domain === "all" ? "All domains" : domain}</span><button>Recently changed <ChevronDown size={12} /></button></div>
          {filter === "units" ? (
            <div className="unit-list">
              {visibleUnits.map((unit) => (
                <button key={unit.id} className={selectedUnit.id === unit.id ? "is-active" : ""} onClick={() => onSelectUnit(unit)}>
                  <span className={`unit-type-mark unit-${unit.kind}`}>{unit.title.slice(0, 1)}</span>
                  <span><small>{unit.kind} · {unit.domains.join(" + ")}</small><strong>{unit.title}</strong><p>{unit.summary}</p><em><Clock3 size={10} />{unit.updated}</em></span>
                  <i className={`unit-status-dot status-${unit.status}`} />
                </button>
              ))}
              {visibleUnits.length === 0 && <p className="prototype-empty">No matching Knowledge Units.</p>}
            </div>
          ) : (
            <div className="prototype-source-list">
              {[
                ["Cell annotation workshop", "Recording · 42 min", "R"],
                ["PBMC annotation field guide", "PDF · 24 pages", "P"],
                ["Deep learning course · week 2", "Course · 68 min", "C"],
                ["Strategy foundations", "Notes · 3,420 words", "N"],
              ].map(([title, meta, initial]) => <button key={title}><i>{initial}</i><span><strong>{title}</strong><small>{meta}</small></span></button>)}
            </div>
          )}
        </aside>

        <article className="panel knowledge-document">
          <header className="knowledge-document-header">
            <div className="document-breadcrumb"><span>Library</span><ArrowRight size={11} /><span>{selectedUnit.domains[0]}</span><ArrowRight size={11} /><strong>{selectedUnit.title}</strong></div>
            <div><button aria-label="More actions"><MoreHorizontal size={16} /></button><button onClick={() => onNotify("Editing is enabled for this prototype unit.")}><Pencil size={13} />Edit</button></div>
          </header>
          <div className="knowledge-document-body">
            <div className="unit-meta-line">
              <span className={`unit-status status-${selectedUnit.status}`}><ShieldCheck size={12} />{statusLabel[selectedUnit.status]}</span>
              {selectedUnit.domains.map((item) => <span key={item} className={`domain-tag domain-tag-${item}`}>{item}</span>)}
              <span>{selectedUnit.kind}</span>
              <span>Updated {selectedUnit.updated}</span>
            </div>
            <h1>{selectedUnit.title}</h1>
            <p className="unit-lede">{selectedUnit.summary}</p>
            <div className="unit-document-rule" />
            {selectedUnit.body.map((block, index) => (
              <section className="unit-body-block" key={`${selectedUnit.id}-${index}`}>
                {block.heading && <h2>{block.heading}</h2>}
                <p>{block.text}</p>
              </section>
            ))}

            <section className="claims-section">
              <header><span><GitBranch size={14} />Evidence-backed claims</span><button onClick={() => onNotify("DeepSeek is checking this unit for missing claims.")}><Sparkles size={13} />Check with AI</button></header>
              {selectedUnit.claims.map((claim) => (
                <article className="unit-claim-card" key={claim.id}>
                  <div className="claim-statement"><span>{claim.subject}</span><em>{claim.predicate}</em><span>{claim.object}</span></div>
                  <div className="claim-qualifiers">{claim.qualifiers.map((qualifier) => <span key={qualifier}>{qualifier}</span>)}</div>
                  <footer><span className={`claim-trust status-${claim.status}`}><Check size={11} />{statusLabel[claim.status]}</span><span><BookOpenCheck size={11} />{claim.evidenceCount} evidence</span><strong>{Math.round(claim.confidence * 100)}%</strong></footer>
                </article>
              ))}
            </section>

            {selectedUnit.evidence[0] && (
              <section className="source-evidence-card">
                <Quote size={18} />
                <div><span className="eyebrow">Source evidence</span><blockquote>{selectedUnit.evidence[0].quote}</blockquote><button><FileText size={12} />{selectedUnit.evidence[0].source} · {selectedUnit.evidence[0].locator}<ArrowRight size={12} /></button></div>
              </section>
            )}
          </div>
          <footer className="knowledge-document-footer"><span><Check size={12} />All changes saved locally</span><span>Revision 8 · <button onClick={() => onNotify("Revision history opened.")}>View history</button></span></footer>
        </article>
      </section>
    </div>
  );
}
