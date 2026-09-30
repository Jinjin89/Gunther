import type { AgentStep } from "@gunther/contracts";
import { useCallback, useState } from "react";
import type { AnswerEvent } from "../api";

export interface LiveStep extends AgentStep {
  running?: boolean;
}

/** An answer being worked out: what the agent is doing and the text so far. */
export interface LiveAnswerState {
  steps: LiveStep[];
  text: string;
}

const EMPTY: LiveAnswerState = { steps: [], text: "" };

export function applyAnswerEvent(state: LiveAnswerState, event: AnswerEvent): LiveAnswerState {
  if (event.type === "text") return { ...state, text: state.text + event.text };
  const step: LiveStep = {
    tool: event.tool,
    label: event.label,
    query: event.query ?? "",
    found: event.found ?? 0,
    error: event.error ?? null,
    running: event.state === "running",
  };
  const at = state.steps.findIndex((item) => item.running && item.label === event.label);
  return { ...state, steps: at === -1 ? [...state.steps, step] : state.steps.map((item, index) => index === at ? step : item) };
}

/** Live progress for one question at a time: `start()`, feed `hear`, then `stop()`. */
export function useLiveAnswer() {
  const [live, setLive] = useState<LiveAnswerState | null>(null);
  const start = useCallback(() => setLive(EMPTY), []);
  const stop = useCallback(() => setLive(null), []);
  const hear = useCallback((event: AnswerEvent) => setLive((current) => applyAnswerEvent(current ?? EMPTY, event)), []);
  return { live, start, stop, hear };
}
