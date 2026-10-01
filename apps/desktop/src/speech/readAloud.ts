import type { SessionMessage } from "@gunther/contracts";
import { useSyncExternalStore } from "react";
import { knowledgeApi } from "../api";
import { describeError, log } from "../log";
import { allowSpeakerSound, SPEAKER_HINT } from "./speakerAccess";

/**
 * Reading answers aloud, one at a time for the whole app.
 *
 * The service makes the audio the first time and keeps it on disk; asking again
 * only fetches it. A long answer being made arrives in parts that play one after
 * another. Here we keep the few most recent files in memory so pausing, resuming
 * and replaying are instant.
 */

export type ReadStatus = "making" | "playing" | "paused" | "error";
/** Where the audio comes from: a kept clip, or a job still making parts. Used to show what was spoken. */
export type ReadSource = { clipId: string } | { jobId: string };
interface State {
  messageId: string | null;
  /** The conversation of the answer being read: opening another one stops the reading. */
  sessionId: string | null;
  status: ReadStatus | null;
  error: string | null;
  /** The part playing (0-based) and how many there are; 1 for a kept clip. */
  part: number;
  parts: number;
  /** How far through the whole answer, 0 to 1. */
  progress: number;
  /** Seconds into a kept clip (0 while parts are being made: their lengths are not known yet). */
  elapsed: number;
  source: ReadSource | null;
  /** A note beside the player, e.g. why the Mac's speakers may stay silent. */
  notice: string | null;
}

const IDLE: State = { messageId: null, sessionId: null, status: null, error: null, part: 0, parts: 0, progress: 0, elapsed: 0, source: null, notice: null };
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
const patch = (change: Partial<State>) => set({ ...state, ...change });
const say = (message: string) => log.info("read-aloud", message);

/** Fetch one piece of audio, noting its size, type and how long it took, so a silent play can be traced. */
async function fetched(label: string, load: () => Promise<Blob>): Promise<Blob> {
  const started = performance.now();
  const blob = await load();
  say(`${label}: ${blob.size} bytes, ${blob.type || "no type"}, ${Math.round(performance.now() - started)} ms`);
  return blob;
}

const MEDIA_ERRORS: Record<number, string> = { 1: "aborted", 2: "network", 3: "decode", 4: "format not supported" };

/** What the audio element reports while playing: enough to tell silence from a stall or a bad file. */
function watch(player: HTMLAudioElement, label: string) {
  const started = performance.now();
  const at = () => `${Math.round(performance.now() - started)} ms`;
  player.addEventListener("loadedmetadata", () => say(`${label}: ${Number.isFinite(player.duration) ? `${player.duration.toFixed(1)} s long` : `length ${player.duration}`}, ${at()}`));
  player.addEventListener("playing", () => say(`${label}: playing at ${player.currentTime.toFixed(1)} s, volume ${player.volume}${player.muted ? ", muted" : ""}, ${at()}`));
  for (const kind of ["waiting", "stalled", "suspend", "emptied"] as const) {
    player.addEventListener(kind, () => say(`${label}: ${kind} at ${player.currentTime.toFixed(1)} s, ready state ${player.readyState}, ${at()}`));
  }
  player.addEventListener("ended", () => say(`${label}: ended after ${player.currentTime.toFixed(1)} s, ${at()}`));
  player.addEventListener("error", () => {
    const problem = player.error;
    log.warn("read-aloud", `${label}: audio error ${problem ? `${problem.code} (${MEDIA_ERRORS[problem.code] ?? "unknown"}) ${problem.message}` : "without details"}, ${at()}`);
  });
}

async function clipUrl(clipId: string): Promise<string> {
  const known = urls.get(clipId);
  if (known) return known;
  const url = URL.createObjectURL(await fetched(`clip ${clipId}`, () => knowledgeApi.speechAudio(clipId)));
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
    audio.ontimeupdate = null;
    audio.pause();
    audio = null;
  }
}

/**
 * Play the parts of an answer one after another, fetching the next while one plays.
 * `temporary` parts are freed once played; a kept clip's address is reused for replays.
 */
async function playParts(mine: number, count: number, part: (index: number) => Promise<string>, temporary: boolean, onStart: () => void) {
  const free = (url: string) => { if (temporary) URL.revokeObjectURL(url); };
  let next = part(0);
  next.catch(() => undefined);
  for (let index = 0; index < count; index += 1) {
    if (index > 0) patch({ status: "making", part: index, progress: index / count });
    const url = await next;
    if (mine !== turn) { free(url); return; }
    if (index + 1 < count) { next = part(index + 1); next.catch(() => undefined); }
    await new Promise<void>((resolve, reject) => {
      const player = new Audio(url);
      watch(player, count > 1 ? `part ${index + 1}/${count}` : "audio");
      audio = player;
      player.onended = () => { free(url); resolve(); };
      player.onerror = () => reject(new Error("This audio could not be played."));
      player.ontimeupdate = () => {
        if (mine !== turn || !player.duration || !Number.isFinite(player.duration)) return;
        patch({ progress: (index + player.currentTime / player.duration) / count, elapsed: count === 1 ? player.currentTime : 0 });
      };
      player.play().then(() => { if (mine === turn) patch({ status: "playing", part: index, error: null }); onStart(); }, (reason: unknown) => {
        log.warn("read-aloud", `play() was refused: ${describeError(reason)}`);
        reject(reason);
      });
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
    if (state.status) say(`stopped (${state.status})`);
    turn += 1;
    release();
    set(IDLE);
  },
  /**
   * Start reading an answer, pause or resume it if it is the one being read.
   * `again` throws away the kept recording and makes it anew.
   */
  async toggle(message: Pick<SessionMessage, "id" | "sessionId">, again = false) {
    if (!again && state.messageId === message.id) {
      if (state.status === "playing") { audio?.pause(); say("paused"); patch({ status: "paused" }); return; }
      if (state.status === "paused" && audio) { say("resumed"); void audio.play(); patch({ status: "playing" }); return; }
      if (state.status === "making") { readAloud.stop(); return; }
    }
    readAloud.stop();
    const mine = turn;
    say(`${message.id}: start${again ? ", recording again" : ""}`);
    set({ ...IDLE, messageId: message.id, sessionId: message.sessionId, status: "making" });
    // On a Mac, ask for what the built-in speakers need while the speech is made.
    const access = allowSpeakerSound();
    // Resolves once the first part plays (or it failed); the rest carries on by itself.
    await new Promise<void>((started) => {
      void (async () => {
        try {
          const begun = await knowledgeApi.beginSpeech(message.sessionId, message.id, again);
          if (mine !== turn) return;
          const allowed = await access;
          if (mine !== turn) return;
          if (allowed === "denied" || allowed === "restricted") patch({ notice: SPEAKER_HINT });
          if (begun.clip) {
            const clipId = begun.clip.id;
            say(`${message.id}: kept clip ${clipId}`);
            patch({ parts: 1, source: { clipId } });
            const url = await clipUrl(clipId);
            if (mine !== turn) return;
            await playParts(mine, 1, async () => url, false, started);
          } else if (begun.jobId) {
            const jobId = begun.jobId;
            const count = begun.parts;
            say(`${message.id}: being made as ${jobId}, ${count} part(s)`);
            patch({ parts: count, source: { jobId } });
            await playParts(mine, count, async (index) => URL.createObjectURL(await fetched(`${jobId} part ${index + 1}/${count}`, () => knowledgeApi.speechPart(jobId, index))), true, started);
          }
        } catch (reason) {
          if (mine !== turn) return;
          log.warn("read-aloud", `${message.id}: failed: ${describeError(reason)}`);
          release();
          set({ ...IDLE, messageId: message.id, status: "error", error: reason instanceof Error ? reason.message : "The answer could not be read aloud." });
        } finally {
          started();
        }
      })();
    });
  },
  /** A conversation came on screen: reading goes on in its own, and stops for another one's answer. */
  focusSession(sessionId: string) {
    if (!state.status || state.sessionId === sessionId) return;
    say(`another conversation opened (${sessionId})`);
    readAloud.stop();
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

const NOTHING = { status: null, error: null, part: 0, parts: 0, progress: 0, elapsed: 0, source: null, notice: null } as const;

export function useReadAloud(messageId: string) {
  const current = useSyncExternalStore(readAloud.subscribe, () => state);
  if (current.messageId !== messageId) return NOTHING;
  const { status, error, part, parts, progress, elapsed, source, notice } = current;
  return { status, error, part, parts, progress, elapsed, source, notice };
}
