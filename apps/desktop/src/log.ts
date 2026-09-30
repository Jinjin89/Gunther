import { invoke } from "@tauri-apps/api/core";
import { error as logError, info as logInfo, warn as logWarn } from "@tauri-apps/plugin-log";

/**
 * Lines for the app's log files (see src-tauri/src/app_log.rs): each day's
 * `…app.log` in the library's `.gunther/logs`, between the shell's own lines and
 * next to the knowledge service's. Outside the desktop app they go to the console.
 * Writing a line never throws and never waits.
 */

type Level = "info" | "warn" | "error";

const native = "__TAURI_INTERNALS__" in window;
const toFile = { info: logInfo, warn: logWarn, error: logError };
// Kept before forwarding wraps console.warn/error, so a line is never sent twice.
const toConsole = { info: console.debug.bind(console), warn: console.warn.bind(console), error: console.error.bind(console) };
const LONGEST = 8_000;

function write(level: Level, area: string, message: string) {
  const line = `${area}: ${message}`.slice(0, LONGEST);
  if (!native) {
    toConsole[level](line);
    return;
  }
  void toFile[level](line).catch(() => undefined);
}

export const log = {
  info: (area: string, message: string) => write("info", area, message),
  warn: (area: string, message: string) => write("warn", area, message),
  error: (area: string, message: string) => write("error", area, message),
};

/** An error in one line and its stack below, for the log. */
export function describeError(reason: unknown): string {
  if (reason instanceof Error) return `${reason.name}: ${reason.message}${reason.stack ? `\n${reason.stack}` : ""}`;
  if (typeof reason === "string") return reason;
  try {
    return JSON.stringify(reason);
  } catch {
    return String(reason);
  }
}

const shown = (value: unknown) => (typeof value === "string" ? value : describeError(value));

/** Keep what would otherwise only reach the web inspector: uncaught errors, rejections, console warnings. */
export function installLogForwarding() {
  if (!native) return;
  window.addEventListener("error", (event) => write("error", "uncaught", describeError(event.error ?? event.message)));
  window.addEventListener("unhandledrejection", (event) => write("error", "unhandled", describeError(event.reason)));
  for (const level of ["warn", "error"] as const) {
    console[level] = (...values: unknown[]) => {
      toConsole[level](...values);
      write(level, "console", values.map(shown).join(" "));
    };
  }
}

/** Where the log files are, in the desktop app; null elsewhere. */
export async function logsFolder(): Promise<string | null> {
  if (!native) return null;
  try {
    return await invoke<string | null>("logs_folder");
  } catch {
    return null;
  }
}

/** Open the log folder in Finder (or the system's file manager). */
export const showLogsFolder = (): Promise<void> => invoke("show_logs_folder");
