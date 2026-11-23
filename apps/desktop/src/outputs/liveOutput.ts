import type { ArtifactBriefState, OutlineItem, OutputApproach, StudioMemo, StudioReview } from "@gunther/contracts";
import { useCallback, useRef, useState } from "react";
import type { ArtifactTurnEvent, OutputEvent } from "../api";
import type { LiveStep } from "../pages/liveAnswer";

export type SectionState = "waiting" | "researching" | "writing" | "checking" | "revising" | "done";

/** A step of the skill an output follows, as it goes. */
export interface LiveStage {
  id: string;
  label: string;
  state: "running" | "done" | "failed";
  detail: string | null;
  /** How long it took, once finished. */
  seconds: number | null;
}

/** One section (or slide) of an output being built: where it is and what it says so far. */
export interface LiveSection {
  heading: string;
  goal: string;
  state: SectionState;
  /** As written, with the model's own numbers: they are not final until the whole text is saved. */
  text: string;
  steps: LiveStep[];
}

/** One thing the Artifacts agent does in a turn (a search, writing a slide, a check), as it goes. */
export interface LiveAction {
  id: string;
  /** What kind of action it is: search_library, write, edit, … (known from the decision that listed it). */
  action: string;
  label: string;
  note: string;
  state: "waiting" | "running" | "done" | "failed" | "skipped";
}

/** An output being worked out: the plan, then each section as it is researched, written and checked. */
export interface LiveOutputState {
  /** What it was asked, when following a build again after leaving. */
  question: string | null;
  title: string | null;
  sections: LiveSection[];
  /** Searches that belong to no one section (a revision of the whole output, or reading the material). */
  steps: LiveStep[];
  /** The skill's steps so far, in order (none when no skill is followed). */
  stages: LiveStage[];
  /** What the skill decided before planning. */
  approach: OutputApproach | null;
  /** The studio's insight memo, when it arrives. */
  memo: StudioMemo | null;
  /** The critic's looks at the storyline, one per round. */
  reviews: StudioReview[];
  /** A turn of the Artifacts agent: the actions it decided on so far, in order. */
  actions: LiveAction[];
  /** The text of an answer or reply as it is written. */
  reply: string;
  /** The question the agent asked: it ends the turn. */
  asking: { text: string; options: string[] } | null;
  /** The brief, when the turn changed it. */
  brief: ArtifactBriefState | null;
  /** Which conversation the turn belongs to (told first, so a follower can find it). */
  turn: { conversationId: string; messageId: string } | null;
}

export const EMPTY_LIVE: LiveOutputState = { question: null, title: null, sections: [], steps: [], stages: [], approach: null, memo: null, reviews: [], actions: [], reply: "", asking: null, brief: null, turn: null };

const DOING: Record<SectionState, string> = { waiting: "Waiting", researching: "Reading sources", writing: "Writing", checking: "Checking sources", revising: "Revising", done: "Done" };

/** What the agent is doing now, in one line. Labels come only from stream events. */
export function outputActivity(live: LiveOutputState | null): string {
  if (!live) return "Ready";
  const action = [...live.actions].reverse().find((item) => item.state === "running");
  if (action) return action.label;
  const stage = [...live.stages].reverse().find((item) => item.state === "running");
  const section = [...live.sections].reverse().find((item) => item.state !== "waiting" && item.state !== "done");
  const step = [...live.steps].reverse().find((item) => item.running);
  if (stage) return stage.detail ? `${stage.label} · ${stage.detail}` : stage.label;
  return (section ? `${DOING[section.state]}${section.heading ? ` · ${section.heading}` : ""}` : step?.label) || "Starting…";
}
const blank = (): LiveSection => ({ heading: "", goal: "", state: "waiting", text: "", steps: [] });

/** The same step, updated: a running search is replaced by its result. */
function withStep(steps: LiveStep[], event: Extract<OutputEvent, { type: "step" }>): LiveStep[] {
  const step: LiveStep = {
    tool: event.tool,
    label: event.label,
    query: event.query ?? "",
    found: event.found ?? 0,
    error: event.error ?? null,
    running: event.state === "running",
  };
  const at = steps.findIndex((item) => item.running && item.label === event.label);
  return at === -1 ? [...steps, step] : steps.map((item, index) => (index === at ? step : item));
}

/** The sections, grown to hold section `index`. */
function reaching(sections: LiveSection[], index: number): LiveSection[] {
  return index < sections.length ? sections : [...sections, ...Array.from({ length: index + 1 - sections.length }, blank)];
}

/** The action with this id, updated or added. */
function withAction(actions: LiveAction[], next: Partial<LiveAction> & { id: string }): LiveAction[] {
  const at = actions.findIndex((item) => item.id === next.id);
  if (at === -1) return [...actions, { action: "", label: "", note: "", state: "waiting", ...next }];
  return actions.map((item, index) => (index === at ? { ...item, ...next } : item));
}

export function applyOutputEvent(state: LiveOutputState, event: ArtifactTurnEvent): LiveOutputState {
  const change = (index: number, edit: (section: LiveSection) => LiveSection): LiveOutputState => ({
    ...state,
    sections: reaching(state.sections, index).map((section, at) => (at === index ? edit(section) : section)),
  });
  switch (event.type) {
    case "resumed":
      return { ...state, question: event.question };
    case "outline":
      return {
        ...state,
        title: event.title,
        sections: event.sections.map((item) => ({ ...blank(), heading: item.heading, goal: item.goal })),
      };
    case "section":
      return change(event.index, (section) => ({ ...section, state: event.state }));
    case "text":
      return change(event.section, (section) => ({ ...section, text: section.text + event.text }));
    case "stage": {
      const stage: LiveStage = { id: event.id, label: event.label, state: event.state, detail: event.detail ?? null, seconds: event.seconds ?? null };
      const at = state.stages.findIndex((item) => item.id === event.id);
      return { ...state, stages: at === -1 ? [...state.stages, stage] : state.stages.map((item, index) => (index === at ? stage : item)) };
    }
    case "memo":
      return { ...state, memo: event.memo };
    case "review":
      return { ...state, reviews: [...state.reviews.filter((item) => item.round !== event.round), { round: event.round, verdict: event.verdict, understood: event.understood, problems: event.problems }] };
    case "approach":
      return { ...state, approach: event.approach };
    case "turn":
      return { ...state, turn: { conversationId: event.conversationId, messageId: event.messageId } };
    case "brief":
      return { ...state, brief: { brief: event.brief, spec: event.spec } };
    case "decision":
      return { ...state, actions: event.actions.reduce((all, item) => withAction(all, { id: item.id, action: item.do, label: item.label }), state.actions) };
    case "action":
      return { ...state, actions: withAction(state.actions, { id: event.id, label: event.label, note: event.note, state: event.state }) };
    case "question":
      return { ...state, asking: { text: event.text, options: event.options } };
    case "reply":
      return { ...state, reply: state.reply + event.text };
    case "step":
      if (event.section === null) return { ...state, steps: withStep(state.steps, event) };
      return change(event.section, (section) => ({ ...section, steps: withStep(section.steps, event) }));
    // The studio's storyline arrives again as the outline; any event not known here changes nothing.
    default:
      return state;
  }
}

interface OutputJob {
  kind: "turn" | "version" | "follow";
  turnId: string;
  text: string;
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
  const watch = useCallback((id: string, info: Omit<OutputJob, "live" | "stopping">) => {
    const previous = listeners.current.get(id);
    if (previous?.visible || (previous && info.kind === "follow")) return null;
    previous?.abort.abort();
    let started!: (value: boolean) => void;
    const ready = new Promise<boolean>((resolve) => { started = resolve; });
    // A Follow that returns 204 should never show a running job.
    const listener = {
      abort: new AbortController(), ready, visible: info.kind !== "follow",
      job: { ...info, live: EMPTY_LIVE, stopping: false },
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
