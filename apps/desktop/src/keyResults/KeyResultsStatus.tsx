import type { KeyResultJob } from "@gunther/contracts";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../api";
import { openSettings } from "../components/SettingsLayout";
import { MODELS_CHANGED_EVENT } from "../models/askModel";
import { KEY_RESULTS_CHANGED_EVENT, keyResultsChanged, type KeyResultsChange } from "./events";
import "./keyResults.css";

const POLL_MS = 2_500;
const isChecking = (job: KeyResultJob) => job.state === "queued" || job.state === "running";

export function KeyResultsStatus({ baseId, initial, visible = true }: {
  baseId: string;
  initial: KeyResultJob;
  visible?: boolean;
}) {
  const [loaded, setLoaded] = useState<{ initial: KeyResultJob; current: KeyResultJob }>();
  const [busy, setBusy] = useState(false);
  const [undone, setUndone] = useState(false);
  const [error, setError] = useState("");
  const job = loaded?.initial === initial ? loaded.current : initial;
  const checking = isChecking(job);
  const state = job.state;

  useEffect(() => {
    if (busy) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let refresh: AbortController | undefined;
    const read = async (signal: AbortSignal) => {
      const current = await knowledgeApi.keyResultOrigin(baseId, initial, signal);
      if (signal.aborted) return false;
      setLoaded({ initial, current });
      if (state !== "done" && current.state === "done") keyResultsChanged(baseId);
      return isChecking(current);
    };
    const poll = () => {
      timer = setTimeout(async () => {
        const pending = await read(controller.signal).catch(() => true);
        if (pending && !controller.signal.aborted) poll();
      }, POLL_MS);
    };
    const readOnce = () => {
      refresh?.abort();
      refresh = new AbortController();
      void read(refresh.signal).catch(() => undefined);
    };
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<KeyResultsChange>).detail;
      if (state === "done" && detail?.baseId === baseId && detail.countsChanged) readOnce();
    };
    const models = () => { if (state === "needs_model") readOnce(); };
    if (visible && checking) poll();
    window.addEventListener(KEY_RESULTS_CHANGED_EVENT, changed);
    window.addEventListener(MODELS_CHANGED_EVENT, models);
    return () => {
      controller.abort();
      refresh?.abort();
      clearTimeout(timer);
      window.removeEventListener(KEY_RESULTS_CHANGED_EVENT, changed);
      window.removeEventListener(MODELS_CHANGED_EVENT, models);
    };
  }, [baseId, busy, checking, initial, state, visible]);

  const change = async (action: "undo" | "retry") => {
    setBusy(true);
    setError("");
    try {
      const current = action === "undo"
        ? await knowledgeApi.undoKeyResults(baseId, job)
        : await knowledgeApi.retryKeyResults(baseId, job.id);
      setLoaded({ initial, current });
      if (action === "undo") {
        setUndone(true);
        keyResultsChanged(baseId, true);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update key results.");
    } finally {
      setBusy(false);
    }
  };

  if (state === "done" && job.keptCount === 0 && !undone) return null;
  return <span className="key-results-status" role="status" onClick={(event) => event.stopPropagation()}>
    {checking && "Checking key results…"}
    {state === "needs_model" && <>Key results need an Analysis model
      <button type="button" className="contents-inline-btn" onClick={() => openSettings("models")}>Open Models settings</button>
    </>}
    {state === "failed" && <>{job.error || "Key results could not be checked."}
      <button type="button" className="contents-inline-btn" onClick={() => void change("retry")} disabled={busy}>Retry</button>
    </>}
    {state === "done" && (job.keptCount > 0 ? <>
      <span>✓ {job.keptCount} key {job.keptCount === 1 ? "result" : "results"} kept</span><span aria-hidden="true">·</span>
      <button type="button" className="contents-inline-btn" onClick={() => void change("undo")} disabled={busy}>Undo</button>
    </> : "Key results undone")}
    {error && <span role="alert">{error}</span>}
  </span>;
}
