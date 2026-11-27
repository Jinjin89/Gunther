import type { KeyResultJob } from "@gunther/contracts";
import { useCallback, useEffect, useRef, useState } from "react";
import { knowledgeApi } from "../api";
import { openSettings } from "../components/SettingsLayout";
import { MODELS_CHANGED_EVENT } from "../models/askModel";
import { KEY_RESULTS_CHANGED_EVENT, keyResultsChanged, type KeyResultsChange } from "./events";
import "./keyResults.css";

const POLL_MS = 2_500;
const isChecking = (state: KeyResultJob["state"] | undefined) => state === "queued" || state === "running";

const sameJob = (a: KeyResultJob, b: KeyResultJob) =>
  a.state === b.state && a.keptCount === b.keptCount && a.error === b.error
  && a.keptIds.length === b.keptIds.length && a.keptIds.every((id, index) => id === b.keptIds[index]);

type LocalStatus = {
  origin: { baseId: string; jobId: string | undefined };
  snapshot: KeyResultJob | null | undefined;
  loaded?: KeyResultJob;
  gen: number;
  needsReconcile: boolean;
  busy: boolean;
  undone: boolean;
  error: string;
};

const initialStatus = (baseId: string, jobId: string | undefined, snapshot: KeyResultJob | null | undefined): LocalStatus => ({
  origin: { baseId, jobId }, snapshot, gen: 0, needsReconcile: false, busy: false, undone: false, error: "",
});

export function useKeyResultsStatus(baseId: string, initial: KeyResultJob | null | undefined, visible = true) {
  const jobId = initial?.id;
  const originKind = initial?.originKind;
  const originId = initial?.originId;
  const [local, setLocal] = useState(() => initialStatus(baseId, jobId, initial));
  const { origin, snapshot, loaded, gen, needsReconcile, busy, undone, error } = local;
  if (origin.baseId !== baseId || origin.jobId !== jobId) {
    setLocal(initialStatus(baseId, jobId, initial));
  } else if (snapshot !== initial) {
    setLocal((current) => {
      if (current.origin !== origin || current.snapshot === initial) return current;
      const changed = !current.snapshot || !initial || !sameJob(current.snapshot, initial);
      const pending = Boolean(initial && (current.busy || (current.loaded && !sameJob(current.loaded, initial))));
      const nextGen = changed || (pending && !current.needsReconcile) ? current.gen + 1 : current.gen;
      return { ...current, snapshot: initial, gen: nextGen, needsReconcile: pending };
    });
  }
  const changing = useRef<LocalStatus["origin"] | null>(null);
  const latest = useRef({ origin, gen, loaded, initial });
  latest.current = { origin, gen, loaded, initial };
  const update = useCallback((changes: Partial<Omit<LocalStatus, "origin">>) => {
    setLocal((current) => current.origin === origin ? { ...current, ...changes } : current);
  }, [origin]);
  const job = loaded ?? initial;
  const state = job?.state;
  const committed = useRef<{ origin: LocalStatus["origin"]; state: KeyResultJob["state"] | undefined }>({ origin, state });
  useEffect(() => {
    const previous = committed.current;
    committed.current = { origin, state };
    if (previous.origin === origin && previous.state !== "done" && state === "done") keyResultsChanged(baseId);
  }, [baseId, origin, state]);

  useEffect(() => {
    if (!originKind || !originId || busy) return;
    let active = true;
    let request: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = async () => {
      if (changing.current === origin) return;
      request?.abort();
      request = new AbortController();
      const { signal } = request;
      const targetGen = gen;
      const current = await knowledgeApi.keyResultOrigin(baseId, { originKind, originId }, signal);
      if (signal.aborted || !active || latest.current.origin !== origin || latest.current.gen !== targetGen) return;
      setLocal((prev) => prev.origin === origin && prev.gen === targetGen
        ? { ...prev, loaded: current, needsReconcile: false } : prev);
      return current.state;
    };
    const poll = () => {
      timer = setTimeout(async () => {
        const next = await read().catch(() => state);
        if (active && next === state && !request?.signal.aborted) poll();
      }, POLL_MS);
    };
    const readOnce = () => { void read().catch(() => undefined); };
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<KeyResultsChange>).detail;
      if (state === "done" && detail?.baseId === baseId && detail.countsChanged) readOnce();
    };
    const models = () => { if (state === "needs_model") readOnce(); };
    const { loaded: currentLoaded, initial: currentInitial } = latest.current;
    const needsSettle = Boolean(needsReconcile && currentLoaded && currentInitial && !sameJob(currentLoaded, currentInitial));
    if (needsReconcile && !needsSettle) {
      update({ needsReconcile: false });
    }
    const settle = () => {
      void read().then(
        (next) => { if (active && visible && isChecking(next ?? state) && next === state) poll(); },
        () => { if (active && visible && !request?.signal.aborted) timer = setTimeout(settle, POLL_MS); },
      );
    };
    if (visible && needsSettle) settle();
    else if (visible && isChecking(state)) poll();
    window.addEventListener(KEY_RESULTS_CHANGED_EVENT, changed);
    window.addEventListener(MODELS_CHANGED_EVENT, models);
    return () => {
      active = false;
      request?.abort();
      clearTimeout(timer);
      window.removeEventListener(KEY_RESULTS_CHANGED_EVENT, changed);
      window.removeEventListener(MODELS_CHANGED_EVENT, models);
    };
  }, [baseId, busy, gen, needsReconcile, origin, originId, originKind, state, update, visible]);

  const change = async (action: "undo" | "retry") => {
    if (!job || !initial || changing.current === origin) return;
    changing.current = origin;
    update({ busy: true, error: "" });
    try {
      const current = action === "undo"
        ? await knowledgeApi.undoKeyResults(baseId, job)
        : await knowledgeApi.retryKeyResults(baseId, job.id);
      update({ loaded: current });
      if (action === "undo") {
        update({ undone: true });
        keyResultsChanged(baseId, true);
      }
    } catch (reason) {
      update({ error: reason instanceof Error ? reason.message : "Could not update key results." });
    } finally {
      if (changing.current === origin) changing.current = null;
      update({ busy: false });
    }
  };

  return {
    job, busy, undone, error,
    visibleStatus: Boolean(job && !(state === "done" && job.keptCount === 0 && !undone && !error)),
    needsAttention: Boolean(job && (isChecking(state) || state === "failed" || state === "needs_model" || error)),
    retry: () => change("retry"), undo: () => change("undo"),
  };
}

export function KeyResultsStatusView({ status }: { status: ReturnType<typeof useKeyResultsStatus> }) {
  const { job, busy, undone, error, visibleStatus, retry, undo } = status;
  if (!job || !visibleStatus) return null;
  const state = job.state;
  return <span className="key-results-status" role="status" onClick={(event) => event.stopPropagation()}>
    {isChecking(state) && "Checking key results…"}
    {state === "needs_model" && <>Key results need an Analysis model
      <button type="button" className="contents-inline-btn" onClick={() => openSettings("models")}>Open Models settings</button>
    </>}
    {state === "failed" && <>{job.error || "Key results could not be checked."}
      <button type="button" className="contents-inline-btn" onClick={() => void retry()} disabled={busy}>Retry</button>
    </>}
    {state === "done" && (job.keptCount > 0 ? <>
      <span>✓ {job.keptCount} key {job.keptCount === 1 ? "result" : "results"} kept</span><span aria-hidden="true">·</span>
      <button type="button" className="contents-inline-btn" onClick={() => void undo()} disabled={busy}>Undo</button>
    </> : undone && "Key results undone")}
    {error && <span role="alert">{error}</span>}
  </span>;
}

export function KeyResultsStatus({ baseId, initial, visible = true }: {
  baseId: string;
  initial: KeyResultJob;
  visible?: boolean;
}) {
  const status = useKeyResultsStatus(baseId, initial, visible);
  return <KeyResultsStatusView status={status} />;
}
