import type { SessionMessage } from "@gunther/contracts";
import { CircleAlert, Loader2, Pause, Play, Volume2 } from "lucide-react";
import { readAloud, useReadAloud } from "./readAloud";

const LABELS = { idle: "Read aloud", making: "Preparing… (click to cancel)", playing: "Pause", paused: "Resume", error: "Try again" } as const;

/** Reads an answer aloud: the first time it is made, after that only played. */
export function SpeakerButton({ message }: { message: Pick<SessionMessage, "id" | "sessionId"> }) {
  const { status, error } = useReadAloud(message.id);
  const key = status ?? "idle";
  const Icon = status === "making" ? Loader2 : status === "playing" ? Pause : status === "paused" ? Play : status === "error" ? CircleAlert : Volume2;
  return <>
    <button type="button" className={`copy-answer speak-answer is-${key}`} aria-label={LABELS[key]} title={error ?? LABELS[key]} onClick={(event) => { event.stopPropagation(); void readAloud.toggle(message); }}>
      <Icon size={12} className={status === "making" ? "is-spinning" : undefined} />{key === "making" ? "Preparing…" : key === "playing" ? "Pause" : key === "paused" ? "Resume" : key === "error" ? "Retry" : "Listen"}
    </button>
    {status === "error" && error && <span className="speak-error" role="alert">{error}</span>}
  </>;
}
