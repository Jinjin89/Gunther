import { type CSSProperties } from "react";
import type { TimelineShape } from "./parseSlide";
import { Frame, Rich } from "./Rich";

/** From this many events on, a timeline is drawn down the slide, for the same reason as a flow. */
export const TIMELINE_ACROSS = 4;

/** Change over time: dots on a line, the time above each and what happened under it. */
export function TimelineSlide({ shape }: { shape: TimelineShape }) {
  const down = shape.events.length > TIMELINE_ACROSS;
  return <Frame layout={shape.layout} title={shape.title} lead={shape.lead} after={shape.after}>
    <div className={`outputs-s-stage${down ? " is-tall" : ""}`}>
      <ol className={`outputs-s-events${down ? " is-down" : ""}`} style={{ "--n": shape.events.length } as CSSProperties}>
        {shape.events.map((event, at) => <li key={at}>
          <div className="outputs-s-when"><Rich text={event.when} /></div>
          <span className="outputs-s-event-dot" aria-hidden="true" />
          <div className="outputs-s-what"><Rich text={event.what} /></div>
        </li>)}
      </ol>
    </div>
  </Frame>;
}
