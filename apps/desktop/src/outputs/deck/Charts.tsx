import { scaleLinear } from "@visx/scale";
import { LinePath } from "@visx/shape";
import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import type { ChartSeries, DeckData } from "./parseDeck";
import { textWidth, wrap } from "./measure";
import { visible } from "./units";

export { textWidth, wrap };

type ChartData = Extract<DeckData, { layout: "chart" }>;

/** The size of an element's layout box (not the scaled picture of it), kept up to date. */
export function useBox(ref: RefObject<HTMLElement | null>, fallback = { w: 1120, h: 380 }) {
  const [box, setBox] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const look = () => { if (el.clientWidth > 0 && el.clientHeight > 0) setBox((old) => old.w === el.clientWidth && old.h === el.clientHeight ? old : { w: el.clientWidth, h: el.clientHeight }); };
    look();
    if (typeof ResizeObserver === "undefined") return undefined;
    const watcher = new ResizeObserver(look);
    watcher.observe(el);
    return () => watcher.disconnect();
  }, [ref]);
  return box;
}

/** Series colours: one series is the accent, more are the accent, ink and a mid grey. */
export const seriesColour = (index: number, count: number) => count === 1 ? "var(--dk-accent)" : ["var(--dk-accent)", "var(--dk-ink)", "var(--dk-bar)"][index] ?? "var(--dk-bar)";

function Legend({ series }: { series: ChartSeries[] }) {
  if (series.length < 2) return null;
  return <ul className="dk-legend">{series.map((s, at) => <li key={at}><i style={{ background: seriesColour(at, series.length) }} />{visible(s.name)}</li>)}</ul>;
}

const maxOf = (series: ChartSeries[]) => Math.max(...series.flatMap((s) => s.values.map((v) => v.n)), 0);
const minOf = (series: ChartSeries[]) => Math.min(...series.flatMap((s) => s.values.map((v) => v.n)), 0);

function Bars({ data, w, h }: { data: ChartData; w: number; h: number }) {
  const n = data.categories.length;
  const m = data.series.length;
  const rh = h / n;
  const labelSize = rh >= 40 ? 22 : rh >= 30 ? 20 : 18;
  const valueSize = labelSize;
  const labels = data.categories.map((c) => visible(c.label));
  const lw = Math.min(w * 0.34, Math.max(...labels.map((l) => textWidth(l, labelSize, 600))) + 8);
  const lines = labels.map((l, at) => wrap(l, labelSize, lw, rh >= 52 ? 2 : 1, data.categories[at]!.highlight ? 700 : 500));
  const vw = Math.max(...data.series.flatMap((s) => s.values.map((v) => textWidth(v.label, valueSize, 700)))) + 14;
  const x0 = lw + 20;
  const hi = maxOf(data.series);
  const lo = minOf(data.series);
  const x = scaleLinear({ domain: [Math.min(lo, 0), hi <= 0 ? 1 : hi], range: [0, Math.max(40, w - x0 - vw)] });
  const zero = x(0);
  const bt = Math.min(44, (rh * 0.7) / m - (m > 1 ? 3 : 0));
  return <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={visible(data.categories.map((c, at) => `${c.label}: ${data.series.map((s) => s.values[at]?.label).join(" / ")}`).join("; "))}>
    <line x1={x0 + zero} x2={x0 + zero} y1={0} y2={h} stroke="var(--dk-rule)" strokeWidth={2} />
    {data.categories.map((c, row) => {
      const cy = row * rh + rh / 2;
      const group = m * bt + (m - 1) * 3;
      return <g key={row}>
        {c.highlight && <rect x={0} y={row * rh + 2} width={w} height={rh - 4} fill="var(--dk-soft)" />}
        <text x={0} y={cy - ((lines[row]!.length - 1) * (labelSize * 1.15)) / 2} fontSize={labelSize} fontWeight={c.highlight ? 700 : 500} fill="var(--dk-ink)" dominantBaseline="central">
          {lines[row]!.map((line, at) => <tspan key={at} x={0} dy={at === 0 ? 0 : labelSize * 1.15}>{line}</tspan>)}
        </text>
        {data.series.map((s, k) => {
          const v = s.values[row]!;
          const y = cy - group / 2 + k * (bt + 3);
          const len = x(v.n) - zero;
          const colour = m === 1 ? (c.highlight || !data.categories.some((cc) => cc.highlight) ? "var(--dk-accent)" : "var(--dk-bar)") : seriesColour(k, m);
          return <g key={k}>
            <rect x={x0 + Math.min(zero, zero + len)} y={y} width={Math.max(2, Math.abs(len))} height={bt} fill={colour} />
            <text x={x0 + (len >= 0 ? zero + len + 10 : zero + len - 10)} y={y + bt / 2} fontSize={valueSize} fontWeight={700} fill="var(--dk-ink)" dominantBaseline="central" textAnchor={len >= 0 ? "start" : "end"}>{v.label}</text>
          </g>;
        })}
      </g>;
    })}
  </svg>;
}

function Columns({ data, w, h }: { data: ChartData; w: number; h: number }) {
  const n = data.categories.length;
  const m = data.series.length;
  const band = w / n;
  const labelSize = band < 80 ? 18 : 20;
  const labels = data.categories.map((c) => visible(c.label));
  const lineCap = band < 110 ? 3 : 2;
  const lines = labels.map((l, at) => wrap(l, labelSize, band - 10, lineCap, data.categories[at]!.highlight ? 700 : 500));
  const lineCount = Math.max(...lines.map((l) => l.length));
  const bottom = lineCount * labelSize * 1.2 + 16;
  const top = 34;
  const hi = maxOf(data.series);
  const lo = Math.min(minOf(data.series), 0);
  const plotH = h - bottom - top;
  const y = scaleLinear({ domain: [lo, hi <= 0 ? 1 : hi], range: [plotH, 0] });
  const inner = Math.min(band * 0.78, m === 1 ? 110 : 150);
  const bw = (inner - (m - 1) * 4) / m;
  const valueSize = bw >= 54 ? 22 : bw >= 40 ? 20 : 18;
  const base = top + y(0);
  return <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={visible(data.categories.map((c, at) => `${c.label}: ${data.series.map((s) => s.values[at]?.label).join(" / ")}`).join("; "))}>
    {data.categories.map((c, col) => {
      const cx = col * band + band / 2;
      return <g key={col}>
        {c.highlight && <rect x={col * band + 2} y={0} width={band - 4} height={h} fill="var(--dk-soft)" />}
        {data.series.map((s, k) => {
          const v = s.values[col]!;
          const bx = cx - inner / 2 + k * (bw + 4);
          const top1 = top + y(Math.max(v.n, 0));
          const hh = Math.max(2, Math.abs(y(v.n) - y(0)));
          const colour = m === 1 ? (c.highlight || !data.categories.some((cc) => cc.highlight) ? "var(--dk-accent)" : "var(--dk-bar)") : seriesColour(k, m);
          return <g key={k}>
            <rect x={bx} y={v.n >= 0 ? top1 : base} width={bw} height={hh} fill={colour} />
            <text x={bx + bw / 2} y={v.n >= 0 ? top1 - 8 : base + hh + 20} fontSize={valueSize} fontWeight={700} fill="var(--dk-ink)" textAnchor="middle">{v.label}</text>
          </g>;
        })}
        <text x={cx} y={base + 26} fontSize={labelSize} fontWeight={c.highlight ? 700 : 500} fill="var(--dk-ink)" textAnchor="middle">
          {lines[col]!.map((line, at) => <tspan key={at} x={cx} dy={at === 0 ? 0 : labelSize * 1.2}>{line}</tspan>)}
        </text>
      </g>;
    })}
    <line x1={0} x2={w} y1={base} y2={base} stroke="var(--dk-ink)" strokeWidth={2} />
  </svg>;
}

function Lines({ data, w, h }: { data: ChartData; w: number; h: number }) {
  const n = data.categories.length;
  const m = data.series.length;
  const labelSize = 18;
  const left = 8;
  const rightLabels = data.series.map((s) => s.values.at(-1)!.label);
  const right = Math.max(...rightLabels.map((l) => textWidth(l, 22, 700))) + 26;
  const bottom = 40;
  const top = 34;
  const hi = maxOf(data.series);
  const lo = Math.min(...data.series.flatMap((s) => s.values.map((v) => v.n)));
  const span = hi - lo || 1;
  const y = scaleLinear({ domain: [lo - span * 0.12, hi + span * 0.12], range: [h - bottom, top] }).nice(4);
  const px = (at: number) => (n === 1 ? (left + w - right) / 2 : left + (at * (w - right - left - 40)) / (n - 1) + 20);
  const ticks = y.ticks(4);
  const labelled = (series: number, at: number) => {
    if (at === n - 1) return false; // drawn at the line's end
    if (data.categories[at]!.highlight) return true;
    const vals = data.series.map((s) => s.values[at]!.n);
    const rank = vals.filter((v) => v > vals[series]!).length;
    if (at === 0 || n * m <= 8) return m === 1 || rank === 0 || rank === m - 1;
    return false;
  };
  // End labels, spread apart so close values do not touch.
  const ends = data.series.map((s, k) => ({ k, y: y(s.values.at(-1)!.n) })).sort((a, b) => a.y - b.y);
  for (let at = 1; at < ends.length; at += 1) if (ends[at]!.y - ends[at - 1]!.y < 24) ends[at]!.y = ends[at - 1]!.y + 24;
  const endY = new Map(ends.map((e) => [e.k, e.y]));
  const step = (w - right - left - 40) / Math.max(1, n - 1);
  const every = Math.max(1, Math.ceil(Math.max(...data.categories.map((c) => textWidth(visible(c.label), labelSize))) / Math.max(20, step - 8)));
  return <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={data.series.map((s) => `${visible(s.name)}: ${s.values.map((v, at) => `${visible(data.categories[at]!.label)} ${v.label}`).join(", ")}`).join("; ")}>
    {ticks.map((t) => <line key={t} x1={left} x2={w - right} y1={y(t)} y2={y(t)} stroke="var(--dk-rule)" strokeWidth={1} />)}
    {data.series.map((s, k) => <g key={k}>
      <LinePath data={s.values} x={(_, at) => px(at)} y={(v) => y(v.n)} stroke={seriesColour(k, m)} strokeWidth={4} strokeLinejoin="round" strokeLinecap="round" fill="none" />
      {s.values.map((v, at) => <circle key={at} cx={px(at)} cy={y(v.n)} r={at === n - 1 || data.categories[at]!.highlight ? 7 : 4.5} fill={seriesColour(k, m)} stroke="var(--dk-bg)" strokeWidth={2} />)}
      {s.values.map((v, at) => {
        if (!labelled(k, at)) return null;
        const vals = data.series.map((q) => q.values[at]!.n);
        const rank = vals.filter((q) => q > v.n).length;
        const below = m > 1 && rank === m - 1 && m > 1;
        if (below && at < n - 1 && Math.abs(y(Math.max(...vals)) - y(v.n)) < 44) return null;
        return <text key={`l${at}`} x={px(at)} y={y(v.n) + (below ? 30 : -14)} fontSize={20} fontWeight={700} fill="var(--dk-ink)" textAnchor="middle">{v.label}</text>;
      })}
      <text x={px(n - 1) + 16} y={endY.get(k)!} fontSize={22} fontWeight={700} fill="var(--dk-ink)" dominantBaseline="central">{s.values.at(-1)!.label}</text>
    </g>)}
    {data.categories.map((c, at) => at % every === 0 || at === n - 1 ? <text key={at} x={px(at)} y={h - 10} fontSize={labelSize} fontWeight={c.highlight ? 700 : 500} fill="var(--dk-muted)" textAnchor="middle">{visible(c.label)}</text> : null)}
    <line x1={left} x2={w - right} y1={h - bottom + 6} y2={h - bottom + 6} stroke="var(--dk-ink)" strokeWidth={2} />
  </svg>;
}

/** A chart drawn as SVG: bar (horizontal), column or line; one to three series; values written on the marks. */
export function Chart({ data }: { data: ChartData }) {
  const plot = useRef<HTMLDivElement>(null);
  const { w, h } = useBox(plot);
  const unitLine = data.series.length === 1 ? visible(data.series[0]!.name) + (data.series[0]!.unit ? ` (${data.series[0]!.unit})` : "") : data.unit;
  return <div className="dk-chart">
    {data.series.length > 1 ? <Legend series={data.series} /> : unitLine ? <p className="dk-measure">{unitLine}</p> : null}
    <div className="dk-plot" ref={plot}>
      {data.kind === "column" ? <Columns data={data} w={w} h={h} /> : data.kind === "line" ? <Lines data={data} w={w} h={h} /> : <Bars data={data} w={w} h={h} />}
    </div>
  </div>;
}
