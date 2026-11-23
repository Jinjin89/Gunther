import type { ArtifactSummary, ConversationCitation, KeyResult, KeyResultEvidence, PendingSavedAnswer } from "@gunther/contracts";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useActivity } from "../activity/useActivity";
import { knowledgeApi } from "../api";
import type { Notify } from "../App";
import { KEY_RESULTS_CHANGED_EVENT, keyResultsChanged, type KeyResultsChange } from "./events";
import "./keyResults.css";

const SHORT_LIST = 5;
const dateLabel = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" });

function originLabel(origin: KeyResult["origin"], outputs: readonly ArtifactSummary[]): string {
  if (origin.kind === "output") {
    const label = outputs.find((output) => output.id === origin.id)?.outputType ?? "Output";
    return origin.title ? `from ${label}: ${origin.title}` : `from ${label}`;
  }
  const date = new Date(origin.createdAt);
  const when = Number.isNaN(date.getTime()) ? "" : `, ${dateLabel.format(date)}`;
  return origin.kind === "answer" ? `from your question${when}` : `from a saved answer${when}`;
}

function asCitation(passage: KeyResultEvidence): ConversationCitation {
  return {
    id: passage.id, kind: "library", sourceId: passage.sourceId, sourceTitle: passage.sourceTitle ?? "Source",
    sourceRevisionId: passage.revisionId, blockId: passage.blockId, locator: passage.locator ?? "",
    quote: passage.quote, assertionId: null, status: "verified", confidence: 1,
  };
}

export function KeyResultsGroup({ baseId, filter, outputs, sourceIds, onInspect, onNotify }: {
  baseId: string;
  filter: string;
  outputs: readonly ArtifactSummary[];
  sourceIds?: readonly string[] | undefined;
  onInspect: (passages: ConversationCitation[]) => void;
  onNotify: Notify;
}) {
  const [loaded, setLoaded] = useState<{
    baseId: string;
    sourceIds: readonly string[] | undefined;
    results: KeyResult[];
  }>();
  const results = loaded?.baseId === baseId && loaded.sourceIds === sourceIds
    ? loaded.results : undefined;
  const [pending, setPending] = useState<PendingSavedAnswer[]>([]);
  const [expanded, setExpanded] = useState<boolean>();
  const [revision, setRevision] = useState(0);
  const [loadFailed, setLoadFailed] = useState(false);
  const [removing, setRemoving] = useState(false);
  const listId = useId();
  const { items } = useActivity();
  const completed = items.filter((item) => item.kind === "key_results" && item.state === "done"
    && item.open?.view === "library" && item.open.libraryId === baseId)
    .map((item) => `${item.id}:${item.endedAt}`).sort().join("|");

  useEffect(() => {
    const refresh = () => setRevision((value) => value + 1);
    const change = (event: Event) => {
      if ((event as CustomEvent<KeyResultsChange>).detail?.baseId === baseId) refresh();
    };
    window.addEventListener(KEY_RESULTS_CHANGED_EVENT, change);
    window.addEventListener("gunther:sources-updated", refresh);
    return () => {
      window.removeEventListener(KEY_RESULTS_CHANGED_EVENT, change);
      window.removeEventListener("gunther:sources-updated", refresh);
    };
  }, [baseId]);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.allSettled([
      knowledgeApi.keyResults(baseId, controller.signal, sourceIds),
      knowledgeApi.pendingSavedAnswers(baseId, controller.signal),
    ]).then(([kept, saved]) => {
      if (controller.signal.aborted) return;
      setLoaded(kept.status === "fulfilled" ? { baseId, sourceIds, results: kept.value } : undefined);
      if (saved.status === "fulfilled") setPending(saved.value);
      setLoadFailed(kept.status === "rejected" || saved.status === "rejected");
    });
    return () => controller.abort();
  }, [baseId, completed, revision, sourceIds]);

  const remove = async (result: KeyResult) => {
    setRemoving(true);
    try {
      const { removedAt } = await knowledgeApi.removeKeyResult(baseId, result.id);
      setLoaded((current) => current && {
        ...current, results: current.results.filter((row) => row.id !== result.id),
      });
      keyResultsChanged(baseId, true);
      onNotify("Key result removed.", {
        label: "Undo",
        run: () => {
          void knowledgeApi.restoreKeyResult(baseId, result.id, removedAt).then(() => {
            keyResultsChanged(baseId, true);
            onNotify("Key result restored.");
          }).catch((reason: unknown) => onNotify(
            reason instanceof Error ? reason.message : "The key result could not be restored.",
          ));
        },
      });
    } catch (reason) {
      keyResultsChanged(baseId);
      onNotify(reason instanceof Error ? reason.message : "The key result could not be removed.");
    } finally {
      setRemoving(false);
    }
  };

  const query = filter.trim().toLocaleLowerCase();
  const shown = results?.filter((item) => item.text.toLocaleLowerCase().includes(query)) ?? [];
  const open = expanded ?? (Boolean(query) || (results?.length ?? 0) <= SHORT_LIST);
  if (!results && pending.length === 0 && !loadFailed) return null;
  return <section className="key-results-group" aria-label="Key results">
    {results && <button type="button" aria-expanded={open} aria-controls={listId} onClick={() => setExpanded(!open)}>
      {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}Key results ({results.length})
    </button>}
    {results && <div id={listId} hidden={!open}>
      {open && query && results.length > 0 && shown.length === 0 && <p className="key-results-empty">No matching key results.</p>}
      {open && shown.length > 0 && <ul>{shown.map((result) => <li key={result.id} className="key-results-row">
        <button type="button" className="key-results-inspect" data-opens-evidence disabled={result.evidence.length === 0}
          onClick={() => onInspect(result.evidence.map(asCitation))}>
          <span>{result.text}</span><small>{originLabel(result.origin, outputs)}</small>
        </button>
        <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={removing} onClick={() => void remove(result)}>Remove</button>
      </li>)}</ul>}
    </div>}
    {pending.length > 0 && <details className="key-results-saved">
      <summary>Saved answers awaiting key results</summary>
      <ul>{pending.map((answer) => <li key={answer.id}><strong>{answer.title}</strong><p>{answer.content}</p></li>)}</ul>
    </details>}
    {loadFailed && <p className="key-results-empty" role="status">Could not load all key results.{" "}
      <button type="button" className="contents-inline-btn" onClick={() => setRevision((value) => value + 1)}>Retry</button>
    </p>}
  </section>;
}
