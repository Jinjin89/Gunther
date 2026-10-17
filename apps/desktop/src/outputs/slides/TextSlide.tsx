import type { TextShape } from "./parseSlide";
import { Heading, layoutClass, Rich } from "./Rich";

/** Reasoning that fits no other layout: the heading and the words as they are, in lines short enough to read. */
export function TextSlide({ shape }: { shape: TextShape }) {
  return <div className={`outputs-s ${layoutClass(shape.layout)}`}>
    <Heading text={shape.title} />
    {shape.body && <div className="outputs-s-body"><Rich text={shape.body} /></div>}
  </div>;
}
