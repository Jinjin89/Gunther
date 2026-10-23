import { scaleBand, scaleLinear } from "@visx/scale";
import { Bar } from "@visx/shape";
import { count } from "./format";
import type { UsageDay, UsageRange } from "./types";

const SERIES = [
  { label: "Cached input", color: "#d4d4d4" },
  { label: "Uncached input", color: "#888888" },
  { label: "Output", color: "#222222" },
];
const dayLabel = (day: string) => new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const dayKey = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

export function UsageChart({ days, range }: { days: UsageDay[]; range: UsageRange }) {
  const end = new Date();
  const start = new Date(end);
  if (range === "all" && days.length) start.setTime(new Date(`${days.map((day) => day.day).sort()[0]}T12:00:00`).getTime());
  else start.setDate(start.getDate() - (range === "30d" ? 29 : 6));
  const dates: string[] = [];
  for (const date = new Date(start); dayKey(date) <= dayKey(end); date.setDate(date.getDate() + 1)) dates.push(dayKey(date));
  const width = 680;
  const height = 180;
  const x = scaleBand<string>({ domain: dates, range: [48, width - 12], padding: 0.25 });
  const complete = days.filter((day) => day.input !== null && day.cached !== null && day.output !== null);
  const max = Math.max(1, ...complete.map((day) => day.input! + day.output!));
  const y = scaleLinear({ domain: [0, max], range: [height, 12], nice: true });
  const ticks = y.ticks(3);
  const labelEvery = Math.max(1, Math.ceil(dates.length / 6));

  return <section className="usage-section" aria-label="Daily usage">
    <div className="setting-heading"><span><strong>Daily usage</strong></span></div>
    <div className="usage-chart">
      <svg viewBox={`0 0 ${width} ${height + 28}`} role="img" aria-label="Daily tokens: cached input, uncached input and output">
        {ticks.map((tick) => <g key={tick} className="usage-chart-tick">
          <line x1={48} x2={width - 12} y1={y(tick)} y2={y(tick)} />
          <text x={40} y={y(tick) + 4} textAnchor="end">{count(tick)}</text>
        </g>)}
        {days.map((day) => {
          const left = x(day.day);
          if (left === undefined) return null;
          if (day.input === null || day.cached === null || day.output === null) return <text key={day.day} x={left + x.bandwidth() / 2} y={height - 4} textAnchor="middle" className="usage-chart-missing"><title>{dayLabel(day.day)}: not reported</title>—</text>;
          const values = [day.cached, day.input - day.cached, day.output];
          let bottom = 0;
          return <g key={day.day}>
            <title>{dayLabel(day.day)}: {values.map((value, index) => `${SERIES[index]!.label} ${count(value)}`).join(", ")}</title>
            {values.map((value, index) => {
              const top = bottom + value;
              const bar = <Bar key={index} x={left} y={y(top)} width={x.bandwidth()} height={y(bottom) - y(top)} fill={SERIES[index]!.color} />;
              bottom = top;
              return bar;
            })}
          </g>;
        })}
        {dates.filter((_, index) => index % labelEvery === 0).map((day) => <text key={day} x={x(day)! + x.bandwidth() / 2} y={height + 20} textAnchor="middle" className="usage-chart-date">{dayLabel(day)}</text>)}
      </svg>
      <div className="usage-legend">{SERIES.map((series) => <span key={series.label}><i style={{ background: series.color }} />{series.label}</span>)}</div>
    </div>
  </section>;
}
