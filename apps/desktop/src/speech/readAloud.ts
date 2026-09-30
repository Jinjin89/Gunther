import type { SessionMessage } from "@gunther/contracts";
import { useSyncExternalStore } from "react";
import { knowledgeApi } from "../api";

/**
 * Reading answers aloud, one at a time for the whole app.
 *
 * The service makes the audio the first time and keeps it on disk; asking again
 * only fetches it. Here we keep the few most recent files in memory so pausing,
 * resuming and replaying are instant.
 */

export type ReadStatus = "making" | "playing" | "paused" | "error";
interface State { messageId: string | null; status: ReadStatus | null; error: string | null }

const IDLE: State = { messageId: null, status: null, error: null };
const MEMORY_CLIPS = 6;

let state: State = IDLE;
const listeners = new Set<() => void>();
let audio: HTMLAudioElement | null = null;
/** Bumped by every start and stop, so a slow answer never plays after it was cancelled. */
let turn = 0;
const urls = new Map<string, string>();

const set = (next: State) => {
  state = next;
  listeners.forEach((listener) => listener());
};

async function clipUrl(clipId: string): Promise<string> {
  const known = urls.get(clipId);
  if (known) return known;
  const url = URL.createObjectURL(await knowledgeApi.speechAudio(clipId));
  urls.set(clipId, url);
  for (const [oldest, oldUrl] of urls) {
    if (urls.size <= MEMORY_CLIPS) break;
    URL.revokeObjectURL(oldUrl);
    urls.delete(oldest);
  }
  return url;
}

function release() {
  if (audio) {
    audio.onended = null;
    audio.onerror = null;
    audio.pause();
    audio = null;
  }
}

/**
 * Play the parts of an answer one after another, fetching the next while one plays.
 * `temporary` parts are freed once played; a kept clip's address is reused for replays.
 */
async function playParts(mine: number, message: Pick<SessionMessage, "id">, count: number, part: (index: number) => Promise<string>, temporary: boolean, onStart: () => void) {
  const free = (url: string) => { if (temporary) URL.revokeObjectURL(url); };
  let next = part(0);
  next.catch(() => undefined);
  for (let index = 0; index < count; index += 1) {
    if (index > 0) set({ messageId: message.id, status: "making", error: null });
    const url = await next;
    if (mine !== turn) { free(url); return; }
    if (index + 1 < count) { next = part(index + 1); next.catch(() => undefined); }
    await new Promise<void>((resolve, reject) => {
      const player = new Audio(url);
      audio = player;
      player.onended = () => { free(url); resolve(); };
      player.onerror = () => reject(new Error("This audio could not be played."));
      player.play().then(() => { if (mine === turn) set({ messageId: message.id, status: "playing", error: null }); onStart(); }, reject);
    });
    if (mine !== turn) return;
  }
  release();
  set(IDLE);
}

export const readAloud = {
  get state() { return state; },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  stop() {
    turn += 1;
    release();
    set(IDLE);
  },
  /** Start reading an answer, pause or resume it if it is the one being read. */
  async toggle(message: Pick<SessionMessage, "id" | "sessionId">, again = false) {
    if (!again && state.messageId === message.id) {
      if (state.status === "playing") { audio?.pause(); set({ ...state, status: "paused" }); return; }
      if (state.status === "paused" && audio) { void audio.play(); set({ ...state, status: "playing" }); return; }
      if (state.status === "making") { readAloud.stop(); return; }
    }
    readAloud.stop();
    const mine = turn;
    set({ messageId: message.id, status: "making", error: null });
    // Resolves once the first part plays (or it failed); the rest carries on by itself.
    await new Promise<void>((started) => {
      void (async () => {
        try {
          const begun = await knowledgeApi.beginSpeech(message.sessionId, message.id, again);
          if (mine !== turn) return;
          if (begun.clip) {
            const url = await clipUrl(begun.clip.id);
            if (mine !== turn) return;
            await playParts(mine, message, 1, async () => url, false, started);
          } else if (begun.jobId) {
            const job = begun.jobId;
            await playParts(mine, message, begun.parts, async (index) => URL.createObjectURL(await knowledgeApi.speechPart(job, index)), true, started);
          }
        } catch (reason) {
          if (mine !== turn) return;
          release();
          set({ messageId: message.id, status: "error", error: reason instanceof Error ? reason.message : "The answer could not be read aloud." });
        } finally {
          started();
        }
      })();
    });
  },
  /** Read a fresh answer if the reader asked for that in Settings → Read aloud. */
  async auto(message: Pick<SessionMessage, "id" | "sessionId">) {
    try {
      const overview = await knowledgeApi.ttsOverview();
      if (overview.autoRead && !overview.problem) await readAloud.toggle(message);
    } catch {
      // Reading aloud is an extra; a settings hiccup must never disturb the answer.
    }
  },
};

export function useReadAloud(messageId: string) {
  const current = useSyncExternalStore(readAloud.subscribe, () => state);
  const mine = current.messageId === messageId;
  return { status: mine ? current.status : null, error: mine ? current.error : null };
}
