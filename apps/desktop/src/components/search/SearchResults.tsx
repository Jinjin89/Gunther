import type { KnowledgeSearchResult, WebSearchResult } from "@gunther/contracts";
import type { CSSProperties, KeyboardEvent } from "react";
import { ArrowRight, ArrowUpRight, BookOpen, Bookmark, Check, ChevronRight, CircleAlert, FileText, Globe2, MessageSquareText, Sparkles, StickyNote, WifiOff } from "lucide-react";
import type { KnowledgeBase } from "../../atlas";
import { LibraryGlyph } from "../../design/LibraryGlyph";
import type { CuratedResult, SubmittedSearch } from "./useKnowledgeSearch";

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Emphasise the searched words inside a snippet. */
function Highlighted({ text, query }: { text: string; query: string }) {
  const terms = query.split(/\s+/).filter((term) => term.length > 1).map(escapeRegExp);
  if (!terms.length || !text) return <>{text}</>;
  const parts = text.split(new RegExp(`(${terms.join("|")})`, "gi"));
  return <>{parts.map((part, index) => index % 2 === 1 ? <mark key={index}>{part}</mark> : part)}</>;
}

const kindLabel: Record<KnowledgeSearchResult["kind"], string> = {
  knowledge_unit: "Accepted knowledge",
  source: "Source",
  session: "Conversation",
  note: "Note",
};

const kindIcon = (kind: KnowledgeSearchResult["kind"]) => kind === "note" ? StickyNote : kind === "session" ? MessageSquareText : kind === "source" ? FileText : Sparkles;

interface SearchResultsProps {
  bases: KnowledgeBase[];
  submitted: SubmittedSearch;
  indexedResults: KnowledgeSearchResult[];
  curatedResults: CuratedResult[];
  webResult: WebSearchResult | null;
  searching: boolean;
  error: string | null;
  savingResearch: boolean;
  researchSaved: boolean;
  onOpenResult: (result: KnowledgeSearchResult) => void;
  onOpenBase: (id: string) => void;
  onOpenChapter: (baseId: string, chapterId: string) => void;
  onAsk: (baseId: string, question: string) => void;
  onSaveResearch: () => void;
  /** Return keyboard focus to the search box (↑ from the first result, or Esc). */
  onExit?: () => void;
}

/** ↑ / ↓ move between results; ↑ from the first one or Esc returns to the search box. */
const moveFocus = (event: KeyboardEvent<HTMLElement>, onExit?: () => void) => {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp" && event.key !== "Escape") return;
  const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("[data-result-item]"));
  const index = items.indexOf(document.activeElement as HTMLElement);
  if (index < 0) return;
  event.preventDefault();
  event.stopPropagation();
  const next = event.key === "Escape" ? -1 : index + (event.key === "ArrowDown" ? 1 : -1);
  if (next < 0) onExit?.();
  else items[Math.min(next, items.length - 1)]?.focus();
};

export function SearchResults({
  bases,
  submitted,
  indexedResults,
  curatedResults,
  webResult,
  searching,
  error,
  savingResearch,
  researchSaved,
  onOpenResult,
  onOpenBase,
  onOpenChapter,
  onAsk,
  onSaveResearch,
  onExit,
}: SearchResultsProps) {
  const scopedBases = submitted.baseIds.map((id) => bases.find((base) => base.id === id)).filter((base): base is KnowledgeBase => Boolean(base));
  const askTarget = scopedBases.length === 1 ? scopedBases[0] : undefined;
  const localCount = indexedResults.length + curatedResults.length;
  const includesKnowledge = submitted.scope !== "web";
  const includesWeb = submitted.scope !== "knowledge";
  let rowIndex = 0;

  return (
    <section className="gx-results" aria-label="Search results" aria-busy={searching} onKeyDown={(event) => moveFocus(event, onExit)}>
      <header className="gx-results-header">
        <h1>
          <span className="gx-results-query">{submitted.query}</span>
          {scopedBases.length > 0 && (
            <span className="gx-results-scope">
              in {scopedBases.map((base, index) => <span key={base.id}>{index > 0 && ", "}<LibraryGlyph base={base} size="xs" /> {base.title}</span>)}
            </span>
          )}
        </h1>
        <p role="status">{searching ? "Searching…" : includesKnowledge ? `${localCount} ${localCount === 1 ? "match" : "matches"} in your knowledge${includesWeb ? " · web included" : ""}` : "Web only"}</p>
      </header>

      {askTarget && (
        <button type="button" className="gx-ask-card" data-result-item onClick={() => onAsk(askTarget.id, submitted.query)}>
          <span className="gx-ask-icon"><Sparkles size={16} /></span>
          <span className="gx-ask-copy">
            <strong>Ask {askTarget.title}</strong>
            <small>Get an answer grounded in this library’s sources, with citations.</small>
          </span>
          <ArrowRight size={15} className="gx-nudge" />
        </button>
      )}

      {error && <p className="gx-inline-alert" role="alert"><CircleAlert size={14} />{error}</p>}

      {includesWeb && webResult && (
        webResult.answer ? (
          <article className="gx-web-answer">
            <header>
              <span><Globe2 size={14} />From the web</span>
              {webResult.mode === "openai" && (
                <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={savingResearch || researchSaved} onClick={onSaveResearch}>
                  {researchSaved ? <Check size={13} /> : <Bookmark size={13} />}
                  {researchSaved ? "Saved to Inbox" : savingResearch ? "Saving…" : "Save research"}
                </button>
              )}
            </header>
            <p>{webResult.answer}</p>
            {webResult.sources.length > 0 && (
              <div className="gx-web-sources">
                {webResult.sources.map((source, index) => (
                  <a href={source.url} target="_blank" rel="noreferrer" key={source.url}>
                    <i>{index + 1}</i>
                    <span><strong>{source.title}</strong><small>{source.url.replace(/^https?:\/\/(www\.)?/, "").split("/")[0]}</small></span>
                    <ArrowUpRight size={12} />
                  </a>
                ))}
              </div>
            )}
          </article>
        ) : (
          <p className="gx-inline-note"><WifiOff size={14} /><span><strong>{webResult.mode === "not_configured" ? "Web search isn’t set up yet." : "The web couldn’t be reached."}</strong> {webResult.message ?? "Your own knowledge is still searched below."}</span></p>
        )
      )}

      {includesKnowledge && (
        <div className="gx-result-list">
          {searching && localCount === 0 && [0, 1, 2].map((index) => <div className="gx-result-skeleton" key={index} aria-hidden="true"><i /><span><b /><b /></span></div>)}
          {indexedResults.map((result) => {
            const base = bases.find((item) => item.id === result.knowledgeBaseId);
            const Icon = kindIcon(result.kind);
            const style = { "--i": rowIndex++ } as CSSProperties;
            return (
              <button type="button" className="gx-result" data-result-item key={`indexed-${result.kind}-${result.id}`} style={style} onClick={() => onOpenResult(result)}>
                <span className="gx-result-icon"><Icon size={15} /></span>
                <span className="gx-result-body">
                  <strong>{result.title}</strong>
                  {result.snippet && <p><Highlighted text={result.snippet} query={submitted.query} /></p>}
                  <small>
                    {base ? <><LibraryGlyph base={base} size="xs" />{base.title}</> : result.kind === "note" ? "Notebook" : result.knowledgeBaseId ?? "Inbox"}
                    <i>·</i>{kindLabel[result.kind]}
                    {result.meta && <><i>·</i>{result.meta}</>}
                  </small>
                </span>
                <ChevronRight size={15} className="gx-result-chevron" />
              </button>
            );
          })}
          {curatedResults.map((result) => {
            const style = { "--i": rowIndex++ } as CSSProperties;
            return (
              <button
                type="button"
                className="gx-result"
                data-result-item
                key={`${result.type}-${result.base.id}-${result.chapter?.id ?? ""}`}
                style={style}
                onClick={() => result.chapter ? onOpenChapter(result.base.id, result.chapter.id) : onOpenBase(result.base.id)}
              >
                <span className="gx-result-icon">{result.type === "base" ? <BookOpen size={15} /> : <FileText size={15} />}</span>
                <span className="gx-result-body">
                  <strong>{result.chapter?.title ?? result.base.title}</strong>
                  <p><Highlighted text={result.chapter?.summary ?? result.base.description} query={submitted.query} /></p>
                  <small><LibraryGlyph base={result.base} size="xs" />{result.base.title}<i>·</i>{result.type === "base" ? "Library" : "Chapter"}</small>
                </span>
                <ChevronRight size={15} className="gx-result-chevron" />
              </button>
            );
          })}
          {!searching && localCount === 0 && (
            <div className="gx-results-empty">
              <strong>Nothing in {scopedBases.length === 1 ? scopedBases[0]!.title : "your knowledge"} matches yet.</strong>
              <span>{askTarget ? "Ask the library instead — it can still reason over its sources." : includesWeb ? "Use the web result as a lead, then save only what you trust." : "Try different words, or turn on Web to look further."}</span>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
