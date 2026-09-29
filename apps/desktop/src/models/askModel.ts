import type { Effort, EffortLevel, ModelMenu, SessionMessage } from "@gunther/contracts";
import { EFFORTS } from "@gunther/contracts";
import { useCallback, useEffect, useState } from "react";
import { knowledgeApi } from "../api";

/** Opens Settings from anywhere (the model menu's "Set up a model"). */
export const OPEN_SETTINGS_EVENT = "gunther:open-settings";
/** The model and effort this device last chose for Ask; new conversations start from it. */
export const ASK_CHOICE_STORAGE_KEY = "gunther.askModel";
const ASK_CHOICE_EVENT = "gunther:ask-model";
/** Providers or jobs changed in Settings: model menus and statuses reload. */
export const MODELS_CHANGED_EVENT = "gunther:models-changed";

export interface AskChoice {
  model: string | null;
  effort: Effort;
}

export const EFFORT_NAMES: Record<Effort, string> = { off: "Off", low: "Low", medium: "Medium", high: "High", max: "Max" };

/**
 * The level a model is sent for the effort asked, as the service decides it:
 * the same level if the model has it, else the nearest (the stronger of two);
 * asking for any thinking never turns thinking off. Null: no setting at all.
 */
export function resolveEffort(effort: Effort, levels: EffortLevel[]): EffortLevel | null {
  if (!levels.length) return null;
  const candidates = effort === "off" ? levels : levels.filter((level) => level.id !== "off");
  const exact = candidates.find((level) => level.id === effort);
  if (exact) return exact;
  const wanted = EFFORTS.indexOf(effort);
  return [...candidates].sort((a, b) => {
    const distance = Math.abs(EFFORTS.indexOf(a.id) - wanted) - Math.abs(EFFORTS.indexOf(b.id) - wanted);
    return distance || EFFORTS.indexOf(b.id) - EFFORTS.indexOf(a.id);
  })[0] ?? null;
}

export function readDeviceChoice(): AskChoice | null {
  try {
    const stored = JSON.parse(window.localStorage.getItem(ASK_CHOICE_STORAGE_KEY) ?? "null") as Partial<AskChoice> | null;
    if (!stored || !EFFORTS.includes(stored.effort as Effort)) return null;
    return { model: typeof stored.model === "string" ? stored.model : null, effort: stored.effort as Effort };
  } catch {
    return null;
  }
}

export function writeDeviceChoice(choice: AskChoice) {
  try {
    window.localStorage.setItem(ASK_CHOICE_STORAGE_KEY, JSON.stringify(choice));
  } catch {
    // Private windows may refuse storage; the choice still holds for this conversation.
  }
  window.dispatchEvent(new CustomEvent(ASK_CHOICE_EVENT));
}

/** A choice the menu can honour: a model that is still set up, else the Ask default. */
export function usableChoice(menu: ModelMenu | null, ...candidates: (AskChoice | null | undefined)[]): AskChoice {
  const fallback: AskChoice = menu?.default ?? { model: menu?.models[0]?.ref ?? null, effort: "high" };
  for (const candidate of candidates) {
    if (candidate?.model && menu?.models.some((model) => model.ref === candidate.model)) return candidate;
  }
  return fallback;
}

/** A conversation continues with the model and effort of its last answer. */
export function lastAnswerChoice(messages: SessionMessage[]): AskChoice | null {
  const last = [...messages].reverse().find((message) => message.role === "assistant" && message.context.model);
  if (!last?.context.model) return null;
  return { model: last.context.model, effort: last.context.effort ?? "high" };
}

/** Tell every model menu that providers or jobs changed (after a save in Settings). */
export function announceModelsChanged() {
  window.dispatchEvent(new CustomEvent(MODELS_CHANGED_EVENT));
}

/** The models Ask can use now, kept fresh when Settings saves. */
export function useModelMenu() {
  const [menu, setMenu] = useState<ModelMenu | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const load = useCallback(() => {
    void Promise.resolve().then(() => knowledgeApi.modelMenu()).then((next) => {
      setMenu(next);
      setUnavailable(false);
    }).catch(() => setUnavailable(true));
  }, []);
  useEffect(() => {
    load();
    window.addEventListener(MODELS_CHANGED_EVENT, load);
    return () => window.removeEventListener(MODELS_CHANGED_EVENT, load);
  }, [load]);
  return { menu, unavailable };
}

/** This device's Ask choice, shared by Home and every conversation. */
export function useDeviceChoice(): [AskChoice | null, (choice: AskChoice) => void] {
  const [choice, setChoice] = useState<AskChoice | null>(() => readDeviceChoice());
  useEffect(() => {
    const sync = () => setChoice(readDeviceChoice());
    window.addEventListener(ASK_CHOICE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(ASK_CHOICE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return [choice, writeDeviceChoice];
}
