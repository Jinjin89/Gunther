import { type CSSProperties } from "react";
import type { FlowShape } from "./parseSlide";
import { Frame, Rich } from "./Rich";

/** From this many steps on, a flow is drawn down the slide: across it a step would get too narrow to read. */
export const FLOW_ACROSS = 4;

/** A leading spaced minus before a value is Markdown list syntax; escape it for literal display. */
function flowText(text: string): string {
  return text.replace(/^([ \t]*)-(?=[ \t]+[\p{Sc}\p{N}.．+\-−–—])/u, "$1\\-");
}

/** Steps in order: numbered circles joined by a line, with what happens in each under or beside it. */
export function FlowSlide({ shape }: { shape: FlowShape }) {
  const down = shape.steps.length > FLOW_ACROSS;
  const alignRows = !down && shape.steps.some((step) => step.title !== null);
  return <Frame layout={shape.layout} title={shape.title} lead={shape.lead} after={shape.after}>
    <div className={`outputs-s-stage${down ? " is-tall" : ""}`}>
      <ol className={`outputs-s-steps${down ? " is-down" : ""}${alignRows ? " has-named-steps" : ""}`} style={{ "--n": shape.steps.length } as CSSProperties}>
        {shape.steps.map((step, at) => {
          return <li key={at}>
          <span className="outputs-s-step-dot" aria-hidden="true">{step.number}</span>
          <div className="outputs-s-step-text">
            {step.title ? <>
              <div className="outputs-s-step-title"><Rich text={step.title} /></div>
              {step.detail && <div className="outputs-s-step-detail"><Rich text={flowText(step.detail)} /></div>}
            </> : alignRows ? <>
              <div className="outputs-s-step-title is-empty" aria-hidden="true" />
              <div className="outputs-s-step-detail"><Rich text={step.detail} /></div>
            </> : <Rich text={step.detail} />}
          </div>
        </li>;
        })}
      </ol>
    </div>
  </Frame>;
}
