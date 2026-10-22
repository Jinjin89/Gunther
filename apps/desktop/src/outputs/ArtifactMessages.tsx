import type { ArtifactChange, ArtifactConversationMessage, ArtifactPending, ArtifactSummary, OutputKind } from "@gunther/contracts";
import { BookOpen, Check, ChevronRight, CircleAlert, CircleHelp, ClipboardCheck, FilePlus2, Globe2, Library, ListChecks, LoaderCircle, MessageSquare, Minus, PenLine, Save, type LucideIcon } from "lucide-react";
import { BrandMark } from "../design/BrandMark";
import { AnswerBody, citationNumbers, withoutMarks } from "../pages/AnswerBody";
import { LiveSections } from "./LiveSections";
import { StudioLive } from "./StudioBrief";
import type { LiveAction, LiveOutputState } from "./liveOutput";
import { outputActivity } from "./ArtifactAgentPlan";

/** One line of what the agent did in a turn: a search, a slide written, a check. */
export interface StepLine {
  id: string;
  /** The kind of action, when it is known (a live turn knows it; a stored one does not). */
  action?: string | undefined;
  state: LiveAction["state"];
  label: string;
  note: string;
}

const STEP_ICON: Record<string, LucideIcon> = {
  search_library: Library, search_web: Globe2, read_source: BookOpen, understand: ListChecks, plan: ListChecks, write: PenLine, edit: PenLine,
  check: ClipboardCheck, review: ClipboardCheck, save: Save, answer: MessageSquare, ask: CircleHelp, reply: MessageSquare, select: FilePlus2,
};

/** The agent's steps, one line each, in the look of Ask's search steps. */
export function TurnSteps({ steps }: { steps: StepLine[] }) {
  if (!steps.length) return null;
  return <ol className="message-steps artifact-steps" aria-label="What the agent did">
    {steps.map((step) => {
      const Icon = step.state === "running" ? LoaderCircle : step.state === "failed" ? CircleAlert : step.state === "skipped" ? Minus : step.state === "waiting" ? Minus : (step.action && STEP_ICON[step.action]) || Check;
      return <li key={step.id} className={step.state === "failed" ? "is-error" : step.state === "waiting" || step.state === "skipped" ? "is-quiet" : ""}>
        <Icon size={12} className={step.state === "running" ? "spin" : undefined} aria-hidden="true" />
        <span title={step.label}>{step.label}</span>
        {step.note && step.state !== "running" && <small title={step.note}>{step.note}</small>}
      </li>;
    })}
  </ol>;
}

/** A finished turn's steps, folded into one line that opens to the list. */
export function FoldedSteps({ steps }: { steps: StepLine[] }) {
  const worked = steps.filter((step) => step.state !== "skipped");
  if (!worked.length) return null;
  return <details className="artifact-folded-steps">
    <summary><ChevronRight size={12} aria-hidden="true" />Worked: {worked.length} {worked.length === 1 ? "step" : "steps"}</summary>
    <TurnSteps steps={steps} />
  </details>;
}

const isRecord = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);

/** The steps a stored turn kept. */
export function stepsOf(payload: Record<string, unknown>): StepLine[] {
  const steps = Array.isArray(payload.steps) ? payload.steps : [];
  return steps.filter(isRecord).filter((step) => typeof step.id === "string" && typeof step.label === "string").map((step) => ({
    id: step.id as string,
    state: (["running", "done", "failed", "skipped"].includes(step.state as string) ? step.state : "done") as StepLine["state"],
    label: step.label as string,
    note: typeof step.note === "string" ? step.note : "",
  }));
}
/** The changes a stored turn made. */
export function changesOf(payload: Record<string, unknown>): ArtifactChange[] {
  const changes = Array.isArray(payload.changes) ? payload.changes : [];
  return changes.filter(isRecord).filter((change) => typeof change.heading === "string" && typeof change.what === "string").map((change) => ({
    position: typeof change.position === "number" ? change.position : null,
    was: typeof change.was === "number" ? change.was : null,
    heading: change.heading as string,
    what: change.what as ArtifactChange["what"],
    note: typeof change.note === "string" ? change.note : "",
  }));
}
const optionsOf = (payload: Record<string, unknown>): string[] => Array.isArray(payload.options) ? payload.options.filter((item): item is string => typeof item === "string") : [];

/** "Slide 4 · Costs — shortened", "Added slide 7 · Results", "Removed slide 5 · Costs". */
export function describeChange(change: ArtifactChange, kind: OutputKind | null): string {
  const unit = kind === "report" ? "section" : kind === "slides" ? "slide" : "part";
  // The note is what the writer was told; a row keeps it short.
  const full = change.note.trim().replace(/\s+/g, " ");
  const note = full.length > 90 ? `${full.slice(0, 89).trimEnd()}…` : full;
  const tail = note && full.toLowerCase() !== change.heading.trim().toLowerCase() ? ` — ${note}` : "";
  if (change.what === "added") return `Added ${unit} ${change.position ?? ""} · ${change.heading}`.replace(/ {2,}/g, " ");
  if (change.what === "deleted") return `Removed ${unit} ${change.was ?? ""} · ${change.heading}${tail}`.replace(/ {2,}/g, " ");
  if (change.what === "moved") return `Moved ${unit} ${change.was ?? "?"} to ${change.position ?? "?"} · ${change.heading}${tail}`;
  return `${unit[0]!.toUpperCase()}${unit.slice(1)} ${change.position ?? ""} · ${change.heading} — ${note || change.what}`.replace(/ {2,}/g, " ");
}

/** What a message of the user's was about, in words. */
export function aboutOf(payload: Record<string, unknown>, kind: OutputKind | null): string | null {
  const focus = isRecord(payload.focus) ? payload.focus : null;
  if (!focus || focus.selected !== true) return null;
  const quote = typeof focus.quote === "string" ? focus.quote : "";
  if (quote) return `“${quote.length > 80 ? `${quote.slice(0, 79)}…` : quote}”`;
  return typeof focus.section === "number" ? `${kind === "report" ? "Section" : kind === "slides" ? "Slide" : "Part"} ${focus.section}` : null;
}

const USER_WORDS: Record<string, string> = { start: "Just start", continue: "Continue" };

/** The chat of an Artifacts conversation, as the service stored it. */
export function ArtifactMessageList({ messages, pending, history, citations, kind, busy, onOption, onStart, onContinue, onRetry, onOpenPart, onOpenVersion, onCite, earlier = false }: {
  messages: ArtifactConversationMessage[];
  /** The question waiting for an answer, if any: only that message offers its options. */
  pending: ArtifactPending | null;
  history: ArtifactSummary[];
  /** The open version's sources: a number in an answer opens the one it names. */
  citations: unknown[];
  /** What the conversation's artifact is made of (a deck's parts are slides). */
  kind: OutputKind | null;
  busy: boolean;
  onOption: (text: string) => void;
  onStart: () => void;
  onContinue: () => void;
  onRetry: (message: ArtifactConversationMessage) => void;
  onOpenPart: (artifactId: string | null, position: number | null) => void;
  onOpenVersion: (item: ArtifactSummary) => void;
  onCite: (index: number) => void;
  /** The conversation of versions that predate conversations: the messages made from them are not a record of what was said, so they are not shown. */
  earlier?: boolean;
}) {
  const hidden = (message: ArtifactConversationMessage) => earlier && (message.payload.imported === true || Boolean(message.clientRequestId?.startsWith("earlier_")));
  const shown = messages.filter((message) => !hidden(message));
  const last = shown.at(-1)?.id;
  return <>
    {shown.length < messages.length && <p className="outputs-hint">These versions were made before conversations were kept. Ask for a change here, or start a new conversation.</p>}
    <div className="aw-messages" role="log" aria-label="Artifact messages">{shown.map((message) => {
    if (message.role === "user") {
      const about = aboutOf(message.payload, kind);
      return <div key={message.id} className="aw-message is-user"><div><small>You</small><p>{message.text || USER_WORDS[message.kind] || ""}</p>{about && <small className="artifact-about">About {about}</small>}</div></div>;
    }
    const steps = stepsOf(message.payload);
    const version = message.artifactId ? history.find((item) => item.id === message.artifactId) ?? null : null;
    const changes = changesOf(message.payload);
    // A new artifact is all "added": its reply says what it is, and a row per part says nothing more.
    const listed = changes.every((change) => change.what === "added") ? [] : changes;
    const unit = version?.kind ?? kind;
    const mine = message.id === last;
    return <div key={message.id} className="aw-message is-assistant"><span className="aw-agent-mark"><BrandMark size={17} /></span><div className="artifact-reply">
      <FoldedSteps steps={steps} />
      {message.kind === "answer"
        ? <AnswerBody content={message.text} numbers={citationNumbers(citations)} onCitation={onCite} />
        : message.kind === "reply"
          ? <AnswerBody content={message.text} numbers={[]} />
          : message.kind === "stopped"
            ? <p>Stopped. Nothing was saved.</p>
            : message.kind === "failed"
              ? <p className="artifact-failed" role="alert">{message.text || "The request wasn’t handled."}</p>
              : <p>{message.text}</p>}
      {message.kind === "question" && pending?.messageId === message.id && !busy && <div className="artifact-pills" role="group" aria-label="Answers">
        {optionsOf(message.payload).map((option) => <button type="button" key={option} className="artifact-pill" onClick={() => onOption(option)}>{option}</button>)}
        <button type="button" className="artifact-pill is-quiet" onClick={onStart}>Just start</button>
      </div>}
      {message.kind === "result" && listed.length > 0 && <ul className="artifact-changes" aria-label="What changed">
        {listed.map((change, index) => <li key={index}><button type="button" className="artifact-change-row" onClick={() => onOpenPart(message.artifactId, change.position)}>{describeChange(change, unit)}<ChevronRight size={11} aria-hidden="true" /></button></li>)}
      </ul>}
      {message.kind === "result" && version && <button type="button" className="artifact-result-link" onClick={() => onOpenVersion(version)}>Open v{version.versionNumber}<ChevronRight size={12} /></button>}
      {message.kind === "paused" && mine && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy} onClick={onContinue}>Continue</button>}
      {message.kind === "failed" && mine && <button type="button" className="gx-btn gx-btn-ghost gx-btn-sm" disabled={busy} onClick={() => onRetry(message)}>Retry</button>}
    </div></div>;
  })}</div>
  </>;
}

/** The turn in progress: what the agent is doing, step by step, and what it has written so far. */
export function LiveTurn({ live, kind, stopping, onStop, only }: {
  live: LiveOutputState;
  kind: OutputKind;
  stopping: boolean;
  onStop: () => void;
  only?: number | null | undefined;
}) {
  const waiting = live.actions.length === 0 && !live.reply;
  return <>
    <div className="aw-message is-assistant" role="status"><span className="aw-agent-mark"><BrandMark size={17} busy /></span><div className="artifact-reply">
      {live.actions.length > 0 ? <TurnSteps steps={live.actions} /> : <p>{outputActivity(live)}</p>}
      <StudioLive state={live} />
      {live.reply && <AnswerBody content={withoutMarks(live.reply)} numbers={[]} />}
      {waiting && live.sections.length === 0 && <div className="live-wait"><span><i /><i /><i /></span><small>Working out what to do…</small></div>}
    </div></div>
    <details className="artifact-live-details"><summary>Generation details</summary><LiveSections state={live} kind={kind} stopping={stopping} onStop={onStop} only={only} /></details>
  </>;
}
