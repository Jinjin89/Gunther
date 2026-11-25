import type { AgentStep } from "@gunther/contracts";

/** Method steps can report errors, but their stored counts are not search results. */
const METHOD_STEPS = ["check", "revise", "frame"];

export function stepResult({ tool, found, error }: AgentStep): number | string | null {
  if (error) return error;
  return METHOD_STEPS.includes(tool) ? null : found;
}
