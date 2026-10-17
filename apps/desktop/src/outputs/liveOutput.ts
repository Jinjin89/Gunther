import type { ArtifactBriefState, OutlineItem, OutputApproach } from "@gunther/contracts";
import { useCallback, useState } from "react";
import type { ArtifactTurnEvent, OutputEvent } from "../api";
import type { LiveStep } from "../pages/liveAnswer";

export type SectionState = "waiting" | "researching" | "writing" | "checking" | "revising" | "done";

/** A step of the skill an output follows, as it goes. */
export interface LiveStage {
  id: string;
  label: string;
  state: "running" | "done" | "failed";
  detail: string | null;
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

export const EMPTY_LIVE: LiveOutputState = { question: null, title: null, sections: [], steps: [], stages: [], approach: null, actions: [], reply: "", asking: null, brief: null, turn: null };
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
      const stage: LiveStage = { id: event.id, label: event.label, state: event.state, detail: event.detail ?? null };
      const at = state.stages.findIndex((item) => item.id === event.id);
      return { ...state, stages: at === -1 ? [...state.stages, stage] : state.stages.map((item, index) => (index === at ? stage : item)) };
    }
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
  }
}

/**
 * Live progress for one build or revision at a time: `start()`, feed `hear`, then `stop()`.
 * A revision starts from the sections the version already has.
 */
export function useLiveOutput() {
  const [live, setLive] = useState<LiveOutputState | null>(null);
  const start = useCallback((seed?: { title: string; sections: OutlineItem[] }) => {
    setLive(seed ? applyOutputEvent(EMPTY_LIVE, { type: "outline", title: seed.title, sections: seed.sections }) : EMPTY_LIVE);
  }, []);
  const stop = useCallback(() => setLive(null), []);
  const hear = useCallback((event: ArtifactTurnEvent) => setLive((current) => applyOutputEvent(current ?? EMPTY_LIVE, event)), []);
  return { live, start, stop, hear };
}
