import { useEffect, useState } from "react";
import type { TopicOverview, TopicSuggestion, TopicSuggestions } from "@gunther/contracts";
import { knowledgeApi } from "../api";
import { MarkdownView } from "../components/markdown/MarkdownView";

const plural = (count: number, word: string) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

/** Groups of papers that are not placed yet, each one click from becoming a section. */
export function SectionSuggestions({ baseId, onCreated, onClose }: { baseId: string; onCreated: () => void; onClose: () => void }) {
  const [result, setResult] = useState<TopicSuggestions | null>(null);
  const [error, setError] = useState("");
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [creating, setCreating] = useState<string>();
  const [done, setDone] = useState<Set<string>>(new Set());

  useEffect(() => {
    let active = true;
    void knowledgeApi.topicSuggestions(baseId).then((next) => {
      if (!active) return;
      setResult(next);
      setTitles(Object.fromEntries(next.suggestions.map((item) => [item.key, item.title])));
    }).catch((reason) => { if (active) setError(message(reason, "Suggestions could not be made.")); });
    return () => { active = false; };
  }, [baseId]);

  const create = async (suggestion: TopicSuggestion) => {
    setCreating(suggestion.key); setError("");
    try {
      await knowledgeApi.saveTopic(baseId, { title: (titles[suggestion.key] ?? suggestion.title).trim() || suggestion.title, description: "", parentId: null, sourceIds: suggestion.sourceIds });
      setDone((previous) => new Set(previous).add(suggestion.key));
      onCreated();
    } catch (reason) {
      setError(message(reason, "The section could not be created."));
    } finally {
      setCreating(undefined);
    }
  };

  const open = result?.suggestions.filter((item) => !done.has(item.key)) ?? [];
  return <section className="contents-suggestions" aria-label="Suggested sections">
    <header>
      <div><strong>Suggested sections</strong><p>{result ? result.method === "semantic" ? `${plural(result.unfiled, "paper")} not placed yet, grouped by meaning.` : result.method === "keywords" ? `${plural(result.unfiled, "paper")} not placed yet, grouped by shared words.` : result.reason : error ? "" : "Grouping papers…"}</p></div>
      <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" onClick={onClose}>Close</button>
    </header>
    {error && <p role="alert">{error}</p>}
    {result && result.suggestions.length > 0 && open.length === 0 && <p>All suggestions are now sections.</p>}
    <div className="contents-suggest-list">{open.map((item) => <article key={item.key}>
      <label>Section name<input value={titles[item.key] ?? item.title} maxLength={160} onChange={(event) => setTitles({ ...titles, [item.key]: event.target.value })} /></label>
      <small>{plural(item.sourceIds.length, "paper")}{item.years ? ` · ${item.years[0] === item.years[1] ? item.years[0] : `${item.years[0]}–${item.years[1]}`}` : ""}{item.keywords.length ? ` · ${item.keywords.join(", ")}` : ""}</small>
      <ul>{item.examples.map((title) => <li key={title}>{title}</li>)}</ul>
      <button type="button" className="gx-btn gx-btn-primary gx-btn-sm" disabled={creating !== undefined} onClick={() => void create(item)}>{creating === item.key ? "Creating…" : "Create section"}</button>
    </article>)}</div>
  </section>;
}

/** A section's overview: the papers' abstracts, or a cited synthesis when a model is configured. */
export function SectionOverview({ baseId, section, paperCount, onOpenSource }: { baseId: string; section: { id: string; title: string }; paperCount: number; onOpenSource?: ((id: string) => void) | undefined }) {
  const [overview, setOverview] = useState<TopicOverview | null>(null);
  const [state, setState] = useState<"loading" | "none" | "ready" | "writing">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void knowledgeApi.topicOverview(baseId, section.id).then((next) => {
      if (active) { setOverview(next); setState("ready"); }
    }).catch(() => { if (active) setState("none"); });
    return () => { active = false; };
  }, [baseId, section.id]);

  const write = async () => {
    setState("writing"); setError("");
    try {
      setOverview(await knowledgeApi.writeTopicOverview(baseId, section.id));
      setState("ready");
    } catch (reason) {
      setError(message(reason, "The overview could not be written."));
      setState(overview ? "ready" : "none");
    }
  };

  const stale = overview !== null && overview.sourceCount !== paperCount;
  return <section className="contents-overview" aria-label={`Overview of ${section.title}`}>
    {state === "loading" && <p>Loading overview…</p>}
    {(state === "none" || (state === "writing" && !overview)) && <p>{paperCount ? "No overview yet. Gunther gathers each paper's abstract into one page, with citations." : "Put papers in this section to write its overview."}</p>}
    {overview && <>
      <small>{overview.method === "local" ? "From the papers’ own abstracts" : `Written by ${overview.method.replace(/^deepseek:/, "DeepSeek:").replace(":", " ")} from the abstracts`} · {new Date(overview.createdAt).toLocaleDateString()}{stale ? " · papers changed since" : ""}</small>
      <MarkdownView source={overview.markdown} headingLevel={3} className="contents-overview-text" />
      {overview.citations.length > 0 && <ol className="contents-overview-sources">{overview.citations.map((citation) => <li key={citation.number} value={citation.number}>
        {onOpenSource ? <button type="button" className="contents-link" onClick={() => onOpenSource(citation.sourceId)}>{citation.sourceTitle}</button> : citation.sourceTitle}
      </li>)}</ol>}
    </>}
    {error && <p role="alert">{error}</p>}
    {state !== "loading" && paperCount > 0 && <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={state === "writing"} onClick={() => void write()}>{state === "writing" ? "Writing…" : overview ? "Rewrite overview" : "Write overview"}</button>}
  </section>;
}
