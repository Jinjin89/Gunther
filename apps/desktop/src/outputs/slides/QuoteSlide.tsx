import type { QuoteShape } from "./parseSlide";
import { Frame, Rich } from "./Rich";

/** Someone's words, large, with an opening mark that hangs in the margin and who said them under. */
export function QuoteSlide({ shape }: { shape: QuoteShape }) {
  return <Frame layout={shape.layout} title={shape.title} lead={shape.lead} after={shape.after}>
    <figure className="outputs-s-quote">
      <blockquote><Rich text={shape.quote} /></blockquote>
      <figcaption><Rich text={shape.by} /></figcaption>
    </figure>
  </Frame>;
}
