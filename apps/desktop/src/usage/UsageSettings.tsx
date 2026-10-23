import type { ModelsOverview } from "@gunther/contracts";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../api";
import { count } from "./format";
import type { UsageRange, UsageRequest, UsageSummary, UsageTotals } from "./types";
import { UsageChart } from "./UsageChart";
import "./usage.css";

const RANGES: Array<{ id: UsageRange; label: string }> = [{ id: "today", label: "Today" }, { id: "7d", label: "7 days" }, { id: "30d", label: "30 days" }, { id: "all", label: "All" }];
const JOBS: Record<string, string> = { ask: "Ask", outputs: "Outputs", analysis: "Analysis", photos: "Photos", other: "Other" };
const jobLabel = (job: string) => JOBS[job] ?? "Other";
const time = (value: string) => {
  const date = new Date(value);
  return `${date.toLocaleDateString("en", { month: "short", day: "numeric" })} · ${date.toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })}`;
};
const duration = (ms: number | null) => ms === null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
const volume = (row: UsageTotals) => row.input === null || row.output === null ? -1 : row.input + row.output;

function CountsTable({ title, firstColumn, rows }: { title: string; firstColumn: string; rows: Array<UsageTotals & { key: string; label: string }> }) {
  return <section className="usage-section">
    <div className="setting-heading"><span><strong>{title}</strong></span></div>
    <div className="usage-table-scroll"><table className="usage-table" aria-label={title}>
      <thead><tr><th scope="col">{firstColumn}</th>{["Input", "Cached", "Output", "Reasoning", "Requests"].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
      <tbody>{rows.map((row) => <tr key={row.key}><th scope="row">{row.label}</th>{(["input", "cached", "output", "reasoning", "requests"] as const).map((key) => <td key={key} title={row[key] === null ? "Not reported" : row[key].toLocaleString()}>{count(row[key])}</td>)}</tr>)}</tbody>
    </table></div>
  </section>;
}

export function UsageSettings() {
  const [range, setRange] = useState<UsageRange>("7d");
  const [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<{ summary: UsageSummary; recent: UsageRequest[]; models: ModelsOverview | null } | null>(null);
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setProblem(null);
    void Promise.all([knowledgeApi.usageSummary(range, -new Date().getTimezoneOffset()), knowledgeApi.usageRecent(50), knowledgeApi.modelsOverview().catch(() => null)]).then(([summary, recent, models]) => {
      if (active) setData({ summary, recent, models });
    }).catch((reason: unknown) => {
      if (active) { setData(null); setProblem(reason instanceof Error ? reason.message : "Usage could not be loaded."); }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [range, refresh]);

  const clear = async () => {
    if (!window.confirm("Clear all usage history? This cannot be undone.")) return;
    setClearing(true);
    setProblem(null);
    try {
      await knowledgeApi.clearUsage();
      setData(null);
      setLoading(true);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setProblem(reason instanceof Error ? reason.message : "Usage history could not be cleared.");
    } finally {
      setClearing(false);
    }
  };

  const totals = data?.summary.totals;
  const hit = totals && totals.input !== null && totals.input > 0 && totals.cached !== null ? `${Math.round(totals.cached / totals.input * 100)}%` : "—";
  const empty = data && data.summary.totals.requests === 0 && data.recent.length === 0;
  const modelLabel = (row: { providerId: string; modelId: string }) => {
    const provider = data?.models?.providers.find((item) => item.id === row.providerId);
    const model = provider?.models.find((item) => item.id === row.modelId);
    return provider && model ? `${provider.name} · ${model.label || model.id}` : `${row.providerId} · ${row.modelId}`;
  };

  return <>
    <section className="usage-section">
      <div className="setting-row">
        <div className="service-segmented" role="group" aria-label="Usage range">
          {RANGES.map((item) => <button key={item.id} type="button" className={range === item.id ? "is-selected" : ""} aria-pressed={range === item.id} disabled={clearing} onClick={() => setRange(item.id)}>{item.label}</button>)}
        </div>
      </div>
      {loading && <div className="setting-row" role="status"><span><strong>Loading…</strong><small>Asking the local service for usage.</small></span></div>}
      {problem && <div className="setting-row is-problem" role="alert"><span><strong>Unavailable</strong><small>{problem}</small></span><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => setRefresh((value) => value + 1)}>Retry</button></div>}
      {!loading && empty && <div className="setting-row"><span><small>No model requests yet.</small></span></div>}
      {!loading && !empty && totals && <dl className="usage-totals" aria-label="Usage totals">
        <div><dt>Input</dt><dd>{count(totals.input)}</dd></div>
        <div><dt>Cached</dt><dd>{count(totals.cached)}</dd><small>{hit} of input</small></div>
        <div><dt>Output</dt><dd>{count(totals.output)}</dd><small>incl. {count(totals.reasoning)} reasoning</small></div>
        <div><dt>Requests</dt><dd>{count(totals.requests)}</dd></div>
      </dl>}
    </section>

    {!loading && !empty && data && <>
      {range !== "today" && <UsageChart days={data.summary.byDay} range={range} />}
      <CountsTable title="By model" firstColumn="Model" rows={[...data.summary.byModel].sort((a, b) => volume(b) - volume(a)).map((row) => ({ ...row, key: JSON.stringify([row.providerId, row.modelId]), label: modelLabel(row) }))} />
      <CountsTable title="By job" firstColumn="Job" rows={[...data.summary.byJob].sort((a, b) => Object.keys(JOBS).indexOf(a.job) - Object.keys(JOBS).indexOf(b.job)).map((row) => ({ ...row, key: row.job, label: jobLabel(row.job) }))} />
      <section className="usage-section">
        <div className="setting-heading"><span><strong>Recent requests</strong><small>Latest 50, across all dates.</small></span></div>
        <div className="usage-table-scroll"><table className="usage-table usage-recent" aria-label="Recent requests">
          <thead><tr>{["Time", "Job · detail", "Model", "In (cached)", "Out", "Duration", "Status"].map((label) => <th scope="col" key={label}>{label}</th>)}</tr></thead>
          <tbody>{data.recent.map((row) => <tr key={row.id}>
            <td title={new Date(row.at).toLocaleString()}><time dateTime={row.at}>{time(row.at)}</time></td>
            <td title={`${jobLabel(row.job)}${row.detail ? ` · ${row.detail}` : ""}`}>{jobLabel(row.job)}{row.detail ? ` · ${row.detail}` : ""}</td>
            <td title={modelLabel(row)}>{modelLabel(row)}</td>
            <td title={`${count(row.input)} (${count(row.cached)})`}>{count(row.input)} <span className="usage-muted">({count(row.cached)})</span></td>
            <td title={count(row.output)}>{count(row.output)}</td><td title={duration(row.ms)}>{duration(row.ms)}</td>
            <td className="usage-muted" title={row.status === "error" ? "Error" : row.status === "unreported" ? "Not reported" : ""}>{row.status === "error" ? "Error" : row.status === "unreported" ? "Not reported" : ""}</td>
          </tr>)}</tbody>
        </table></div>
      </section>
    </>}
    {!loading && data && !empty && <section className="usage-section"><div className="setting-row"><button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" disabled={clearing} onClick={() => void clear()}>{clearing ? "Clearing…" : "Clear history"}</button></div></section>}
  </>;
}
