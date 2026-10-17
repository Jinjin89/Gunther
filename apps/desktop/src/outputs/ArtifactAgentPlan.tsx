import type { ArtifactConversationMessage } from "@gunther/contracts";
import { Check, ChevronDown, Circle, CircleAlert, ListChecks, LoaderCircle } from "lucide-react";
import { useState } from "react";
import type { LiveOutputState } from "./liveOutput";

const words = { waiting: "Waiting", researching: "Reading sources", writing: "Writing", checking: "Checking sources", revising: "Revising", done: "Done" };
export function outputActivity(live: LiveOutputState | null): string {
  if (!live) return "Ready";
  const action = [...live.actions].reverse().find((item) => item.state === "running");
  if (action) return action.label;
  const stage = [...live.stages].reverse().find((item) => item.state === "running");
  const section = [...live.sections].reverse().find((item) => item.state !== "waiting" && item.state !== "done");
  const step = [...live.steps].reverse().find((item) => item.running);
  return stage?.label || (section ? `${words[section.state]}${section.heading ? ` · ${section.heading}` : ""}` : step?.label) || "Starting…";
}

const ENDED: Record<string, string> = { result: "Saved a new version", answer: "Answered your question", reply: "Replied", question: "Waiting for your answer", paused: "Paused: ask it to continue", stopped: "Stopped", failed: "The request wasn’t handled" };

/** Progress labels come only from stream events and actual stored outcomes. The task list is what the agent does in the turn: its actions. */
export function ArtifactAgentPlan({ live, latest, stopping, onStop }: { live: LiveOutputState | null; latest: ArtifactConversationMessage | null; stopping: boolean; onStop: () => void }) {
  const [expanded, setExpanded] = useState(true);
  const tasks = live?.actions.length ? live.actions.map((item) => ({ label: item.label, detail: item.note, status: item.state === "done" ? "done" : item.state === "failed" || item.state === "skipped" ? "skipped" : item.state === "waiting" ? "waiting" : "current" })) : live?.stages.length ? live.stages.map((item) => ({ label: item.label, detail: item.detail, status: item.state === "done" ? "done" : item.state === "failed" ? "skipped" : "current" })) : live?.sections.map((item) => ({ label: item.heading || "Section", detail: words[item.state], status: item.state === "done" ? "done" : item.state === "waiting" ? "waiting" : "current" })) ?? [];
  const current = outputActivity(live);
  const summary = live ? current : latest ? ENDED[latest.kind] ?? "Ready for your next request" : "Ready for your next request";
  return <section className={`aw-agent-plan${live ? " is-running" : ""}`} aria-label="Agent plan">
    <header><button type="button" className="aw-agent-plan-toggle" aria-label={expanded ? "Collapse agent plan" : "Expand agent plan"} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><ListChecks size={15} /><strong>Agent plan</strong><ChevronDown size={12} /></button><span className="aw-agent-plan-state">{live ? <><LoaderCircle size={11} className="spin" />Working</> : latest?.kind === "result" ? <><Check size={11} />Saved</> : ""}</span></header>
    {expanded && tasks.length > 0 ? <><ol className="aw-agent-plan-steps" aria-label="Agent tasks">{tasks.map((task, index) => <li key={index} className={task.status === "current" ? "is-current" : task.status === "done" ? "is-done" : "is-pending"} aria-current={task.status === "current" ? "step" : undefined}><span className="aw-agent-step-mark">{task.status === "done" ? <Check size={12} /> : task.status === "current" ? <LoaderCircle className="spin" size={12} /> : task.status === "skipped" ? <CircleAlert size={12} /> : <Circle size={11} />}</span><div><strong title={task.label}>{task.label}</strong>{task.status === "current" && task.detail && <small>{task.detail}</small>}{task.status === "skipped" && <small>{task.detail || "Skipped"}</small>}</div>{task.status === "current" && <span className="aw-agent-now">Now</span>}</li>)}</ol><footer><span>{tasks.filter((item) => item.status === "done").length} of {tasks.length} done</span></footer></> : <p className="aw-agent-plan-summary" role="status">{summary}</p>}
    {live && <footer><span>{current}</span><button type="button" disabled={stopping} onClick={onStop} aria-label="Stop agent">{stopping ? "Stopping…" : "Stop"}</button></footer>}
  </section>;
}
