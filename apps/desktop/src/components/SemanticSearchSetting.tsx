import type { RetrievalStatus } from "@gunther/contracts";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../api";

const REFRESH_WHILE_INDEXING_MS = 5_000;
const count = (value: number) => value.toLocaleString();

/** Whether search by meaning is on, how far indexing has got, and why it is off. */
export function SemanticSearchSetting() {
  const [status, setStatus] = useState<RetrievalStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const indexing = Boolean(status?.semanticConfigured && status.embeddingJobs > 0);

  useEffect(() => {
    let active = true;
    const load = () => knowledgeApi.retrievalStatus().then((next) => {
      if (active) { setStatus(next); setUnavailable(false); }
    }).catch(() => {
      if (active) setUnavailable(true);
    });
    void load();
    // Follow a large import's progress; stop once every passage has a vector.
    const timer = indexing ? window.setInterval(() => void load(), REFRESH_WHILE_INDEXING_MS) : undefined;
    return () => { active = false; window.clearInterval(timer); };
  }, [indexing]);

  let detail = "Checking with the local service…";
  let state = "Checking";
  if (unavailable) {
    detail = "Reconnect the local service to see search status.";
    state = "Unavailable";
  } else if (status && !status.semanticConfigured) {
    detail = `Keyword search only. ${status.semanticOffReason ?? ""}`.trim();
    state = "Off";
  } else if (status && indexing) {
    detail = `Indexing ${count(status.embeddedBlocks)} of ${count(status.passages)} passages. Search works meanwhile; new passages join as they finish.`;
    state = "Indexing";
  } else if (status) {
    detail = status.warning
      ? `The last search fell back to keywords. ${status.warning}`
      : `Finds passages by meaning in Chinese and English, on this device. ${count(status.embeddedBlocks)} passages indexed.`;
    state = "On";
  }

  return <div className="setting-row">
    <span><strong>Search by meaning</strong><small>{detail}</small></span>
    <span className={`setting-state ${state === "On" || state === "Indexing" ? "" : "is-muted"}`}><i />{state}</span>
  </div>;
}
