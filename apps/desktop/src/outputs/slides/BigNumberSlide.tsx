import { type CSSProperties } from "react";
import { type BigNumberShape, plainText } from "./parseSlide";
import { Heading, layoutClass, Rich } from "./Rich";

/**
 * How large the number is drawn, in px on the 1280-wide slide: 200 for a short figure, smaller as
 * it gets longer so that it stays on a line. A CJK character is as wide as it is high, and a digit
 * or a Latin letter about 0.6 of that.
 */
export function figureSize(figure: string): number {
  const width = [...plainText(figure)].reduce((total, char) => total + (/[\u3000-\u9fff\uff00-\uffef]/.test(char) ? 1 : 0.6), 0);
  return Math.round(Math.min(200, Math.max(64, 1000 / Math.max(width, 1))));
}

/** One number, or one short phrase, drawn huge, with what it is compared with and where it comes from. */
export function BigNumberSlide({ shape }: { shape: BigNumberShape }) {
  return <div className={`outputs-s ${layoutClass(shape.layout)}`}>
    <Heading text={shape.title} />
    <div className="outputs-s-stage">
      <div className="outputs-s-bignum" style={{ "--outputs-figure": `${figureSize(shape.figure)}px` } as CSSProperties}><Rich text={shape.figure} /></div>
      <div className="outputs-s-caption"><Rich text={shape.caption} /></div>
    </div>
  </div>;
}
