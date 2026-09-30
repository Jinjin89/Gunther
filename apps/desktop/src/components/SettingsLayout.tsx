import { AudioLines, Brain, HardDrive, Search, Settings2, Smartphone } from "lucide-react";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { knowledgeApi } from "../api";
import { MODELS_CHANGED_EVENT, OPEN_SETTINGS_EVENT } from "../models/askModel";

export type SettingsPaneId = "general" | "models" | "voice" | "search" | "library" | "devices";

export interface SettingsPane {
  id: SettingsPaneId;
  label: string;
  description: string;
  icon: ReactNode;
}

/** Settings, one topic at a time, in the order people usually need them. */
export const SETTINGS_PANES: SettingsPane[] = [
  { id: "general", label: "General", description: "How Gunther looks and where it sits on this computer.", icon: <Settings2 size={15} /> },
  { id: "models", label: "Models", description: "The language models that answer, read and summarize, and where they come from.", icon: <Brain size={15} /> },
  { id: "voice", label: "Voice", description: "How Gunther hears you and reads answers aloud.", icon: <AudioLines size={15} /> },
  { id: "search", label: "Search", description: "Finding things by meaning in your libraries, and on the web.", icon: <Search size={15} /> },
  { id: "library", label: "Library & storage", description: "Where your libraries and their files live on this computer.", icon: <HardDrive size={15} /> },
  { id: "devices", label: "Devices", description: "Phones paired with this computer.", icon: <Smartphone size={15} /> },
];

const PANE_KEY = "gunther:settings-pane";
const isPane = (value: unknown): value is SettingsPaneId => SETTINGS_PANES.some((pane) => pane.id === value);

const rememberedPane = (): SettingsPaneId => {
  try {
    const saved = window.localStorage.getItem(PANE_KEY);
    return isPane(saved) ? saved : "general";
  } catch {
    return "general";
  }
};

/** Open Settings on one topic, from anywhere: `openSettings("voice")`. */
export function openSettings(pane?: SettingsPaneId) {
  if (pane) {
    try { window.localStorage.setItem(PANE_KEY, pane); } catch { /* opens on the last topic instead */ }
  }
  window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: pane ? { pane } : undefined }));
}

/** The topic on screen, remembered on this device and changed by `openSettings`. */
export function useSettingsPane() {
  const [pane, setPaneState] = useState<SettingsPaneId>(rememberedPane);
  const setPane = useCallback((next: SettingsPaneId) => {
    setPaneState(next);
    try { window.localStorage.setItem(PANE_KEY, next); } catch { /* still shown, just not remembered */ }
  }, []);
  useEffect(() => {
    const onOpen = (event: Event) => {
      const wanted = (event as CustomEvent<{ pane?: unknown } | undefined>).detail?.pane;
      if (isPane(wanted)) setPaneState(wanted);
    };
    window.addEventListener(OPEN_SETTINGS_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_SETTINGS_EVENT, onOpen);
  }, []);
  return [pane, setPane] as const;
}

/** Topics with a job that cannot run, e.g. a model that needs a key; a dot marks them. */
export function useSettingsAttention(pane: SettingsPaneId): Partial<Record<SettingsPaneId, string>> {
  const [attention, setAttention] = useState<Partial<Record<SettingsPaneId, string>>>({});
  useEffect(() => {
    let active = true;
    const check = () => void Promise.allSettled([knowledgeApi.modelsOverview(), knowledgeApi.speechOverview(), knowledgeApi.ttsOverview()]).then(([models, speech, tts]) => {
      if (!active) return;
      const next: Partial<Record<SettingsPaneId, string>> = {};
      const modelProblem = models.status === "fulfilled" ? models.value.roles.find((role) => role.problem)?.problem : null;
      if (modelProblem) next.models = modelProblem;
      const speechProblem = speech.status === "fulfilled" ? speech.value.roles.find((role) => role.problem)?.problem : null;
      // Read aloud that is off is not a problem; a chosen voice that cannot speak is.
      const ttsProblem = tts.status === "fulfilled" ? tts.value.roles.find((role) => role.model && role.problem)?.problem : null;
      if (speechProblem ?? ttsProblem) next.voice = (speechProblem ?? ttsProblem) as string;
      setAttention(next);
    });
    check();
    window.addEventListener(MODELS_CHANGED_EVENT, check);
    return () => { active = false; window.removeEventListener(MODELS_CHANGED_EVENT, check); };
    // Checked again on each topic change: a fix made in one topic clears its dot when you move on.
  }, [pane]);
  return attention;
}

export function SettingsNav({ pane, attention, onPane }: { pane: SettingsPaneId; attention: Partial<Record<SettingsPaneId, string>>; onPane: (pane: SettingsPaneId) => void }) {
  return <nav className="settings-nav" aria-label="Settings topics">
    {SETTINGS_PANES.map((item) => <button key={item.id} type="button" className="settings-nav-item" aria-current={pane === item.id ? "page" : undefined} onClick={() => onPane(item.id)}>
      <span className="settings-nav-icon" aria-hidden="true">{item.icon}</span>
      <span className="settings-nav-label">{item.label}</span>
      {attention[item.id] && <i className="settings-nav-dot" title={attention[item.id]} aria-label="Needs attention" />}
    </button>)}
  </nav>;
}
