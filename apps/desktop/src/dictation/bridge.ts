import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";

export interface DictationRequest { serial: number; listening: boolean }
export const listenForDictation = (handler: (request: DictationRequest) => void) =>
  listen<DictationRequest>("gunther://dictation-request", (event) => handler(event.payload));
export const pendingDictationRequest = () => invoke<DictationRequest>("dictation_request");
export const stopDictation = () => invoke<void>("stop_dictation_window");
export const finishDictation = (serial: number) => invoke<void>("dictation_finished", { serial });
export const copyDictation = (text: string) => invoke<void>("copy_dictation_text", { text });
export const dragDictation = () => getCurrentWindow().startDragging();
