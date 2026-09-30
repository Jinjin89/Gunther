import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { reconnectBackend } from "./api";

/** Where the libraries live is the desktop app's choice; the backend reads it as LIBRARY_ROOT. */
export interface LibraryLocation {
  /** The folder chosen in Settings, or null for the default (~/Gunther). */
  chosen: string | null;
  /** Only the installed app starts its own backend, so only it can move the libraries. */
  canChange: boolean;
}

const desktopApp = () => "__TAURI_INTERNALS__" in window;

export async function libraryLocation(): Promise<LibraryLocation | null> {
  if (!desktopApp()) return null;
  try {
    return await invoke<LibraryLocation>("library_location");
  } catch {
    return null;
  }
}

/** Ask for a folder, and say where the libraries would go in it (or why they cannot). */
export async function pickLibraryDestination(current: string): Promise<string | null> {
  const picked = await open({ directory: true, multiple: false, defaultPath: current, title: "Choose where your libraries live" });
  if (typeof picked !== "string") return null;
  return invoke<string>("library_destination", { from: current, picked });
}

/** Move the libraries there. The local service restarts meanwhile; this waits until it is back. */
export async function moveLibraries(from: string, to: string): Promise<string> {
  try {
    return await invoke<string>("change_library_location", { from, to });
  } finally {
    // Moved or not, the service was restarted with a new token.
    await reconnectBackend();
  }
}
