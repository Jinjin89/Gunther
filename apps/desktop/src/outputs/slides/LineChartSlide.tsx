import { Group } from "@visx/group";
import { scaleLinear, scalePoint } from "@visx/scale";
import { Line, LinePath } from "@visx/shape";
import { type CSSProperties } from "react";
import { ChartTip, useTip } from "./chartTip";
import { type ChartPoint, type ChartShape, plainText } from "./parseSlide";
import { Frame, Rich } from "./Rich";

// The chart is drawn in px on the 1280-wide slide, in a column 1088 wide, and CSS scales the whole
// of it with the slide's fit (the words over the marks are placed in percentages of it).
const WIDTH = 1088;
/** Room to the left of the first point, for its label, which is centred under it. */
const LEFT = 56;
/** Room above the highest point. */
const TOP = 40;
/** The height of the plot, from the top line to the baseline. */
const HEIGHT = 270;
/** Under the plot: the labels of the points. */
const LABELS = 64;
/** The value at the end is 44 px type; a CJK character is as wide as high, anything else about 0.6 of that. */
const VALUE_SIZE = 44;

const widthOf = (text: string) => [...text].reduce((total, char) => total + (/[\u3000-\u9fff\uff00-\uffef]/.test(char) ? 1 : 0.6), 0) * VALUE_SIZE;
const BASE = TOP + HEIGHT;
const TOTAL = BASE + LABELS;
const across = (x: number) => `${(x / WIDTH) * 100}%`;
const down = (y: number) => `${(y / TOTAL) * 100}%`;

/**
 * A quantity over time: one line in ink, a dot on every point, the value written at the end only.
 * The scale starts at zero (there are no numbers on it: a number on the slide is one the text
 * gave), so the slope is true; the other values are on the dots' tooltips and in the table the
 * chart carries for screen readers. Each point's citation stands under its label. The marks are
 * SVG, so they print whatever the print settings; the words are HTML over them, for the same
 * reason as in the bar chart.
 */
export function LineChartSlide({ shape }: { shape: ChartShape }) {
  const { active, bind, interactive } = useTip();
  const points = shape.points;
  const last = points.length - 1;
  const end = points[last];
  // The end value sits right of the last dot, so the plot ends as far from the edge as it is wide.
  const right = Math.min(360, Math.round(widthOf(plainText(end?.text ?? "")) + 48));

  const timed = points.every((point) => point.x !== null);
  const range: [number, number] = [LEFT, WIDTH - right];
  const time = scaleLinear<number>({ domain: [points[0]?.x ?? 0, end?.x ?? 1], range });
  const order = scalePoint<number>({ domain: points.map((_, at) => at), range });
  const at = (point: ChartPoint, index: number) => (timed ? time(point.x ?? 0) : (order(index) ?? LEFT));

  // Zero is always in view: a line that starts above it would exaggerate its own slope.
  const values = points.map((point) => point.value);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const up = scaleLinear<number>({ domain: [low, high === low ? low + 1 : high], range: [BASE, TOP] });
  const edges = { from: LEFT - 24, to: WIDTH - right + 24 };

  return <Frame layout={shape.layout} title={shape.title} lead={shape.lead} after={shape.after}>
    {shape.measure && <div className="outputs-s-measure"><Rich text={shape.measure} /></div>}
    <div className="outputs-s-line-chart" style={{ "--w": WIDTH, "--h": TOTAL } as CSSProperties}>
      <svg className="outputs-s-marks" viewBox={`0 0 ${WIDTH} ${BASE + 8}`} style={{ height: down(BASE + 8) }}>
        <Group aria-hidden="true">
          {[0, 1, 2, 3, 4].map((step) => <Line key={step} className={step === 0 && low === 0 ? "outputs-s-axis" : "outputs-s-rule-line"}
            from={{ x: edges.from, y: BASE - (HEIGHT * step) / 4 }} to={{ x: edges.to, y: BASE - (HEIGHT * step) / 4 }} />)}
          {low < 0 && <Line className="outputs-s-axis" from={{ x: edges.from, y: up(0) }} to={{ x: edges.to, y: up(0) }} />}
          <LinePath<ChartPoint> className="outputs-s-line" data={points} x={(point, index) => at(point, index)} y={(point) => up(point.value)} />
          {points.map((point, index) => <circle key={index} className={`outputs-s-dot${point.highlight ? " is-accent" : ""}`}
            cx={at(point, index)} cy={up(point.value)} r={point.highlight ? 9 : index === last ? 8 : 6} />)}
        </Group>
        {/* What can be pointed at or tabbed to is named for a screen reader; where nothing can, the table under the chart says it. */}
        {interactive && points.map((point, index) => <circle key={index} className="outputs-s-hit" cx={at(point, index)} cy={up(point.value)} r={22}
          role="img" aria-label={`${plainText(point.label)}: ${plainText(point.text)}`} {...bind(index)} />)}
      </svg>
      {end && <div className="outputs-s-end" style={{ left: across(at(end, last) + 22), top: down(up(end.value)) }}><Rich text={end.text} /></div>}
      {points.map((point, index) => point.highlight && index !== last
        ? <div key={index} className="outputs-s-flag" style={{ left: across(at(point, index)), top: down(up(point.value) - 18) }}><Rich text={point.text} /></div>
        : null)}
      {points.map((point, index) => <div key={index} className="outputs-s-tick" style={{ left: across(at(point, index)), top: down(BASE + 18) }}>
        <Rich text={`${point.label} ${point.marks}`.trim()} />
      </div>)}
      {active !== null && points[active] && <ChartTip point={points[active]} style={{ left: across(at(points[active], active)), top: down(up(points[active].value) - 20), transform: "translate(-50%, -100%)" }} />}
    </div>
    <table className="outputs-sr-only">
      <caption>{plainText(shape.title ?? "")}</caption>
      <thead><tr><th scope="col">{plainText(shape.axis)}</th><th scope="col">{plainText(shape.measure)}</th></tr></thead>
      <tbody>{points.map((point, index) => <tr key={index}><th scope="row">{plainText(point.label)}</th><td>{plainText(point.text)}</td></tr>)}</tbody>
    </table>
  </Frame>;
}
