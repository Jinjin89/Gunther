import type { TitleShape } from "./parseSlide";
import { Heading, Rich } from "./Rich";

/** The first slide: the title large, a short accent rule over it, and the message under it. */
export function TitleSlide({ shape }: { shape: TitleShape }) {
  return <div className="outputs-s is-title">
    <span className="outputs-s-rule" aria-hidden="true" />
    <Heading text={shape.title} level={1} />
    {shape.message && <div className="outputs-s-message"><Rich text={shape.message} /></div>}
  </div>;
}
