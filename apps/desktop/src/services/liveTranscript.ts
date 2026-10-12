/**
 * The live transcript as data: live sentences, confirmed sentences, check windows and
 * paragraphs (docs/LIVE_TRANSCRIPT_PLAN.md §4.2). Pure functions, no React: the recorder
 * holds one `TranscriptState` and replaces it with the result of each call.
 *
 * Times are milliseconds on the recording clock (audio recorded so far, pauses excluded).
 * Nothing here decides a sentence or paragraph end from the length of a pause: a live
 * sentence end is only a draft, and a sentence is confirmed once a check has heard what
 * came after it (or when the recording is paused or stopped and nothing follows).
 */

export interface LiveSentence { id: string; startMs: number; endMs: number; text: string }
export interface LiveDraft { pieceId: string; startMs: number; text: string }

export interface Sentence {
  id: string;
  startMs: number;
  endMs: number;
  text: string;
  /** The minute check (or live batch) it came from. */
  windowId: string;
  /** Token ranges, [from, to) in this sentence's tokens, that differ from the live text. */
  changed?: [number, number][];
}

export interface CheckWindow {
  id: string;
  startMs: number;
  endMs: number;
  /** What was heard live in this range. */
  live: LiveSentence[];
  /** "off" = checks off, the live text was confirmed as it is. */
  state: "checked" | "checking" | "failed" | "off";
  pass: "minute" | "retry";
  model?: string | undefined;
  at?: string | undefined;
  error?: string | undefined;
}

export interface TranscriptParagraph {
  id: string;
  sentenceIds: string[];
  /** False only for the newest paragraph while grouping can still split it. */
  frozen: boolean;
  /** The user's text. It wins, locks the paragraph, and is a fixed boundary for grouping. */
  edited?: string;
}

export interface TranscriptGap { startMs: number; endMs: number; reason: string }

export interface TranscriptState {
  version: 1;
  sentences: Sentence[];
  windows: CheckWindow[];
  paragraphs: TranscriptParagraph[];
  live: LiveSentence[];
  draft: LiveDraft | null;
  confirmedUntilMs: number;
  checking: { startMs: number; endMs: number } | null;
  grouping: { state: "idle" | "running" | "failed" | "off"; error?: string };
  gaps: TranscriptGap[];
  /** End of a window whose whole result was held back; the next window waits for more audio. */
  heldEndMs?: number | undefined;
}

/** What the backend sends for one piece of live text (``transcript.piece``). */
export interface PieceEvent {
  id: string;
  start_ms: number;
  end_ms: number | null;
  text: string;
  final: boolean;
  sentences?: { start_ms: number; end_ms: number; text: string }[];
}

export interface TimedText { start_ms: number; end_ms: number; text: string }

/** A listen-again answer (§4.4): sentences on the recording clock, [] when no word times. */
export interface WindowResult {
  text: string;
  sentences: TimedText[];
  model?: string;
}
export interface WindowError { error: string }
export type WindowOutcome = WindowResult | WindowError;

export interface WindowRules {
  /** Live final sentences must reach this far past `confirmedUntilMs` before a check. */
  windowMs: number;
  /** A window is never longer than this (the listen-again limit). */
  maxWindowMs: number;
  /** A one-sentence answer for a window this long is confirmed instead of held back. */
  wholeWindowMs: number;
}

export const DEFAULT_RULES: WindowRules = { windowMs: 60_000, maxWindowMs: 180_000, wholeWindowMs: 150_000 };

/** Live sentences that end this close after a cut belong to the text before it (timing jitter). */
const CUT_TOLERANCE_MS = 250;

export function emptyTranscript(): TranscriptState {
  return {
    version: 1,
    sentences: [],
    windows: [],
    paragraphs: [],
    live: [],
    draft: null,
    confirmedUntilMs: 0,
    checking: null,
    grouping: { state: "off" },
    gaps: [],
  };
}

export function hasTranscriptContent(state: TranscriptState): boolean {
  return state.paragraphs.length > 0 || state.live.length > 0 || state.draft !== null;
}

/** Loose check for a state read back from storage or the server. */
export function isTranscriptState(value: unknown): value is TranscriptState {
  if (!value || typeof value !== "object") return false;
  const state = value as Partial<TranscriptState>;
  return state.version === 1
    && Array.isArray(state.sentences)
    && Array.isArray(state.windows)
    && Array.isArray(state.paragraphs)
    && Array.isArray(state.live)
    && Array.isArray(state.gaps)
    && typeof state.confirmedUntilMs === "number";
}

/** A state as it is stored: no draft, and nothing "in flight" that a restart cannot finish. */
export function storableTranscript(state: TranscriptState): TranscriptState {
  // A check that was on its way when the app closed cannot finish: it shows as failed, with Retry.
  const windows = state.windows.some((window) => window.state === "checking")
    ? state.windows.map((window) => (window.state === "checking"
      ? { ...window, state: "failed" as const, error: "The check was interrupted." }
      : window))
    : state.windows;
  // A grouping request that was on its way is forgotten: the sentences stay in the open paragraph.
  const grouping = state.grouping.state === "running" ? { state: "idle" as const } : state.grouping;
  return { ...state, windows, draft: null, checking: null, grouping };
}

// Text ---------------------------------------------------------------------------

const CJK_CHAR = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u;
const CJK_PUNCTUATION = /[　-〿＀-￯…]/u;
const TOKEN = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]|[\p{L}\p{N}][\p{L}\p{N}'’_-]*/gu;

/** Join sentence texts: no space between CJK characters or after CJK punctuation, else one. */
export function joinSentences(texts: string[]): string {
  let output = "";
  for (const raw of texts) {
    const text = raw.trim();
    if (!text) continue;
    if (!output) {
      output = text;
      continue;
    }
    const last = Array.from(output).at(-1)!;
    const first = Array.from(text)[0]!;
    const tight = CJK_PUNCTUATION.test(last) || (CJK_CHAR.test(last) && CJK_CHAR.test(first));
    output += (tight ? "" : " ") + text;
  }
  return output;
}

/** Words for Latin-style text, single characters for Chinese and Japanese. */
export function tokenize(text: string): string[] {
  return (text.match(TOKEN) ?? []).map((token) => token.toLowerCase());
}

/** [mm:ss], or [h:mm:ss] after an hour: the form `parseTranscript` reads. */
export function clockLabel(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total / 60) % 60;
  const seconds = total % 60;
  const two = (value: number) => String(value).padStart(2, "0");
  return hours > 0 ? `${two(hours)}:${two(minutes)}:${two(seconds)}` : `${two(Math.floor(total / 60))}:${two(seconds)}`;
}

function sentenceById(state: TranscriptState): Map<string, Sentence> {
  return new Map(state.sentences.map((sentence) => [sentence.id, sentence]));
}

export function paragraphSentences(state: TranscriptState, paragraph: TranscriptParagraph): Sentence[] {
  const byId = sentenceById(state);
  return paragraph.sentenceIds.flatMap((id) => {
    const sentence = byId.get(id);
    return sentence ? [sentence] : [];
  });
}

/** What the paragraph says: the user's text if they edited it, else its sentences joined. */
export function paragraphText(state: TranscriptState, paragraph: TranscriptParagraph): string {
  if (paragraph.edited !== undefined) return paragraph.edited;
  return joinSentences(paragraphSentences(state, paragraph).map((sentence) => sentence.text));
}

/** A paragraph's time is its first sentence's start. */
export function paragraphStartMs(state: TranscriptState, paragraph: TranscriptParagraph): number {
  const first = paragraphSentences(state, paragraph)[0];
  if (first) return first.startMs;
  const fromId = Number(paragraph.id.replace(/^p-/, ""));
  return Number.isFinite(fromId) ? fromId : 0;
}

export function liveText(state: TranscriptState): string {
  return joinSentences(state.live.map((sentence) => sentence.text));
}

/**
 * Every line of the transcript in time order: confirmed paragraphs, gaps, and the live
 * sentences since the last check as one last paragraph. A draft is never part of it.
 */
export function transcriptLines(state: TranscriptState): { startMs: number; text: string; kind: "paragraph" | "gap" | "live" }[] {
  const lines: { startMs: number; text: string; kind: "paragraph" | "gap" | "live" }[] = [];
  for (const paragraph of state.paragraphs) {
    const text = paragraphText(state, paragraph).replace(/\s+/g, " ").trim();
    if (text) lines.push({ startMs: paragraphStartMs(state, paragraph), text, kind: "paragraph" });
  }
  for (const gap of state.gaps) {
    lines.push({
      startMs: gap.startMs,
      text: `(no audio was transcribed from ${clockLabel(gap.startMs)} to ${clockLabel(gap.endMs)})`,
      kind: "gap",
    });
  }
  const live = liveText(state).replace(/\s+/g, " ").trim();
  if (live) lines.push({ startMs: state.live[0]!.startMs, text: live, kind: "live" });
  // The live text is one line at its first sentence's time, so a gap inside it sorts after it.
  lines.sort((left, right) => left.startMs - right.startMs);
  return lines;
}

/** "[mm:ss] text" paragraphs separated by blank lines; what checkpoints and saved notes carry. */
export function renderTranscript(state: TranscriptState): string {
  return transcriptLines(state).map((line) => `[${clockLabel(line.startMs)}] ${line.text}`).join("\n\n");
}

// Diff ---------------------------------------------------------------------------

/**
 * Which tokens of `after` are not in `before`, as [from, to) ranges per sentence of `after`.
 * A longest-common-subsequence diff on tokens: plain, and the same for every language.
 */
export function changedRanges(before: string, afterSentences: string[]): [number, number][][] {
  const old = tokenize(before);
  const tokens = afterSentences.map(tokenize);
  const flat = tokens.flat();
  if (!flat.length || old.length * flat.length > 4_000_000) return afterSentences.map(() => []);
  const width = flat.length + 1;
  const table = new Uint16Array((old.length + 1) * width);
  for (let row = old.length - 1; row >= 0; row -= 1) {
    for (let column = flat.length - 1; column >= 0; column -= 1) {
      table[row * width + column] = old[row] === flat[column]
        ? table[(row + 1) * width + column + 1]! + 1
        : Math.max(table[(row + 1) * width + column]!, table[row * width + column + 1]!);
    }
  }
  const same = new Array<boolean>(flat.length).fill(false);
  let row = 0;
  let column = 0;
  while (row < old.length && column < flat.length) {
    if (old[row] === flat[column]) {
      same[column] = true;
      row += 1;
      column += 1;
    } else if (table[(row + 1) * width + column]! >= table[row * width + column + 1]!) row += 1;
    else column += 1;
  }
  let offset = 0;
  return tokens.map((sentenceTokens) => {
    const ranges: [number, number][] = [];
    sentenceTokens.forEach((_, index) => {
      if (same[offset + index]) return;
      const last = ranges.at(-1);
      if (last && last[1] === index) last[1] = index + 1;
      else ranges.push([index, index + 1]);
    });
    offset += sentenceTokens.length;
    return ranges;
  });
}

// Live text ----------------------------------------------------------------------

/** A piece from the socket: a draft replaces the grey tail, a final adds its sentences. */
export function applyPiece(state: TranscriptState, event: PieceEvent): TranscriptState {
  if (!event.final) {
    const text = event.text.trim();
    const draft = text ? { pieceId: event.id, startMs: event.start_ms, text } : null;
    return { ...state, draft };
  }
  const timed = event.sentences?.length
    ? event.sentences
    : event.text.trim()
      ? [{ start_ms: event.start_ms, end_ms: event.end_ms ?? event.start_ms, text: event.text }]
      : [];
  const known = new Set(state.live.map((sentence) => sentence.id));
  const added: LiveSentence[] = [];
  timed.forEach((item, index) => {
    const text = item.text.trim();
    const id = `${event.id}.${index + 1}`;
    // Audio replayed after a reconnect can bring back what is already confirmed.
    if (!text || known.has(id) || item.end_ms <= state.confirmedUntilMs) return;
    added.push({ id, startMs: item.start_ms, endMs: item.end_ms, text });
  });
  const live = [...state.live, ...added].sort((left, right) => left.startMs - right.startMs);
  const draft = state.draft && state.draft.pieceId === event.id ? null : state.draft;
  return { ...state, live, draft };
}

/** Audio the backend could not transcribe. */
/** Gaps closer than this are one stretch of lost audio (it is reported a piece at a time). */
const GAP_JOIN_MS = 250;

export function applyGap(state: TranscriptState, gap: TranscriptGap): TranscriptState {
  if (gap.endMs <= gap.startMs) return state;
  const merged: TranscriptGap[] = [];
  for (const item of [...state.gaps, gap].sort((left, right) => left.startMs - right.startMs)) {
    const last = merged.at(-1);
    if (last && item.startMs <= last.endMs + GAP_JOIN_MS) {
      merged[merged.length - 1] = { ...last, endMs: Math.max(last.endMs, item.endMs) };
    } else merged.push({ ...item });
  }
  return { ...state, gaps: merged };
}

/**
 * One transcript event from the live socket: a piece (draft or final) or a gap. Final
 * sentences that complete a minute are confirmed as they are until the minute check is on.
 * With ``checks`` (the backend said it can listen again) a piece only updates the live text.
 * Returns null for any other event.
 */
export function applyLiveEvent(
  state: TranscriptState,
  event: Record<string, unknown>,
  options: { checks?: boolean } = {},
): TranscriptState | null {
  if (event.type === "transcript.piece") {
    const next = applyPiece(state, event as unknown as PieceEvent);
    // With the minute check on, only the check confirms text (the recorder runs it).
    return options.checks ? next : confirmLive(next);
  }
  if (event.type === "transcript.gap" && typeof event.start_ms === "number" && typeof event.end_ms === "number") {
    return applyGap(state, { startMs: event.start_ms, endMs: event.end_ms, reason: String(event.reason ?? "connection") });
  }
  return null;
}

// Windows ------------------------------------------------------------------------

/**
 * The next stretch to listen to again, or null. Once live final sentences reach `windowMs`
 * past `confirmedUntilMs`, the window is everything up to the newest of them (capped); at
 * Pause and Stop (`closing`) it takes everything that is left. One check at a time.
 */
export function nextWindow(
  state: TranscriptState,
  rules: WindowRules = DEFAULT_RULES,
  options: { closing?: boolean | undefined } = {},
): { startMs: number; endMs: number } | null {
  if (state.checking) return null;
  const startMs = state.confirmedUntilMs;
  const ahead = state.live.filter((sentence) => sentence.endMs > startMs + CUT_TOLERANCE_MS);
  if (!ahead.length) return null;
  const newest = ahead.reduce((end, sentence) => Math.max(end, sentence.endMs), 0);
  if (!options.closing) {
    if (newest - startMs < rules.windowMs) return null;
    if (state.heldEndMs !== undefined && newest < state.heldEndMs + rules.windowMs) return null;
  }
  const within = ahead.filter((sentence) => sentence.endMs <= startMs + rules.maxWindowMs);
  const endMs = within.length
    ? within.reduce((end, sentence) => Math.max(end, sentence.endMs), 0)
    : Math.min(...ahead.map((sentence) => sentence.endMs));
  return { startMs, endMs };
}

/** The window is on its way to the checking model. */
export function beginCheck(state: TranscriptState, window: { startMs: number; endMs: number }): TranscriptState {
  return { ...state, checking: { startMs: window.startMs, endMs: window.endMs } };
}

function newSentence(item: TimedText, windowId: string, taken: Set<string>): Sentence {
  let id = `s-${item.start_ms}`;
  while (taken.has(id)) id += "+";
  taken.add(id);
  return { id, startMs: item.start_ms, endMs: item.end_ms, text: item.text.trim(), windowId };
}

/** Add confirmed sentences to the open paragraph, or start a frozen one when grouping is off. */
function addToParagraphs(state: TranscriptState, added: Sentence[]): TranscriptParagraph[] {
  if (!added.length) return state.paragraphs;
  const ids = added.map((sentence) => sentence.id);
  const last = state.paragraphs.at(-1);
  if (state.grouping.state !== "off" && last && !last.frozen && last.edited === undefined) {
    return [...state.paragraphs.slice(0, -1), { ...last, sentenceIds: [...last.sentenceIds, ...ids] }];
  }
  return [
    ...state.paragraphs,
    { id: `p-${added[0]!.startMs}`, sentenceIds: ids, frozen: state.grouping.state === "off" },
  ];
}

function dropConfirmedLive(live: LiveSentence[], confirmedUntilMs: number): LiveSentence[] {
  return live.filter((sentence) => sentence.endMs > confirmedUntilMs + CUT_TOLERANCE_MS);
}

/**
 * Apply what the check said about a window (§2.5). All sentences but the last are confirmed
 * and the last is held back to be heard again with what follows it, unless the recording is
 * closing, the answer has no sentences with times, or it has a single sentence for a long
 * window. With `checksOff`, or when the check failed, the live sentences are confirmed as
 * they are (a failed window is marked, so it can be retried).
 */
export function applyWindowResult(
  state: TranscriptState,
  window: { startMs: number; endMs: number },
  outcome: WindowOutcome | null,
  options: { closing?: boolean | undefined; checksOff?: boolean | undefined; rules?: WindowRules | undefined; model?: string | undefined; now?: string | undefined } = {},
): TranscriptState {
  const rules = options.rules ?? DEFAULT_RULES;
  const windowId = `w-${window.startMs}`;
  const inWindow = state.live.filter((sentence) => sentence.endMs <= window.endMs + CUT_TOLERANCE_MS && sentence.endMs > window.startMs);
  const taken = new Set(state.sentences.map((sentence) => sentence.id));
  const record = (patch: Partial<CheckWindow>): CheckWindow[] => [
    ...state.windows.filter((item) => item.id !== windowId),
    { id: windowId, startMs: window.startMs, endMs: window.endMs, live: inWindow, state: "checked", pass: "minute", ...patch },
  ];

  const asIs = (patch: Partial<CheckWindow>): TranscriptState => {
    const added = inWindow.map((sentence) => newSentence(
      { start_ms: sentence.startMs, end_ms: sentence.endMs, text: sentence.text }, windowId, taken,
    ));
    const confirmedUntilMs = Math.max(state.confirmedUntilMs, window.endMs);
    return {
      ...state,
      sentences: [...state.sentences, ...added],
      windows: record(patch),
      paragraphs: addToParagraphs(state, added),
      live: dropConfirmedLive(state.live, confirmedUntilMs),
      confirmedUntilMs,
      checking: null,
      heldEndMs: undefined,
    };
  };

  if (options.checksOff || outcome === null) return asIs({ state: "off" });
  if ("error" in outcome) return asIs({ state: "failed", error: outcome.error, at: options.now });

  const heard = outcome.sentences
    .map((item) => ({ ...item, text: item.text.trim() }))
    .filter((item) => item.text && item.end_ms > window.startMs)
    .sort((left, right) => left.start_ms - right.start_ms);
  const text = outcome.text.trim();
  if (!heard.length && !text) {
    return asIs({ state: "failed", error: "The model returned no text.", at: options.now });
  }
  // No word times: the whole window is one sentence.
  const sentences: TimedText[] = heard.length ? heard : [{ start_ms: window.startMs, end_ms: window.endMs, text }];
  const length = window.endMs - window.startMs;
  let kept = sentences;
  if (!options.closing && heard.length) {
    if (sentences.length >= 2) kept = sentences.slice(0, -1);
    else if (length < rules.wholeWindowMs) kept = [];
  }
  const model = options.model ?? outcome.model;
  if (!kept.length) {
    // Everything is held back and heard again with the next minute.
    return { ...state, checking: null, heldEndMs: window.endMs };
  }
  const confirmedUntilMs = options.closing
    ? Math.max(state.confirmedUntilMs, window.endMs, kept.at(-1)!.end_ms)
    : Math.max(state.confirmedUntilMs, kept.at(-1)!.end_ms);
  const added = kept.map((item) => newSentence(item, windowId, taken));
  const liveBefore = joinSentences(inWindow.map((sentence) => sentence.text));
  const ranges = changedRanges(liveBefore, added.map((sentence) => sentence.text));
  added.forEach((sentence, index) => {
    if (ranges[index]?.length) sentence.changed = ranges[index];
  });
  return {
    ...state,
    sentences: [...state.sentences, ...added],
    windows: record({ model, at: options.now }),
    paragraphs: addToParagraphs(state, added),
    live: dropConfirmedLive(state.live, confirmedUntilMs),
    confirmedUntilMs,
    checking: null,
    heldEndMs: undefined,
  };
}

/**
 * Confirm live sentences as they are, window by window, until none is left to take.
 * This is the "checks are off" path; `closing` takes everything (Pause and Stop).
 */
export function confirmLive(
  state: TranscriptState,
  options: { closing?: boolean | undefined; rules?: WindowRules | undefined } = {},
): TranscriptState {
  let current = state;
  for (let guard = 0; guard < 10_000; guard += 1) {
    const window = nextWindow(current, options.rules ?? DEFAULT_RULES, { closing: options.closing });
    if (!window) break;
    const next = applyWindowResult(current, window, null, { closing: options.closing, checksOff: true, rules: options.rules });
    if (next.confirmedUntilMs <= current.confirmedUntilMs) break;
    current = next;
  }
  return options.closing ? { ...current, draft: null } : current;
}

// Paragraphs by content (§2.11) ------------------------------------------------------

/** The pool a grouping request may hold: past this, the oldest sentences are frozen first. */
export const GROUPING_POOL = { sentences: 60, chars: 6_000 };
/** The most sentences one request may hold (the backend's limit); the regroup is chunked to it. */
export const GROUPING_REQUEST_MAX = 300;
/** How much of the paragraph before a request is sent as context. */
const BEFORE_CHARS = 400;

export type GroupingOutcome = { starts: number[] } | { error: string };

/** What goes to the Analysis model: the sentences to group, and context that is not grouped. */
export interface GroupingRequest {
  sentences: string[];
  /** The ids of those sentences, in order: the answer applies to exactly these. */
  sentenceIds: string[];
  before: string;
}

function tail(text: string): string {
  return text.length > BEFORE_CHARS ? text.slice(-BEFORE_CHARS) : text;
}

/** The newest paragraph, if it can still be split: not frozen, not the user's. */
function openParagraph(state: TranscriptState): TranscriptParagraph | null {
  const last = state.paragraphs.at(-1);
  return last && !last.frozen && last.edited === undefined ? last : null;
}

function paragraphOf(sentences: Sentence[]): TranscriptParagraph {
  return { id: `p-${sentences[0]!.startMs}`, sentenceIds: sentences.map((sentence) => sentence.id), frozen: true };
}

/**
 * The open paragraph's sentences (at most the newest 60 sentences / 6000 characters, the older
 * ones being frozen as a paragraph when the answer is applied), with the end of the paragraph
 * before as context. Null when grouping is off or there is nothing to group.
 */
export function groupingRequest(state: TranscriptState): GroupingRequest | null {
  if (state.grouping.state === "off") return null;
  const open = openParagraph(state);
  if (!open) return null;
  const sentences = paragraphSentences(state, open);
  if (!sentences.length) return null;
  let from = sentences.length;
  let chars = 0;
  while (from > 0) {
    const size = sentences[from - 1]!.text.length;
    if (from < sentences.length && (sentences.length - from >= GROUPING_POOL.sentences || chars + size > GROUPING_POOL.chars)) break;
    chars += size;
    from -= 1;
  }
  const pool = sentences.slice(from);
  const earlier = from > 0
    ? joinSentences(sentences.slice(0, from).map((sentence) => sentence.text))
    : state.paragraphs.length > 1
      ? paragraphText(state, state.paragraphs.at(-2)!)
      : "";
  return { sentences: pool.map((sentence) => sentence.text), sentenceIds: pool.map((sentence) => sentence.id), before: tail(earlier) };
}

/** Zero-based starts: whole numbers inside the range, sorted, no repeats, and 0. */
function cleanStarts(starts: unknown, count: number): number[] {
  const picked = new Set<number>([0]);
  if (Array.isArray(starts)) {
    for (const value of starts) {
      if (typeof value === "number" && Number.isInteger(value) && value >= 0 && value < count) picked.add(value);
    }
  }
  return [...picked].sort((left, right) => left - right);
}

function failedGrouping(state: TranscriptState, error: string): TranscriptState {
  return { ...state, grouping: { state: "failed", error } };
}

/** The grouping request is on its way. */
export function beginGrouping(state: TranscriptState): TranscriptState {
  return state.grouping.state === "off" ? state : { ...state, grouping: { state: "running" } };
}

/**
 * Split the open paragraph where the answer says. It applies to the sentences that were sent,
 * even if more were confirmed meanwhile: those join the last part. Every part but the last
 * is frozen. Sentences older than the request (the pool cap) become a frozen paragraph first.
 * A failure changes nothing but `grouping`.
 */
export function applyGrouping(state: TranscriptState, request: GroupingRequest, outcome: GroupingOutcome): TranscriptState {
  if ("error" in outcome) return failedGrouping(state, outcome.error);
  const idle: TranscriptState = { ...state, grouping: { state: "idle" } };
  const open = openParagraph(state);
  if (!open || !request.sentenceIds.length) return idle;
  const at = open.sentenceIds.indexOf(request.sentenceIds[0]!);
  const sent = open.sentenceIds.slice(at, at + request.sentenceIds.length);
  // The paragraph changed under the request (an edit, a retry): the answer no longer fits.
  if (at < 0 || sent.length !== request.sentenceIds.length || sent.some((id, index) => id !== request.sentenceIds[index])) return idle;

  const byId = sentenceById(state);
  const pick = (ids: string[]) => ids.flatMap((id) => byId.get(id) ?? []);
  const starts = cleanStarts(outcome.starts, sent.length);
  const parts: Sentence[][] = [];
  if (at > 0) parts.push(pick(open.sentenceIds.slice(0, at)));
  starts.forEach((start, index) => parts.push(pick(sent.slice(start, starts[index + 1] ?? sent.length))));
  // What was confirmed while the request ran joins the last part.
  parts[parts.length - 1]!.push(...pick(open.sentenceIds.slice(at + sent.length)));
  const made = parts.filter((part) => part.length).map((part, index, all) => ({ ...paragraphOf(part), frozen: index < all.length - 1 }));
  return { ...state, paragraphs: [...state.paragraphs.slice(0, -1), ...made], grouping: { state: "idle" } };
}

/** A run of paragraphs to group again after Stop. */
export interface RegroupChunk {
  /** Index in `paragraphs` of the run's first paragraph. */
  chunkStart: number;
  sentenceIds: string[];
  sentences: string[];
  before: string;
  /** Just past the start of the run's last sentence: ask for the next chunk from here. */
  endMs: number;
}

/**
 * The first run of paragraphs that starts at or after `fromMs`, to group again: at most
 * `max` sentences, beginning at a paragraph start and ending before a paragraph the user
 * edited. Edited paragraphs are never in a run. Runs of one sentence are skipped.
 */
export function nextRegroupChunk(state: TranscriptState, fromMs = 0, max = GROUPING_REQUEST_MAX): RegroupChunk | null {
  const sentencesOf = state.paragraphs.map((paragraph) => paragraphSentences(state, paragraph));
  let index = 0;
  while (index < state.paragraphs.length) {
    const paragraph = state.paragraphs[index]!;
    const own = sentencesOf[index]!;
    if (paragraph.edited !== undefined || !own.length || own[0]!.startMs < fromMs || own.length > max) {
      index += 1;
      continue;
    }
    let end = index;
    let count = 0;
    while (end < state.paragraphs.length && state.paragraphs[end]!.edited === undefined && count + sentencesOf[end]!.length <= max) {
      count += sentencesOf[end]!.length;
      end += 1;
    }
    const run = sentencesOf.slice(index, end).flat();
    if (run.length < 2) {
      index = Math.max(end, index + 1);
      continue;
    }
    const previous = index > 0 ? paragraphText(state, state.paragraphs[index - 1]!) : "";
    return {
      chunkStart: index,
      sentenceIds: run.map((sentence) => sentence.id),
      sentences: run.map((sentence) => sentence.text),
      before: tail(previous),
      endMs: run.at(-1)!.startMs + 1,
    };
  }
  return null;
}

/**
 * After Stop: split a run of paragraphs again where the answer says; every new paragraph is
 * frozen. The run must still be exactly those paragraphs and none edited, else nothing
 * changes (an edited paragraph is never merged, split or moved). A failure changes nothing
 * but `grouping`.
 */
export function applyRegroup(
  state: TranscriptState,
  chunkStart: number,
  chunkSentenceIds: string[],
  outcome: GroupingOutcome,
): TranscriptState {
  if ("error" in outcome) return failedGrouping(state, outcome.error);
  const idle: TranscriptState = { ...state, grouping: { state: "idle" } };
  const involved: TranscriptParagraph[] = [];
  let count = 0;
  for (let index = chunkStart; index < state.paragraphs.length && count < chunkSentenceIds.length; index += 1) {
    const paragraph = state.paragraphs[index]!;
    if (paragraph.edited !== undefined) return idle;
    involved.push(paragraph);
    count += paragraph.sentenceIds.length;
  }
  const ids = involved.flatMap((paragraph) => paragraph.sentenceIds);
  if (ids.length !== chunkSentenceIds.length || ids.some((id, index) => id !== chunkSentenceIds[index])) return idle;
  const byId = sentenceById(state);
  const sentences = ids.flatMap((id) => byId.get(id) ?? []);
  if (sentences.length !== ids.length) return idle;
  const starts = cleanStarts(outcome.starts, sentences.length);
  const made = starts.map((start, index) => paragraphOf(sentences.slice(start, starts[index + 1] ?? sentences.length)));
  return {
    ...state,
    paragraphs: [...state.paragraphs.slice(0, chunkStart), ...made, ...state.paragraphs.slice(chunkStart + involved.length)],
    grouping: { state: "idle" },
  };
}

// Retry --------------------------------------------------------------------------

/** The range of a window to listen to again, or null if it is unknown. */
export function retryRange(state: TranscriptState, windowId: string): { startMs: number; endMs: number } | null {
  const window = state.windows.find((item) => item.id === windowId);
  return window ? { startMs: window.startMs, endMs: window.endMs } : null;
}

/** Mark a window as being checked again. */
export function beginRetry(state: TranscriptState, windowId: string): TranscriptState {
  return { ...state, windows: state.windows.map((item) => (item.id === windowId ? { ...item, state: "checking" } : item)) };
}

/**
 * Replace one window's sentences with a new answer (no hold-back). Each new sentence goes to
 * the paragraph whose time range holds its start; a paragraph the user edited keeps its text.
 */
export function applyRetry(
  state: TranscriptState,
  windowId: string,
  outcome: WindowOutcome,
  options: { model?: string | undefined; now?: string | undefined } = {},
): TranscriptState {
  const window = state.windows.find((item) => item.id === windowId);
  if (!window) return state;
  const mark = (patch: Partial<CheckWindow>): CheckWindow[] => state.windows.map((item) => (item.id === windowId ? { ...item, pass: "retry", ...patch } : item));
  if ("error" in outcome) return { ...state, windows: mark({ state: "failed", error: outcome.error, at: options.now }) };
  const heard = outcome.sentences
    .map((item) => ({ ...item, text: item.text.trim() }))
    .filter((item) => item.text && item.end_ms > window.startMs && item.start_ms < window.endMs + CUT_TOLERANCE_MS);
  const text = outcome.text.trim();
  if (!heard.length && !text) {
    return { ...state, windows: mark({ state: "failed", error: "The model returned no text.", at: options.now }) };
  }
  const fresh: TimedText[] = heard.length ? heard : [{ start_ms: window.startMs, end_ms: window.endMs, text }];

  const old = new Set(state.sentences.filter((sentence) => sentence.windowId === windowId).map((sentence) => sentence.id));
  const starts = state.paragraphs.map((paragraph) => paragraphStartMs(state, paragraph));
  const taken = new Set(state.sentences.filter((sentence) => !old.has(sentence.id)).map((sentence) => sentence.id));
  const added = fresh.map((item) => newSentence(item, windowId, taken));
  const ranges = changedRanges(joinSentences(window.live.map((sentence) => sentence.text)), added.map((sentence) => sentence.text));
  added.forEach((sentence, index) => {
    if (ranges[index]?.length) sentence.changed = ranges[index];
  });

  const paragraphs = state.paragraphs.map((paragraph) => ({
    ...paragraph,
    sentenceIds: paragraph.sentenceIds.filter((id) => !old.has(id)),
  }));
  const fallback = Math.max(0, state.paragraphs.findIndex((paragraph) => paragraph.sentenceIds.some((id) => old.has(id))));
  for (const sentence of added) {
    let target = -1;
    starts.forEach((start, index) => {
      if (start <= sentence.startMs && (target < 0 || start >= starts[target]!)) target = index;
    });
    const index = target >= 0 ? target : fallback;
    paragraphs[index]?.sentenceIds.push(sentence.id);
  }
  const sentences = [...state.sentences.filter((sentence) => !old.has(sentence.id)), ...added]
    .sort((left, right) => left.startMs - right.startMs);
  const order = new Map(sentences.map((sentence, index) => [sentence.id, index]));
  const settled = paragraphs
    .map((paragraph) => ({ ...paragraph, sentenceIds: [...paragraph.sentenceIds].sort((a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0)) }))
    .filter((paragraph) => paragraph.sentenceIds.length > 0 || paragraph.edited !== undefined);
  return {
    ...state,
    sentences,
    paragraphs: settled,
    windows: mark({ state: "checked", error: undefined, model: options.model ?? outcome.model, at: options.now }),
  };
}

// Editing ------------------------------------------------------------------------

/** The user's own text for a paragraph: it wins, locks the paragraph, and no check changes it. */
export function editParagraph(state: TranscriptState, paragraphId: string, text: string): TranscriptState {
  const clean = text.replace(/\r/g, "").trim();
  const paragraph = state.paragraphs.find((item) => item.id === paragraphId);
  if (!paragraph || !clean) return state;
  const original = joinSentences(paragraphSentences(state, paragraph).map((sentence) => sentence.text));
  if (clean === paragraphText(state, paragraph) || (paragraph.edited === undefined && clean === original)) return state;
  return {
    ...state,
    paragraphs: state.paragraphs.map((item) => (item.id === paragraphId ? { ...item, edited: clean, frozen: true } : item)),
  };
}

// Reading saved text -------------------------------------------------------------

/**
 * Paragraphs from plain "[mm:ss] text" lines (an older draft or recording): each becomes one
 * locked, frozen paragraph whose text is the saved text. Returns null when no line has a time.
 */
export function transcriptFromText(
  segments: { startSeconds: number | null; text: string }[],
): TranscriptState | null {
  if (!segments.some((segment) => segment.startSeconds !== null)) return null;
  let cursor = 0;
  const taken = new Set<string>();
  const sentences: Sentence[] = [];
  const paragraphs: TranscriptParagraph[] = [];
  segments.forEach((segment, index) => {
    const startMs = segment.startSeconds === null ? cursor : Math.round(segment.startSeconds * 1000);
    cursor = Math.max(cursor, startMs);
    const next = segments.slice(index + 1).find((item) => item.startSeconds !== null);
    const endMs = next ? Math.max(startMs, Math.round(next.startSeconds! * 1000)) : startMs;
    const sentence = newSentence({ start_ms: startMs, end_ms: endMs, text: segment.text }, "w-restored", taken);
    sentences.push(sentence);
    paragraphs.push({ id: `p-${sentence.id.slice(2)}`, sentenceIds: [sentence.id], frozen: true, edited: segment.text.trim() });
  });
  const last = sentences.at(-1);
  return {
    ...emptyTranscript(),
    sentences,
    paragraphs,
    windows: [{ id: "w-restored", startMs: 0, endMs: last?.endMs ?? 0, live: [], state: "off", pass: "minute" }],
    confirmedUntilMs: last?.endMs ?? 0,
  };
}

// What the screen shows -----------------------------------------------------------

/** Text cut into pieces, the ones in `changed` being the words a check changed. */
export interface TextPart { text: string; changed: boolean }

/** Cut `text` at the tokens in `ranges` ([from, to) token numbers, as `changedRanges` gives). */
export function splitChanged(text: string, ranges: [number, number][] | undefined): TextPart[] {
  if (!ranges?.length) return [{ text, changed: false }];
  const tokens = Array.from(text.matchAll(TOKEN)).map((match) => ({ from: match.index ?? 0, to: (match.index ?? 0) + match[0].length }));
  const parts: TextPart[] = [];
  let cursor = 0;
  for (const [first, last] of [...ranges].sort((a, b) => a[0] - b[0])) {
    const from = tokens[first]?.from;
    const to = tokens[Math.min(last, tokens.length) - 1]?.to;
    if (from === undefined || to === undefined || from < cursor) continue;
    if (from > cursor) parts.push({ text: text.slice(cursor, from), changed: false });
    parts.push({ text: text.slice(from, to), changed: true });
    cursor = to;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), changed: false });
  return parts;
}

/**
 * A paragraph's text as pieces, the words its check changed marked. A paragraph the user
 * edited has no marks: their text is not the check's.
 */
export function paragraphParts(state: TranscriptState, paragraph: TranscriptParagraph): TextPart[] {
  if (paragraph.edited !== undefined) return [{ text: paragraph.edited, changed: false }];
  const parts: TextPart[] = [];
  let text = "";
  for (const sentence of paragraphSentences(state, paragraph)) {
    const own = sentence.text.trim();
    if (!own) continue;
    const joined = joinSentences([text, own]);
    const separator = joined.slice(text.length, joined.length - own.length);
    if (separator) parts.push({ text: separator, changed: false });
    parts.push(...splitChanged(own, sentence.changed));
    text = joined;
  }
  return parts;
}

/** Whether a paragraph's check failed or is being tried again (its sentences' windows say). */
export function paragraphCheck(
  state: TranscriptState,
  paragraph: TranscriptParagraph,
): { failed: CheckWindow | null; checking: boolean } {
  const ids = new Set(paragraphSentences(state, paragraph).map((sentence) => sentence.windowId));
  const windows = state.windows.filter((window) => ids.has(window.id));
  return {
    failed: paragraph.edited !== undefined ? null : windows.find((window) => window.state === "failed") ?? null,
    checking: windows.some((window) => window.state === "checking"),
  };
}

/**
 * What the live model heard where a paragraph is, from the windows that overlap it; null when
 * there is none or it reads the same as the paragraph.
 */
export function heardLive(state: TranscriptState, paragraph: TranscriptParagraph): string | null {
  const sentences = paragraphSentences(state, paragraph);
  if (!sentences.length) return null;
  const from = sentences[0]!.startMs;
  const to = Math.max(...sentences.map((sentence) => sentence.endMs));
  const seen = new Map<string, LiveSentence>();
  for (const window of state.windows) {
    if (window.endMs <= from || window.startMs >= to) continue;
    for (const live of window.live) {
      if (live.endMs > from && live.startMs < to) seen.set(live.id, live);
    }
  }
  const text = joinSentences([...seen.values()].sort((a, b) => a.startMs - b.startMs).map((live) => live.text));
  if (!text || tokenize(text).join(" ") === tokenize(joinSentences(sentences.map((sentence) => sentence.text))).join(" ")) return null;
  return text;
}

/** The end of the last paragraph that starts before `ms`, as context for listening again. */
export function textBefore(state: TranscriptState, ms: number, limit = 400): string {
  const earlier = state.paragraphs.filter((paragraph) => paragraphStartMs(state, paragraph) < ms);
  const last = earlier.at(-1);
  return last ? paragraphText(state, last).slice(-limit) : "";
}
