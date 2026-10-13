import type { Artifact, ModelMenu, OutputKind } from "@gunther/contracts";
import { ArrowUp, SlidersHorizontal, Square } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { DictationStatus, MicGlyph } from "../components/Dictation";
import { ModelPicker } from "../models/ModelPicker";
import type { AskChoice } from "../models/askModel";
import { useDictatedField } from "../services/useDictatedField";

export type ArtifactActionMode = "create" | "modify" | "rebuild";
export function ArtifactComposer({ draft, onDraft, mode, onMode, kind, onKind, artifact, scopeText, menu, choice, onChoice, disabled, busy, stopping, onSend, onStop, settings, settingsOpen, onSettings, focusRequest = 0, allowVoiceInput = true }: {
  draft: string; onDraft: (value: string) => void; mode: ArtifactActionMode; onMode: (mode: ArtifactActionMode) => void;
  kind: OutputKind; onKind: (kind: OutputKind) => void; artifact: Artifact | null; scopeText: string;
  menu: ModelMenu | null; choice: AskChoice; onChoice: (choice: AskChoice) => void;
  disabled: boolean; busy: boolean; stopping: boolean; onSend: (text: string, section: number | null) => void; onStop: () => void;
  settings: ReactNode; settingsOpen: boolean; onSettings: () => void; focusRequest?: number; allowVoiceInput?: boolean;
}) {
  const field = useRef<HTMLTextAreaElement>(null);
  const dictation = useDictatedField(draft, onDraft, { enabled: allowVoiceInput && !disabled && !busy });
  const [part, setPart] = useState<number | null>(null);
  useEffect(() => setPart(null), [artifact?.id, mode]);
  useEffect(() => { if (focusRequest) field.current?.focus(); }, [focusRequest]);
  const send = () => { if (disabled || busy || !draft.trim() || dictation.dictating) return; dictation.discard(); onSend(draft.trim(), mode === "modify" ? part : null); };
  return <>
    {settingsOpen && settings}
    <form className="aw-composer" onSubmit={(event) => { event.preventDefault(); send(); }}>
      <div className="aw-composer-target">
        <select aria-label="Conversation action" value={mode} disabled={busy} onChange={(event) => onMode(event.target.value as ArtifactActionMode)}>
          <option value="create">{artifact ? "Create another artifact" : "Create artifact"}</option><option value="modify" disabled={!artifact || artifact.origin === "legacy"}>Modify selected artifact</option><option value="rebuild" disabled={!artifact}>Rebuild from sources</option>
        </select>
        {mode !== "modify" && <select aria-label="New artifact format" value={kind} disabled={busy} onChange={(event) => onKind(event.target.value as OutputKind)}><option value="report">Report / PDF</option><option value="slides">Slides</option></select>}
        {mode === "modify" && artifact && <select aria-label="Which part" value={part ?? ""} disabled={busy} onChange={(event) => setPart(event.target.value === "" ? null : Number(event.target.value))}><option value="">Whole {artifact.kind === "slides" ? "deck" : "report"}</option>{artifact.sections.map((section) => <option key={section.index} value={section.index}>{section.heading}</option>)}</select>}
        <button type="button" className="gx-icon-button" aria-label="Artifact settings" aria-expanded={settingsOpen} onClick={onSettings}><SlidersHorizontal size={15} /></button>
      </div>
      <div className={`aw-input-box${dictation.dictating ? " is-dictating" : ""}`}>
        <textarea ref={field} aria-label="Artifact message" placeholder={mode === "modify" ? "Describe what to change…" : "Describe the report or slides you need…"} value={draft} onChange={(event) => onDraft(event.target.value)} onFocus={dictation.claim} rows={3} maxLength={2000} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} />
        <div className="aw-input-context">{mode === "modify" && artifact ? `${artifact.title} · v${artifact.versionNumber} · saved sources` : scopeText}</div>
        <footer><div className="aw-model-controls"><ModelPicker menu={menu} choice={choice} onChange={onChoice} disabled={busy} /></div><div className="aw-input-actions"><button type="button" className={`aw-voice-button${dictation.dictating ? " is-on" : ""}`} aria-label={dictation.listening ? "Stop voice input" : "Voice input"} aria-pressed={dictation.listening} disabled={!allowVoiceInput || disabled || busy} title={!allowVoiceInput ? "Microphone input is available in the desktop app" : dictation.error ?? "Speak instead of typing · ⌘⇧M"} onClick={dictation.toggle}><MicGlyph state={dictation.state} size={16} /></button><button type={busy ? "button" : "submit"} className="gx-btn gx-btn-primary aw-send-button" aria-label={busy ? "Stop generation" : "Send message"} disabled={busy ? stopping : disabled || !draft.trim() || dictation.dictating} onClick={busy ? onStop : undefined}>{busy ? <Square size={12} fill="currentColor" /> : <ArrowUp size={19} />}</button></div></footer>
      </div>
      <div className="aw-composer-help" role={dictation.error ? "alert" : undefined}>{dictation.error ?? (dictation.dictating ? <DictationStatus state={dictation.state} level={dictation.level} /> : mode === "modify" ? "Each change saves a new version. Rebuild to use your current selection." : "Enter to send · Shift + Enter for a new line")}</div>
    </form>
  </>;
}
