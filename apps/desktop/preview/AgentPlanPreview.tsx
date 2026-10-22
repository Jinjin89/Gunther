import { Check, ChevronDown, Circle, ListChecks, LoaderCircle, Play, Square } from "lucide-react";
import { useState } from "react";

export interface PreviewAgentPlan {
  /** The active step, or steps.length when the work is complete. */
  step: number;
  state: "running" | "complete" | "stopped";
  target: string;
  steps: Array<{ title: string; detail: string }>;
}

/** A compact activity plan for the design preview; these are sample job events. */
export function AgentPlanPreview({ plan, automatic = false, onNext, onStop, onReplay }: {
  plan: PreviewAgentPlan | null;
  automatic?: boolean;
  onNext: () => void;
  onStop: () => void;
  onReplay: () => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const running = plan?.state === "running";
  const done = plan?.state === "complete";
  const current = plan?.steps[plan.step];
  const summary = !plan ? "Ready for your next request" : done ? "All steps completed" : plan.state === "stopped" ? "Stopped · saved artifacts are available" : current?.title ?? "Working…";
  return <section className={`cw-agent-plan${running ? " is-running" : ""}`} aria-label="Agent plan">
    <header>
      <button type="button" className="cw-agent-plan-toggle" aria-label={expanded ? "Collapse agent plan" : "Expand agent plan"} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <ListChecks size={15} /><strong>Agent plan</strong><ChevronDown size={12} />
      </button>
      <span className="cw-agent-plan-state">{running ? <><LoaderCircle size={11} className="spin" />Working</> : done ? <><Check size={11} />Completed</> : plan ? "Stopped" : "Ready"}</span>
    </header>
    {!expanded && <p className="cw-agent-plan-summary" role="status">{summary}</p>}
    {expanded && plan && <>
      <ol className="cw-agent-plan-steps" aria-label="Agent tasks">{plan.steps.map((step, index) => {
        const completed = done || index < plan.step;
        const active = running && index === plan.step;
        return <li key={step.title} className={completed ? "is-done" : active ? "is-current" : "is-pending"} aria-current={active ? "step" : undefined}>
          <span className="cw-agent-step-mark">{completed ? <Check size={12} /> : active ? <LoaderCircle size={12} className="spin" /> : <Circle size={11} />}</span>
          <div><strong>{step.title}</strong>{active && <small>{step.detail}</small>}</div>
          {active && <span className="cw-agent-now">Now</span>}
        </li>;
      })}</ol>
      <footer><span>{done ? plan.steps.length : plan.step} of {plan.steps.length} done<span className="cw-agent-demo-label"> · Demo</span></span><div>
        {running ? <><button type="button" aria-label="Advance agent plan demo" disabled={automatic} onClick={onNext}>{automatic ? "Live demo" : "Next step"}</button><button type="button" aria-label="Stop agent plan demo" onClick={onStop}><Square size={9} />Stop</button></> : <button type="button" aria-label="Restart agent plan demo" onClick={onReplay}><Play size={10} />Restart</button>}
      </div></footer>
    </>}
    {expanded && !plan && <p className="cw-agent-plan-summary">The agent’s progress will appear here when you send a request.</p>}
  </section>;
}
