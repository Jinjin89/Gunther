import type { ModelMenu } from "@gunther/contracts";
import { ArrowUp, SlidersHorizontal, Square, X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { DictationStatus, MicGlyph } from "../components/Dictation";
import { ModelPicker } from "../models/ModelPicker";
import type { AskChoice } from "../models/askModel";
import { useDictatedField } from "../services/useDictatedField";

/**
 * The one message box of an Artifacts conversation. What the message is (a change, a question,
 * a new report or deck, an answer to the agent's question) is the agent's to work out; the box
 * only says what the message is about, when the person has pointed at something.
 */
export function ArtifactComposer({ draft, onDraft, aboutLabel, onClearAbout, scopeText, menu, choice, onChoice, disabled, busy, stopping, answering, onSend, onStop, settings, settingsOpen, onSettings, focusRequest = 0, allowVoiceInput = true }: {
  draft: string; onDraft: (value: string) => void;
  /** What the message is about ("Slide 4 · Costs"), when the person pointed at it. */
  aboutLabel: string | null; onClearAbout: () => void;
  scopeText: string;
  menu: ModelMenu | null; choice: AskChoice; onChoice: (choice: AskChoice) => void;
  disabled: boolean; busy: boolean; stopping: boolean;
  /** The agent asked a question: what is typed answers it. */
  answering: boolean;
  onSend: (text: string) => void; onStop: () => void;
  settings: ReactNode; settingsOpen: boolean; onSettings: () => void; focusRequest?: number; allowVoiceInput?: boolean;
}) {
  const field = useRef<HTMLTextAreaElement>(null);
  const dictation = useDictatedField(draft, onDraft, { enabled: allowVoiceInput && !disabled && !busy });
  useEffect(() => { if (focusRequest) field.current?.focus(); }, [focusRequest]);
  // Pointing at something puts the cursor back in the box, ready to say what is wanted.
  useEffect(() => { if (aboutLabel) field.current?.focus(); }, [aboutLabel]);
  const send = () => { if (disabled || busy || !draft.trim() || dictation.dictating) return; dictation.discard(); onSend(draft.trim()); };
  return <>
    {settingsOpen && settings}
    <form className="aw-composer" onSubmit={(event) => { event.preventDefault(); send(); }}>
      <div className={`aw-input-box${dictation.dictating ? " is-dictating" : ""}`}>
        {aboutLabel && <div className="aw-focus-chip" role="group" aria-label="About"><span title={aboutLabel}>{aboutLabel}</span><button type="button" className="gx-icon-button" aria-label="Clear what this is about" onClick={onClearAbout}><X size={12} /></button></div>}
        <textarea ref={field} aria-label="Artifact message" placeholder="Ask for a change, a question, or a new report or deck…" value={draft} onChange={(event) => onDraft(event.target.value)} onFocus={dictation.claim} rows={3} maxLength={2000} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} />
        <div className="aw-input-context">{scopeText}</div>
        <footer><div className="aw-model-controls"><ModelPicker menu={menu} choice={choice} onChange={onChoice} disabled={busy} /></div><div className="aw-input-actions"><button type="button" className={`gx-icon-button${settingsOpen ? " is-selected" : ""}`} aria-label="Artifact settings" aria-expanded={settingsOpen} onClick={onSettings}><SlidersHorizontal size={15} /></button><button type="button" className={`aw-voice-button${dictation.dictating ? " is-on" : ""}`} aria-label={dictation.listening ? "Stop voice input" : "Voice input"} aria-pressed={dictation.listening} disabled={!allowVoiceInput || disabled || busy} title={!allowVoiceInput ? "Microphone input is available in the desktop app" : dictation.error ?? "Speak instead of typing · ⌘⇧M"} onClick={dictation.toggle}><MicGlyph state={dictation.state} size={16} /></button><button type={busy ? "button" : "submit"} className="gx-btn gx-btn-primary aw-send-button" aria-label={busy ? "Stop generation" : "Send message"} disabled={busy ? stopping : disabled || !draft.trim() || dictation.dictating} onClick={busy ? onStop : undefined}>{busy ? <Square size={12} fill="currentColor" /> : <ArrowUp size={19} />}</button></div></footer>
      </div>
      <div className="aw-composer-help" role={dictation.error ? "alert" : undefined}>{dictation.error ?? (dictation.dictating ? <DictationStatus state={dictation.state} level={dictation.level} /> : answering ? "Click an option above, or type your answer · Enter to send" : "Enter to send · Shift + Enter for a new line")}</div>
    </form>
  </>;
}
