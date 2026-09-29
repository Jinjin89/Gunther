import { useEffect, useRef, useState } from "react";
import type { ContentBlock, KnowledgeTopic, SourceStructure } from "@gunther/contracts";
import { knowledgeApi } from "../api";
import { EvidencePanel } from "./evidence/EvidencePanel";

export function SourceEvidence({ sourceId, baseId, assetId, revisionId, blockId }: {
  sourceId: string; baseId?: string | undefined; assetId?: string | undefined;
  revisionId?: string | null | undefined; blockId?: string | null | undefined;
}) {
  const [data, setData] = useState<SourceStructure | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [topics, setTopics] = useState<KnowledgeTopic[]>([]);
  const [topicId, setTopicId] = useState("");
  const [linked, setLinked] = useState<string[]>([]);
  const [reload, setReload] = useState(0);
  const [viewing, setViewing] = useState<ContentBlock | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    const run = ++generation.current;
    let timer: number | undefined;
    setData(null); setError(""); setBusy(false); setLinked([]); setTopicId(""); setTopics([]);
    const load = async () => {
      try {
        const result = await knowledgeApi.sourceStructure(sourceId, 0, revisionId, blockId);
        if (generation.current !== run) return;
        setData(result); setError("");
        if (!revisionId && ["pending", "queued", "running"].includes(result.processing.state)) {
          timer = window.setTimeout(() => void load(), 2000);
        }
      } catch (reason) {
        if (generation.current === run) setError(reason instanceof Error ? reason.message : "Could not load evidence.");
      }
    };
    void load();
    if (baseId) void knowledgeApi.topics(baseId).then((items) => {
      if (generation.current === run) setTopics(items);
    }).catch(() => { /* Topic editing is optional; evidence remains readable. */ });
    return () => { generation.current++; window.clearTimeout(timer); };
  }, [baseId, sourceId, revisionId, blockId, reload]);

  const action = async (operation: () => Promise<unknown>) => {
    const run = generation.current;
    setBusy(true); setError("");
    try {
      await operation();
      const result = await knowledgeApi.sourceStructure(sourceId, 0, revisionId, blockId);
      if (generation.current === run) { setData(result); if (["queued", "running"].includes(result.processing.state)) setReload((value) => value + 1); }
      window.dispatchEvent(new CustomEvent("gunther:sources-updated"));
    } catch (reason) {
      if (generation.current === run) setError(reason instanceof Error ? reason.message : "Action failed.");
    } finally { if (generation.current === run) setBusy(false); }
  };
  const more = async () => {
    if (data?.nextOffset == null) return;
    const run = generation.current;
    setBusy(true);
    try {
      const next = await knowledgeApi.sourceStructure(sourceId, data.nextOffset, data.revisionId);
      if (generation.current === run) setData({ ...next, blocks: [...data.blocks, ...next.blocks] });
    } catch (reason) { if (generation.current === run) setError(String(reason)); }
    finally { if (generation.current === run) setBusy(false); }
  };

  return <section className="structured-evidence" aria-label="Structured evidence">
    <header><strong>Structured evidence</strong><small aria-live="polite">{data?.processing.state ?? "Loading…"}</small></header>
    {error && <p role="alert">{error}</p>}
    {data?.processing.warning && <p className="evidence-warning">{data.processing.warning}</p>}
    {data && !revisionId && <div className="evidence-controls">
      {["queued", "running"].includes(data.processing.state)
        ? <><span>Original saved. Processing continues in the background.</span><button disabled={busy} onClick={() => void action(() => knowledgeApi.cancelSourceProcessing(sourceId))}>Cancel processing</button></>
        : <button disabled={busy} onClick={() => void action(() => knowledgeApi.reprocessSource(sourceId))}>Reprocess source</button>}
    </div>}
    {topics.length > 0 && <label className="evidence-topic-picker">File evidence under<select aria-label="Evidence topic" value={topicId} onChange={(event) => setTopicId(event.target.value)}><option value="">Choose a topic</option>{topics.map((topic) => <option key={topic.id} value={topic.id}>{topic.title}</option>)}</select></label>}
    <div className="structured-blocks">{data?.blocks.map((block) => <article key={block.id} className={`structured-block is-${block.kind}`}>
      <small>{block.headings.join(" / ") || block.locator}</small>
      {block.kind === "heading" ? <h4>{block.content}</h4> : <p>{block.content}</p>}
      <footer>{assetId && block.anchor.page ? <button onClick={() => setViewing(block)}>Open page {block.anchor.page}</button> : <span>{block.locator}</span>}
        {baseId && topicId && block.kind !== "heading" && <button disabled={busy || linked.includes(`${topicId}:${block.id}`)} onClick={() => void action(async () => {
          await knowledgeApi.linkTopicEvidence(baseId, topicId, block.id);
          setLinked((items) => [...items, `${topicId}:${block.id}`]);
          window.dispatchEvent(new CustomEvent("gunther:topics-updated"));
        })}>{linked.includes(`${topicId}:${block.id}`) ? "Linked" : "Link to topic"}</button>}
      </footer>
    </article>)}</div>
    {data?.nextOffset != null && <button disabled={busy} onClick={() => void more()}>Load more evidence</button>}
    {data && data.blocks.length === 0 && <p>No readable blocks yet. The original is preserved.</p>}
    {viewing && <EvidencePanel
      citation={{ id: `view-${viewing.id}`, kind: "library", sourceId, sourceTitle: viewing.headings[0] ?? "Source", assertionId: null, quote: viewing.content, locator: viewing.locator, status: "verified", confidence: 1, blockId: viewing.id, sourceRevisionId: data?.revisionId ?? null, anchor: viewing.anchor }}
      onClose={() => setViewing(null)} />}
  </section>;
}
