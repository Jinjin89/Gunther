import type { OutlineItem, OutputApproach } from "@gunther/contracts";
import { useCallback, useState } from "react";
import type { OutputEvent } from "../api";
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
}

const EMPTY: LiveOutputState = { question: null, title: null, sections: [], steps: [], stages: [], approach: null };
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

export function applyOutputEvent(state: LiveOutputState, event: OutputEvent): LiveOutputState {
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
    setLive(seed ? applyOutputEvent(EMPTY, { type: "outline", title: seed.title, sections: seed.sections }) : EMPTY);
  }, []);
  const stop = useCallback(() => setLive(null), []);
  const hear = useCallback((event: OutputEvent) => setLive((current) => applyOutputEvent(current ?? EMPTY, event)), []);
  return { live, start, stop, hear };
}
