import { useEffect, useState } from "react";
import type { KnowledgeTopic, TopicInput, TopicOverview, TopicSuggestion, TopicSuggestions } from "@gunther/contracts";
import { knowledgeApi } from "../api";
import { MarkdownView } from "./markdown/MarkdownView";

const plural = (count: number, word: string) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

/** Groups of papers not yet in a topic, each one click from becoming a topic. */
function TopicSuggestionsPanel({ baseId, onCreated, onClose }: { baseId: string; onCreated: () => void; onClose: () => void }) {
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
      await knowledgeApi.saveTopic(baseId, { title: (titles[suggestion.key] ?? suggestion.title).trim() || suggestion.title, description: "", parentId: null, position: 0, sourceIds: suggestion.sourceIds });
      setDone((previous) => new Set(previous).add(suggestion.key));
      onCreated();
    } catch (reason) {
      setError(message(reason, "The topic could not be created."));
    } finally {
      setCreating(undefined);
    }
  };

  const open = result?.suggestions.filter((item) => !done.has(item.key)) ?? [];
  return <section className="topic-suggestions" aria-label="Suggested topics">
    <header>
      <div><strong>Suggested topics</strong><p>{result ? result.method === "semantic" ? `${plural(result.unfiled, "paper")} not in a topic, grouped by meaning.` : result.method === "keywords" ? `${plural(result.unfiled, "paper")} not in a topic, grouped by shared words.` : result.reason : error ? "" : "Grouping papers…"}</p></div>
      <button type="button" onClick={onClose}>Close</button>
    </header>
    {error && <p role="alert">{error}</p>}
    {result && result.suggestions.length > 0 && open.length === 0 && <p>All suggestions are now topics.</p>}
    <div className="topic-suggestion-list">{open.map((item) => <article key={item.key}>
      <label>Topic name<input value={titles[item.key] ?? item.title} maxLength={160} onChange={(event) => setTitles({ ...titles, [item.key]: event.target.value })} /></label>
      <small>{plural(item.sourceIds.length, "paper")}{item.years ? ` · ${item.years[0] === item.years[1] ? item.years[0] : `${item.years[0]}–${item.years[1]}`}` : ""}{item.keywords.length ? ` · ${item.keywords.join(", ")}` : ""}</small>
      <ul>{item.examples.map((title) => <li key={title}>{title}</li>)}</ul>
      <button type="button" className="primary-button" disabled={creating !== undefined} onClick={() => void create(item)}>{creating === item.key ? "Creating…" : "Create topic"}</button>
    </article>)}</div>
  </section>;
}

/** A topic's overview: the papers' abstracts, or a cited synthesis when a model is configured. */
function TopicOverviewPanel({ baseId, topic, onOpenSource }: { baseId: string; topic: KnowledgeTopic; onOpenSource?: ((id: string) => void) | undefined }) {
  const [overview, setOverview] = useState<TopicOverview | null>(null);
  const [state, setState] = useState<"loading" | "none" | "ready" | "writing">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void knowledgeApi.topicOverview(baseId, topic.id).then((next) => {
      if (active) { setOverview(next); setState("ready"); }
    }).catch(() => { if (active) setState("none"); });
    return () => { active = false; };
  }, [baseId, topic.id]);

  const write = async () => {
    setState("writing"); setError("");
    try {
      setOverview(await knowledgeApi.writeTopicOverview(baseId, topic.id));
      setState("ready");
    } catch (reason) {
      setError(message(reason, "The overview could not be written."));
      setState(overview ? "ready" : "none");
    }
  };

  const stale = overview !== null && overview.sourceCount !== topic.sourceIds.length;
  return <section className="topic-overview" aria-label={`Overview of ${topic.title}`}>
    {state === "loading" && <p>Loading overview…</p>}
    {(state === "none" || (state === "writing" && !overview)) && <p>{topic.sourceIds.length ? "No overview yet. Gunther gathers each paper's abstract into one page, with citations." : "File papers under this topic to write its overview."}</p>}
    {overview && <>
      <small>{overview.method === "local" ? "From the papers’ own abstracts" : `Written by ${overview.method.replace(/^deepseek:/, "DeepSeek:").replace(":", " ")} from the abstracts`} · {new Date(overview.createdAt).toLocaleDateString()}{stale ? " · papers changed since" : ""}</small>
      <MarkdownView source={overview.markdown} headingLevel={3} className="topic-overview-text" />
      {overview.citations.length > 0 && <ol className="topic-overview-sources">{overview.citations.map((citation) => <li key={citation.number} value={citation.number}>
        {onOpenSource ? <button type="button" className="link-button" onClick={() => onOpenSource(citation.sourceId)}>{citation.sourceTitle}</button> : citation.sourceTitle}
      </li>)}</ol>}
    </>}
    {error && <p role="alert">{error}</p>}
    {state !== "loading" && topic.sourceIds.length > 0 && <button type="button" disabled={state === "writing"} onClick={() => void write()}>{state === "writing" ? "Writing…" : overview ? "Rewrite overview" : "Write overview"}</button>}
  </section>;
}

export function TopicManager({ baseId, topics, onChange, onAsk, onOpenSource }: {
  baseId: string; topics: KnowledgeTopic[]; onChange: () => void; onAsk: (id: string) => void;
  /** Open a cited paper. */
  onOpenSource?: (id: string) => void;
}) {
  const empty = { title: "", description: "", parentId: null, position: 0 };
  const [form, setForm] = useState<TopicInput>(empty);
  const [editing, setEditing] = useState<string>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [overviewOf, setOverviewOf] = useState<string>();
  useEffect(() => { setEditing(undefined); setForm({ title: "", description: "", parentId: null, position: 0 }); setError(""); setSuggesting(false); setOverviewOf(undefined); }, [baseId]);
  const paths = (topic: KnowledgeTopic) => {
    const parts = [topic.title];
    const seen = new Set([topic.id]);
    let parent = topics.find((item) => item.id === topic.parentId);
    while (parent && !seen.has(parent.id)) {
      parts.unshift(parent.title); seen.add(parent.id);
      parent = topics.find((item) => item.id === parent?.parentId);
    }
    return parts.join(" / ");
  };
  const contents = (topic: KnowledgeTopic) => [
    topic.sourceIds.length ? plural(topic.sourceIds.length, "paper") : "",
    topic.blockIds.length ? plural(topic.blockIds.length, "passage") : "",
  ].filter(Boolean).join(" · ") || "Empty";
  return <section className="topic-manager" aria-label="Knowledge topics">
    <header>
      <div><strong>Your knowledge structure</strong><p>Group papers into topics, then ask a topic or read its overview.</p></div>
      <span className="topic-manager-actions"><small>{plural(topics.length, "topic")}</small><button type="button" aria-expanded={suggesting} onClick={() => setSuggesting((value) => !value)}>Suggest topics</button></span>
    </header>
    {suggesting && <TopicSuggestionsPanel baseId={baseId} onCreated={onChange} onClose={() => setSuggesting(false)} />}
    <div className="topic-manager-list">{topics.map((topic) => <div key={topic.id} className="topic-manager-item">
      <article>
        <div><strong>{paths(topic)}</strong><p>{topic.description}</p><small>{contents(topic)}</small></div>
        <button type="button" aria-expanded={overviewOf === topic.id} onClick={() => setOverviewOf(overviewOf === topic.id ? undefined : topic.id)}>Overview</button>
        <button onClick={() => { setEditing(topic.id); setForm({ title: topic.title, description: topic.description, parentId: topic.parentId, position: topic.position, version: topic.version }); }}>Edit</button>
        <button disabled={!topic.blockIds.length && !topic.sourceIds.length && !topics.some((item) => item.parentId === topic.id)} onClick={() => onAsk(topic.id)}>Ask this topic</button>
      </article>
      {overviewOf === topic.id && <TopicOverviewPanel baseId={baseId} topic={topic} onOpenSource={onOpenSource} />}
    </div>)}</div>
    <form onSubmit={(event) => {
      event.preventDefault(); setBusy(true); setError("");
      void knowledgeApi.saveTopic(baseId, form, editing).then(() => {
        setEditing(undefined); setForm(empty); onChange();
      }).catch((reason) => setError(message(reason, "Could not save topic. Reload and try again."))).finally(() => setBusy(false));
    }}>
      <label>Topic name<input required maxLength={160} value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></label>
      <label>Parent topic<select value={form.parentId ?? ""} onChange={(event) => setForm({ ...form, parentId: event.target.value || null })}><option value="">Top level</option>{topics.filter((topic) => topic.id !== editing).map((topic) => <option key={topic.id} value={topic.id}>{paths(topic)}</option>)}</select></label>
      <label>Description<textarea maxLength={4000} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
      {error && <p role="alert">{error}</p>}
      <button className="primary-button" disabled={busy || !form.title.trim()}>{busy ? "Saving…" : editing ? "Save topic" : "Add topic"}</button>
      {editing && <button type="button" disabled={busy} onClick={() => { setEditing(undefined); setForm(empty); }}>Cancel edit</button>}
    </form>
  </section>;
}
