import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";

interface TakeRequest {
  revision: number;
  takeId: number;
}

type DictationControl = TakeRequest & { action: "idle" | "start" | "stop" };
type OutcomeNotification = TakeRequest & { action: "outcome" };

export type DictationEvent = DictationControl | OutcomeNotification;
export type DictationRequest = DictationControl | (OutcomeNotification & { outcome: VisibleOutcome });

export interface VisibleOutcome {
  kind: "error" | "copy_failed" | "copied_presentation_failed" | "invalid_text";
  copied: boolean;
  text?: string;
  message?: string;
}

export type DictationOutcome =
  | { kind: "completed"; text: string }
  | { kind: "error"; text: string; message: string }
  | { kind: "no_speech" | "cancelled" };

export interface CompleteResult {
  status: "applied" | "ignored" | "copy_failed" | "invalid_text" | "presentation_failed";
  copied: boolean;
  request?: DictationRequest;
}

export const listenForDictation = (handler: (event: DictationEvent) => void) =>
  listen<DictationEvent>("gunther://dictation-request", (event) => handler(event.payload));
export const pendingDictationRequest = () => invoke<DictationRequest>("dictation_request");
export const stopDictation = (takeId: number, revision: number) =>
  invoke<void>("stop_dictation_window", { takeId, revision });
export const completeDictation = (takeId: number, outcome: DictationOutcome) =>
  invoke<CompleteResult>("complete_dictation", { takeId, outcome });
export const dragDictation = () => getCurrentWindow().startDragging();
