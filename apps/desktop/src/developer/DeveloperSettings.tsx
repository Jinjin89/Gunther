import type { BackgroundJob, LogLine } from "@gunther/contracts";
import { Activity, Route, ScrollText, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { knowledgeApi } from "../api";
import { useTraces } from "./useTraces";

const time = (value: string) => new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });
const JOB_KINDS: Record<string, string> = { parse_asset: "Reading", embed: "Search by meaning", summarize: "Paper details", digest: "Summary" };
type Level = "info" | "warning" | "error";

/** Settings → Developer: keep how answers are made, and read the backend's recent lines and jobs. */
export function DeveloperSettings({ onNotify }: { onNotify: (message: string) => void }) {
  const traces = useTraces();
  const [level, setLevel] = useState<Level>("warning");
  const [lines, setLines] = useState<LogLine[] | null>(null);
  const [jobs, setJobs] = useState<BackgroundJob[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(async (chosen: Level) => {
    setProblem(null);
    try {
      const [nextLines, nextJobs] = await Promise.all([knowledgeApi.developerLogs(chosen), knowledgeApi.developerJobs()]);
      setLines(nextLines);
      setJobs(nextJobs);
    } catch (reason) {
      setProblem(reason instanceof Error ? reason.message : "The local service could not be asked.");
    }
  }, []);
  useEffect(() => { void load(level); }, [level, load]);

  const toggle = async (on: boolean) => {
    try {
      await traces.change(on);
      onNotify(on ? "New answers keep how they were made. Open one and choose Trace." : "Answers no longer keep how they were made.");
    } catch (reason) {
      onNotify(reason instanceof Error ? reason.message : "That could not be saved.");
    }
  };

  return <>
    <section>
      <div className="setting-heading"><Route size={16} /><span><strong>How answers are made</strong><small>For checking why an answer came out the way it did.</small></span></div>
      <label className="setting-row">
        <span><strong>Keep how answers are made</strong><small>Each new answer keeps its steps: what was searched and found, and the exact prompts sent to the model with its replies. Open an answer and choose Trace beside it. Kept on this computer; the latest 200.</small></span>
        <input type="checkbox" role="switch" aria-label="Keep how answers are made" checked={traces.on} onChange={(event) => void toggle(event.target.checked)} />
      </label>
    </section>

    <section>
      <div className="setting-heading developer-heading">
        <ScrollText size={16} />
        <span><strong>Logs</strong><small>The local service’s recent lines, newest first.</small></span>
        <span className="setting-actions">
          <select aria-label="Which lines" value={level} onChange={(event) => setLevel(event.target.value as Level)}>
            <option value="info">Everything</option>
            <option value="warning">Warnings and errors</option>
            <option value="error">Errors</option>
          </select>
          <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => void load(level)}><RefreshCw size={13} />Refresh</button>
        </span>
      </div>
      {problem && <div className="setting-row is-problem"><span><strong>Unavailable</strong><small>{problem}</small></span></div>}
      {lines && lines.length === 0 && <div className="setting-row"><span><small>Nothing yet.</small></span></div>}
      {lines && lines.length > 0 && <ol className="developer-log">{lines.map((line, index) => <li key={index} className={`is-${line.level}`}>
        <time>{time(line.at)}</time><em>{line.level}</em><code>{line.logger.replace(/^gunther\./, "")}</code><p>{line.message}</p>
      </li>)}</ol>}
    </section>

    <section>
      <div className="setting-heading"><Activity size={16} /><span><strong>Background jobs</strong><small>Reading, search by meaning and summaries for each source, newest first.</small></span></div>
      {jobs && jobs.length === 0 && <div className="setting-row"><span><small>None yet.</small></span></div>}
      {jobs && jobs.length > 0 && <ol className="developer-jobs">{jobs.map((job) => <li key={job.id}>
        <span><strong>{JOB_KINDS[job.kind] ?? job.kind}</strong><small>{job.sourceTitle ?? job.sourceId}</small>{job.error && <p>{job.error}</p>}</span>
        <span className={`developer-job-state is-${job.state}`}>{job.state}{job.attempts > 1 ? ` · ${job.attempts} tries` : ""}</span>
        <time>{time(job.updatedAt)}</time>
      </li>)}</ol>}
    </section>
  </>;
}
