import type { SessionMessage } from "@gunther/contracts";
import { CircleAlert, FileText, Loader2, Pause, Play, RotateCw, Volume2, X } from "lucide-react";
import { useState } from "react";
import { knowledgeApi } from "../api";
import { readAloud, useReadAloud } from "./readAloud";

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

/**
 * Reads an answer aloud. Idle it is one Listen button; while reading it opens into a small
 * player (pause, progress, record again, what is spoken, stop), so its controls are plainly
 * about the audio and never mistaken for answering again.
 */
export function SpeakerButton({ message }: { message: Pick<SessionMessage, "id" | "sessionId"> }) {
  const { status, error, part, parts, progress, elapsed, source } = useReadAloud(message.id);
  const [script, setScript] = useState<{ text: string | null; problem: string | null } | null>(null);
  const toggle = (again = false) => (event: React.MouseEvent) => { event.stopPropagation(); void readAloud.toggle(message, again); };

  const showScript = async (event: React.MouseEvent) => {
    event.stopPropagation();
    if (script) { setScript(null); return; }
    if (!source) return;
    setScript({ text: null, problem: null });
    try {
      setScript({ text: (await knowledgeApi.speechScript(source)).script, problem: null });
    } catch (reason) {
      setScript({ text: null, problem: reason instanceof Error ? reason.message : "What was spoken could not be loaded." });
    }
  };

  const scriptPanel = script && <div className="speak-script" onClick={(event) => event.stopPropagation()}>
    <header><strong>What is spoken</strong><button type="button" className="speak-control" aria-label="Close what is spoken" onClick={() => setScript(null)}><X size={12} /></button></header>
    {script.problem ? <p className="is-problem">{script.problem}</p> : script.text === null ? <p>Loading…</p> : <p>{script.text}</p>}
  </div>;

  if (status === null || status === "error") {
    return <>
      <button type="button" className={`copy-answer speak-answer ${status === "error" ? "is-error" : ""}`} aria-label={status === "error" ? "Try again" : "Read aloud"} title={error ?? "Read aloud"} onClick={toggle()}>
        {status === "error" ? <CircleAlert size={12} /> : <Volume2 size={12} />}{status === "error" ? "Retry" : "Listen"}
      </button>
      {status === "error" && error && <span className="speak-error" role="alert">{error}</span>}
      {scriptPanel}
    </>;
  }

  const making = status === "making";
  const primary = making ? "Preparing… (click to cancel)" : status === "playing" ? "Pause" : "Resume";
  const Icon = making ? Loader2 : status === "playing" ? Pause : Play;
  const where = parts > 1 ? `${part + 1}/${parts}` : elapsed > 0 ? clock(elapsed) : making ? "Preparing…" : "";
  return <>
    <span className={`speak-player is-${status}`} role="group" aria-label="Reading aloud" onClick={(event) => event.stopPropagation()}>
      <button type="button" className="speak-control" aria-label={primary} title={primary} onClick={toggle()}>
        <Icon size={13} className={making ? "is-spinning" : undefined} />
      </button>
      <span className="speak-progress" role="progressbar" aria-label="How far it has read" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)}>
        <i style={{ width: `${Math.round(progress * 100)}%` }} />
      </span>
      {where && <small className="speak-where" title={parts > 1 ? "Long answers are read part by part as they are made" : undefined}>{where}</small>}
      <button type="button" className="speak-control" aria-label="Record again" title="Throw away this recording and make it again" onClick={toggle(true)}><RotateCw size={12} /></button>
      <button type="button" className="speak-control" aria-label="What is spoken" aria-pressed={Boolean(script)} title="Show what is spoken, e.g. how a table was put into words" disabled={!source} onClick={(event) => void showScript(event)}><FileText size={12} /></button>
      <button type="button" className="speak-control" aria-label="Stop" title="Stop" onClick={(event) => { event.stopPropagation(); readAloud.stop(); }}><X size={12} /></button>
    </span>
    {scriptPanel}
  </>;
}
