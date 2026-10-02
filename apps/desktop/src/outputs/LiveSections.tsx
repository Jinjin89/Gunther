import type { OutputKind } from "@gunther/contracts";
import { Check, CircleAlert, CircleDashed, LoaderCircle } from "lucide-react";
import { BrandMark } from "../design/BrandMark";
import { AgentSteps, AnswerBody, withoutMarks } from "../pages/AnswerBody";
import type { LiveOutputState, LiveStage, SectionState } from "./liveOutput";

const STATE_LABEL: Record<SectionState, string> = {
  waiting: "Waiting",
  researching: "Looking things up",
  writing: "Writing",
  checking: "Checking",
  revising: "Revising",
  done: "Done",
};

function StageMark({ state }: { state: LiveStage["state"] }) {
  if (state === "done") return <Check size={12} aria-hidden="true" />;
  if (state === "failed") return <CircleAlert size={12} aria-hidden="true" />;
  return <LoaderCircle className="spin" size={12} aria-hidden="true" />;
}

function StateMark({ state }: { state: SectionState }) {
  if (state === "done") return <Check size={13} aria-hidden="true" />;
  if (state === "waiting") return <CircleDashed size={13} aria-hidden="true" />;
  return <LoaderCircle className="spin" size={13} aria-hidden="true" />;
}

/**
 * An output while the agents work: the plan first, then each section as it is researched,
 * written and checked. The numbers in the text are not final until the whole output is
 * saved, so they and the unverified marks wait for it.
 */
export function LiveSections({ state, kind, onStop, stopping, only }: {
  state: LiveOutputState;
  kind: OutputKind;
  onStop: () => void;
  stopping: boolean;
  /** A revision of one section or slide: the others are kept as they are. `undefined` for a build. */
  only?: number | null | undefined;
}) {
  const unit = kind === "slides" ? "slide" : "section";
  return <div className="outputs-live" aria-live="polite" aria-busy="true">
    <header className="outputs-live-head">
      <span><BrandMark size={14} busy /><strong>{state.title ?? (state.sections.length ? "Building…" : "Planning the outline…")}</strong></span>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={onStop} disabled={stopping}>{stopping ? "Stopping…" : "Stop"}</button>
    </header>
    {state.question && <p className="outputs-live-brief">{state.question}</p>}
    {state.stages.length > 0 && <ol className="outputs-live-stages" aria-label="Steps">
      {state.stages.map((stage) => <li key={stage.id} className={`is-${stage.state}`} title={stage.detail ?? undefined}>
        <StageMark state={stage.state} />{stage.label}{stage.state === "failed" ? " — skipped" : stage.detail ? ` · ${stage.detail}` : ""}
      </li>)}
    </ol>}
    {state.approach && <dl className="outputs-live-approach">
      <dt>Question</dt><dd>{state.approach.question}</dd>
      <dt>Answer</dt><dd>{state.approach.answer}</dd>
    </dl>}
    <AgentSteps steps={state.steps} />
    {state.sections.length === 0 && <div className="live-wait"><span><i /><i /><i /></span><small>{state.approach ? "Planning the outline…" : "Reading what is in the library…"}</small></div>}
    <ol className="outputs-live-sections">
      {state.sections.map((section, index) => {
        // A section a revision leaves alone is answered with the one word UNCHANGED.
        const kept = (typeof only === "number" && index !== only) || section.text.trim().toUpperCase() === "UNCHANGED";
        const shown: SectionState = kept ? "done" : section.state;
        return <li key={index} className={kept ? "is-kept" : `is-${section.state}`}>
        <div className="outputs-live-title">
          <StateMark state={shown} />
          <strong>{section.heading || `${unit[0]?.toUpperCase()}${unit.slice(1)} ${index + 1}`}</strong>
          <small>{kept ? "Kept as it is" : STATE_LABEL[section.state]}</small>
        </div>
        {section.goal && section.state !== "done" && !kept && <p className="outputs-live-goal">{section.goal}</p>}
        <AgentSteps steps={section.steps} />
        {section.text && !kept && <div className="outputs-live-text"><AnswerBody content={withoutMarks(section.text)} numbers={[]} /></div>}
      </li>;
      })}
    </ol>
  </div>;
}
