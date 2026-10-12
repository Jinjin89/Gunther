# Live transcript: sentences that correct themselves, paragraphs that are checked again — implementation plan

> **Status 2026-10-12: Phase 0 done (see the progress log); Layer 2 changed from pause-based paragraph checks to a minute check that holds back its last sentence (user's request, 2026-10-12, §1.6); Phases 1–3 and 5 not started; Phase 4 deferred (no gain measured).** Written by Claude Opus for Sonnet agents that build it, one phase per agent; Opus reviews each phase. Phase 3B (paragraphs by content) added 2026-10-12. Line numbers are from 2026-10-11 and drift: grep for the names.

The user's request (2026-10-11), in short: the recorder's live transcript is not accurate. Audio is cut into pieces and sent to the cloud one piece at a time, so background noise or a bad cut ruins a piece and nothing ever fixes it. Each piece should be written with what was said before and after it. Text already on screen should be corrected as more is heard, the way other live-caption products do it. The subtitle look (time on the left, one short line per piece) should become paragraphs, but times still matter, so they need to fit in. The user asked for research first, then a complete plan.

Confirmed with the user: (1) more accurate text, using context; (2) corrections after the fact, a draft first and then better text in its place; (3) paragraphs with timestamps; (4) a complete design, not a patch. Desktop recorder first; the phone app keeps working unchanged.

---

## How to run this plan

1. **Phase 0 needs the user's Qwen key once.** Either the user runs the probe command on their Mac, or puts `QWEN_STT_API_KEY` (and `QWEN_STT_BASE_URL` if not the default) in the repo's `.env` here. Its findings settle the open protocol questions in §2 before Phase 1 starts.
2. **One Sonnet agent per phase**, spawned by Opus, editing `main` directly (no worktree). The agent stops at the end of its phase and adds an entry to the progress log at the bottom.
3. **Opus reviews** each phase against its tasks, its "Done when" and the review checklist, then the user decides whether to commit.
4. Verification stays light (targeted pytest + vitest + lint). The user tries the real app on the Mac.

---

## The design

### The problem, with an example

Someone says "We'll use gradient descent to train the model." Today `apps/backend/gunther/realtime.py` cuts the audio every 3.2 seconds by the clock, so the cut can fall inside a word: `We'll use gra` | `dient descent to train the` | `model`. Each piece goes to the model alone, with no hint of what came before, and the model guesses: `We'll use grey` / `didn't decent to train the` / `model`. The text is final the moment it arrives. Nothing goes back.

### The fix: three layers

| Layer | When | What it does | Fixes |
|---|---|---|---|
| **1. Live sentences** | while speaking, ~0.2–1 s | Qwen's streaming model hears the audio as one continuous stream. The sentence being spoken shows as light grey text that rewrites itself; when Qwen ends it, it turns dark grey. Every sentence has a start and end time. Live sentence ends are only drafts: Qwen ends a sentence at any pause, including a thinking pause in the middle of one (measured, progress log 2026-10-12). | cuts inside words, noise in a single piece, lost words at cuts |
| **2. Minute check** | about once a minute | The backend sends everything since the last confirmed sentence (about a minute) to the stronger non-streaming model, with the previous paragraph's text as context. All sentences it returns are confirmed **except the last one**, which is held back and heard again with the next minute, so a sentence is only confirmed once the model has heard what came after it. The confirmed sentences become a paragraph, in black. | words that need more context than one sentence; sentences split at a thinking pause |
| **3. Final check** (deferred: no gain measured, see the progress log) | after Stop, ~1 minute for an hour | The Analysis model lists the names and terms used anywhere in the recording. Every paragraph is listened to again with those terms plus the paragraphs before *and after* it. | a term misheard early but said clearly later ("what's said later fixes what came before") |

The same example after each layer: Layer 1 shows `We'll use gradient descent to train the model.` once the pause arrives, because the model heard the whole sentence. If the speaker mumbled and Layer 1 wrote `grey didn't decent`, Layer 2 re-hears the whole minute with the previous paragraph ("…the loss function… gradient…") as context. If the speaker stopped to think in the middle (`So if the learning rate is too large…` 10 s of silence `…the loss jumps around.`), Layer 1 shows two sentences, and Layer 2 joins them, because it hears both halves in one go (or, if the minute ended in the pause, in the next minute, since the last sentence is held back). Layer 3 adds "gradient descent" from the whole lecture's term list. Each layer keeps the earlier text in the saved data, so nothing is lost.

### Display: paragraphs with a timestamp each

```
 00:00  Today we look at how neural networks learn. We start with a loss
        function, and then we use gradient descent to train the model.

 01:04  The learning rate decides how big each step is. If it's too large,
        the model jumps around the minimum. So how do we pick a      (dark grey:
        good value? One way is to start large. And shrink it  checking…  live, not
        over time. Most libraries call this a sched▍          yet checked; light
                                                              grey: still hearing)
```

One time per paragraph, in a narrow grey gutter, clickable to play from there once the audio is saved. Black text for confirmed paragraphs; dark grey for live sentences since the last check (readable, but may still change, e.g. the wrong full stop in "start large. And shrink it"); light grey for the sentence still being heard; a small grey "checking…" while a minute is listened to again. When the check comes back, the confirmed part turns black and becomes a paragraph in the same place (no layout jump); the held-back last sentence stays dark grey.

**The check is visible, but quiet** (2026-10-12): the user should be able to tell that text was improved without being distracted while listening to a lecture.
- While recording: the three shades above, plus "checking…" in the margin of the text being re-heard.
- When a check changes words, those words get a faint dotted underline for about 4 s, then it fades (no colour; tokens are CJK characters and Latin words, a plain diff of live vs checked text). Unchanged text just darkens to black.
- After the recording: nothing extra on screen. A paragraph's menu has "Show what was heard live", which shows the live text under it in grey (the provenance kept in `live`).
- A failed check is never silent: "Couldn't check · Retry" beside the paragraph, which keeps its live text in dark grey. No colours (see the UI taste notes: white, black, colour only for identity). Click a paragraph to edit it; an edited paragraph is never changed by a later check.

### What others do (research, 2026-10-11)

- **Draft, then final, keyed by sentence.** Qwen's streaming ASR sends `result-generated` events with `sentence_id`; text with `sentence_end: false` is a draft that "may be revised", so the screen updates by `sentence_id` instead of appending ([Qwen-Audio-ASR-Message server events](https://help.aliyun.com/en/model-studio/qwen-asr-message-server-events)). Deepgram (`is_final`) and Apple's SpeechAnalyzer (volatile vs finalized results) work the same way: show the draft dimmed, replace it, keep only finals.
- **Fast pass, then an accurate pass at the end of each sentence.** FunASR's "2pass" mode streams a fast model and re-decodes each sentence with a non-streaming model when the pause detector ends it ([FunASR runtime](https://github.com/alibaba-damo-academy/FunASR/blob/main/runtime/docs/SDK_advanced_guide_online_zh.md)). Layers 2 and 3 are this idea at paragraph scale, with context added.
- **Context biases the model by word matching.** Qwen's context enhancement takes up to 5 turns of ≤400 characters each: term lists or earlier transcript. It helps "mainly through word matching, so include the exact target terms" ([Improve recognition accuracy](https://www.alibabacloud.com/help/en/model-studio/improve-asr-accuracy)). Hence Layer 3's term list.
- **Considered, not chosen:**
  - *LocalAgreement* ([whisper_streaming](https://github.com/ufal/whisper_streaming), [paper](https://arxiv.org/abs/2307.14743)): it re-sends a growing buffer every second and keeps only what two runs agree on. That's built for a local Whisper; with a paid cloud model it multiplies calls, and Qwen's streaming model already revises drafts itself.
  - *Apple SpeechAnalyzer* (macOS 26, on-device, volatile results): free and private, but it needs a Swift plugin for Tauri and its Chinese support is unconfirmed. It's a possible future local provider.
  - *An LLM rewriting the transcript text*: it can't hear the audio, so it would guess. Corrections in this plan always come from listening again; the LLM only lists terms that already appear in the transcript.

---

## How to work (for the implementer)

- Read §1–§4 and your phase in full before editing. Follow `AGENTS.md`: general behaviour only; no rules for particular recordings, words or languages.
- Match the surrounding code's naming and comment style. Docstrings in plain English, like `realtime.py`.
- Keep the old socket protocol (v1) working byte-for-byte for clients that don't ask for v2 (the phone app, Ask dictation).
- Tests: backend `cd apps/backend && /tmp/uvbin/uv run pytest tests/<files> -q` and `uv run ruff check gunther tests`; desktop `cd apps/desktop && npx vitest run <files>` and `npx tsc --noEmit`. No app builds.
- Stop at the end of your phase. Add a progress-log entry: what changed, test results, anything you deviated on and why.

---

## 1. Decided with the user (don't reopen)

1. Corrections come from **listening again**, with context (before, and in the final check after). Not from a text model guessing.
2. Text on screen **may change**: drafts rewrite in place; finished paragraphs can be replaced by a check.
3. **Paragraphs with one timestamp each**, replacing the subtitle list, live and in the saved recording view. The time sits in the **left margin** beside the paragraph's first line, small and grey, clickable (user's choice 2026-10-11, over "above the paragraph", "inside the text" and "on hover"). Only very narrow windows may move it above the paragraph.
4. **Desktop recorder.** Phone app and Ask dictation keep working unchanged (v1 protocol).
5. **Model output or an error**: if a check fails, the paragraph keeps its live text and shows "Couldn't check · Retry". Never a made-up fallback.
6. **No pause-length rules for sentences or paragraphs** (user, 2026-10-12: "it could be thinking"; a 10 s thinking pause can sit inside one sentence). Where a sentence ends is the model's call, and a sentence is confirmed only after the model has heard what follows it (the minute check holds back its last sentence, §2.5). Audio is never cut inside a word. The minute check is **on by default**, with a switch to turn it off; it roughly doubles the audio sent (about 2.1× with the held-back sentences), which costs cents per hour.
7. **Paragraphs by content, not by time** (user, 2026-10-12: "certain content put in one paragraph"). The Analysis model reads the confirmed sentences and says only where paragraphs start (sentence numbers); it never sees or returns changed text, so every word still comes from listening. Live: earlier paragraphs are frozen and only the newest one can still split. After Stop: one regroup pass over the whole transcript. Without an Analysis model: one paragraph per minute check, with a hint to add one. Details §2.11, prompt §5.2.

## 2. Calls the planner made (the user may still override)

1. **Qwen streaming is the live model** when the recording job uses a Qwen provider: `qwen-audio-3.1-asr-flash-streaming` over DashScope's duplex WebSocket (`run-task` / `result-generated` / `finish-task`). The existing HTTP model `qwen-audio-3.1-asr-flash` becomes the **checking** model.
2. **A new transcription job "Checking"** in Settings → Transcription (beside Recording and Ask dictation). Default: the recording provider's first non-streaming model. Choosing "Off" (empty) is the switch that turns Layers 2 and 3 off, with a one-line hint ("Live text only: sentences aren't heard again").
3. **The desktop owns the transcript.** The backend streams sentence events and answers "listen to this time range again" requests; the desktop builds paragraphs, decides when to check, and checkpoints as today. This keeps one writer and leaves the durable-recording code alone.
4. **The backend keeps its own copy of the audio it heard** (16 kHz mono PCM, ~115 MB per hour) next to the recording, so it can listen again without decoding WebM/MP4 (there is no ffmpeg in the sidecar). It is deleted when the recording is deleted, and a startup sweep removes copies of completed recordings older than 7 days.
5. **Minute check and paragraphs** (replaces the 2 s pause rule, §1.6):
    - **Window.** `confirmedUntilMs` marks where confirmed text ends (0 at the start). Once the live final sentences reach ≥ 60 s past it, the next window is `[confirmedUntilMs, end of the newest live final sentence]`. A live sentence end is always between words, so no word is cut. Hard cap 180 s (the relisten limit). One check at a time: each window starts where the previous result ended.
    - **Hold back the last sentence.** The check returns sentences with times (§4.4). All but the last become one paragraph; `confirmedUntilMs` moves to the end of the last kept sentence. The next window starts there, so the held-back sentence is heard again together with what followed it.
    - **When nothing is held back:** at Pause and Stop (nothing follows), when the result has a single sentence and the window is ≥ 150 s (someone talking without a sentence end), and when the model returns no word times (then the whole window is one paragraph).
    - **Confirmed sentences** go to the paragraph grouping (§2.11). With the check off, or before Phase 3: every ≥ 60 s of live final sentences is confirmed as it is, at the newest live sentence end (here a thinking pause can still leave a wrong full stop; that is the live model's text).
    - **Failure:** the window's live sentences become a paragraph marked `failed` with Retry, `confirmedUntilMs` moves to the window end (no hold-back), and the next window goes on. Retry re-hears that paragraph's own range and replaces its text.
    - Why 60 s: long enough that a thinking pause usually falls inside a window and the model hears the whole thought; short enough that text settles within about a minute and one request stays ~2 MB.
6. **Final check (deferred after Phase 0 measured no gain; if built:)** runs automatically after Stop, with progress and a "Skip" button; Save waits for it (the existing "content not ready yet" path). About 3 paragraph requests at a time.
7. **Recording clock**: the desktop numbers audio by samples sent since the recording began (pauses excluded, same as the saved audio file). All times on the wire are milliseconds on this clock, so reconnects no longer restart times at 0 (a bug today: `audio_cursor_seconds` restarts for each socket).
8. **Desktop sends 16 kHz** when it uses v2; the backend resamples 24 kHz audio from v1 clients to 16 kHz itself.
9. **Other providers (SenseVoice, OpenAI-style servers, Qwen without a streaming model)** get pause-based cuts with Silero VAD (ONNX, MIT, ~2 MB, run with the `onnxruntime` already in the sidecar) in Phase 5, emitting the same v2 events. Until then they keep today's behaviour, and Layers 2–3 still work for them once the pause cuts exist.
10. **Measured in Phase 0 (2026-10-11, the user's key, from this Linux box in the US; see the progress log):**
    - (a) `wss://<host of the saved address>/api-ws/v1/inference` works (`wss://maas.qianwenaiapi.com/api-ws/v1/inference`). Derive it; no second address in Settings.
    - Opening the socket takes 5–9 s from here; a task on an open socket starts in ~0.2 s. **Open one upstream socket when the live socket opens, and run a new task per stretch of audio on it.** Buffer audio until `task-started`.
    - (b) `run-task` with `input.context` and a mid-task `continue-task` are both accepted.
    - (c) Drafts arrive without any flag, about every 0.5–1 s, and are rewritten (`grailint第nt` → `Gradient Descent`); the final comes ~0.7 s after the speech ends.
    - (d) **23 s without audio fails the task** (`CLIENT_ERROR: request timeout after 23 seconds`); silent audio with `heartbeat: true` keeps it alive. Plan: `finish-task` on the client's `commit` (pause); a new task on the next audio. An idle socket closes after ~60 s without a task: reconnect on resume and buffer meanwhile.
    - (e) Faster than real time is accepted and gives the same text, but it doesn't finish sooner (Qwen reads at about real-time pace). Replaying buffered audio after a reconnect delays drafts by that much; keep the 60 s cap.
    - (f) `semantic_punctuation_enabled: true` gave the same text on the sample: keep the default pause rule.
    - The HTTP model (`qwen-audio-3.1-asr-flash`) also returns **word times with punctuation** (`sentence.words[]`, ms from the start of the audio sent). `relisten` (§4.4) therefore returns sentences with times, so a checked paragraph keeps sentence-level times.
    - Audio with no speech gets HTTP `400 ASR_RESPONSE_HAVE_NO_WORDS`: that is empty text, not an error (fixed in `realtime.py` already: `qwen_heard_nothing()`).
    - Qwen's model list (`/compatible-mode/v1/models`) leaves out the 3.1 recognisers; Test connection now asks each model with half a second of silence (`try_qwen_model()`). It reports a live-only model (`url error`), an older Qwen3-ASR model (`Input should be…`), an unknown id (404) and a bad key (401).
    - `qwen3-asr-flash` takes a different request; Gunther uses Qwen-Audio models only.
    - **Which ids are live** (tested 2026-10-12): ids ending in `-streaming` work only on the socket, the same ids without it only by upload. `qwen-audio-3.0-asr-flash-streaming` and `qwen-audio-3.1-asr-flash-streaming` stream; `qwen-audio-3.0-asr-flash` and `qwen-audio-3.1-asr-flash` upload. An upload id sent to the socket is either refused (`url error`, 3.0) or gets **no answer at all** (3.1 hangs): the 10 s wait for `task-started` must end with a clear `service.error` ("<model> isn't a live model; choose one ending in -streaming"), never a silent hang.
11. **Paragraph grouping** (§1.7; measured 2026-10-12, progress log):
    - **Live.** Each time sentences are confirmed (about once a minute), the desktop calls `POST /api/recordings/{id}/paragraphs` (§4.7) with the open paragraph's sentences plus the new ones (numbered), and the tail of the paragraph before it as context only. The answer is where paragraphs start in that pool. Every resulting paragraph but the last is **frozen**; the last stays open and goes into the next call. So the screen only ever changes at its end. The pool is capped at 60 sentences / 6000 characters; past that, the oldest sentences of the pool are frozen as a paragraph first.
    - **After Stop.** One regroup over the whole transcript (chunks of ≤ 300 sentences, each starting at a paragraph start), so early paragraphs benefit from seeing the whole talk. Edited paragraphs are fixed boundaries: never merged, split or moved. Content-ready waits for it, with the same "Skip" as the closing check.
    - **Validation.** Only integers in range are used; sentence 1 always starts a paragraph; nothing else from the model is used. A failed or malformed answer changes nothing (the sentences stay in the open paragraph) and shows a small "Couldn't group into paragraphs · Retry"; the next call includes them anyway.
    - **No Analysis model:** one paragraph per minute check (or per 60 s with checks off), and a one-line hint: "Add an Analysis model in Settings → Models to group paragraphs by topic."
    - **Times:** a paragraph's time is its first sentence's start, which the live and check sentences both carry.
    - Cost: one small text request per minute (≈1–3k tokens), a few cents per hour at most.

## 3. What exists today (read before starting)

- `apps/backend/gunther/realtime.py` — `proxy_realtime_transcription()` picks a transcriber per provider kind and calls `_proxy_segmented_transcription()`, which cuts `pending` PCM into fixed `segment_seconds` blocks (3.2 s from `settings.sensevoice_segment_seconds`), transcribes each with one HTTP call, and sends `conversation.item.input_audio_transcription.completed` with `item_id`, `transcript`, `start_seconds`. Qwen goes through `_qwen_transcriber()` → `qwen_request()` (multimodal-generation, audio only, no context). Only the OpenAI-style path sends `prompt` (the title).
- `apps/backend/gunther/api.py` — `@router.websocket("/recordings/live")` → `live_recording(context, purpose)`; resolves the job with `speech_registry.resolve()`.
- `apps/backend/gunther/speech_registry.py` — `PRESETS`, `ROLES` (`recording`, `dictation`), `Choice`, `default_roles()`, `resolve()`. Settings UI: `apps/desktop/src/models/SpeechSettings.tsx`.
- `apps/desktop/src/components/LectureRecorder.tsx` — `openTranscriptionSocket()` (events → `partsRef` → `publishTranscript()` → `[mm:ss] text` lines), the `ScriptProcessor` that sends 24 kHz PCM (`encodePcm16` in `services/pcm.ts`), `pause()` sends `input_audio_buffer.commit`, `stop()`, the 5 s `queueCheckpoint()`, `RecordingDraft` (`formatVersion: 2`), `knowledgeDocument()` (the saved Markdown: `## Transcript` / `## Full transcript`), the editable `<textarea>` (also used to paste a transcript when transcription is off or for imported audio). Pending audio while reconnecting is capped at 120 chunks (~10 s).
- `apps/backend/gunther/recording_service.py` — `checkpoint()` stores `transcript`, duration, moments with revision + hash; `RecordingCheckpointInput` in `schemas.py`; `RecordingSession` model (`transcript` Text). Contracts: `packages/contracts/src/index.ts` (`RecordingCheckpointInput`, `RecordingSession`).
- Saved view: `apps/desktop/src/items/sourceContent.ts` `parseTranscript()` (a line starting `[mm:ss] ` starts a segment; blank lines separate untimed paragraphs) and `apps/desktop/src/items/views/RecordingView.tsx` `Transcript` (time button + text, active segment while playing).
- Phone: `apps/mobile/lib/data/services/live_transcription_service.dart` uses the same socket with v1 events.
- Analysis model calls: `apps/backend/gunther/lecture.py` (`gateway.for_role("analysis")`).
- Pinned ONNX model fetching and bundling: `scripts/fetch_embedding_model.py`, `scripts/build-backend-sidecar.mjs` (`--add-data`).
- Migrations: `apps/backend/gunther/migrations.py`, last is `Migration(22, "canonical_documents", …)`.

## 4. Contracts

### 4.1 Live socket, protocol v2

Query: `/api/recordings/live?context=<title>&purpose=recording&protocol=2&sample_rate=16000&offset_ms=<n>[&recording_id=<id>]`

- `protocol` absent → v1, exactly as today.
- `offset_ms`: where on the recording clock this connection's audio starts (0 for a new recording; the resume point after a reconnect).
- `recording_id`: lets the backend keep its audio copy (Phase 3). Must name a recording in `capturing` state, or it is ignored.

Client → server (v2):

```jsonc
{"type": "input_audio_buffer.append", "audio": "<base64 PCM16 mono>", "offset_ms": 61230}
{"type": "input_audio_buffer.commit"}   // pause or stop: finish the current sentence
```

`offset_ms` on each append lets the backend drop audio it already has (replay after reconnect) and notice gaps.

Server → client (v2):

```jsonc
{"type": "service.ready", "provider": "qwen", "model": "...", "protocol": 2, "live": "stream" | "segments"}
{"type": "transcript.piece", "id": "k3f9-2-14", "start_ms": 61230, "end_ms": 64810 | null,
 "text": "We'll use gradient descent", "final": false,
 "sentences": [{"start_ms": 61390, "end_ms": 63100, "text": "…。"}, …]}   // final pieces only
{"type": "transcript.gap", "start_ms": 120000, "end_ms": 131500, "reason": "connection"}
{"type": "service.error", "code": "...", "message": "..."}   // unchanged
```

- **A piece is not a sentence.** Qwen's `sentence_id` marks a stretch its own pause detector chose. In a quiet room that is about one sentence. On the noisy sample the pauses between paragraphs were filled with fan noise and chatter, so pieces ran 15–30 s, across paragraph breaks (measured 2026-10-11; see the progress log). Noise taken for speech also comes back as a final piece with empty text: drop it (it isn't a sentence, and counting it hides the pause it sits in). So the backend splits each **final** piece into real `sentences` using the word times and punctuation Qwen sends (`words[].begin_time/end_time/punctuation`; the same splitter as `relisten`, §4.4), and the desktop builds sentences and paragraphs from those.
- `transcript.piece` is an **upsert by `id`**: the same id arrives many times with `final: false` (the grey tail on screen), then once with `final: true` and its `sentences`. Ids are `<random per socket>-<task number>-<sentence_id>`, so they never repeat across reconnects.
- v1 clients on the same streaming model get one `conversation.item.input_audio_transcription.completed` per **final sentence** (`item_id`, `transcript`, `start_seconds`), so the phone also gains sentence-aligned text.

### 4.2 Transcript model (desktop, new `apps/desktop/src/services/liveTranscript.ts`)

```ts
export interface LiveSentence { id: string; startMs: number; endMs: number; text: string }   // from final pieces' `sentences`; id "<piece id>.<n>"
export interface LiveDraft { pieceId: string; startMs: number; text: string }               // the newest non-final piece, light grey at the end
export interface Sentence {                 // a confirmed sentence, black
  id: string;                               // "s-<startMs>"
  startMs: number; endMs: number; text: string;
  windowId: string;                         // the minute check (or live batch) it came from
  changed?: [number, number][];             // token ranges that differ from the live text (the brief underline)
}
export interface CheckWindow {              // one minute check, kept for provenance
  id: string;                               // "w-<startMs>"
  startMs: number; endMs: number;
  live: LiveSentence[];                     // what was heard live in this range
  state: "checked" | "checking" | "failed" | "off";   // "checking" during Retry; "off" = checks off, live text confirmed as is
  pass: "minute" | "retry";
  model?: string; at?: string; error?: string;
}
export interface TranscriptParagraph {
  id: string;                               // "p-<first sentence startMs>"
  sentenceIds: string[];
  frozen: boolean;                          // false only for the newest paragraph while grouping can still split it
  edited?: string;                          // the user's text; wins, locks the paragraph, and is a fixed boundary for grouping
}
export interface TranscriptState {
  version: 1;
  sentences: Sentence[];                    // all confirmed sentences, in time order
  windows: CheckWindow[];
  paragraphs: TranscriptParagraph[];        // groups of `sentences` (§2.11); a paragraph's time = its first sentence's start
  live: LiveSentence[];                     // live final sentences after confirmedUntilMs, dark grey
  draft: LiveDraft | null;                  // light grey
  confirmedUntilMs: number;                 // where the next minute check starts (§2.5)
  checking: { startMs: number; endMs: number } | null;      // the window in flight, at most one
  grouping: { state: "idle" | "running" | "failed" | "off"; error?: string };   // "off" = no Analysis model
  gaps: { startMs: number; endMs: number; reason: string }[];
}

export function applyPiece(state, event): TranscriptState                 // draft → replace; final → append its sentences to `live`
export function nextWindow(state, rules, { closing }): { startMs: number; endMs: number } | null   // §2.5; closing = Pause/Stop takes everything
export function applyWindowResult(state, window, result | error, { closing, checksOff }): TranscriptState
  // §2.5: keep all but the last sentence (unless closing etc.) → `sentences` (with `changed` from a token diff
  // against the window's live text); move confirmedUntilMs; drop live sentences that end before it; the new
  // sentences join the open paragraph. checksOff → the live sentences are confirmed as they are (window "off").
  // Failure → the live sentences are confirmed, window "failed".
export function groupingRequest(state): { sentences: string[]; before: string } | null   // the open paragraph + unfrozen sentences (§2.11)
export function applyGrouping(state, starts | error): TranscriptState     // split the open paragraph at `starts`; freeze all but the last
export function applyRegroup(state, starts | error): TranscriptState      // after Stop: regroup everything between edited paragraphs
export function applyRetry(state, windowId, result | error): TranscriptState   // replace that window's sentences; each new sentence goes to the paragraph whose time range holds its start; edited paragraphs keep their text
export function paragraphText(state, p): string   // edited ?? joined sentences
export function renderTranscript(state): string   // "[mm:ss] text" paragraphs separated by blank lines, then the unconfirmed live text as its own last paragraph
```

A live sentence that straddles `confirmedUntilMs` (its live end is later than the kept check sentences) stays in `live`: the held-back part of the speech keeps showing in dark grey until the next window confirms it. "Show what was heard live" for a paragraph shows the `live` sentences of the windows that overlap its time range.

`joinSentences` puts no space between two sentences when the boundary is between CJK characters or after CJK punctuation, and one space otherwise (a script rule, not a language list). `renderTranscript` output is what goes into `transcript` (checkpoint, draft, `knowledgeDocument`); `parseTranscript()` already reads it as one timed segment per paragraph. Gaps render as their own line: `[12:03] (no audio was transcribed from 12:03 to 12:40)`.

### 4.3 Checkpoint and storage

- `RecordingCheckpointInput` gains `transcript_segments: dict | None` (the `TranscriptState`, camelCase on the wire, ≤ 2 MB serialized). It is part of the checkpoint document and hash.
- Migration 23 `recording_transcript_segments`: `recording_sessions.transcript_segments_json TEXT NULL`. Returned on `RecordingSessionOut` as `transcriptSegments`, so recovery restores paragraphs.
- `RecordingDraft.formatVersion: 3` adds `segments`. A v2 draft still loads: its text becomes paragraphs (via the same parse as `parseTranscript`), each one sentence marked `edited` (locked), frozen, in a window with state `"off"`.

### 4.4 Listen again — `POST /api/recordings/{id}/relisten`

```jsonc
// request
{"startMs": 61230, "endMs": 118400, "pass": "minute" | "retry" | "final",
 "context": {"terms": ["gradient descent", "learning rate"], "before": "…", "after": "…"}}
// 200
{"text": "…", "model": "qwen-audio-3.1-asr-flash", "provider": "Qwen",
 "sentences": [{"start_ms": 61390, "end_ms": 64810, "text": "…。"}]}   // on the recording clock; [] when the model gives no word times
// 409 {"detail": "No audio was kept for 61.2–118.4 s."} · 424 {"detail": "Choose a Checking model in Settings → Transcription."} · 502 {"detail": "<provider error>"}
```

Sentences come from Qwen's `sentence.words[]`: words are joined until one carries sentence-ending punctuation (`。！？.!?`), and times are shifted by the padded start. Sentences that lie entirely in the padding are dropped. These sentences are what the minute check keeps or holds back (§2.5).

Reads the audio copy (padded 300 ms each side, range ≤ 180 s), calls the **Checking** job's model:
- Qwen HTTP: `qwen_request()` gains `context: list[str]`; each non-empty item becomes a `{"role": "user", "content": [{"type": "input_text", "text": item[:400]}]}` message **before** the audio message (order: terms, before, after).
- OpenAI-style: `prompt` = terms joined by ", " then the tail of `before`, ≤ 800 characters.
- SenseVoice: no context.

### 4.5 Terms — `POST /api/recordings/{id}/terms` (Phase 4)

Body `{"transcript": "<rendered text>", "title": "…"}` → `{"terms": ["…"]}` from the Analysis model with prompt §5.1. The backend keeps only terms that occur in the transcript (case-insensitive substring), dedupes, ≤ 60. No Analysis model → 424 with the usual "needs a model" message; the final check then runs with before/after context only and says so.

### 4.6 Speech registry

- `ROLES` gains `Role("checking", "Checking", "Listens to each minute again, with what was said before it, and replaces the live text. Sends the audio a second time.", False)`. `default_roles()`: the recording provider's first model that isn't a streaming model, else `None`.
- `is_duplex_stream(kind, model) -> bool`: `kind == "qwen"` and the model id ends with `-streaming` or starts with `fun-asr-realtime`. Only these use the duplex socket.
- `PRESETS["qwen"].models` lists `qwen-audio-3.1-asr-flash-streaming` and `qwen-audio-3.1-asr-flash`. The note says which is for live and which for checking.
- When the Recording job's model is a Qwen model that isn't streaming, Settings shows one line: "For live sentences that correct themselves, use qwen-audio-3.1-asr-flash-streaming" with a **Use it** button (adds the model to the provider if missing and selects it). Saved choices are never switched silently.

### 4.7 Paragraphs — `POST /api/recordings/{id}/paragraphs` (Phase 3B)

```jsonc
// request: sentences in order; `before` is the tail of the previous paragraph (≤ 400 chars), context only
{"sentences": ["好，我们开始上课。", "今天这节课讲…", "…"], "before": "…", "pass": "live" | "regroup"}
// 200: zero-based indices of the sentences that start a paragraph; always includes 0
{"starts": [0, 4, 9], "model": "deepseek-…"}
// 424 {"detail": "<the usual needs-a-model message>"} · 502 {"detail": "<provider error>"}
```

The backend calls the Analysis model (`gateway.for_role("analysis")`) with prompt §5.2, numbering sentences from 1 in the message and converting back. It keeps only integers in range, sorts and dedupes them, and adds 0. Anything else in the answer is ignored; an answer with no usable `starts` is a 502 ("The model didn't say where paragraphs start"). Up to 300 sentences per request; the desktop chunks the regroup (§2.11).

## 5. Prompts (use verbatim)

### 5.1 `TERMS` (Phase 4, Analysis model, JSON out)

```
You read a transcript made by speech recognition. List the names, technical terms, acronyms and other
distinctive words or phrases that are used in it, written the way they are most often and most clearly
written in this transcript. These will help a speech recognizer hear the same words again.
Rules: copy each term exactly as it appears in the transcript; do not translate, explain or add terms
that do not appear; skip common everyday words; at most 60 terms, most important first.
Answer with JSON only: {"terms": ["...", "..."]}
```

### 5.2 `PARAGRAPHS` (Phase 3B, Analysis model, JSON out)

System message:

```
You group the sentences of a spoken transcript (speech recognition output) into paragraphs, the way a
careful editor would. A paragraph holds one point of the talk: one idea, one example, one step of an
explanation. Start a new paragraph where the speaker moves to a new point, where someone asks a question,
or where a different person starts speaking. Keep sentences about the same point together, even many of
them. Avoid a paragraph of one short sentence unless it is a question, an answer's first sentence, or a
different speaker. Never change, merge or split sentences; only say where paragraphs start. Sentences are
numbered from 1, and sentence 1 always starts a paragraph. The last sentences may be unfinished talk; group
them as they are.
Answer with JSON only: {"starts": [numbers of the sentences that start a paragraph]}
```

User message: `Before (context only, already grouped):\n<before>\n\nSentences:\n1. …\n2. …` (the "Before" part only when `before` isn't empty).

---

## Phase 0: Probe Qwen's streaming model (no change to the app)

### Tasks
1. `apps/backend/evals/transcript/probe_qwen_stream.py <audio.wav> [--model qwen-audio-3.1-asr-flash-streaming] [--context "terms…"]`. Reads the key and address like the app (service-settings.json path via `--settings`, else `.env` `QWEN_STT_API_KEY`/`QWEN_STT_BASE_URL`). Uses the `websockets` package already in the backend.
2. It runs these trials and prints one line per event (arrival time, `sentence_id`, `sentence_end`, `begin_time`, `end_time`, text):
   - connect to `wss://<host of base URL>/api-ws/v1/inference`; on failure try the documented workspace hosts and print what worked (§2.10a);
   - `run-task` with `format: "pcm"`, `sample_rate: 16000`, `input.context` set; stream the WAV in 100 ms chunks at real-time pace; `finish-task`; wait for `task-finished`;
   - the same with `continue-task` sent halfway (10b), without and with `intermediate_result_enabled: true` (10c), with 30 s of no audio in the middle (10d), at 3× real time (10e), with `semantic_punctuation_enabled: true` (10f);
   - one HTTP call to `qwen-audio-3.1-asr-flash` with a context message before the audio, to confirm §4.4.
3. `evals/transcript/README.md`: how to run it, and the sample WAV the user should record (2–3 minutes of a lecture with some technical terms and a little background noise).

### Done when
The user (or Opus with the key) has run it, and the progress log records the answers to §2.10 a–f. Opus updates §2 and §4 if any answer changes the design.

## Phase 1: Live sentences from Qwen's streaming model (backend)

### Tasks
1. `realtime.py`: a `_proxy_duplex_stream()` path used when `is_duplex_stream(choice.kind, choice.model)`. Open the upstream socket (`wss://<host>/api-ws/v1/inference`, `Authorization: Bearer`) as soon as the client connects, before speech (opening takes seconds; §2.10). One upstream **task** per stretch of continuous audio on that socket: `run-task` (wait for `task-started`, buffer audio meanwhile, 10 s timeout) → binary PCM frames of 100 ms → `finish-task` on the client's `commit` (pause), so no task ever sits 23 s without audio. The next `append` starts a new task at its `offset_ms`; if the socket closed meanwhile (~60 s idle), reconnect first. If the upstream task fails or the socket closes while recording, reconnect and start a new task (backoff 0.5/1/2/4 s), keep up to 30 s of audio buffered, and send `transcript.gap` for anything lost.
2. Map each `result-generated` to `transcript.piece` (§4.1): `start_ms = task_start_offset + begin_time`, same for `end_ms` and every word time; skip `heartbeat` packets and empty sentence-begin events. Final pieces carry `sentences` from `split_sentences(words)` (a shared helper, also used by `relisten`): words are joined until one carries sentence-ending punctuation (`。！？.!?；;`). v1 clients get `…completed` per final sentence instead.
3. Context: `input.context` = [terms turn: the `context` query (title) ≤ 400 chars] + [recent turn: the last final sentences of this socket, ≤ 400 chars, newest kept]. Refresh mid-task with `continue-task` every 3 final sentences (at most every 20 s) only if Phase 0 showed it works; otherwise only at each new task.
4. Audio in: decode base64, resample to 16 kHz when `sample_rate` ≠ 16000 (`resample_pcm16()` with numpy, low-pass before decimating; unit-tested), drop bytes before the expected offset, note gaps after it.
5. `speech_registry.py`: `is_duplex_stream()`, Qwen preset models (§4.6). The `checking` role is added in Phase 3, not here.
6. `api.py` `live_recording()`: accept `protocol`, `sample_rate`, `offset_ms`, `recording_id` (validated, unused until Phase 3).
7. Fix the desktop label bug while here: `openTranscriptionSocket()` shows "OpenAI live" for every non-SenseVoice provider; use the `service.ready` provider and model.

### Tests (`tests/test_realtime_stream.py`)
A fake upstream WebSocket server (local `websockets.serve`) that speaks run-task/result-generated/finish-task:
- drafts and a final for the same `sentence_id` arrive as the same v2 `id`, with recording-clock times after an `offset_ms` of 60 000;
- `commit` sends `finish-task`, and the next append opens a new task whose times continue;
- an upstream failure mid-task reconnects and emits `transcript.gap` only for audio it couldn't resend;
- v1 clients receive only final sentences as `…completed`;
- 24 kHz input reaches upstream as 16 kHz (byte count ×2/3 ±1 frame);
- run-task carries both context turns, each ≤ 400 chars.
Existing `tests/test_transcription.py` and `tests/test_speech.py` still pass unchanged.

### Done when
With the fake server, a v2 client sees sentences that revise in place and finalize with correct times across a pause and a reconnect; v1 behaviour is unchanged.

## Phase 2: Paragraphs on screen (desktop)

### Tasks
1. `services/liveTranscript.ts` per §4.2, pure functions, no React.
2. `LectureRecorder.tsx`: v2 socket (`protocol=2`, `sample_rate=16000`, `offset_ms`, `recording_id`); `encodePcm16(samples, rate, 16_000)`; track `sentOffsetMs`; append messages carry `offset_ms`; pending audio while reconnecting keeps up to 60 s (was ~10 s). Replace `partsRef`/`publishTranscript` with a `TranscriptState` held in a ref and mirrored to state; `transcript` text = `renderTranscript(state)`. With no Checking job yet (this phase), paragraphs form with `checksOff` (§2.5): every ≥ 60 s of live sentences, and everything at `pause()`/`stop()`.
3. Paragraph view replaces the `<textarea>` when the state has paragraphs or live text: gutter time, black confirmed paragraphs, dark grey live sentences, light grey draft, "checking…" / "Couldn't check · Retry" (wired in Phase 3), click a confirmed paragraph to edit (inline textarea, Esc cancels, blur/⌘↵ saves → `edited`; live text isn't editable while it can still change), "edited" faint label, "Show what was heard live" in the paragraph menu. Keep auto-scroll unless the user has scrolled up. When there are no paragraphs (transcription off, imported audio), keep today's textarea exactly.
4. Checkpoint and draft carry `transcriptSegments` / `formatVersion: 3` (§4.3), backend migration 23 + schema + contracts. Recovery restores the paragraph view.
5. `RecordingView.tsx` `Transcript`: paragraphs read as paragraphs (time in a narrow gutter above the first line on narrow widths, readable line length, the active paragraph slightly darker while playing). No change to `parseTranscript()` needed; add a test that the new rendered format parses to one timed segment per paragraph.
6. CSS in the existing design files; reuse tokens; no new colours.

### Tests
- `liveTranscript.test.ts`: upsert by id; draft → final appends to `live`; with checks off a paragraph forms at the first live sentence end ≥ 60 s, never at a pause by itself (a 10 s gap between live sentences at 20 s does not start a paragraph); pause/stop confirm everything; edited paragraphs ignore retries; CJK/Latin joining; gaps render; render → `parseTranscript` round trip.
- `LectureRecorder.test.ts`: a mocked v2 socket drives drafts/finals into paragraphs; a v2 draft and a v3 draft both restore.
- Backend: checkpoint stores and returns `transcriptSegments`; migration 23 applies on a v22 database.

### Done when
Live recording shows a light grey sentence that rewrites itself, dark grey live sentences, and black paragraphs of about a minute; the saved recording shows paragraphs with clickable times; old recordings still display.

## Phase 3: Minute check (backend audio copy + listen again + Checking job)

### Tasks
1. Audio copy: when the live socket has a valid `recording_id`, write the 16 kHz PCM to `<recordings_dir>/<id>.listen.pcm` at byte `offset_ms × 32`, and append covered ranges to `<id>.listen.ranges` (`start_ms end_ms` per line). Delete both with the recording; startup sweep per §2.4.
2. `POST /api/recordings/{id}/relisten` per §4.4, using the `checking` job. Context per provider kind. Errors as listed.
3. `speech_registry.py`: the `checking` role and its default; `SpeechSettings.tsx` shows it like the other jobs, plus the "Use it" hint (§4.6).
4. Desktop: after each final piece, `nextWindow()`; when it returns a window and none is in flight, set `checking` and call `relisten` with `pass: "minute"`. Context: `terms` = the recording title, `before` = the last confirmed paragraph's text tail. Apply with `applyWindowResult()`; failure → `failed` paragraph + Retry (§2.5). At Pause/Stop, one closing window takes everything; Stop's "content ready" waits for it (the existing "content not ready yet" path), with a "Skip" that confirms the live text as it is.
5. The underline on changed words (§Display): a token diff of the window's live text vs the kept check sentences; mark changed tokens for ~4 s, then fade. Pure function in `liveTranscript.ts`, tested.
6. `evals/transcript/compare.py` gains a `minute` method (the same §2.5 rules over the stream results, via `--reuse`). It must score no worse than `paragraph` on `noisy.wav` and `hard.wav`, and keep the sentence in `probe_pause.py` whole. Record the numbers in the progress log.

### Tests
- Backend: the audio copy lands at the right offsets with a gap; `relisten` cuts the right bytes (with padding), drops sentences that lie only in the padding, sends context messages before the audio for Qwen and `prompt` for OpenAI-style (mock transport); 409 / 424 / 502 cases; deleting a recording removes the copy.
- Desktop: a window starts once live sentences reach 60 s past `confirmedUntilMs` and not before; only one is in flight; the result's last sentence is held back and the next window starts at the end of the kept ones; a sentence split by the live model at a pause is replaced by the check's single sentence; a single-sentence result under 150 s is held back entirely, over 150 s confirmed; Pause/Stop confirm everything; a failure makes a `failed` paragraph and the next window continues after it; Retry replaces only that paragraph; an edit wins over a retry.

### Done when
During a recording, about once a minute "checking…" appears and the live text a few seconds later turns into a black paragraph (changed words briefly underlined), with its last sentence held back; the live text stays in the saved data.

## Phase 3B: Paragraphs by content (Analysis model)

### Tasks
1. Backend: `POST /api/recordings/{id}/paragraphs` per §4.7 with prompt §5.2 (`gateway.for_role("analysis")`, JSON out). Validation as in §4.7.
2. Desktop: after every `applyWindowResult()` (minute check, failed check, or live batch with checks off), if grouping isn't `off` and none is running, send `groupingRequest(state)` and apply with `applyGrouping()` (§2.11): all resulting paragraphs but the last are frozen. Only confirmed sentences are ever sent, never live or draft text. No Analysis model → `grouping.state = "off"`: each window's sentences form one frozen paragraph, and the hint from §2.11 shows once under the transcript.
3. After Stop, when the closing check has settled: regroup (`pass: "regroup"`) in chunks of ≤ 300 sentences, each chunk starting at a paragraph start and ending before an edited paragraph; apply with `applyRegroup()`. Content-ready waits for it; "Skip" keeps the live grouping.
4. Failure: "Couldn't group into paragraphs · Retry" in small grey text under the open paragraph; the sentences stay where they are and the next call includes them.
5. `evals/transcript/probe_paragraphs.py` exists (2026-10-12); keep it working and add its result after the change to the progress log.

### Tests
- Backend: sentence numbers convert to zero-based indices; out-of-range, non-integer and duplicate starts are dropped; 0 is always present; an answer without usable `starts` is a 502; no Analysis model is a 424 (fake gateway, no network).
- Desktop: a grouping answer splits only the open paragraph and freezes all but its last part; frozen paragraphs never change in later calls; an edited paragraph is never merged, split or moved, live or in the regroup; a failure leaves the sentences in the open paragraph and shows Retry; regroup chunks start at paragraph starts; with grouping `off` each window is one paragraph.

### Done when
During a recording, paragraphs follow the speaker's points (a question gets its own paragraph), only the newest paragraph ever changes, and after Stop the whole transcript is regrouped once; without an Analysis model the minute paragraphs and the hint appear.

## Phase 4: Final check after Stop (deferred)

> Phase 0 measured no gain from this layer (progress log). Build it only if `evals/transcript/compare.py` on a real recording shows the final check beating the minute check.

### Tasks
1. `POST /api/recordings/{id}/terms` (§4.5, prompt §5.1).
2. After Stop and the closing minute check settles: fetch terms, then re-listen every non-edited paragraph with `pass: "final"`, `terms` chosen for that paragraph (terms found in it or its neighbours first, then the rest, joined to ≤ 400 chars), `before` = previous paragraph tail, `after` = next paragraph head. 3 at a time. Progress line: "Checking the whole recording · 12 of 40 · Skip".
3. While it runs, `onKnowledgeContent` emits `""` (not ready), so Save waits. "Skip" stops queuing and makes the content ready. Recovery after a restart offers "Check the rest" for paragraphs without a `final` check.
4. Notices: no Analysis model → "Checked with the paragraphs around each one; add an Analysis model to also use the recording's terms."

### Tests
- Backend: `terms` keeps only terms present in the transcript; no model → 424.
- Desktop: final pass requests carry before/after/terms; Skip makes the content ready; edited paragraphs are skipped.

### Done when
After Stop, the transcript is re-checked paragraph by paragraph with terms from the whole recording, and saving uses the result.

## Phase 5: Pause-based cuts for the other providers

### Tasks
1. `scripts/fetch_vad_model.py` (pattern of `fetch_embedding_model.py`): Silero VAD `silero_vad.onnx`, pinned revision + SHA-256, into `apps/backend/models/silero-vad/`; bundle with `--add-data` in `build-backend-sidecar.mjs`; `npm run models:fetch` fetches both.
2. `gunther/vad.py`: Silero VAD on 16 kHz frames with `onnxruntime`. A sentence ends after ≥ 500 ms of silence, or is cut at the quietest point of its last 2 s once it reaches 15 s; minimum 1 s.
3. `_proxy_segmented_transcription()` uses these cuts instead of fixed blocks and emits v2 `transcript.piece` (final only, one sentence per piece) with times, v1 `…completed` as before. Pass the last final text as context the same way as §4.4.
4. Missing model file → keep today's fixed blocks and say so once in `service.ready` (`"live": "segments-fixed"`); the build always includes the file.

### Tests
VAD on synthetic audio (tone bursts separated by silence) cuts at the silences; a 20 s continuous tone is cut at ≤ 15 s; the segmented proxy emits v2 sentences with correct times.

### Done when
SenseVoice and OpenAI-style providers produce sentence-aligned live text, and Layers 2–3 work for them.

---

## Pitfalls

- **Draft text is not a final.** Never checkpoint drafts as if settled, never send them to a check, never use them as context.
- **A live sentence end is not a real sentence end.** The live model ends a sentence at any pause, even in the middle of a thought. Never start a paragraph or end a check window because of a pause; only the minute check confirms sentence ends (§2.5).
- **One minute check at a time.** Each window starts where the previous result ended; running two in parallel would cut the held-back sentence.
- **Two clocks.** Qwen's `begin_time` counts from its task's start; always add the task's start offset. Reconnects and pauses start new tasks.
- **Silence timeout.** During pause no audio flows; the plan sends `finish-task` on commit so the upstream task never sits idle (Phase 0 measures the limit).
- **Replay after reconnect** may resend audio the backend already has; drop by `offset_ms`, don't transcribe twice.
- **Edits win.** A check result that arrives after the user edited the paragraph is stored in `checks` but not shown.
- **v1 must not change** for the phone and Ask dictation (`useDictation.ts`).
- **Don't trust a check that came back empty**: keep the live text and mark `failed` with the reason ("The model returned no text"). It's an error, not a silent swap.
- **Context limits are silent.** Qwen cuts turns over 400 chars and keeps only 5; trim on our side so the important part (newest text, first terms) survives.
- **Privacy is unchanged**: the same audio already goes to the same provider; checks send it a second (and third) time. Say so in the Checking job's description.

## Review checklist (Opus)

- [ ] No rule mentions a particular language, word, recording or publisher (AGENTS.md).
- [ ] No pause length decides a sentence or paragraph end; the check's last sentence is held back except at Pause/Stop and the 150 s case.
- [ ] v1 socket output is identical for v1 clients (existing tests untouched and passing).
- [ ] Times are on the recording clock everywhere; a pause or reconnect doesn't reset or shift them.
- [ ] The live text, every check and every edit are kept in `transcriptSegments`; the rendered transcript shows edited ?? last check ?? live.
- [ ] Failures are visible with Retry; no fallback text is invented.
- [ ] Audio copies are deleted with their recording and swept when stale.
- [ ] UI: black/grey only, gutter times, no layout jump when a paragraph is replaced (same block, text swaps).
- [ ] Old recordings and v2 drafts still open.

## Sources

- [Qwen real-time speech recognition (models, endpoints, events)](https://www.alibabacloud.com/help/en/model-studio/qwen-real-time-speech-recognition)
- [Improve recognition accuracy: context enhancement and hotwords, per model](https://www.alibabacloud.com/help/en/model-studio/improve-asr-accuracy)
- [Qwen-Audio-ASR-Streaming server events (result-generated, sentence_end, words)](https://www.alibabacloud.com/help/en/model-studio/qwen-audio-asr-streaming-server-events)
- [Fun-ASR / Qwen-Audio streaming client events (run-task, continue-task, parameters)](https://help.aliyun.com/en/model-studio/fun-asr-client-events)
- [Duplex WebSocket endpoints and flow](https://www.alibabacloud.com/help/en/model-studio/fun-asr-realtime-websocket-api)
- [Qwen-Audio-ASR-Message server events (drafts may be revised; update by sentence_id)](https://help.aliyun.com/en/model-studio/qwen-asr-message-server-events)
- [A field report moving live transcription to Qwen-Audio 3.1 (partials flag, endpoints)](https://github.com/rasonyang/ai-native-callcenter/issues/26)
- [FunASR 2pass: streaming plus a non-streaming correction at sentence end](https://github.com/alibaba-damo-academy/FunASR/blob/main/runtime/docs/SDK_advanced_guide_online_zh.md)
- [Deepgram interim vs final results](https://developers.deepgram.com/docs/understand-endpointing-interim-results)
- [Apple SpeechAnalyzer, volatile and finalized results (WWDC25)](https://developer.apple.com/videos/play/wwdc2025/277/)
- [Whisper-Streaming / LocalAgreement](https://github.com/ufal/whisper_streaming) · [paper](https://arxiv.org/abs/2307.14743)

## Progress log

_(each phase adds an entry here)_

### Phase 0 — 2026-10-11 (Opus, with the user's Qwen key, from a US Linux box)

Built: `apps/backend/evals/transcript/` (`make_sample.py`, `script.toml`, `compare.py`, `probe_stream.py`, README). The user had no recording, so the test lecture is made with Qwen's text-to-speech (`qwen3-tts-flash`, voices Ethan and Cherry): 132 s, 17 sentences in 6 paragraphs, Chinese with English terms, then a room is added (fan 12 dB under the speech, distant talking 20 dB under, four knocks inside words, the student quieter).

Scores on `noisy.wav` (errors ÷ reference tokens, a token = one CJK character or one Latin word):

| Method | Error rate | Notes |
|---|---|---|
| today (3.2 s clock cuts, HTTP model, no context) | 4.9% | 17 edits: words lost or doubled at cuts (`梯。度下降`, `真。真实答案`), invented words (`行情`, `自由`), `SGD` → `HDD`; full stops inside words |
| Layer 1: streaming model | 0.7% | 3 edits, none real errors: `eta` → `η`, `you will` → `you'll`, `这一组` → `这组`. Finals arrive 3.2 s after the speech on average (max 6.7 s) over this box's slow link; 151 draft updates |
| Layer 2: paragraph check | 0.7% | same 3 edits; nothing left to fix on this sample |
| Layer 3: final check (terms from DeepSeek) | 0.7% | same; the 41 terms were all correct |

Findings that changed the design (already folded into §2.10 and §4):
- Qwen's pause detector hears no pauses in the noisy room: its pieces ran 15–30 s across paragraph breaks. Sentences and paragraphs now come from the **word times** (§4.1). With them, the 5 paragraph starts land within 0.2 s of the script's.
- `multi_threshold_mode_enabled` changed nothing. `speech_noise_threshold: 0.4` produced empty pieces inside the pauses, harmless once empty pieces are dropped. Keep Qwen's defaults.
- The connection takes 5–9 s to open from here, and tasks start in 0.2 s; 23 s without audio ends a task; context and `continue-task` are accepted; faster than real time is accepted but not faster; semantic sentence ends changed nothing.
- Fixed in the app right away (not part of a later phase): silence answered with `ASR_RESPONSE_HAVE_NO_WORDS` is now empty text, not "missed one segment" (`realtime.qwen_heard_nothing`); Test connection asks each Qwen model instead of trusting a model list that leaves out the 3.1 recognisers (`realtime.try_qwen_model`); a new Qwen provider starts with `qwen-audio-3.1-asr-flash`.

**Hard room** (`hard.wav`: the same lecture with 0.6 s of echo, the fan 3 dB under the speech, distant talking 10 dB under, the student twice as far again):

| Method | Error rate | What went wrong |
|---|---|---|
| today | 23.3% | 30 edits spread everywhere: `好行那咱们就咱们就咱们就我靠神级版你`, `运费`, `python的` for `Adam`, the student's question as English nonsense |
| Layer 1: streaming | 23.7% | 9 edits, mostly two large ones: an invented English sentence (`as I've written I'm a very big believer`) in place of a Chinese one, and the student's question dropped |
| Layer 2: paragraph check | **4.2%** | both large failures fixed; left: the quiet student's question partly wrong (`老师我想问一下…之间有关系吗` → `那如果…变大了…怎么调`) |
| Layer 3: final check | 6.7% | identical to Layer 2 except in the student's paragraph, where even more of the question was lost |

Conclusions:
1. **Phases 1–2 are the clear win in normal rooms** (4.9% → 0.7%, and the text reads properly: no cut words, no stray full stops).
2. **Phase 3 (paragraph check) is the clear win in bad rooms** (23.7% → 4.2%). In bad audio the live text can be badly wrong for a paragraph's length; the check fixes it a few seconds later. Build it.
3. **Phase 4 (final check) showed no gain** on either sample and was worse on the hardest paragraph, and it costs a third pass plus a wait after Stop. **Deferred**: build it only if a real recording shows a gain (run `compare.py` on it).
4. Still weak: a quiet, distant second speaker. Nothing in this plan fixes that; note it for later (a closer microphone, or speaker separation).

### Design change — 2026-10-12 (Opus, with the user)

The user asked whether the 2 s pause rule was hard-coded ("it could be thinking") and suggested listening again over a whole stretch of time instead. Probe `evals/transcript/probe_pause.py`: one sentence spoken in two halves (`所以学习率如果设得太大的话` … `模型的损失就会来回震荡，甚至越来越大。`) with 3 s, then 10 s, of fan noise between them:

| How Qwen heard it | 3 s pause | 10 s pause |
|---|---|---|
| live model | split, full stop after `太大的话。` (and `的` for `得`) | same, plus an empty piece in the pause |
| HTTP model, cut at the pause, first half as context | split | split |
| HTTP model, the whole stretch | **one sentence**: `…太大的话，模型的损失就会…` (`得` right) | **one sentence**, same |

So the model keeps a thought whole when it hears both halves together; only our cut splits it. Layer 2 changed from "check a paragraph when a ≥ 2 s pause ends it" to the minute check that holds back its last sentence (§1.6, §2.5, §4.2, Phase 3). Paragraphs are now about a minute, ending at a sentence end the check confirmed. The check is visible but quiet (§Display). Cost: about 2.1× the audio, cents per hour.

**Paragraphs by content** (same day; the user wants paragraphs that each hold one point, not one per sentence or per minute). Probe `evals/transcript/probe_paragraphs.py` on the noisy run's 22 live sentences, `deepseek-flash`, prompt §5.2, script's true paragraph starts at 1.0 / 25.3 / 46.2 / 77.6 / 85.0 / 108.5 s:
- whole transcript at once: 9 paragraphs; 5 of the 6 true starts exactly, the sixth one sentence late (54.0 s); the extra 3 split long points sensibly (e.g. Adam and warmup apart);
- live, about once a minute with earlier paragraphs frozen: 11 paragraphs; the same 5 starts, the sixth one sentence early (38.6 s), a little finer.
- The student's question and the teacher's answer got their own paragraphs both ways. An earlier prompt without the "avoid one short sentence" rule left `好，我们开始上课。` alone; §5.2 fixed that.
So: group live (cheap, stable), regroup once after Stop (sees the whole talk). Added §1.7, §2.11, §4.7, §5.2, Phase 3B; the data model (§4.2) now keeps confirmed sentences, check windows and paragraph groups apart.
