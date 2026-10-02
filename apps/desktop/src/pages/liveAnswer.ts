import type { AgentStep, ResearchUsed } from "@gunther/contracts";
import { useCallback, useState } from "react";
import type { AnswerEvent } from "../api";

export interface LiveStep extends AgentStep {
  running?: boolean;
}

/** An answer being worked out: what the agent is doing and the text so far. */
export interface LiveAnswerState {
  steps: LiveStep[];
  text: string;
  /** Deep research: what it set out to find, and how much of its budget the latest step had used. */
  research?: {
    coreQuestion: string;
    subQuestions: Array<{ id: string; text: string }>;
    limits: { searches: number; reads: number };
    used?: ResearchUsed;
  };
}

const EMPTY: LiveAnswerState = { steps: [], text: "" };

export function applyAnswerEvent(state: LiveAnswerState, event: AnswerEvent): LiveAnswerState {
  if (event.type === "text") return { ...state, text: state.text + event.text };
  if (event.type === "resumed") return state;
  if (event.type === "research_plan") {
    return { ...state, research: { coreQuestion: event.coreQuestion, subQuestions: event.subQuestions, limits: event.limits } };
  }
  const step: LiveStep = {
    tool: event.tool,
    label: event.label,
    query: event.query ?? "",
    found: event.found ?? 0,
    error: event.error ?? null,
    running: event.state === "running",
  };
  const at = state.steps.findIndex((item) => item.running && item.label === event.label);
  const steps = at === -1 ? [...state.steps, step] : state.steps.map((item, index) => index === at ? step : item);
  const research = event.used && state.research ? { ...state.research, used: event.used } : state.research;
  return { ...state, steps, ...(research ? { research } : {}) };
}

/** Live progress for one question at a time: `start()`, feed `hear`, then `stop()`. */
export function useLiveAnswer() {
  const [live, setLive] = useState<LiveAnswerState | null>(null);
  const start = useCallback(() => setLive(EMPTY), []);
  const stop = useCallback(() => setLive(null), []);
  const hear = useCallback((event: AnswerEvent) => setLive((current) => applyAnswerEvent(current ?? EMPTY, event)), []);
  return { live, start, stop, hear };
}
