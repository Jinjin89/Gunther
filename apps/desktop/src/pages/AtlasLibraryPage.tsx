import { ArrowUpRight, BookOpen, Clock3, FilePlus2, Layers3, Plus } from "lucide-react";
import { knowledgeBases, type KnowledgeBase } from "../atlas";

function BaseIllustration({ base }: { base: KnowledgeBase }) {
  if (base.id === "single-cell-annotation") {
    return (
      <div className="base-illustration cell-illustration" aria-hidden="true">
        <svg viewBox="0 0 320 180">
          <path className="cell-path" d="M39 98C79 42 118 62 154 88S222 151 286 64" />
          <path className="cell-path is-faint" d="M45 131C90 120 118 137 158 119S231 59 281 105" />
          <g className="cell-dot dot-a"><circle cx="56" cy="94" r="20" /><circle cx="56" cy="94" r="6" /></g>
          <g className="cell-dot dot-b"><circle cx="126" cy="75" r="27" /><circle cx="126" cy="75" r="8" /></g>
          <g className="cell-dot dot-c"><circle cx="198" cy="118" r="22" /><circle cx="198" cy="118" r="7" /></g>
          <g className="cell-dot dot-d"><circle cx="275" cy="65" r="15" /><circle cx="275" cy="65" r="5" /></g>
          <circle className="cell-speck" cx="84" cy="132" r="3" />
          <circle className="cell-speck" cx="164" cy="51" r="4" />
          <circle className="cell-speck" cx="246" cy="136" r="3" />
        </svg>
        <span>context</span><span>evidence</span><span>identity</span>
      </div>
    );
  }

  return (
    <div className={`base-illustration ${base.color}-illustration`} aria-hidden="true">
      <span className="book-line line-one" />
      <span className="book-line line-two" />
      <span className="book-line line-three" />
      <span className="book-node node-one" />
      <span className="book-node node-two" />
      <span className="book-node node-three" />
    </div>
  );
}

interface AtlasLibraryPageProps {
  bases: KnowledgeBase[];
  onOpen: (id: string) => void;
  onAdd: () => void;
  onCreateBase: () => void;
}

export function AtlasLibraryPage({ bases = knowledgeBases, onOpen, onAdd, onCreateBase }: AtlasLibraryPageProps) {
  const sourceSummary = (base: KnowledgeBase) => base.indexedSourceCount === undefined
    ? `${base.sourceCount} ${base.sourceCount === 1 ? "reference" : "references"}`
    : `${base.indexedSourceCount} indexed · ${base.sourceCount} ${base.sourceCount === 1 ? "reference" : "references"}`;

  return (
    <div className="atlas-library page-enter">
      <header className="library-intro">
        <div>
          <span className="atlas-eyebrow">Libraries</span>
          <h1>Knowledge with a lasting home.</h1>
          <p>Each knowledge base brings related sources, questions, conversations, and outputs together. Captures can wait in Inbox until their home is clear.</p>
        </div>
        <div className="library-actions">
          <button className="quiet-button" onClick={onAdd}><FilePlus2 size={15} />Capture</button>
          <button className="primary-button" onClick={onCreateBase}><Plus size={15} />New knowledge base</button>
        </div>
      </header>

      <section className="library-section">
        <div className="section-heading">
          <span>All knowledge bases</span>
          <small>{bases.length} {bases.length === 1 ? "library" : "libraries"}</small>
        </div>
        <div className="base-grid">
          {bases.map((base) => (
            <button className={`base-card color-${base.color}`} key={base.id} onClick={() => onOpen(base.id)}>
              <BaseIllustration base={base} />
              <span className="base-kicker">{base.eyebrow}</span>
              <h3>{base.title}</h3>
              <p>{base.description}</p>
              <footer>
                <span><BookOpen size={12} />{sourceSummary(base)}</span>
                <span><Layers3 size={12} />{base.chapterCount} chapters</span>
                <span><Clock3 size={12} />{base.updated}</span>
                <ArrowUpRight size={15} />
              </footer>
            </button>
          ))}
          <button className="base-card new-base-card" onClick={onCreateBase}>
            <span className="new-base-icon"><Plus size={21} /></span>
            <h3>Start a knowledge base</h3>
            <p>Begin with a question, a source, a course, or your own working notes.</p>
          </button>
        </div>
      </section>
    </div>
  );
}
