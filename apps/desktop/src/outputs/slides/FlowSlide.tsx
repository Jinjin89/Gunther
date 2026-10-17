import { type CSSProperties } from "react";
import type { FlowShape } from "./parseSlide";
import { Frame, Rich } from "./Rich";

/** From this many steps on, a flow is drawn down the slide: across it a step would get too narrow to read. */
export const FLOW_ACROSS = 4;

/** Steps in order: numbered circles joined by a line, with what happens in each under or beside it. */
export function FlowSlide({ shape }: { shape: FlowShape }) {
  const down = shape.steps.length > FLOW_ACROSS;
  return <Frame layout={shape.layout} title={shape.title} lead={shape.lead} after={shape.after}>
    <div className={`outputs-s-stage${down ? " is-tall" : ""}`}>
      <ol className={`outputs-s-steps${down ? " is-down" : ""}`} style={{ "--n": shape.steps.length } as CSSProperties}>
        {shape.steps.map((step, at) => <li key={at}>
          <span className="outputs-s-step-dot" aria-hidden="true">{at + 1}</span>
          <div className="outputs-s-step-text"><Rich text={step} /></div>
        </li>)}
      </ol>
    </div>
  </Frame>;
}
