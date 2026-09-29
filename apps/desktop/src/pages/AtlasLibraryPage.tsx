import { ArrowUpRight, FilePlus2, Plus, Search, X } from "lucide-react";
import { useMemo, useState, type CSSProperties } from "react";
import { knowledgeBases, type KnowledgeBase } from "../atlas";
import { LibraryGlyph } from "../design/LibraryGlyph";

interface AtlasLibraryPageProps {
  bases: KnowledgeBase[];
  loading?: boolean;
  onOpen: (id: string) => void;
  onAdd: () => void;
  onCreateBase: () => void;
}

const FILTER_THRESHOLD = 4;

/** Indexed counts come from the local service; bundled guides only have references. */
const sourceSummary = (base: KnowledgeBase) => base.indexedSourceCount === undefined
  ? `${base.sourceCount} ${base.sourceCount === 1 ? "reference" : "references"}`
  : `${base.indexedSourceCount} indexed · ${base.sourceCount} ${base.sourceCount === 1 ? "reference" : "references"}`;

export function AtlasLibraryPage({ bases = knowledgeBases, loading = false, onOpen, onAdd, onCreateBase }: AtlasLibraryPageProps) {
  const [filter, setFilter] = useState("");
  const needle = filter.trim().toLowerCase();
  const visible = useMemo(() => needle
    ? bases.filter((base) => `${base.title} ${base.eyebrow} ${base.description}`.toLowerCase().includes(needle))
    : bases, [bases, needle]);

  return (
    <div className="gx-libraries page-enter">
      <header className="gx-page-header">
        <div>
          <h1>Libraries</h1>
          <p>Long-lived homes for a subject — its sources, questions, conversations and outputs.</p>
        </div>
        <div className="gx-page-actions">
          <button type="button" className="gx-btn gx-btn-quiet" onClick={onAdd}><FilePlus2 size={15} />Capture</button>
          <button type="button" className="gx-btn gx-btn-primary" onClick={onCreateBase}><Plus size={15} />New library</button>
        </div>
      </header>

      <div className="gx-toolbar">
        {bases.length >= FILTER_THRESHOLD && (
          <label className="gx-filter">
            <Search size={14} />
            <input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter libraries" aria-label="Filter libraries" />
            {filter && <button type="button" onClick={() => setFilter("")} aria-label="Clear filter"><X size={13} /></button>}
          </label>
        )}
        {!loading && <span className="gx-count">{bases.length} {bases.length === 1 ? "library" : "libraries"}</span>}
      </div>

      <div className="gx-library-grid" aria-busy={loading}>
        {loading && [0, 1, 2].map((index) => <div className="gx-library-card is-skeleton" key={index} aria-hidden="true"><i /><b /><b /><b /></div>)}
        {!loading && visible.map((base, index) => (
          <button type="button" className="gx-library-card" key={base.id} style={{ "--i": index } as CSSProperties} onClick={() => onOpen(base.id)}>
            <span className="gx-library-card-top">
              <LibraryGlyph base={base} size="lg" />
              <span className="gx-library-eyebrow">{base.eyebrow}</span>
              <ArrowUpRight size={15} className="gx-card-arrow" />
            </span>
            <h2>{base.title}</h2>
            <p>{base.description}</p>
            <span className="gx-library-meta">
              <span>{sourceSummary(base)} · {base.chapterCount} {base.chapterCount === 1 ? "chapter" : "chapters"}</span>
              <span title="Last updated">{base.updated}</span>
            </span>
          </button>
        ))}
        {!needle && !loading && (
          <button type="button" className="gx-library-card is-new" onClick={onCreateBase} aria-label="Start a new library">
            <span className="gx-new-icon"><Plus size={18} /></span>
            <h2>New library</h2>
            <p>Begin with a question, a course, a pile of papers, or your own working notes.</p>
          </button>
        )}
      </div>
      {needle && visible.length === 0 && (
        <p className="gx-empty-line">No library matches “{filter.trim()}”.</p>
      )}
    </div>
  );
}
