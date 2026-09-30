import { LoaderCircle, Mic, Square } from "lucide-react";
import type { CSSProperties } from "react";
import type { DictationState } from "../services/useDictation";

/** The mic button's icon: a mic to start, a stop square while listening, a spinner while it gets ready or finishes. */
export function MicGlyph({ state, size = 15 }: { state: DictationState; size?: number }) {
  if (state === "listening") return <Square size={size - 3} fill="currentColor" aria-hidden />;
  if (state === "starting" || state === "finishing") return <LoaderCircle size={size} className="spin" aria-hidden />;
  return <Mic size={size} aria-hidden />;
}

const WORDS: Record<Exclude<DictationState, "idle">, string> = {
  starting: "Starting the microphone…",
  listening: "Listening — speak now. Click the mic or ⌘⇧M to stop",
  finishing: "Finishing…",
};

/** Says that voice input is on, and moves with your voice, so it can't be missed. */
export function DictationStatus({ state, level, className = "" }: { state: DictationState; level: number; className?: string }) {
  if (state === "idle") return null;
  return <span className={`gx-dictation is-${state} ${className}`} role="status">
    <span className="gx-dictation-bars" style={{ "--level": state === "listening" ? level : 0 } as CSSProperties} aria-hidden><i /><i /><i /><i /></span>
    <span>{WORDS[state]}</span>
  </span>;
}
