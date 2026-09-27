import { useEffect, useState } from "react";
import type { KnowledgeTopic, TopicInput } from "@gunther/contracts";
import { knowledgeApi } from "../api";

export function TopicManager({ baseId, topics, onChange, onAsk }: {
  baseId: string; topics: KnowledgeTopic[]; onChange: () => void; onAsk: (id: string) => void;
}) {
  const empty = { title: "", description: "", parentId: null, position: 0 };
  const [form, setForm] = useState<TopicInput>(empty);
  const [editing, setEditing] = useState<string>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setEditing(undefined); setForm({ title: "", description: "", parentId: null, position: 0 }); setError(""); }, [baseId]);
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
  return <section className="topic-manager" aria-label="Knowledge topics">
    <header><div><strong>Your knowledge structure</strong><p>Organize ideas across sources. Add evidence from a source’s structured view.</p></div><small>{topics.length} topics</small></header>
    <div className="topic-manager-list">{topics.map((topic) => <article key={topic.id}>
      <div><strong>{paths(topic)}</strong><p>{topic.description}</p><small>{topic.blockIds.length} evidence blocks</small></div>
      <button onClick={() => { setEditing(topic.id); setForm({ title: topic.title, description: topic.description, parentId: topic.parentId, position: topic.position, version: topic.version }); }}>Edit</button>
      <button disabled={!topic.blockIds.length && !topics.some((item) => item.parentId === topic.id)} onClick={() => onAsk(topic.id)}>Ask this topic</button>
    </article>)}</div>
    <form onSubmit={(event) => {
      event.preventDefault(); setBusy(true); setError("");
      void knowledgeApi.saveTopic(baseId, form, editing).then(() => {
        setEditing(undefined); setForm(empty); onChange();
      }).catch((reason) => setError(reason instanceof Error ? reason.message : "Could not save topic. Reload and try again.")).finally(() => setBusy(false));
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
