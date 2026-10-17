import type { TakeawaysShape } from "./parseSlide";
import { Frame, Rich } from "./Rich";

/** The last slide: what to take away or do next, each with a large numeral in the accent. */
export function TakeawaysSlide({ shape }: { shape: TakeawaysShape }) {
  return <Frame layout={shape.layout} title={shape.title} lead={shape.lead} after={shape.after}>
    {shape.items.length > 0 && <ol className="outputs-s-takeaways">
      {shape.items.map((item, at) => <li key={at}>
        <span className="outputs-s-numeral" aria-hidden="true">{at + 1}</span>
        <div className="outputs-s-point"><Rich text={item} /></div>
      </li>)}
    </ol>}
  </Frame>;
}
