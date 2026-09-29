import { invoke } from "@tauri-apps/api/core";
import { emit, listen, type UnlistenFn } from "@tauri-apps/api/event";
import type {
  CaptureControl,
  CaptureLaunchRequest,
  CaptureRuntimeStatus,
  CaptureSavedEvent,
} from "./captureTypes";

export const CAPTURE_REQUEST_EVENT = "gunther://capture-request";
export const CAPTURE_CONTROL_EVENT = "gunther://capture-control";
export const CAPTURE_SAVED_EVENT = "gunther://capture-saved";
export const OPEN_SEARCH_EVENT = "gunther://open-search";
export const MENU_COMMAND_EVENT = "gunther://menu-command";

/** Commands the native menus hand to the main window. */
export type MenuCommand = "new-note" | "settings" | "shortcuts" | "theme";
/** Whether Gunther's menu bar item stays visible when nothing is being captured. */
export type MenuBarMode = "always" | "whileCapturing";

export function listenForMenuCommand(handler: (command: MenuCommand) => void): Promise<UnlistenFn> {
  return listen<MenuCommand>(MENU_COMMAND_EVENT, (event) => handler(event.payload));
}

export async function getMenuBarMode(): Promise<MenuBarMode> {
  return invoke<MenuBarMode>("menu_bar_mode");
}

export async function setMenuBarMode(mode: MenuBarMode): Promise<void> {
  await invoke("set_menu_bar_mode", { mode });
}

export function isTauriRuntime(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

export async function openCaptureWindow(request: CaptureLaunchRequest): Promise<void> {
  await invoke("open_capture_window", { request });
}

export async function hideCaptureWindow(): Promise<void> {
  await invoke("hide_capture_window");
}

export async function showMainWindow(): Promise<void> {
  await invoke("show_main_window");
}

export async function takeCaptureLaunchRequest(): Promise<CaptureLaunchRequest | null> {
  return invoke<CaptureLaunchRequest | null>("take_capture_launch_request");
}

export async function updateCaptureStatus(status: CaptureRuntimeStatus): Promise<void> {
  await invoke("update_capture_status", { status });
}

export async function emitCaptureSaved(message: string, captureContinues = false): Promise<void> {
  await emit(CAPTURE_SAVED_EVENT, { message, captureContinues } satisfies CaptureSavedEvent);
}

export async function emitOpenSearch(): Promise<void> {
  await emit(OPEN_SEARCH_EVENT);
}

export function listenForCaptureRequest(
  handler: (request: CaptureLaunchRequest) => void,
): Promise<UnlistenFn> {
  return listen<CaptureLaunchRequest>(CAPTURE_REQUEST_EVENT, (event) => handler(event.payload));
}

export function listenForCaptureControl(
  handler: (control: CaptureControl) => void,
): Promise<UnlistenFn> {
  return listen<CaptureControl>(CAPTURE_CONTROL_EVENT, (event) => handler(event.payload));
}

export function listenForCaptureSaved(
  handler: (event: CaptureSavedEvent) => void,
): Promise<UnlistenFn> {
  return listen<CaptureSavedEvent>(CAPTURE_SAVED_EVENT, (event) => handler(event.payload));
}

export function listenForOpenSearch(handler: () => void): Promise<UnlistenFn> {
  return listen(OPEN_SEARCH_EVENT, handler);
}
