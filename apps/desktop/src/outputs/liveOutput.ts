import type { BuildTurnInput } from "@gunther/contracts";
import { useCallback, useRef, useState } from "react";
import type { ArtifactTurnEvent, OutputEvent } from "../api";

type SectionEvent = Extract<OutputEvent, { type: "section" }>;
type Running = { kind: "stage" | "action" | "section" | "step"; id: string; label: string };

interface LiveOutputState {
  title: string | null;
  headings: string[];
  running: Running[];
  reply: string;
}

export const EMPTY_LIVE: LiveOutputState = { title: null, headings: [], running: [], reply: "" };
const sectionLabels: Record<Exclude<SectionEvent["state"], "done">, string> = {
  researching: "Reading sources", writing: "Writing", checking: "Checking sources", revising: "Revising",
};

export function outputActivity(live: LiveOutputState): string {
  return [...live.running].reverse().find((item) => item.kind === "stage")?.label ?? live.running.at(-1)?.label ?? "Starting…";
}

export function applyOutputEvent(state: LiveOutputState, event: ArtifactTurnEvent): LiveOutputState {
  const update = (kind: Running["kind"], id: string, label: string | null): LiveOutputState => {
    const running = state.running.filter((item) => item.kind !== kind || item.id !== id);
    return { ...state, running: label ? [...running, { kind, id, label }] : running };
  };
  switch (event.type) {
    case "outline":
      return { ...state, title: event.title, headings: event.sections.map((section) => section.heading) };
    case "stage":
      return update("stage", event.id, event.state === "running" ? [event.label, event.detail].filter(Boolean).join(" · ") : null);
    case "action":
      return update("action", event.id, event.state === "running" ? event.label : null);
    case "section":
      return update("section", String(event.index), event.state === "done" ? null : [sectionLabels[event.state], state.headings[event.index]].filter(Boolean).join(" · "));
    case "step":
      return update("step", `${event.section}:${event.tool}:${event.label}`, event.state === "running" ? event.label : null);
    case "reply":
      return { ...state, reply: state.reply + event.text };
    default:
      return state;
  }
}

interface OutputJob {
  kind: "turn" | "version" | "follow";
  title?: string | undefined;
  lineageId?: string | undefined;
  artifactId?: string | undefined;
  build?: BuildTurnInput | undefined;
  startedAt: string;
  stop: () => Promise<void>;
  live: LiveOutputState;
  stopping: boolean;
}

/** Keep one listener per conversation. Switching views leaves the other runs listening. */
export function useLiveOutputs() {
  const listeners = useRef(new Map<string, { abort: AbortController; ready: Promise<boolean>; visible: boolean; job: OutputJob }>());
  const [jobs, setJobs] = useState(new Map<string, OutputJob>());
  const publish = useCallback((id: string, job: OutputJob) => {
    setJobs((current) => new Map(current).set(id, job));
  }, []);
  const watch = useCallback((id: string, info: Omit<OutputJob, "live" | "stopping" | "startedAt">) => {
    const previous = listeners.current.get(id);
    if (previous?.visible || (previous && info.kind === "follow")) return null;
    previous?.abort.abort();
    let started!: (value: boolean) => void;
    const ready = new Promise<boolean>((resolve) => { started = resolve; });
    // A Follow that returns 204 should never show a running job.
    const listener = {
      abort: new AbortController(), ready, visible: info.kind !== "follow",
      job: { ...info, startedAt: new Date().toISOString(), live: EMPTY_LIVE, stopping: false },
    };
    listeners.current.set(id, listener);
    if (listener.visible) publish(id, listener.job);
    const { signal } = listener.abort;
    signal.addEventListener("abort", () => started(false), { once: true });
    return {
      signal,
      hear(event: ArtifactTurnEvent) {
        if (signal.aborted) return;
        started(true);
        listener.visible = true;
        listener.job = { ...listener.job, live: applyOutputEvent(listener.job.live, event) };
        publish(id, listener.job);
      },
      finish() {
        started(false);
        if (listeners.current.get(id) !== listener) return;
        listeners.current.delete(id);
        if (!listener.visible) return;
        setJobs((current) => {
          const next = new Map(current);
          next.delete(id);
          return next;
        });
      },
    };
  }, [publish]);
  const stop = async (id: string) => {
    const listener = listeners.current.get(id);
    if (!listener?.visible || listener.job.stopping) return;
    listener.job = { ...listener.job, stopping: true };
    publish(id, listener.job);
    try {
      // A turn can spend time saving its conversation before the server starts the run.
      if (await listener.ready && listeners.current.get(id) === listener) await listener.job.stop();
    } catch (reason) {
      if (listeners.current.get(id) === listener) {
        listener.job = { ...listener.job, stopping: false };
        publish(id, listener.job);
      }
      throw reason;
    }
  };
  const reset = useCallback(() => {
    for (const listener of listeners.current.values()) listener.abort.abort();
    listeners.current.clear();
    setJobs(new Map());
  }, []);
  return { jobs, watch, stop, reset };
}
