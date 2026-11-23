import type { ContentsSource, SourceDigestState } from "@gunther/contracts";
import { ArrowUpRight } from "lucide-react";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../api";
import { PaperCard } from "../items/views/PaperCard";
import { kindIcon } from "./ContentsRow";
import { addedOn, fileDetail, recordingLength } from "./contentsTree";

const message = (reason: unknown, fallback: string) => reason instanceof Error ? reason.message : fallback;

export function SourceSummary({ source, onOpen }: { source: ContentsSource; onOpen: () => void }) {
  const Icon = kindIcon[source.kind];
  const file = fileDetail(source);
  return <article className="library-reading" aria-label="Source summary">
    <header className="library-reading-heading">
      <span className={`gx-kind-icon kind-${source.kind}`}><Icon size={16} /></span>
      <h1>{source.title}</h1>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onOpen}>
        <ArrowUpRight size={14} />Open full page
      </button>
    </header>
    <dl className="contents-facts">
      <div><dt>Added</dt><dd>{addedOn(source)}</dd></div>
      {source.durationSeconds ? <div><dt>Length</dt><dd>{recordingLength(source.durationSeconds)}</dd></div> : null}
      {file && <div><dt>File</dt><dd>{file}</dd></div>}
      {source.host && <div><dt>Site</dt><dd>{source.host}</dd></div>}
      <div><dt>Status</dt><dd>{source.processing === "reading" ? "Being read…" : source.processing === "failed" ? "Couldn’t be read" : "Ready"}</dd></div>
    </dl>
    <h2>Summary</h2>
    <Overview key={source.id} id={source.id} />
    {source.kind === "paper" && <PaperCard sourceId={source.id} revisionId={null} />}
  </article>;
}

/** The source's written summary, or plainly why there is none (no model, still reading, failed). */
function Overview({ id }: { id: string }) {
  const [state, setState] = useState<SourceDigestState | null>(null);
  const [failed, setFailed] = useState("");
  const [asking, setAsking] = useState(false);
  useEffect(() => {
    let active = true;
    void knowledgeApi.sourceDigest(id).then((next) => { if (active) setState(next); }).catch((reason) => { if (active) setFailed(message(reason, "The summary could not be loaded.")); });
    return () => { active = false; };
  }, [id]);
  const write = async () => {
    setAsking(true); setFailed("");
    try { setState(await knowledgeApi.writeSourceDigest(id)); } catch (reason) { setFailed(message(reason, "The summary could not be started.")); } finally { setAsking(false); }
  };
  if (failed) return <p className="contents-panel-quiet" role="alert">{failed}</p>;
  if (!state) return <p className="contents-panel-quiet">Loading…</p>;
  if (state.digest && state.digest.overview) {
    return <div className="contents-summary">
      <p>{state.digest.overview}</p>
      <small>{state.digest.engine ? `Written by ${state.digest.engine}` : "Written by a model"}{state.stale ? " · the source changed since" : ""}</small>
    </div>;
  }
  if (state.state === "reading") return <p className="contents-panel-quiet">Gunther is still reading this source.</p>;
  if (state.state === "writing") return <p className="contents-panel-quiet">The summary is being written…</p>;
  if (state.state === "off") return <p className="contents-panel-quiet">{state.offReason === "no_key" ? "Summaries need a model. Add a key in Settings → Models." : "Summaries are turned off in Settings."}</p>;
  return <div className="contents-panel-quiet">
    <p>{state.state === "failed" ? `The summary could not be written${state.error ? `: ${state.error}` : "."}` : "No summary yet."}</p>
    <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={asking} onClick={() => void write()}>{state.state === "failed" ? "Try again" : "Write a summary"}</button>
  </div>;
}
