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
const clips = new Map<string, string>();

const set = (next: State) => {
  state = next;
  listeners.forEach((listener) => listener());
};

async function audioUrl(sessionId: string, messageId: string): Promise<string> {
  let clipId = clips.get(messageId);
  if (!clipId) {
    clipId = (await knowledgeApi.speakMessage(sessionId, messageId)).id;
    clips.set(messageId, clipId);
  }
  const known = urls.get(clipId);
  if (known) return known;
  const url = URL.createObjectURL(await knowledgeApi.speechAudio(clipId));
  urls.set(clipId, url);
  for (const [oldest, oldUrl] of urls) {
    if (urls.size <= MEMORY_CLIPS) break;
    URL.revokeObjectURL(oldUrl);
    urls.delete(oldest);
    for (const [id, clip] of clips) if (clip === oldest) clips.delete(id);
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
  async toggle(message: Pick<SessionMessage, "id" | "sessionId">) {
    if (state.messageId === message.id) {
      if (state.status === "playing") { audio?.pause(); set({ ...state, status: "paused" }); return; }
      if (state.status === "paused" && audio) { void audio.play(); set({ ...state, status: "playing" }); return; }
      if (state.status === "making") { readAloud.stop(); return; }
    }
    readAloud.stop();
    const mine = turn;
    set({ messageId: message.id, status: "making", error: null });
    try {
      const url = await audioUrl(message.sessionId, message.id);
      if (mine !== turn) return;
      const player = new Audio(url);
      audio = player;
      player.onended = () => { if (mine === turn) { release(); set(IDLE); } };
      player.onerror = () => { if (mine === turn) { release(); set({ messageId: message.id, status: "error", error: "This audio could not be played." }); } };
      await player.play();
      if (mine === turn) set({ messageId: message.id, status: "playing", error: null });
    } catch (reason) {
      if (mine !== turn) return;
      release();
      set({ messageId: message.id, status: "error", error: reason instanceof Error ? reason.message : "The answer could not be read aloud." });
    }
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
