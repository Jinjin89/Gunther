import { scaleLinear } from "@visx/scale";
import { type CSSProperties, Fragment } from "react";
import { ChartTip, useTip } from "./chartTip";
import type { ChartShape } from "./parseSlide";
import { Frame, Rich } from "./Rich";

/** The thickness of a bar and the gap between bars, in px on the 1280-wide slide: fewer bars are drawn heavier. */
const BARS: Record<number, readonly [number, number]> = { 2: [72, 30], 3: [60, 26], 4: [50, 24], 5: [44, 22], 6: [40, 20] };

/**
 * Magnitudes side by side: a bar for each row, the value at its tip. One series, so it is in ink;
 * the row the table makes bold is in the accent and the others in grey. The bars are HTML, not
 * SVG: the names wrap and the rows grow with them (so a slide too full for its page is found out
 * and drawn smaller), and a citation next to a value is the same button as in the text. A box
 * prints as sharp as a path, and the print sheet asks for backgrounds to be printed.
 */
export function BarChartSlide({ shape }: { shape: ChartShape }) {
  const { active, bind } = useTip();
  const points = shape.points;
  const [thickness, gap] = BARS[points.length] ?? [40, 20];
  // Zero is the baseline; a negative value grows to the left of it.
  const values = points.map((point) => point.value);
  const share = scaleLinear<number>({ domain: [Math.min(0, ...values), Math.max(0, ...values, Number.EPSILON)], range: [0, 100] });
  const zero = share(0);
  const pointed = points.some((point) => point.highlight);
  return <Frame layout={shape.layout} title={shape.title} lead={shape.lead} after={shape.after}>
    {shape.measure && <div className="outputs-s-measure"><Rich text={shape.measure} /></div>}
    <div className={`outputs-s-bars${values.some((value) => value < 0) ? " has-negative" : ""}`} style={{ "--bar": `${thickness}px`, "--gap": `${gap}px`, "--rows": points.length } as CSSProperties}>
      <div className="outputs-s-grid" aria-hidden="true">
        <div className="outputs-s-plot">
          {[0, 25, 50, 75, 100].map((at) => <i key={at} style={{ left: `${at}%` }} />)}
          <i className="is-zero" style={{ left: `${zero}%` }} />
        </div>
      </div>
      {points.map((point, at) => {
        const tip = share(point.value);
        const negative = point.value < 0;
        const tone = pointed ? (point.highlight ? " is-accent" : " is-grey") : "";
        return <Fragment key={at}>
          <div className={`outputs-s-bar-label${point.highlight ? " is-pointed" : ""}`} style={{ gridRow: at + 1 }} {...bind(at, false)}><Rich text={point.label} /></div>
          <div className="outputs-s-track" style={{ gridRow: at + 1 }} {...bind(at)}>
            <div className="outputs-s-plot">
              <span className={`outputs-s-bar${negative ? " is-negative" : ""}${tone}`} style={{ left: `${Math.min(zero, tip)}%`, width: `${Math.abs(tip - zero)}%` }} aria-hidden="true" />
              <div className="outputs-s-bar-value" style={negative ? { right: `calc(${100 - tip}% + 16px)` } : { left: `calc(${tip}% + 16px)` }}>
                <Rich text={`${point.text} ${point.marks}`.trim()} />
              </div>
            </div>
            {active === at && <ChartTip point={point} style={{ left: `calc(var(--room-left, 0px) + (100% - var(--room) - var(--room-left, 0px)) * ${tip / 100})`, bottom: "calc(100% + 10px)" }} />}
          </div>
        </Fragment>;
      })}
    </div>
  </Frame>;
}
