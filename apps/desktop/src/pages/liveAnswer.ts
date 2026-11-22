import type { AgentStep, ResearchUsed } from "@gunther/contracts";
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
