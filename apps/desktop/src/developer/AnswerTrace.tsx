import type { AnswerTrace as Trace, TraceStep } from "@gunther/contracts";
import { BookOpenCheck, Brain, CircleAlert, Copy, Filter, Hash, ListChecks, PenLine, Route, Search } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { knowledgeApi } from "../api";

const ICONS: Record<string, ReactNode> = {
  plan: <Route size={13} />,
  search: <Search size={13} />,
  grade: <Filter size={13} />,
  write: <PenLine size={13} />,
  audit: <ListChecks size={13} />,
  check: <BookOpenCheck size={13} />,
  cite: <Hash size={13} />,
  model: <Brain size={13} />,
};

const seconds = (ms: number | undefined) => ms === undefined ? "" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const text = (value: unknown) => typeof value === "string" ? value : value === undefined || value === null ? "" : JSON.stringify(value, null, 2);

/** One line under a step's name: what it decided or found. */
function summary(step: TraceStep): string {
  if (step.error) return step.error;
  switch (step.kind) {
    case "plan": return step.failed ? `Plan unreadable; searched the question as asked` : step.action === "answer" ? "Ready to answer" : `${text(step.action)} “${text(step.query)}”`;
    case "search": return `${list(step.results).length} found`;
    case "grade": return `Kept ${list(step.kept).length}`;
    case "write": { const count = list(step.sources).length; return `${count} ${count === 1 ? "source" : "sources"} to choose from · ${text(step.style)}`; }
    case "audit": return `${list(step.claims).length} claims without a source`;
    case "check": return list(step.supports).length ? "Supported" : list(step.contradicts).length ? "Contradicted" : "Neither";
    case "cite": return `${list(step.sources).length} cited · ${list(step.uncited).length} not`;
    case "model": {
      const usage = step.usage as { prompt_tokens?: number; completion_tokens?: number } | undefined;
      const effort = text(step.effortApplied ?? step.effort) || "default effort";
      return [text(step.model), effort, usage?.prompt_tokens !== undefined ? `${usage.prompt_tokens} → ${usage.completion_tokens ?? "?"} tokens` : ""].filter(Boolean).join(" · ");
    }
    default: return "";
  }
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return <div className="trace-block"><span className="trace-block-title">{title}</span>{children}</div>;
}

/** What a step kept: prompts and replies for model calls, results for searches, and so on. */
function Details({ step }: { step: TraceStep }) {
  if (step.kind === "model") {
    return <>
      <Block title="System"><pre>{text(step.system)}</pre></Block>
      {list(step.messages).map((message, index) => {
        const turn = message as { role?: string; content?: unknown };
        return <Block key={index} title={turn.role === "assistant" ? "Earlier reply" : "Prompt"}><pre>{text(turn.content)}</pre></Block>;
      })}
      {Boolean(step.reasoning) && <Block title="Reasoning"><pre>{text(step.reasoning)}</pre></Block>}
      {Boolean(step.reply) && <Block title="Reply"><pre>{text(step.reply)}</pre></Block>}
      {list(step.notes).length > 0 && <Block title="Notes"><pre>{list(step.notes).map(text).join("\n")}</pre></Block>}
    </>;
  }
  if (step.kind === "search") {
    return <Block title="Results"><ol className="trace-results">{list(step.results).map((item, index) => {
      const result = item as { title?: string; where?: string; text?: string };
      return <li key={index}><strong>{result.title}</strong>{result.where && <small>{result.where}</small>}<p>{result.text}</p></li>;
    })}</ol></Block>;
  }
  const shown = Object.entries(step).filter(([key]) => !["kind", "label", "atMs", "ms", "error", "children"].includes(key));
  if (shown.length === 0) return null;
  return <>{shown.map(([key, value]) => <Block key={key} title={key}><pre>{Array.isArray(value) ? value.map(text).join("\n") : text(value)}</pre></Block>)}</>;
}

function Step({ step }: { step: TraceStep }) {
  const children = step.children ?? [];
  return <li className={`trace-step is-${step.kind} ${step.error ? "has-error" : ""}`}>
    <details>
      <summary>
        <span className="trace-icon" aria-hidden="true">{step.error ? <CircleAlert size={13} /> : ICONS[step.kind] ?? <Route size={13} />}</span>
        <span className="trace-label"><strong>{step.label}</strong><small>{summary(step)}</small></span>
        <span className="trace-time">{seconds(step.ms)}</span>
      </summary>
      <div className="trace-details"><Details step={step} /></div>
    </details>
    {children.length > 0 && <ol className="trace-children">{children.map((child, index) => <Step key={index} step={child} />)}</ol>}
  </li>;
}

/** The Trace tab: how the selected answer was made, step by step, with the exact prompts. */
export function AnswerTrace({ sessionId, messageId, onCopy }: { sessionId: string; messageId: string; onCopy: (label: string, value: string) => void }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    setTrace(null);
    setProblem(null);
    void knowledgeApi.messageTrace(sessionId, messageId).then((found) => { if (active) setTrace(found); }).catch((reason: unknown) => {
      if (active) setProblem(reason instanceof Error ? reason.message : "This answer's trace could not be loaded.");
    });
    return () => { active = false; };
  }, [sessionId, messageId]);

  if (problem) return <div className="trace-empty"><CircleAlert size={18} /><p>{problem}</p></div>;
  if (!trace) return <div className="trace-empty"><span className="gx-spinner" aria-hidden="true" /><p>Loading how this answer was made…</p></div>;
  return <div className="answer-trace">
    <header className="trace-heading">
      <span><strong>{trace.steps.length} steps</strong><small>{seconds(trace.totalMs)} in all · open a step for its exact prompt and reply</small></span>
      <button type="button" className="gx-btn gx-btn-quiet gx-btn-sm" onClick={() => onCopy("Trace", JSON.stringify(trace, null, 2))}><Copy size={13} />Copy</button>
    </header>
    <ol className="trace-steps">{trace.steps.map((step, index) => <Step key={index} step={step} />)}</ol>
  </div>;
}
