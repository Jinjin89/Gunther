# Old recordings: heard again and grouped into paragraphs, by themselves — implementation plan

> **Status 2026-10-12: U1 and U2 built and reviewed (see the progress log); not yet tried on a real old recording.** Written by Claude Opus for Sonnet agents, one phase per agent; Opus reviews each phase. Line numbers drift: grep for the names. Companion plan (built after this one): `docs/LIBRARY_CONTENTS_PLAN.md`.

The user's request (2026-10-12), in short: recordings made before the live-transcript rebuild (`docs/LIVE_TRANSCRIPT_PLAN.md`) are in an obsolete format, so upgrade them, simply. Discussed and decided with the user:

1. **Listen again + paragraphs.** The saved audio is heard again by the Recording job's model, the way the minute check does it (context, the last sentence held back), and the text is grouped into paragraphs by topic with the Analysis model. Only text from listening; the model that groups never writes text.
2. **No button, no setting.** Gunther upgrades every old recording **by itself, in the background, newest first, one recording at a time**. A recording being upgraded shows a quiet grey line; then the new text swaps in.
3. **The old text is kept**, never lost: in the source's previous revision, in the upgrade record, and as the `live` text of each window of the structured transcript.
4. Time: about 1–3 minutes per hour of audio (pieces heard in parallel). Cost: a few cents per hour of audio, once.

---

## The design

### What "old" means, with an example

An old recording's source content ends like this (one line per fixed 3.2 s cut, words broken at the cuts):

```
## Transcript

[00:00] Today we look at how neural
[00:03] networks learn we start with a loss func
[00:06] tion and then we use grey
[00:09] didn't decent to train the model
```

A new one has paragraphs separated by blank lines and, in its recording session, a structured transcript (`recording_sessions.transcript_segments_json`, migration 23):

```
## Transcript

[00:00] Today we look at how neural networks learn. We start with a loss function, and then we use gradient descent to train the model.

[01:04] The learning rate decides how big each step is. …
```

A recording is **old** when all of these hold (general rules, nothing about any one recording):
- its `RecordingSession` is `completed`, has `transcript_segments_json IS NULL`, and its audio file exists and is not empty;
- at least one live (not trashed) `Source` references it (`Local recording: rec_…` in the content, the same pattern as `trash.RECORDING_REFERENCE`);
- that source's transcript section (everything after the first `## Full transcript` or `## Transcript` heading, as `tailAfter()` in `apps/desktop/src/items/sourceContent.ts` reads it) is **made by the old live recorder**: it is not empty and **every non-empty line starts with a time stamp** (`[mm:ss]` / `[h:mm:ss]`, optional fraction). A pasted or typed transcript (no stamps) and a recording with no transcript are left alone.

### The pipeline (one recording)

```
saved audio (WebM/Opus, MP4/AAC, MP3, WAV…)
   │ PyAV decode → 16 kHz mono PCM → a listen copy (listen_copy.py, same file format as live)
   ▼
stretches of 10 min, up to 3 heard at once
   │ in each stretch, windows of 120 s, one after another:
   │   hear [confirmed, confirmed+120 s] with the text before as context
   │   keep every sentence but the last; the next window starts where the kept ones end
   ▼
seams: the last sentence of a stretch + the first of the next are heard again together
   ▼
paragraphs: the Analysis model says where paragraphs start (chunks of ≤ 300 sentences)
   ▼
write: recording transcript + structured transcript, source content (new revision), folder sync
```

Why stretches with seams: one chain of windows is sequential (each starts where the last kept sentence ended), so an hour is ~35 requests in a row. Ten-minute stretches run in parallel, and each fixed stretch boundary is repaired by hearing the sentence before it and the sentence after it **in one request**, so the model alone decides whether the words around the boundary are one sentence or two. No pause length decides anything (`LIVE_TRANSCRIPT_PLAN.md` §1.6).

Example: stretch 1 ends at 10:00 inside "…so the loss jumps | around and we lower the rate." Stretch 1's last sentence is `So the loss jumps` (09:55–10:00), stretch 2's first is `around and we lower the rate.` (10:00–10:03). The seam request hears 09:55–10:03 and returns `So the loss jumps around and we lower the rate.`, which replaces both.

### What the user sees

- Nothing to click. In a recording's Transcript tab, while it is waiting or being redone, one grey line above the transcript:
  - `Improving this transcript… 12 of 47 min`
  - `Improving this transcript after 2 other recordings.`
  - `Will improve this transcript once a Recording model is set in Settings → Transcription.`
  - `Waiting until the current recording ends.`
  - on failure: `Couldn't improve this transcript: <provider message> · Retry` (model output or an error, never a stand-in; Retry is the one control, only on failure).
- When it is done, the page shows the new paragraphs (the item reloads). No extra note.
- Search, Ask, the summary card (`digest`) and the library folder use the new text automatically (a new source revision does that).

---

## How to work (for the implementer)

- Read this whole document and your phase before editing. Follow `AGENTS.md`: general behaviour only; nothing for particular recordings, words or languages.
- Match the surrounding code's naming, docstrings and comment density (`realtime.py`, `listen_copy.py`, `paragraphs.py` are the models).
- Tests: backend `cd apps/backend && /tmp/uvbin/uv run pytest tests/<files> -q` and `/tmp/uvbin/uv run ruff check gunther tests`; desktop `cd apps/desktop && npx vitest run <files>` and `npm run typecheck` (not `npx tsc --noEmit`, which checks nothing). No app builds.
- Stop at the end of your phase and add a progress-log entry at the bottom: what changed, test results, deviations and why.

## 1. Decided with the user (don't reopen)

1. Listen again with the Recording job's model **and** group paragraphs with the Analysis model.
2. Automatic, every old recording, background, newest first, one recording at a time. **No button, no setting.** The only control is Retry after a failure.
3. Old text kept (previous source revision + upgrade record + window `live` text).
4. Corrections only from listening; the grouping model returns paragraph-start numbers only (`paragraphs.py`).

## 2. Calls the planner made (the user may still override)

1. **PyAV** (`av`, FFmpeg's Python binding, LGPL wheels) decodes the saved audio in the backend. Checked 2026-10-12 here: `av` 19.0.1 decodes WebM/Opus, MP4/AAC and MP3 to 16 kHz mono with `av.AudioResampler(format="s16", layout="mono", rate=16000)`. macOS arm64 wheel 18 MB. The desktop can't do it in the background (WebAudio decodes whole files into memory: ~700 MB per hour). Bundle with `--collect-all av` in `scripts/build-backend-sidecar.mjs`.
2. **The backend runs it** (a background task started with the app), so it works with no page open, and survives restarts (state in a table).
3. **Recording clock = decoded samples** (pauses were never recorded into the file), so times match the player.
4. **Stretches 10 min, 3 at once; windows 120 s; hold back the last sentence** except in the stretch's last window. A one-sentence answer for a window that is not the stretch's last: ask once more with a 180 s window (the relisten limit); if it is still one sentence, keep it. The confirmed point always moves forward (if the kept sentences end at or before it, move it to the window end). A final stretch shorter than 2 min joins the one before.
5. **Context** for each window: the last ≤ 400 characters of text confirmed in this chain; at a stretch's start, the old transcript's text from the 60 s before it (free, and its terms help). Terms: the source's `Terms:` line from its saved summary, if any (`parseRecordingContent` reads the same line).
6. **Providers without word times** (SenseVoice, OpenAI-style): `relisten_audio` returns text only. Windows end at pauses found by the Phase 5 `vad.Segmenter` (pieces grouped into windows of ≥ 60 s, ending at a piece end); each window is one sentence and one paragraph. Without the VAD model: fixed 60 s windows (the existing fallback). No seams for them.
7. **Paragraphs:** with an Analysis model, regroup in chunks of ≤ 300 sentences (port `nextRegroupChunk` / `applyRegroup` / `cleanStarts` from `liveTranscript.ts`; starting with one paragraph per window). Without one: one paragraph per window, `grouping.state = "off"` (what the recorder does).
8. **Failures:** a window or grouping request is tried 3 times (0, 5, 20 s waits). Then the upgrade fails, **nothing is written**, and the record keeps the error. Heard sentences are saved before grouping (`sentences_json`), so a later attempt only regroups. Automatic re-tries of a failed recording: after 10 min, then after 1 h, then it waits for Retry. One failing recording never blocks the queue.
9. **Live recordings come first:** while any recording session is `capturing` and was updated in the last 2 minutes, the upgrader waits.
10. **The listen copy** is written under the recording's own id (no live copy exists for old recordings) and removed when the upgrade finishes or fails.
11. **One record per recording**, table `transcript_upgrades` (migration 24). The status endpoint computes "waiting" reasons live (no model, a recording in progress, place in line).
12. **Several sources referencing one recording:** each whose transcript section is in the old format gets the new section.

## 3. What exists today (read before starting)

- `apps/backend/gunther/listen_copy.py` — 16 kHz PCM copy per recording: `copy_paths`, `ListenCopy(directory, recording_id).write(start_byte, pcm)`/`close()`, `read_range`, `covered`, `missing_in`, `remove_copy`, `sweep`, `recording_state`.
- `apps/backend/gunther/realtime.py` — `relisten_audio(choice, pcm, *, audio_start_ms, start_ms, end_ms, terms, before, after)` → `{"text", "sentences": [{start_ms, end_ms, text}]}` (sentences only for Qwen), `ListenError`, `RELISTEN_PAD_MS`, `_join_text` (not identical to the desktop's `joinSentences`: port that one), `LIVE_RATE`.
- `apps/backend/gunther/api.py` — `POST /recordings/{id}/relisten` (how a range is read and heard; `MAX_RELISTEN_MS`), `POST /recordings/{id}/paragraphs` (how the Analysis model is reached: `request.app.state.models.for_role("analysis")` → `(model, effort)`; `paragraphs.group_paragraphs(gateway, model, effort, sentences, before)` is sync, raises `ParagraphsError`).
- `apps/backend/gunther/speech_registry.py` — `resolve(registry, "recording")` → `(Choice | None, problem)`; the registry lives at `app.state.speech_registry` and is replaced live when settings change (read it each time, don't cache).
- `apps/backend/gunther/vad.py` — `new_detector()`, `Segmenter`, `FixedBlocks` (Phase 5).
- `apps/backend/gunther/models.py` — `RecordingSession` (`status`, `file_name`, `content_type`, `byte_size`, `transcript`, `transcript_segments_json`, `duration_seconds`, `created_at`, `updated_at`), `Source`, `SourceRevision`, `SourceIndexHead`.
- `apps/backend/gunther/recording_service.py` — where the audio file lives (`recordings_dir / file_name`) and the status values (`capturing`, `completed`, `failed`).
- `apps/backend/gunther/source_identity.py` — `source_fingerprint(title, kind, content)` is `Source.content_hash`.
- `apps/backend/gunther/knowledge_index.py` — `KnowledgeIndex.add_source(session, source)` → `publish()`: a new `SourceRevision` (old one kept, old citations still work), head moved, `embed`/`summarize`/`digest` jobs queued. The index is `app.state.knowledge_service.index`.
- `apps/backend/gunther/library_folders.py` — `app.state.library_folders` (may be `None`), `request_sync()`.
- `apps/backend/gunther/main.py` — `lifespan()`: `sweep_listen_copies`, the processing worker, `purge_trash` task, folder start/stop; `background` flag. Start the upgrader the same way.
- `apps/backend/gunther/migrations.py` — last is `Migration(23, "recording_transcript_segments", …)`; tests in `tests/test_migrations.py`.
- Desktop: `apps/desktop/src/services/liveTranscript.ts` (the `TranscriptState` shape, `joinSentences`, `clockLabel`, `renderTranscript`, `nextRegroupChunk`, `applyRegroup`), `apps/desktop/src/items/sourceContent.ts` (`parseRecordingContent`, `parseTranscript`, `tailAfter`), `apps/desktop/src/items/views/RecordingView.tsx` (`RecordingBody`, `Transcript`), `apps/desktop/src/items/SourceItem.tsx` (how a source page loads and reloads).

## 4. Contracts

### 4.1 Table `transcript_upgrades` (migration 24, `transcript_upgrades`)

| column | type | |
|---|---|---|
| `recording_id` | String(32) PK, FK `recording_sessions.id` ON DELETE CASCADE | |
| `state` | String(16) | `running`, `done`, `failed` (no row = not started) |
| `attempts` | Integer | failed attempts since the last success/Retry |
| `error` | Text, null | provider's message |
| `heard_ms`, `total_ms` | Integer | progress |
| `sentences_json` | Text, null | heard sentences `[{start_ms,end_ms,text,window}]` + windows, saved before grouping |
| `old_transcript` | Text | the transcript section as it was |
| `model` | String(200), null | the Recording model used |
| `retry_at` | DateTime, null | when a failed one may be tried again automatically |
| `started_at`, `finished_at`, `updated_at` | DateTime | |

A row left `running` by a crash is treated as not started (the copy is rebuilt).

### 4.2 Status — `GET /api/recordings/{id}/upgrade`

```json
{ "state": "none" | "waiting" | "running" | "done" | "failed",
  "reason": "model" | "service" | "recording" | "queue" | null,
  "ahead": 2,
  "heardMs": 720000, "totalMs": 2820000,
  "error": "Qwen: 401 Invalid API key" | null }
```

`none`: not an old recording (or already new). `waiting`: old, not started; `reason` = no usable Recording model / a live recording in progress / others ahead in line (`ahead` = how many). `failed` shows `error`; it also counts as waiting again once `retry_at` passes (report `failed` until a new attempt starts).

### 4.3 Retry — `POST /api/recordings/{id}/upgrade/retry`

Resets a `failed` row to be tried next (attempts 0, `retry_at` now, moved to the front of the line) and wakes the upgrader. 404 for an unknown id, 409 if the recording isn't failed.

### 4.4 What is written (one transaction, after everything was heard and grouped)

- `TranscriptState` JSON (version 1) in `recording_sessions.transcript_segments_json`: `sentences` (`id` `s-…`, `windowId`, no `changed`), `windows` (one per window heard: `state: "checked"`, `pass: "minute"`, `model`, `at`; `live` = the old lines whose stamp falls in the window, as `{id, startMs, endMs, text}`, end = next stamp), `paragraphs` (all `frozen: true`), `live: []`, `draft: null`, `confirmedUntilMs` = the last sentence's end, `checking: null`, `grouping: {state: "idle"}` (or `"off"` without an Analysis model), `gaps: []`. `isTranscriptState()` in the desktop must accept it.
- `recording_sessions.transcript` = the rendered text: `[clock] paragraph` blocks joined by a blank line, `clock` per `clockLabel()` and paragraph text per `joinSentences()` (ported exactly; test them against the desktop's own examples in `liveTranscript.test.ts`).
- Each old-format source: content = everything before the transcript heading, the heading line itself unchanged, a blank line, the rendered text. `content_hash` = `source_fingerprint(title, kind, content)`; then `index.add_source(session, source)`. Assertions, fragments and the old revision stay.
- The upgrade row: `done`, `finished_at`, `model`. Then `library_folders.request_sync()` if present, and the listen copy is removed.

## Phase U1: the upgrader (backend)

### Tasks

1. `pyproject.toml`: add `"av>=14,<20"`; `/tmp/uvbin/uv lock` (and `uv sync`). `scripts/build-backend-sidecar.mjs`: `"--collect-all", "av"` beside the others.
2. Migration 24 `transcript_upgrades` (§4.1) + model class `TranscriptUpgrade` in `models.py`. Extend `tests/test_migrations.py` like its neighbours.
3. New `gunther/audio_decode.py`: `decode_to_copy(path, directory, recording_id) -> int` (total ms): streams frames through the resampler into `ListenCopy` in ~1 s blocks, never the whole file in memory; raises `AudioUnreadable` with a plain message ("The audio file couldn't be read: …") on `av.error.FFmpegError`/no audio stream.
4. New `gunther/transcript_upgrade.py`, pure parts first (no DB, no network), each with docstrings:
   - `is_old_transcript(text) -> bool`, `transcript_section(content) -> (head, heading_line, body) | None`, `old_lines(body) -> [(start_ms, end_ms, text)]`.
   - `join_sentences(texts)`, `clock_label(ms)` — exact ports.
   - `hear_stretch(hear, start_ms, end_ms, context, terms) -> list[Heard]` — the window chain (§2.4–2.5) over an injected `hear(start_ms, end_ms, before) -> {"text", "sentences"}` coroutine.
   - `stretches(total_ms)` and `mend_seams(hear, stretches_heard) -> list[Heard]` (§2.4).
   - `text_windows(pieces, min_ms=60_000)` for providers without word times (§2.6).
   - `regroup(sentences, windows, group) -> paragraphs` over an injected sync `group(sentences, before) -> starts` (§2.7).
   - `transcript_state(...)` and `render(...)` (§4.4).
5. The runner, same module: `class TranscriptUpgrader` with `run()` (async loop, started in `lifespan()` when `background`), `wake()`, `status(recording_id)`, `retry(recording_id)`; `candidates(session)` (§"What old means", newest `created_at` first, skipping `done`, `failed` before `retry_at`, and running ones); `upgrade_one(recording_id)` (decode in `asyncio.to_thread`, `asyncio.Semaphore(3)` across stretches, progress saved to `heard_ms` about every window, `sentences_json` saved before grouping, write per §4.4 in one `session_scope`). Re-read the speech registry and Analysis model each recording. Wait 30 s after start-up before the first; poll every 60 s when idle or waiting; `wake()` cuts the wait short (call it from the speech-settings save path too, if that is a one-line hook).
6. `api.py`: `GET /recordings/{id}/upgrade`, `POST /recordings/{id}/upgrade/retry` (§4.2–4.3), same id validation as `relisten`.
7. Trash: a trashed source is not a candidate; deleting a recording forever cascades the row. Nothing else changes in `trash.py`.

### Tests (`tests/test_transcript_upgrade.py`, `tests/test_audio_decode.py`)

- Old-format detection: stamped lines → old; blank-line paragraphs with a structured transcript → not a candidate; pasted text without stamps → not old; empty → not old; stamps with `[h:mm:ss]` and fractions.
- Transcript section split keeps everything before it byte for byte, including `## Summary`, `Terms:` and `## Marked moments`.
- Decode: PyAV-encoded WebM/Opus, M4A/AAC and WAV fixtures made in the test (sine + silence, seconds long) → copy length matches the duration within 50 ms; an unreadable file → `AudioUnreadable`.
- Window chain with a fake `hear`: the last sentence held back and re-heard; the stretch's last window keeps everything; one-sentence answer → 180 s re-ask → kept; no forward progress → moves to the window end; empty answer (silence) moves on; context is the previous confirmed text.
- Seams replace the two edge sentences with the seam answer; an empty seam answer keeps them; a seam longer than 180 s is left alone.
- Text-only provider: windows end at piece ends, ≥ 60 s; one paragraph each.
- Regroup: chunks of ≤ 300 sentences; starts applied; no Analysis model → one paragraph per window, `grouping.state == "off"`.
- `join_sentences`/`clock_label` agree with the desktop's examples (CJK, full-width punctuation, Latin, > 1 h).
- End to end with a fake provider (monkeypatch `relisten_audio`) and a fake gateway, through the app (`TestClient`, `GUNTHER_DESKTOP_SIDECAR=1`, as other recording tests do): an old recording + its source → after `upgrade_one`, the source content has the new section and the old head, a new revision is head, the old revision's blocks exist, the session has a `TranscriptState` and the rendered transcript, the row is `done` with `old_transcript`; a second run does nothing.
- Runner: newest first; waits while a session is `capturing` (fresh `updated_at`), not when it is stale; waits with no Recording model (`status` says `reason: "model"`); a provider failure → `failed`, error kept, nothing written, `retry_at` set; `retry` endpoint → tried next; a trashed source is skipped.

### Done when

The tests pass, ruff is clean, the existing recording/listen-again/migration tests still pass, and the progress log has an entry.

## Phase U2: the grey line (desktop)

### Tasks

1. `packages/contracts/src/index.ts`: `TranscriptUpgradeStatus` (§4.2). `apps/desktop/src/api.ts`: `transcriptUpgrade(recordingId)`, `retryTranscriptUpgrade(recordingId)`.
2. `RecordingView.tsx`: `RecordingBody` gets the recording id (`parsed.recordingId`) and an `onUpgraded` callback. A small hook `useTranscriptUpgrade(recordingId)` polls the status: every 5 s while `running`, every 30 s while `waiting`, never for `none`/`done`; stops on unmount. The line sits above the transcript bar in the Transcript tab and above the tabs when the Overview tab is showing, in small grey text (wording in "What the user sees"); minutes are `Math.round(ms / 60000)`. `failed` shows the error and a text button "Retry". When the state goes from `running` to `done`, call `onUpgraded()`.
3. `SourceItem.tsx` (or wherever `RecordingBody` is used): `onUpgraded` reloads the source (the same path a reprocess uses), and dispatch `gunther:sources-updated`.
4. CSS in `design/item.css`, existing tokens only, no colour (the error line in the normal text colour, not red).

### Tests

`RecordingView` (or a new `TranscriptUpgradeLine.test.tsx`): each state's text; Retry calls the API; `none` renders nothing and stops polling; `running` → `done` calls `onUpgraded` once. `npm run typecheck`.

### Done when

Tests and typecheck pass; progress-log entry.

## Pitfalls

- Never write anything until every window was heard and grouping finished (or there is no Analysis model). A half-upgraded transcript is worse than the old one.
- WebM files from `MediaRecorder` can have no duration and odd timestamps: count decoded samples, don't trust `pts` or the container duration.
- Don't hold a SQLite write transaction while waiting on the network. Read what you need, close the session, hear, then write in one short transaction.
- `relisten_audio` drops sentences lying only in the padding; ask for the padded range the way the `relisten` endpoint does (`RELISTEN_PAD_MS`), or the first/last words get cut.
- The speech registry is replaced when the user saves settings; resolve it per recording.
- Keep `realtime.py`'s live paths untouched.
- Old recordings with long silences: a window may come back empty (`ASR_RESPONSE_HAVE_NO_WORDS` is empty text, not an error) — move on.
- Don't let the upgrader compete with a live recording for the upload link (§2.9).

## Review checklist (Opus)

- [ ] Only old-format transcripts are touched; pasted/typed/empty ones and new recordings never.
- [ ] No pause length decides a sentence or paragraph; seams are heard as one.
- [ ] Nothing is written on any failure; the error is shown; Retry works; the queue moves on.
- [ ] Old text kept in three places (§4.4); old citations still resolve.
- [ ] Times match the audio player.
- [ ] The listen copy is removed afterwards.
- [ ] `av` bundled in the sidecar build script.
- [ ] No button or setting added except Retry on failure.

## Progress log

(Add entries below: date, phase, who, what changed, tests, deviations.)

### Phase U2 — 2026-10-12 (Sonnet)

Changed: `TranscriptUpgradeStatus` in `packages/contracts/src/index.ts`; `knowledgeApi.transcriptUpgrade` / `retryTranscriptUpgrade` in `apps/desktop/src/api.ts` (beside `relistenRecording`); new `apps/desktop/src/items/views/TranscriptUpgradeLine.tsx` (hook `useTranscriptUpgrade(recordingId, onUpgraded)` + `TranscriptUpgradeLine`); `RecordingView.tsx` (`RecordingBody` takes `onUpgraded`, line above the tabs on Overview, between tabs and transcript bar on Transcript); `SourceItem.tsx` (`onUpgraded` → `refresh()` then `gunther:sources-updated`); `.gx-upgrade-line` rules in `design/item.css` (faint text, failure in `--gx-ink-2`, Retry as an underlined text button). Polling: 5 s running, 30 s waiting, none otherwise; stops on unmount; `running` → `done` calls `onUpgraded` once. A failed status request is ignored (no line).
Tests: new `TranscriptUpgradeLine.test.tsx` (9 tests: each state's wording, Retry, none stops polling, running→done once, 30 s waiting poll and unmount); `ItemPage.test.tsx` mock gained the two calls. `npx vitest run src/items src/api`: 7 files, 57 tests pass. `npm run typecheck` clean.
Deviations: the hook lives in its own file instead of RecordingView.tsx. After Retry the hook looks again immediately (rather than trusting the retry response). Without a recording id nothing is polled. Not run against the real backend (U1 endpoints unbuilt at the time).

### Phase U1 — 2026-10-12 (Sonnet)

Backend upgrader built. Nothing under `apps/desktop` or `packages/contracts` was touched by this phase.

**What changed**
- `av>=14,<20` added (`pyproject.toml`, `uv.lock`; 19.0.1 installed); `"--collect-all", "av"` in `scripts/build-backend-sidecar.mjs`.
- Migration 24 `transcript_upgrades` + model `TranscriptUpgrade` (`migrations.py`, `models.py`); `test_migrations.py` extended (history list, version 24, a v23 database gains the table without losing a recording).
- New `gunther/audio_decode.py`: `decode_to_copy(path, directory, recording_id)` (PyAV, 16 kHz mono through the resampler, ~1 s blocks into `ListenCopy`, length = decoded samples, replaces an earlier copy, removes a half-written copy on failure), `AudioUnreadable`.
- New `gunther/transcript_upgrade.py`: the pure parts (`transcript_section`, `is_old_transcript`, `old_lines`, `recording_terms`, `join_sentences`, `clock_label`, `stretches`, `hear_stretch`, `mend_seams`, `text_windows`, `hear_text_run`, `paragraphs_by_window`, `regroup`, `transcript_state`, `render`, `speech_pieces`) and the runner `TranscriptUpgrader` (`run`, `step`, `wake`, `status`, `retry`, `old_recordings`, `candidates`, `upgrade_one`).
- `api.py`: `GET /recordings/{id}/upgrade`, `POST /recordings/{id}/upgrade/retry` (404 unknown/bad id, 409 not failed; Retry answers with the new status).
- `main.py`: the upgrader is created with the app (`app.state.transcript_upgrader`), started in `lifespan()` when `background`, cancelled on shutdown; `_connect_models()` (runs at start-up and on every settings save) calls `wake()`, which covers the speech-settings save path.
- Tests: new `tests/test_transcript_upgrade.py` (35), `tests/test_audio_decode.py` (6), `test_migrations.py` +1 (25 total there).

**Tests:** new files 41 passed; with `test_migrations`, `test_listen_again`, `test_recording_sessions`, `test_paragraphs`, `test_trash` together 100 passed; whole backend suite 717 passed, 1 skipped; `ruff check gunther tests` clean.

**Deviations (and why)**
- `hear_stretch(hear, start_ms, end_ms, context, progress)` has no `terms` argument and returns a `Stretch` (`sentences` + `windows`), and `mend_seams` returns one merged `Stretch`: the terms live in the `hear` closure the runner builds, and the windows have to travel with the sentences to become `windows` in the structured transcript. `regroup(sentences, group)` has no `windows` argument: each sentence carries its window id, which is all the starting paragraphs need.
- A window that would end within 30 s of the stretch's end runs to the end (at most 150 s, under the 180 s limit), so no tiny last request.
- `text_windows` also closes a window rather than let it span more than 150 s, so a long silence between two pieces never sits inside one request. The first 30 s start-up wait is a plain sleep, not cut short by `wake()` (settings are saved during start-up; the plan says wait 30 s).
- The status endpoint reports `waiting` (not `failed`) for a failed recording that the user just retried, until its attempt starts; "front of the line" after Retry is kept in memory (lost on restart, where the retry_at of "now" still makes it eligible).
- `old_lines(body, total_ms=None)` has an optional second argument (the last line ends at the recording's length). `is_old_transcript` requires the bracketed stamp form; the transcript heading match ignores trailing spaces only (not the newline after it) so the heading line is kept byte for byte.
- The "no forward progress" rule (kept sentences end at or before the window start) is in `hear_stretch` as a guard, but it can't be reached through the same `end_ms > start_ms` filter the live check uses, so there is no test that triggers it.
- `sentences_json` is cleared when an upgrade finishes (the plan left it open); `old_transcript` stays.
- Unknown recording ids give state `none` from the status endpoint (not 404), so a stale page doesn't log errors.

**Notes / risks**
- The default speech registry already has a Recording model (SenseVoice, local), so "waiting for a model" only shows when the Recording job was cleared. SenseVoice gives no word times, so it takes the text-only path (pause-cut windows, one paragraph per window before regrouping). If SenseVoice isn't running, the first attempt fails with its "could not be reached" message and retries by itself after 10 min, 1 h, then waits for Retry.
- Not tried against a real Qwen/Analysis model; the tests use a made-up listener that cuts a timeline the way a real window would.
- Another agent may add migration 25; the upgrade tables are 24 only.

### Opus review of U1 + U2 — 2026-10-12

Checked against the plan and the review checklist. Two changes made:

- **When no model answers at all** (offline, a wrong key, the service down), the old code failed every old recording in turn, and after two automatic retries each waited for Retry: a night offline would have left a Retry on every recording. Now a failure where neither model answered once in that attempt is not counted against the recording; the whole queue waits 10 min, then 1 h, then every 6 h (`PAUSE_AFTER`), the recording is due again when the wait ends, and a saved setting or a Retry ends the wait at once (`wake()` → `_resume`). The status of the others says `reason: "service"` ("Will improve this transcript when the transcription service answers again."). A failure after some answers (something about this recording) keeps the 10 min / 1 h / then Retry schedule. Test: `test_when_no_model_answers_nothing_else_is_tried_until_the_wait_ends_or_settings_change`; the automatic-retry test now fails a later window, so it is a recording-level failure.
- **`old_recordings()` read the full text of every recording source** on every status request (every 5–30 s per open recording) and every minute in the runner. Now it starts from recordings without a structured transcript (none, once all are upgraded), matches sources on the `Local recording:` reference with `instr`/`substr` in SQL, and reads the text only of those.
- Desktop: the status line keeps looking (every 30 s) after a failure, since failures are tried again by themselves, and reloads the page when a recording goes from any not-done state to done (a short one can go from waiting to done between two looks).

Tests: backend `test_transcript_upgrade.py` + `test_audio_decode.py` 42 passed, ruff clean; desktop `TranscriptUpgradeLine.test.tsx` 12 passed, `npm run typecheck` clean.


### Real check — 2026-10-12 (Opus, the user's Qwen key, DeepSeek Flash as Analysis model, this US Linux box)

An old-format recording made from the sample lecture (`evals/results/transcript/sample/noisy.wav` six times over, 13.2 min, encoded as WebM/Opus like `MediaRecorder`'s, with `[mm:ss] old piece N` lines every 3.2 s), through the real app and `upgrade_one`:

- **First run failed** after 9 of 13 minutes: `Qwen could not be reached ().` The live check's 60 s read timeout (`RELISTEN_TIMEOUT`) is too short when two or three 2–2.5-minute windows are in flight from here; one 2-minute window alone took 23 s as WAV and 29 s as 32 kbps MP3 (so compressing the upload doesn't help: the time is Qwen's, not the upload's). Fixed: `relisten_audio(..., timeout=)`, and the upgrader waits up to 300 s per request (`HEAR_TIMEOUT`); the live check keeps its minute. The empty brackets were `f"{error or …}"` on an exception whose text is empty: now `str(error) or type(error).__name__` ("ReadTimeout"), with a test.
- **Second run: written in 682 s** (~52 s per minute of audio from this box; the user's link is likely faster). 133 sentences, 54 paragraphs by content (each of the script's points its own paragraph, the student's question alone), 8 windows including one seam (595.4–603.1 s) that joined a sentence cut at the 10-minute mark. Text accurate (Chinese with English terms). The source has a new revision (the old one kept), `old_transcript` kept (5 345 characters), status `done`.
