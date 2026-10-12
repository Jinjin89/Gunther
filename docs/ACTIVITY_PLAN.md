# Activity in the top bar — plan

Agreed with the user on 2026-10-12. They want to see what Gunther is doing in the background
(a recording, Transcribe again, a report or slides being built, an answer being written) from
any page, in the top bar, and an API that later features (big imports, read-aloud, folder sync)
plug into the same way. **Several jobs can run at the same time** and the design must hold all
of them: several recordings waiting to be transcribed again, one build per library in several
libraries, one answer per conversation in several conversations, and a recording on top.

Two phases, built in parallel against the contract in §2: **B** (backend) and **D** (desktop +
Tauri shell). Each is one Sonnet agent; Opus reviews both against §7.

---

## 1. What the user sees

The top bar's status spot (today `● Saved locally`, `gx-save-state` in
`apps/desktop/src/components/AtlasChrome.tsx`) becomes a **status pill**, a button. Clicking it
opens a small **Activity panel** under it.

### Pill states (exact words)

| Situation | Pill shows | Look |
| --- | --- | --- |
| Nothing running, service online | `Saved locally` | green dot exactly as today |
| Service offline, no recording | `Reconnecting…` | warning dot exactly as today |
| Recording | `12:34 Recording` | red dot (`--gx-danger`), slow breathe; digits `tabular-nums` |
| Recording paused | `12:34 Paused` | pause glyph, grey |
| Recording stopped, waiting to be saved in Capture | `Recording to save` | grey dot |
| Recording starting / importing audio | `Starting recording…` / `Importing audio…` | small spinner ring |
| One job running | `<title> · 42%` (or `<title>` when progress is unknown) | progress ring (determinate) or turning arc (unknown) |
| Several jobs running (no recording) | `3 running` | ring with the mean of the known progresses, else turning arc |
| Recording + jobs | `12:34 Recording` then a small count chip `+2` | |
| A job just finished (≤ 6 s ago) and you are not on its page | `✓ <done title>` e.g. `✓ Slides ready` | ring fills, then a check draws (brand green) |
| A job failed and the panel has not been opened since | `! <failed title>` e.g. `! Couldn't build slides` | warning colour; stays until the panel is opened |

Priority when several apply: recording › unseen failure › just-finished flash › running jobs ›
offline › saved locally. The `+N` chip counts everything running that the pill's main text does
not show. Long titles truncate with an ellipsis at `max-width: 260px`.

### Activity panel

```
┌──────────────────────────────────────────────────┐
│ Now                                              │
│ ● Recording: Cell biology lecture        12:34   │
│   1,240 words                         ❚❚     ■   │
│                                                  │
│ ◔ Transcribing again: Week 3 lecture       42%   │
│   ━━━━━━━━━━━━━━────────────  5:02 of 12:00  ■   │
│                                                  │
│ ◔ Building slides: Single-cell library           │
│   Writing section 3 of 7                     ■   │
│ ──────────────────────────────────────────────── │
│ Earlier                                  Clear   │
│ ✓ Report ready: Protein folding       2 min ago  │
│ ! Couldn't write the answer: "Why does…"     ×   │
│   The model timed out.                           │
└──────────────────────────────────────────────────┘
```

- 380 px wide, anchored under the pill, right edges aligned, portalled to `document.body`
  (`position: fixed` inside `.page-enter` breaks; see the fixed-layers note in DESIGN_SYSTEM /
  `contents/MovePicker.tsx` for the portal + Esc-in-capture-phase pattern).
- **Now**: the recording first, then running jobs, then waiting ones, newest started first in each. **Earlier**:
  done/failed, newest ended first, with `Clear` (dismisses all finished) and a `×` per row.
- Row = icon column (16 px) · line 1 `<title>: <about>` (about truncates) · line 2 `detail`
  (grey) · a 2 px progress bar under line 2 when `progress` is a number · right column: `42%`,
  or the recording clock, or `2 min ago` for Earlier rows · action icons: Pause/Resume (recording
  only), Stop ■ (when `canStop`, or the recording), `×` dismiss (Earlier rows).
- Clicking a row (not an action) **opens its page** and closes the panel (§5.6). The recording's
  row shows the Capture window (`show` control).
- Empty state (nothing now, nothing earlier): one grey line, "Nothing running. Recordings,
  transcripts, reports, slides and answers show here while they work."
- Esc and a click outside close it. Opening the panel marks every failure seen.
- Stop asks no confirmation for jobs, but **the recording's Stop is `finish`** (it moves the
  recording to review in Capture; nothing is lost), so no confirmation there either.

### Motion and polish (design system: "nothing loops unless work is happening")

- Pill appears/changes with a 160 ms fade + 2 px rise (`--gx-ease`); text swaps crossfade, keyed
  on what is shown.
- Ring: 14 px SVG, 2 px stroke, `stroke-dashoffset` transitions 400 ms; unknown progress = a 90°
  arc turning once per 1.1 s. On done the ring completes, then a check path draws in 280 ms.
- Recording dot breathes (opacity .55↔1, 2 s). Every animation is off under
  `prefers-reduced-motion` (the token file already has the reduced-motion rule; extend it).
- Panel pops from its anchor: opacity + `translateY(-4px)` → 0, 160 ms.
- Narrow window (the existing `@media` that hides `.gx-save-state` today): the pill keeps only
  its icon and the clock/percent; text hides.
- Colour only for status: red = microphone on, green = done, warning = failed. Everything else
  ink and greys. Light and dark themes both from tokens.
- Accessibility: the pill is a `button` with `aria-haspopup="dialog"` and `aria-expanded`; a
  visually hidden `aria-live="polite"` line announces *changes of state* ("Slides ready",
  "Transcribing again failed") — never the ticking clock or percent.

---

## 2. The contract (locked — both phases build against exactly this)

### HTTP (all under the API prefix, same auth as every other route)

| Method | Path | Answer |
| --- | --- | --- |
| GET | `/activity?after=<version>` | `ActivityList`. Without `after`, or when `after` ≠ the current version: at once. When `after` = current version: waits until something changes or **25 s** pass, then answers with the current list (same version if nothing changed). |
| POST | `/activity/{id}/stop` | 204. 404 when no running/waiting entry has this id. 409 `{"detail": "This can't be stopped."}` when it has no stop. |
| POST | `/activity/{id}/dismiss` | 204. Removes a finished entry; 404 if none; 409 if it is still running. |
| POST | `/activity/clear` | 204. Removes every finished entry. |

`{id}` contains a `:`; the client sends it through `encodeURIComponent`.

### JSON (camelCase, like every other response)

```ts
export type ActivityKind = "transcribe" | "output" | "answer";
export type ActivityState = "waiting" | "running" | "done" | "failed";

export type ActivityOpen =
  | { view: "item"; itemType: "source"; id: string }
  | { view: "library"; libraryId: string; tab: "outputs" | "ask"; sessionId?: string | null }
  | { view: "home"; sessionId: string };

export interface ActivityItem {
  id: string;              // "transcribe:<recording id>" | "output:<library id>" | "answer:<session id>"
  kind: ActivityKind;
  title: string;           // the words for its state: "Building slides" → "Slides ready" / "Couldn't build slides"
  about: string;           // what it is about: the recording's title, the library's title, the question
  state: ActivityState;
  progress: number | null; // 0..1, null when unknown
  detail: string;          // one line: "5:02 of 12:00 heard", "Writing section 3 of 7", "Searching the library"
  error: string | null;    // plain words, only when failed
  startedAt: string;       // ISO 8601 UTC
  endedAt: string | null;
  open: ActivityOpen | null;
  canStop: boolean;        // true only while waiting/running and a stop exists
}

export interface ActivityList { version: number; items: ActivityItem[] }
```

A **stopped** job is removed from the list at once (the person stopped it; it needs no record).
Finished entries (done/failed) stay until dismissed or **60 minutes** after `endedAt`, and at most
the **20** newest finished are kept. Starting a job whose id is already in the list replaces that
entry. The list lives in memory only: quitting empties it.

### Words (titles) — backend owns them

| Kind | waiting | running | done | failed |
| --- | --- | --- | --- | --- |
| transcribe | Waiting to transcribe again | Transcribing again | Transcript updated | Transcribing again failed |
| output, build slides | — | Building slides | Slides ready | Couldn't build slides |
| output, build report | — | Writing a report | Report ready | Couldn't write the report |
| output, revise slides | — | Revising slides | Slides revised | Couldn't revise the slides |
| output, revise report | — | Revising the report | Report revised | Couldn't revise the report |
| answer | — | Writing an answer | Answer ready | Couldn't write the answer |

`about`: transcribe → the recording's title; output → the library's title; answer → the
question, first 80 characters with `…` when cut.

`detail`:
- transcribe waiting → `Waiting for another recording`; running → `M:SS of M:SS heard` from
  `heard_ms`/`total_ms` (use `retranscribe.clock_label` if it formats like that, else a local
  `m:ss` / `h:mm:ss` helper) — before decoding finishes: `Reading the audio`; after all heard,
  while the Analysis model groups paragraphs: `Finding paragraphs`.
- output → before the `outline` event: `Planning`; then from `section` events
  `Researching section i of n` / `Writing section i of n` / `Checking section i of n` (i is
  index + 1); `progress` = sections done / n.
- answer → the latest running `step` event's `label` when it has one, else `Thinking`; once the
  first `text` event arrives, `Writing`. `progress` stays null.
- failed → `detail` empty, `error` = the same message the page shows today.

---

## 3. Phase B — backend (`apps/backend`)

### B1. `gunther/activity.py` (new, small: aim ≤ 200 lines)

```python
class Activity:
    """What Gunther is working on in the background, for the top bar."""
    def track(self, id: str, kind: str, title: str, about: str, *, open: dict | None,
              stop: Callable[[], None] | None, state: str = "running", detail: str = "") -> Tracked
    def snapshot(self) -> dict            # {"version": int, "items": [...]} — prunes old finished
    async def wait_for_change(self, after: int, timeout: float) -> dict
    def stop(self, id: str) -> bool | None  # None: no running entry; False: no stop; True: asked
    def dismiss(self, id: str) -> bool | None  # None: none; False: still running; True: gone
    def clear(self) -> None

class Tracked:
    def update(self, *, state=None, title=None, detail=None, progress=...) -> None
    def done(self, title: str, detail: str = "") -> None
    def failed(self, title: str, error: str) -> None
    def stopped(self) -> None             # removes the entry
```

- Thread-safe: answers and builds run on threads (`answer_runs.py`), Transcribe again on the
  event loop. One `threading.Lock` guards the entries and an integer `version` that bumps on
  every change.
- `wait_for_change` waits without polling: register an `asyncio.Event` (with its loop) under the
  lock; every bump calls `loop.call_soon_threadsafe(event.set)` for each waiter — the same
  pattern as `AnswerRun._watchers` in `answer_runs.py`. Always unregister in `finally`.
- `Tracked` is also a context manager: leaving it by an exception while still waiting/running
  marks it failed with `"Something went wrong."` so no spinner can stay forever. Calls on a
  `Tracked` whose entry was replaced by a newer job with the same id do nothing (keep an
  instance token per entry).
- `update` with no real change does not bump the version (progress rounded to 0.01 before the
  comparison), so a chatty job doesn't wake the desktop for nothing.
- `startedAt`/`endedAt`: ISO strings in UTC (`datetime.now(UTC).isoformat(timespec="seconds")`).
- Prune in `snapshot`: finished older than 60 min, and all but the 20 newest finished.
- `stop` calls the registered callable **outside** the lock.

### B2. `gunther/activity_api.py` (new) + `main.py`

- Router with the four routes of §2. `GET /activity` takes `after: int | None = None`; when
  `after` equals the current version it `await`s `wait_for_change(after, 25.0)`. Response models
  in the file (or `schemas.py` style `ApiModel` so the aliases are camelCase).
- `main.py`: `application.state.activity = Activity()` **before** the Retranscriber is made;
  include the router next to the others (`include_router(activity_router, prefix=...)`).

### B3. Transcribe again (`gunther/retranscribe.py`)

- `Retranscriber.__init__`: read `self.state.activity` lazily (`getattr(self.state, "activity",
  None)`) so the unit tests that build a Retranscriber by hand still work; no activity → nothing
  is reported.
- `start()`: after `_plan` succeeds, `track("transcribe:<id>", "transcribe", "Waiting to
  transcribe again", <recording title>, state="waiting", detail="Waiting for another recording",
  open={"view": "item", "itemType": "source", "id": <first source id>} or None,
  stop=lambda: self.stop(recording_id))`. `_plan` must also return the recording's title and the
  first source id (it already reads both).
- `_run`: on taking the lock → `update(state="running", title="Transcribing again",
  detail="Reading the audio")`; in `_listen` after decoding and in the `reporter` → progress =
  heard/total and detail `"M:SS of M:SS heard"`; before `_paragraphs` → `detail="Finding
  paragraphs"`; success → `done("Transcript updated")`; the two `except` branches →
  `failed("Transcribing again failed", job.error)`.
- **Stop (new)**: `Retranscriber.stop(recording_id) -> bool`: cancels that job's task (keep a
  `recording_id → task` map). `_run` catches `asyncio.CancelledError` **only for a stop**: set
  `job.state = "stopped"`, call `tracked.stopped()`, run the existing `finally`, and do not
  re-raise when the cancel came from `stop()` (track that with a set of ids being stopped);
  `close()` at shutdown still cancels everything and re-raises as today. A waiting job (still
  queued on `_one_at_a_time`) stops the same way. The `Job.state` doc and
  `TranscribeAgainStatus` gain `"stopped"`; after a stop the button is offered again (desktop
  treats `stopped` like `idle`; see D9).

### B4. Builds and revisions (`gunther/api.py`, `_output_run` and its two callers)

- `_output_run` gains `title_words: tuple[str, str, str]` (running, done, failed) and `about`.
  `build_output_stream` passes the words from `payload.kind` (`slides` / `report`) and the
  library title; `revise_output_stream` looks up the artifact's kind (a cheap service read; add
  `KnowledgeService.artifact_kind(base_id, artifact_id) -> str` if nothing exists) and uses the
  revise words.
- Library title: add `KnowledgeService.library_title(base_id) -> str` (falls back to the id).
- Inside `work(run)`: `with activity.track(f"output:{knowledge_base_id}", "output", running,
  about, open={"view": "library", "libraryId": knowledge_base_id, "tab": "outputs"},
  stop=lambda: _answer_runs(request).stop(_outputs_key(knowledge_base_id)), detail="Planning")
  as tracked:` — and in `hear`, before publishing: `outline` → remember n = number of sections
  (read the event; it carries the planned sections), `section` → detail + progress per §2.
  `done` → `tracked.done(done_word)`; `Stopped` → `tracked.stopped()`; every error branch →
  `tracked.failed(failed_word, <the same detail string it publishes>)`.
- Do **not** touch the request object from the worker thread for anything but what is already
  captured; capture `activity = request.app.state.activity` before `start`.

### B5. Answers (`gunther/api.py`, `stream_session_message`)

- Before starting the run: look up the session's library id (add
  `KnowledgeService.session_library(session_id) -> str`; raise 404 as today if missing — check
  what the current code does for a missing session and keep that behaviour).
- `open`: library id `"@home"` → `{"view": "home", "sessionId": id}`, else `{"view":
  "library", "libraryId": kb, "tab": "ask", "sessionId": id}`.
- `stop=lambda: _answer_runs(request).stop(session_id)`.
- In `hear`: `step` events with `state == "running"` and a `label` → `detail=label`; first
  `text` → `detail="Writing"`. On `done` → `done("Answer ready")`; `Stopped` → `stopped()`;
  error branches → `failed("Couldn't write the answer", detail)`.
- If the run could not start (409, one already running) nothing is tracked.

### B6. Tests (`tests/test_activity.py`, new; plus additions to existing files)

1. Registry unit tests: track → snapshot shows it; update bumps version, a no-change update
   does not; done/failed set `endedAt`; stopped removes; replace by same id; a stale `Tracked`
   cannot touch the new entry; prune (60 min via an injectable clock, 20 newest finished);
   context manager marks failed on exception; `stop` returns None/False/True correctly and calls
   the callable; dismiss/clear rules.
2. **Many at once**: three tracked entries from three threads updating concurrently, then each
   finishes; the snapshot holds all three with the right states and the version only grows.
3. `wait_for_change`: returns at once when `after` differs; wakes when another thread updates;
   returns after the timeout (use a small timeout) with the same version.
4. API through `TestClient`: GET without `after`; GET with stale `after`; stop 204/404/409;
   dismiss 204/404/409; clear.
5. Transcribe again (extend `tests/test_retranscribe.py`, reuse its fake hearing): a job shows
   `waiting` → `running` with progress → `done` "Transcript updated" with `open` pointing at
   the source; **two recordings started back to back: one `running`, one `waiting`**, both in
   the list; stopping a running job removes its entry, the status endpoint says `stopped`, the
   listen copy is removed, and the next waiting job then runs; stopping a waiting job works
   too.
6. Builds (extend `tests/test_outputs.py` with its fake models): a build shows `output:<id>`
   running, then `done` "Slides ready"/"Report ready"; **two libraries building at once both
   appear**; stop through `/activity/output:<id>/stop` ends the build like the old stop route.
7. Answers: an answer appears as `answer:<session id>` with the right `open` for a library
   conversation and a Home one; done → "Answer ready"; stop via the activity route.

Run: `cd apps/backend && .venv/bin/python -m pytest tests/test_activity.py tests/test_retranscribe.py
tests/test_outputs.py -q` then the whole suite once (`-q -x` is fine; ~735 tests), and
`.venv/bin/ruff check gunther tests` + `.venv/bin/ruff format --check` on the touched files
(follow whatever the repo's CI does — check `.github/workflows`).

**Done when**: all of the above pass, no existing test changed its meaning, and B touched only
`apps/backend/**` and this plan's progress log.

---

## 4. Phase D — desktop (`apps/desktop`, `packages/contracts`)

Build against §2 with the API mocked in tests; the backend lands in parallel.

### D1. Contracts (`packages/contracts/src/index.ts`)

Add the §2 types next to `TranscribeAgainStatus`, and add `"stopped"` to
`TranscribeAgainStatus["state"]`.

### D2. API (`apps/desktop/src/api.ts`, inside `knowledgeApi`)

```ts
activity: (after?: number, signal?: AbortSignal) => request<ActivityList>(`/activity${after === undefined ? "" : `?after=${after}`}`, signal ? { signal } : undefined),
stopActivity: (id: string) => request<void>(`/activity/${encodeURIComponent(id)}/stop`, { method: "POST" }),
dismissActivity: (id: string) => request<void>(`/activity/${encodeURIComponent(id)}/dismiss`, { method: "POST" }),
clearActivity: () => request<void>(`/activity/clear`, { method: "POST" }),
```

Check that `request` handles 204 (the other `request<void>` callers say it does) and passes
`signal` through `init`.

### D3. `src/activity/useActivity.ts` (new)

- One long-poll loop for the whole app: `GET /activity` once, then `GET /activity?after=v`
  again as soon as each answer arrives. On error wait 2 s, 5 s, then 10 s between tries (reset on
  success). Abort on unmount. When `document.visibilityState` turns visible, or `online`
  fires, retry at once.
- Returns `{ items: ActivityItem[], reachable: boolean, refresh(): void }`. `refresh` aborts the
  wait and asks again at once — call it right after the app itself starts something (Transcribe
  again, a build, an answer) and after stop/dismiss/clear so the pill reacts within a frame
  (dispatch a `gunther:activity-changed` window event from those places, the hook listens).
- Must be one loop even if two components use it: put it in a tiny context provider
  (`ActivityProvider`) mounted once in `App.tsx`.

### D4. Recording status (`src/activity/recordingStatus.ts` + shell)

The recording runs in the Capture **window** (Tauri) or the Capture **sheet** (browser/dev).

- Tauri, `apps/desktop/src-tauri/src/capture_shell.rs`:
  - `pub const CAPTURE_STATUS_EVENT: &str = "gunther://capture-status";` In
    `update_capture_status`, after storing it, `app.emit_to("main", CAPTURE_STATUS_EVENT,
    &status)` (ignore the error).
  - New command `capture_status(app) -> CaptureStatus` returning the stored status (the main
    window reads it once on load, e.g. after a reload mid-recording).
  - New command `send_capture_control(app, control: String) -> Result<(), String>` accepting only
    `pause | resume | mark | finish | show`; `show` does what `MenuAction::ShowCapture` does
    (`show_window(app, CAPTURE_WINDOW_LABEL)` + emit `show`); the others
    `emit_capture_control`. Register both in `lib.rs`'s `invoke_handler`. Add Rust unit tests
    for the control whitelist if the module's tests make that easy.
  - `captureBridge.ts`: `getCaptureStatus()`, `sendCaptureControl(control)`,
    `listenForCaptureStatus(handler)`.
- Browser: `App.tsx`'s `trackCaptureSnapshot` already receives the `RecorderSnapshot` (and
  title); keep the latest in state while Capture is open; controls dispatch
  `CAPTURE_CONTROL_DOM_EVENT`. In `AtlasUtilities.tsx` the control listener returns early unless
  `surface === "window"`; let the overlay surface handle `pause`, `resume`, `finish` and `show`
  too (`show` = un-minimise the sheet / `showRecorder`). Keep `quit-blocked` window-only.
- `useRecordingStatus()` returns `{ phase, seconds, title, transcriptWords } | null` — null for
  `idle` (and any phase not in `requesting | importing | recording | paused | stopped`).
  `sendRecordingControl(control)` hides which of the two paths is used.

### D5. Pure view logic (`src/activity/summary.ts`)

All decisions in one pure, unit-tested module:

- `pillView({ items, recording, reachable, now, seenFailures, flash }) → { tone, icon, text,
  clock?, percent?, extra, ariaLabel }` implementing §1's table and priority. `flash` is the id
  of an item that finished ≤ 6 s ago and is not on the current page (D7 decides "here").
- `nowRows(items, recording)` / `earlierRows(items)` ordering per §1.
- `clock(seconds)` → `m:ss` under an hour, `h:mm:ss` from an hour; `ago(iso, now)` →
  `just now`, `2 min ago`, `1 h ago`.
- Mean progress for "N running" = mean of numeric progresses; none known → indeterminate.

### D6. Components (`src/activity/ActivityStatus.tsx`, `src/activity/ProgressRing.tsx`,
`src/design/activity.css`)

- `AtlasTitlebar` gets a `status: ReactNode` prop rendered where `gx-save-state` is now; the
  old span moves into `ActivityStatus` (keep its title texts, class names and the offline
  styling so nothing about the idle look changes). Remove `serviceOnline` from the titlebar
  props if nothing else uses it there.
- `ActivityStatus` props: `serviceOnline`, `onOpen(open: ActivityOpen)`, `isHere(open) =>
  boolean`, plus what it needs from `useRecordingStatus` / `useActivity`.
- Panel per §1, portalled, Esc (capture phase, `stopPropagation`) and outside click close it,
  focus moves into the panel on open and back to the pill on close. Use lucide icons at 13–14 px
  (`Pause`, `Play`, `Square`, `X`, `Check`, `AlertCircle`) — the ring is SVG.
- `activity.css`: import it in `main.tsx` after `shell.css`. Use tokens only (`--gx-danger`,
  `--gx-brand`, `--gx-warning`, `--gx-line-2`, `--gx-surface`, `--gx-shadow-md`, radii, motion
  tokens). Mirror the existing `gx-save-state` sizing (font `--gx-text-xs`, 26 px tall pill,
  `--gx-radius-pill`). Narrow-window rule next to the existing `.gx-save-state { display: none
  }` media query in `shell.css` (move that rule so the pill shows icon-only instead of
  vanishing).
- Finished-flash: when an item's state goes running/waiting → done between two lists, flash it
  for 6 s unless `isHere(item.open)`; when it is here, dismiss it right away (the person saw it
  finish on the page). Failures are never auto-dismissed.

### D7. Opening a page (`App.tsx`)

`openActivity(open)`:
- `item` → `openItem({ type: "source", id }, originOf(view))`.
- `library` + `outputs` → `openBase(libraryId, undefined, "outputs")` (the Outputs page already
  follows a running build when it mounts — `followOutputBuild`).
- `library` + `ask` → write `gunther:active-session:<libraryId>` = sessionId in
  `persistentStorage`, `openBase(libraryId, undefined, "ask")`, then dispatch
  `gunther:open-session` `{ libraryId, sessionId }`. `SessionWorkspace.tsx` listens and calls
  its `loadSession(id)` when the library matches (covers "already on that Ask tab").
- `home` → write a one-shot key `gunther:open-home-session` = sessionId, `navigate("home")`,
  dispatch `gunther:open-home-session`. `HomePage` (or `useHomeAsk`) reads and clears the key on
  mount and on the event, then calls `homeAsk.open(id)`.
- `isHere(open)`: true when the current view already shows it (item open with that id; base
  view with that library and tab; home with that session shown — approximate is fine:
  `view === "home"` for home).

### D8. Kick the refresh

Dispatch `gunther:activity-changed` after: Transcribe again starts (`TranscribeAgain.tsx`
`start`), an output build/revise starts (where `buildOutputStream`/`reviseOutputStream` are
called), an answer starts (`sendMessageStream` callers or inside `api.ts` right after the POST
returns OK — pick one place), and after stop/dismiss/clear.

### D9. Transcribe again tab

`TranscribeAgain.tsx`: treat `stopped` like `idle` (button offered again); the grey line says
"Stopped." once. Nothing else changes there.

### D10. Tests (vitest + Testing Library, `src/activity/*.test.ts(x)`)

1. `summary.test.ts`: every row of §1's table; priority (recording beats failure beats flash
   beats running); `+N`; mean progress; **a recording plus five jobs** (2 transcribe: 1 running
   1 waiting; 2 builds in two libraries; 1 answer) → pill `12:34 Recording` `+5`, Now has six
   rows in order; clock/ago formatting incl. ≥ 1 h.
2. `useActivity.test.tsx`: long-poll loop passes `after`, backs off on error, `refresh` asks at
   once, one loop with two consumers.
3. `ActivityStatus.test.tsx`: idle shows "Saved locally"; offline shows "Reconnecting…";
   opening the panel lists rows; clicking a row calls `onOpen` with its `open`; Stop calls
   `stopActivity(id)`; Clear calls `clearActivity`; Esc closes; failure marked seen after open;
   finished flash suppressed when `isHere`.
4. Existing tests that render `AtlasTitlebar`/`App` still pass (mock `knowledgeApi.activity` to
   a never-resolving or empty list where needed so tests don't spin).

Run: `cd apps/desktop && npm run typecheck && npx vitest run src/activity` then the full
`npx vitest run` once (known load-flaky: HomeResearch, ItemPage note page — re-run those alone
if they fail). `cargo check` in `src-tauri` with `~/.cargo/bin/cargo` if it finishes in
reasonable time; say so if skipped. **No** `tauri build`.

**Done when**: typecheck clean, the new and existing desktop tests pass, and D touched only
`apps/desktop/**`, `packages/contracts/**` and this plan's progress log.

---

## 5. Pitfalls

- Several jobs at once is the normal case, not an edge case: never assume one entry per kind.
- Don't poll per component: one long-poll loop (D3).
- Don't send every streamed `text` token to the activity: only steps, sections and state
  changes (B4/B5).
- Activity must never break the job: any exception inside the tracking calls is a bug in
  activity — wrap nothing silently, but keep `Tracked` methods trivially safe (no I/O).
- Retranscriber's `close()` must still cancel and re-raise at shutdown; only `stop()` swallows.
- `position: fixed` inside `.page-enter` fills the page → portal to body.
- Reveal (slides) swallows Esc in the capture phase; our Esc handler is on window capture too —
  only act when the panel is open.
- Headless Chromium reports `hover: none`; don't hide controls behind hover only.
- The titlebar is a drag region: the pill and panel need `data-no-drag`.
- Keep the idle look pixel-identical to today's `Saved locally`.

## 6. Out of scope (later, same API)

Imports/OCR/summaries, read-aloud audio being made, library folder sync, macOS notifications,
history beyond an hour, a Stop for the recording's import.

## 7. Review checklist (Opus)

- [ ] §2 contract matches on both sides byte for byte (field names, words, ids).
- [ ] Many-at-once tests exist on both sides and pass.
- [ ] No stuck spinner: every exit path of every job ends its entry (done/failed/stopped).
- [ ] Stop works for transcribe (running + waiting), builds, answers, and the recording.
- [ ] Idle titlebar unchanged; light + dark; narrow window; reduced motion.
- [ ] Opening each kind lands on the right page, including "already there".
- [ ] Real screen check: backend + Vite + headless Chrome with mocked `/activity` answers
      (recording, several jobs, flash, failure, panel open), light and dark.
- [ ] Backend full suite, ruff; desktop typecheck + full vitest.

## Progress log

- 2026-10-12: plan written; B and D started in parallel.
- 2026-10-12: D done: pill + Activity panel, one long-poll provider, recording status (Tauri events + browser), open-page paths, Rust capture_status/send_capture_control; typecheck clean, src/activity 30 tests + Home open-session 2, full vitest 514/514, cargo check ok, new Rust test ok. Not yet seen on screen.
- 2026-10-12: B done: activity.py + activity_api.py, Transcribe again (with stop), builds/revisions and answers tracked; 22 new tests (test_activity 9, retranscribe 5, outputs 5, agent 3); full backend suite 750 passed 1 skipped; `ruff check apps/backend` clean.
- 2026-10-12: Opus review. Fixes: Transcribe again refuses Stop once it is saving the new transcript (a stop's callable may return False → 409), builds count each section once for progress (a section can be checked again after done), "N running" shows the ring without a percent, waiting jobs list after running ones, Stop/Pause icons filled (the outlined square read as a checkbox), panel 380 px. Seen on screen (headless Chrome, `/api/activity` mocked over DevTools): idle, one job, several, done flash, recording +N, paused, failure, panel light/dark, empty panel, narrow window. Below 1080 px the idle pill keeps its dot (it used to vanish) so the panel stays reachable. Revisions have no outline, so they show "Writing section 2" without "of n" and no percent.
