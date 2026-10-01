import { invoke } from "@tauri-apps/api/core";
import { describeError, log } from "../log";

export type SpeakerAccess = "granted" | "denied" | "restricted" | "undetermined";

/** Shown while reading aloud when macOS was told no. */
export const SPEAKER_HINT = "No sound from your Mac's speakers? macOS keeps Gunther off them until it may use the microphone: System Settings → Privacy & Security → Microphone → Gunther. Earphones play either way.";

let asked: Promise<SpeakerAccess> | null = null;

/**
 * Before Gunther's first sound on a Mac, ask for the microphone permission if macOS has not
 * asked yet: without it the built-in speakers stay silent while earphones play (see
 * src-tauri/src/microphone_access.rs). Asking turns no microphone on. Asked once per launch;
 * elsewhere it is "granted" at once.
 */
export function allowSpeakerSound(): Promise<SpeakerAccess> {
  if (!("__TAURI_INTERNALS__" in window) || !/Mac/i.test(navigator.userAgent)) return Promise.resolve("granted");
  asked ??= invoke<SpeakerAccess>("ask_microphone_access").then((access) => {
    log.info("read-aloud", `microphone permission, which the Mac's speakers need: ${access}`);
    // Not answered: ask again before the next sound.
    if (access === "undetermined") asked = null;
    return access;
  }, (reason: unknown) => {
    log.warn("read-aloud", `could not ask for the microphone permission: ${describeError(reason)}`);
    asked = null;
    return "undetermined";
  });
  return asked;
}
