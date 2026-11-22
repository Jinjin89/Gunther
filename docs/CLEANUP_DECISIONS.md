# Cleanup — supervisor decisions on round 2

Reviewer: Claude (supervisor). Worker: Codex. Applies to `docs/CLEANUP_ARCHITECTURE.md`
and `docs/CLEANUP_REVIEW.md`. The owner delegated all owner decisions to the supervisor.
Read `AGENTS.md` first: it now leads with "General rules, never special cases" and
"One path per job".

## 1. Owner decisions

| Item | Decision |
| --- | --- |
| Earlier Skills + plain whole builds | **Remove** (agree). |
| Earlier work / direct revision writers | **Remove** (agree). |
| Manual rebuild, typed edit, Check, visual review, restyle | **Keep** (agree). |
| D-12 developer trace / log / job viewers | **Keep — disagree.** The owner asked for them and debugs in the installed Mac app, where repo commands don't exist. Simplify their code if it repeats, but keep them in the app. |
| Offline Library preview + sample-library fallback | **Remove** (agree). |
| Local evidence reply when no model (`service.py:2109–2200`, `conversation.py`) | **Remove — disagree.** Owner rule: no heuristic stand-in for model output. With no model: the Ask UI shows "needs a model / key" with a link to Settings; on model failure: the error + Retry. Keep search itself. |
| Automatic Ask skill selection (`config.py:69`, off by default) | **Remove.** A finished experiment behind a flag. Keep explicit Compare / Research. |
| Blocking Ask route | **Remove** unless more than a small eval change is needed: make `evals/ask/run.py` read the stream route. Push back with evidence if that is costly. |
| Web search, snapshot, save-summary | **Keep** (agree). |
| Lecture summary, provider settings, usage, cached read aloud | **Keep** (agree). |
| Demo seeding + embedded smoke inputs | **Remove** (agree). |
| Contents, filing, notes, Trash, backup, pairing | **Keep** (agree). |

## 2. Compatibility rule (replaces the per-item "keep for existing clients")

- **Saved data must keep opening**: old artifacts, old settings values, old source
  revisions, old recordings and drafts, queued records on disk. Keep a small read-only
  converter at the edge, never the old pipeline.
- **Old builds of our own apps are not a reason to keep a second path.** Desktop and
  mobile ship from this repo together. So, once no client in the repo uses them, delete:
  the one-shot audio upload, the unsequenced append, the blocking speech route, the
  inline upload receipt (after mobile moves to the queued receipt), the v1 live events
  (after mobile moves to v2), and every unused desktop wrapper.
- Condition: mobile changes need `flutter test` / `flutter analyze` to run on this box.
  Check in batch 00. If Flutter is not available, leave the mobile-facing server paths
  and the mobile client as they are, and say so.

## 3. Added scope

1. **Special-case sweep** (the owner's strongest rule). Go over production code for:
   names of documents/journals/publishers/sites/people/examples; keyword or regex lists
   (including multi-language heading lists such as `structure_draft.py:56,88`,
   `content.py:280`); thresholds that only make sense for one fixture;
   `if provider/model == …` outside the provider adapter (inside the adapter is its
   job, fine); per-format hacks outside the format reader. For each: keep (a general
   convention, with the reason), or redesign (the general rule that replaces it).
   Don't just delete heading recognition; replace a tuned list with the general signal
   (layout, typography, position) where one exists.
2. **No-model stand-ins sweep.** Find every place that makes a title, summary, outline,
   answer or check by heuristics when the model is missing or fails. Replace with
   error + retry (or "needs a key"), except plain non-AI defaults such as a file name
   used as a title.
3. **Docs.** 51 files in `docs/`. Per the new `AGENTS.md`, docs describe only the
   current system. Give each file a verdict: keep / merge into X / delete. Built or
   abandoned plans, research notes, acceptance reports, batch logs and progress notes
   go (git keeps them) after any still-true content moves into a guide. Expect roughly
   10–15 files left. `FACTS_AND_STEPS_PLAN.md` is agreed but unbuilt: keep it, and fix
   its references to retired modules at the end. The docs batch runs **last**, after
   the code is final. The three `CLEANUP_*` docs and this file are deleted in that batch.

## 4. Order

- **Phase A — delete and fix** (largest value first): 00 baseline; 01–07 output
  engine retirement; the removals in §1–§2; 09, 10, 11, 32, 33; special-case and
  no-model sweeps; bugs B-13, B-05, D-09, D-10, M-04, D-14, R-05, S-05.
- **Phase B — shared building blocks**: 12–15, 17–25, 38, 39.
- **Phase C — splits**: 26–31, 36, 37. Re-check each after A and B. Skip a split
  when the file is already coherent and under ~800 lines. Moves only, no behaviour
  change.
- **Phase D — docs** (§3.3).
- Push after each phase when the full suite is green.

**Batch 00:** run and record the full baseline on the current HEAD: backend `pytest`
+ `ruff`, desktop `npm run typecheck` + `vitest`, Rust `cargo test` (cargo may be off
PATH: try `~/.cargo/bin`), Flutter if present. Known failures become the reference;
a batch must not add any.

## 5. Batch protocol

- One batch at a time. Run the batch's tests plus lint/typecheck for every touched area.
- Then **stop** and write `/tmp/gunther-cleanup/batches/NN.md` (outside the repo):
  what changed, lines +/−, tests run with pass/fail counts, any deviation from the
  plan and why, anything unsure. Stopping is the request for approval.
- Do not commit. The supervisor reviews and commits, or sends corrections.
- A plan that turns out wrong while building: stop and explain in the batch file.
  Don't silently change scope.
- Still no model calls, no network, no app builds, no package installs.

---

# Round 3 — supervisor decisions on `CLEANUP_PLAN.md`

1. **Pushback on blocking Ask: accepted** (08a as written).
2. **New Phase E — reading rules.** Move SC-03, 04, 05, 06, 07, 09, 10, 17, the
   document-frequency part of SC-12, and SC-16 (A41, A42a–b, the SC-12 part of A43,
   and the SC-16 part of A43d) out of Phase A into Phase E, run after B. These change
   how documents are read: a core feature. **Gate:** an offline before/after run over
   the existing corpus (recorded Docling replays, structure fixtures, any local paper
   files the parsing harness uses), with every changed block disposition / heading /
   date listed in the batch file and justified as a fix. Kept-before content must not
   disappear without a reason. If no offline comparison is possible, Phase E is not
   done: report it as the next project and stop there.
   NM-05 (false abstract) stays in Phase A: it removes a fallback, it doesn't add rules.
3. **Stays in Phase A:** SC-01, SC-02, SC-11 (only with **no added model calls**: reuse
   a reviewer call that already runs, otherwise drop the word lists and keep the
   structural checks), NM-01, 02, 03, 04, 05, 07, and NM-11 in its smallest form:
   delete the post-check numeric citation insertion; no new candidate flow.
   **SC-13 moves to Phase B** (it is a shared capability abstraction).
4. **NM-06:** keep the planning fallback (control flow only; the answer is still
   written by the model). Record the failure in the trace. The grading part stands
   as written.
5. **NM-08, NM-09, A45: no new Retry/incomplete-review UI** in this cleanup. Only
   honesty fixes that are small: don't label window grouping as model-grouped, and
   don't drop a failure to a log when the UI already has a status for it.
6. **Flutter gate:** in batch 00, install the Flutter SDK into `/tmp/flutter` (the
   version the cached `package_config.json` / `local.properties` expect, from the
   official source). This is the one allowed install. If it fails, skip the gated
   batches and report them.
7. **Docs target**: English, named by area, no version suffixes. About:
   `README.md` (root), `ARCHITECTURE.md` (+ repository structure), `DEVELOPER.md`,
   `DESIGN_SYSTEM.md`, `LIBRARY.md` (folders, Contents, documents and reading),
   `CAPTURE.md` (capture, web, recording, live transcript, dictation, menu bar,
   summaries, Activity; keep the tray SVG), `ASK.md`, `OUTPUTS.md` (+ scientific
   workflow if it fits), `STUDIO_FORMAT.md`, `SETTINGS.md` (services, models, usage,
   prompt cache, read aloud), `OPERATIONS.md`, `FACTS_AND_STEPS_PLAN.md`.
   Re-map the merge targets to this set.
8. **Phase order:** A → B → E → C → D. Phase C only for files still over ~1,000 lines
   or still mixing unrelated concerns after A and B.
9. **Batch file additions:** `git diff --stat`; a self-check line "special cases
   added: none / no-model stand-ins added: none" (or the exception and why).

**Next:** update `CLEANUP_PLAN.md` to these decisions. If you disagree with any of
them, write the pushback at the top and stop. If not, mark the plan `FINAL`, run
batch 00, write `/tmp/gunther-cleanup/batches/00.md`, and stop for approval.

---

# Round 4 — batch 01 scope cut (supervisor)

Batch 01 grew to ~1,150 lines while retrofitting Skills-era outline controls into Studio,
and each review found new lock-vs-verification edge cases. Top-down decision:

- **Retire** per-slide layout locks, design locks, and the outline fields only the
  Skills planner used (message, audience question, preceding slides, transition,
  composition). They go with the old engines. Studio designs each slide; people change
  the look with the style menu and the words with typed edit.
- **Outline rebuild keeps** headings, goals and order as guidance for Studio's story
  stage. Approach stays read-only "How it was made" for Studio work.
- **Old saved outlines** with those fields still open (ignored on rebuild, shown
  read-only at most).
- Batch 01 keeps: style intent (resolution + request fingerprint, with replay of old
  fingerprints), sync of a direct rebuild into the conversation (including a later
  typed edit), checkpoint/figure keys from the inputs actually used, report repairs that
  keep the approved section count and never clear parts before validating, kind,
  explicit web choice, scientific purpose.

**Directive for batch 13 (from batch 01 review):** the UI's Approach/Outline rebuild
should run as an action on the conversation work, not as a separate direct route whose
result `sync` then has to import. One entry, one state owner; then delete the
rebuild-import branch in `artifact_workspace.sync`. Keep the HTTP build route only if a
current client still needs it.

# Round 5 — batch caps and batch 02

- **Caps count added lines only** (insertions in `git diff --numstat`). Deletions are
  free: removing code is the goal. Soft limit ~450 insertions per batch; going over by
  a little is fine when the batch can't sensibly split. Don't stop at a cap, note it.
- **`library_supplements` retires.** No client sets it and Studio never ran it. Accept
  and ignore it on input for one release (old callers don't 422), drop it from
  contracts, keep old provenance readable.
- Tests that compare against `SkillAgents.make` (the retired engine) are deleted with
  it. Tests that only used the old build route to create a first version switch to
  saved-version fixtures or the Studio fixture gateway.

# Round 6 — no parsing of user intent from free text (batch 04)

Batch 04 widened `requested_pages` with `张`, which also counts figures. The root problem
is the pattern: `slide_planning.requested_pages` and `studio/pipeline.py:_length` read the
user's brief with English/Chinese regexes (slides, pages, 页, minutes, 分钟, words, 字) and
turn the matches into hard rules. Rule from now on: **code never infers what the user
asked for from free text with word lists or regexes.** The model that already reads the
brief declares it as structured fields in the reply it already returns (e.g. the
storyline step adds `requested_count: int | null` and `requested_minutes: int | null`),
and code validates against those fields. No extra model calls. Default length guidance
stays plain text. Delete `requested_pages` and the `_length` regexes. Typed UI inputs
(a number field) are of course fine.

# Round 7 — every batch runs the full backend suite

`test_journal_club.py:259` passed in batch 00 and fails at `321ad62`: batches 01–03
broke it and the focused suites missed it. From batch 04 on, before stopping, run the
**full** backend pytest (loopback namespace, as in batch 00), plus the full desktop
vitest when desktop files changed, and report pass/fail totals in NN.md. The Skills
planner's private brief parser stays until batch 07 deletes the planner.

When a batch touches the smoke's journey through Home, Capture, Inbox, Libraries,
source details, Ask, Artifacts, Settings or Trash, also run
`npm run smoke:desktop-ui` with headless Chromium and isolated loopback. Record the
result in NN.md.

# Round 8 — evals of retired code (batch 06)

An eval that only measured the retired Skills planner (`evals/slides/plan.py`,
`over_limit.py`, `verify.py`) is **deleted with its archived fixtures**
(`artifacts/validation/presentation-batches-0-2/…`), not ported. Git keeps them. Evals
that measure current behaviour stay and must be able to fail. This overrides the
plan's "repoint the slide evals" for those three.

# Round 9 — batch 08c

NM-06's grading policy (required grading failure → Retry, optional → raw hits marked
ungraded) lands in 08c with Ask's failure handling; A43c no longer carries that part.

# Round 10 — batch 08f

File and photo captures from both clients follow the desktop queued path, which has
never run assertion analysis on files. The old mobile inline path's file assertions
retire (no new model call per import). Text imports keep their deferred assertion
analysis; explicit structured assertions keep their parser and its test.

**Round 10b — legacy capture identity.** No parsing of user notes (headings, section
order, prefixes) to recover an identity. A retried capture reuses an old inline
capture only through an exact stored id (client capture / request id) or an exact
fingerprint recomputed from the submitted fields alone. Otherwise it is a new capture:
a rare duplicate is acceptable, attaching notes to the wrong source is not.

# Round 11 — batch 08h (live audio)

One simple clock rule for both clients: the client's stamp sets the session clock at
each (re)connect; within a connection, frames advance by their sample count and a
difference smaller than one frame is jitter, not a gap or an overlap. Replace the
stricter rule from the previous 08h round with this. After this round, new live-audio
edge cases go to follow-ups unless they lose saved audio or hide a failure.

# Round 12 — Phase B candidate: one server-state library for the desktop

Batch 09 review rounds kept finding hand-rolled read/refresh bugs (all-or-nothing
bootstrap, updates dropped while a read is in flight). The desktop has no server-state
library; ~66 components hand-roll loading/refresh with useEffect. Phase B adds a batch
(before 18/19) to evaluate and, if it holds up, adopt **TanStack Query** for desktop
server reads: in-flight dedupe, invalidate-on-event, retry, per-read error. Start with
Ask (Home + Library) and the settings panes; migrate others only where it deletes code.
Codex to give a short go/no-go with evidence before building.

# Round 13 — prompts are not code (batch 32)

The special-case rule is about production **code** that branches on particular
inputs. Worked examples and good/bad contrasts inside a model prompt teach a general
rule; they stay. No prompt rewording in this cleanup: it can't be verified without
model runs and changes output quality. Prompts change only where they reference removed
code. SC-02's prompt part is dropped (prompts.py and reference.py stay as they were);
the smoke-input and seed moves stand. Batches 32 and 33 are merged as batch 32, and the
batch 11 CSS follow-up rides along (supervisor's instruction).

# Round 14 — merged batches

The supervisor merges small plan batches; the merged scope is whatever the cleaner was
told and lists at the top of NN.md. Don't FIX a batch for being merged. So far:
32 = 32+33 (+11 CSS); A42 = A42c+A43d.
A43 = A43b + A43c remainder (NM-07)

# Round 15 — one shape for check state (batch A43)

A check is a set of verdicts, one per (claim text hash, evidence hash, stage), each
`supported | unsupported | unchecked | unavailable`. Edits, retries and sync only merge
verdicts by key: a verdict survives while its key exists. Coverage, "partial" and
"unavailable" are derived from the verdicts, never stored separately. Old check rows
are read into this shape at the edge.
A44 = A44a + A44b

# Round 16 — written facts and Retry (batch A44)

Written facts have two forms: a Markdown table headed exactly `subject | predicate |
object`, and an arrow line `A -> B -> C` outside any table. A standalone `a | b | c`
line is dropped on purpose: it can't be told apart from a table row, and no app screen or
doc offers it. Saved assertions keep opening. A table starts at a header row plus a
delimiter row and ends at the first blank line (the GFM rule). Every line in between is a
table row, whatever it contains.
Retry replaces only unreviewed model output. Reviewed assertions (verified or disputed)
are user decisions and stay. A new model claim with the same subject, predicate, object
and quote as an existing one reuses its id, evidence and review state.

# Round 17 — no written-fact parser (supersedes Round 16's first paragraph)

Three review rounds kept finding Markdown shapes (short rows, one-cell rows, quoted
tables) that the hand-rolled parser misreads. That means the parser is a guess over
free text, not an explicit input. `parse_structured_assertions` is deleted with both
of its callers. Facts in a capture come only from Analysis, which reads tables and arrows
itself. With no Analysis model, the capture shows needs-a-key or an error with Retry.
Saved user-entered assertions keep opening and keep their review state. Round 16's
Retry paragraph stands.
A45 = A45 + A47a + A47b
A48 = A48a + A48b + A48c + A49a + A49b

# Round 18 — one reader for a source's recording (batch A48)

The backend owns the recording reference. `source_recording_id` is the only reader, and
the source API returns its result (and the measured duration). The desktop uses those
fields and stops parsing recording ids from text. Precedence is one rule. Before the
transcript heading, the current header line comes first, then a standalone
`Local recording:` line. With nothing there, a standalone reference on the last
non-empty line counts. Everything else is transcript text. Whenever the app rewrites a
recording source (Transcribe again), it writes the resolved id into the current header
line, so the source moves to the current shape. Old shapes outside this rule lose their
audio link. That rare miss is acceptable.
A50 = A50a + A50b

# Round 19 — old in-flight conversation states are not migrated (batch 13)

Rule A from the batch 13 review (rebuild conversation work from the latest version)
grew into guessing which revisions are plans, so it is withdrawn. Saved, sealed versions
keep opening and stay correct, and they stay in history. Conversation work left
mid-flight by older builds (an outline rebuild that never synced, an interrupted save) is
not reconstructed. The conversation reopens with what it stored, and the user can
approve or make again. Replaying a turn whose version is already published returns that
version and leaves the conversation unchanged (no draft key, no lineage switch). Rule B
stands: one owner commits version, workspace and conversation together, only on success.

# Round 12 answer: no TanStack Query in this cleanup

Accepted no-go (batch 18). The desktop targets Safari 13 / macOS 11, and Query v5 supports
Safari 15+. The gain is about 11 components. Batch 19b keeps REST reads as inputs to its
shared hook. Revisit if the minimum macOS rises.

# Round 20 — batch 19 scope cut

Each review round of batch 19 found another startup-selection regression that the base
commit didn't have. The 19b hook owns only the live answer listener: attach, swap,
detach, Stop. Library Ask's startup and selection code (remembered session, Activity
destination, StrictMode replay, archived sessions) goes back to a447bb5's logic,
apart from the wiring it needs to call the hook. The round-1 listener fixes stay. Shared
startup selection is not part of this cleanup.

# Round 21 — file type evidence and partial reads (batch 20)

"Never by names" means no particular document, publisher or example names. A file
extension is a standard type signal. The evidence order is one rule. Magic bytes or a
BOM decide binary formats and text encoding. For text content, the extension (looked up
in the standard mimetypes table) picks the text format. A declared media type is used
only when it agrees with the content class. Readers keep every part they could read
within the existing budgets and report what they skipped (missing members, parts over
the cap). They never drop the whole reading.

# Round 22 — the decoder decides (batch 20)

No hand-written signature tables for formats a library we already ship can open. Images
are an image when Pillow's `Image.open` + `verify` accepts them, which covers every TIFF
variant Pillow reads. Text is UTF-8 (or what its BOM says), otherwise windows-1252, the
WHATWG rule for "latin-1". No control-character classifier beyond what that decode
rejects. Archive member paths from manifests are URL-decoded (`urllib.parse.unquote`)
before lookup, and the path checks apply to the decoded path. Round 21's evidence order
stays. These steps are how it is carried out.

# Round 23 — batch 22 scope cut (same pattern as Round 20)

The third review round of batch 22 found another LectureRecorder completion regression
that 318ad32 didn't have. LectureRecorder.tsx goes back to 318ad32's completion, finish,
restore and spool logic, apart from the minimal wiring to call the shared upload
boundary. The checker's completion cases become regression tests. They pass on 318ad32
and must pass again.

# Round 24 — Phase B leaves UI state machines alone

Learned from Rounds 20 and 23: Phase B extracts shared transport, readers and helpers.
It does not rewrite a component's state machine (startup selection, recorder
completion, finish and restore). A shared piece is wired in with the smallest change. If
a batch needs to change a state machine to share code, it stops and says so in its
report.

# Round 25 — BFIX item 2 deferred

The checkpoint's `checkpointedAt` is the server's commit time, not the user's edit time,
so the two can't be compared safely. BFIX item 2 (draft recovery order) goes back to
886b42c's behaviour, where the local draft wins. That never loses typed text. The real
fix (store the client edit time per snapshot) belongs to the recorder redesign, the next
project after this cleanup. The checker's item-2 FIX is overruled.

# Round 26 — Phase E scope (from E0)

The offline replay works (about 14 s), but here it covers only four inputs: two recorded
Docling layouts and two synthetic ones. The real development corpus (the 13-paper
harness) is not on this box. Reading-rule changes (SC-03/04/05/06/07/09/10/17) would be
checked only against those four inputs. That is too thin for rules tuned on real papers.
They are **deferred as the next project**, with E0.md as its plan and the user's corpus as
its gate. This cleanup does only the two items fixtures can verify fully: SC-12 (topic
terms by document frequency, fixed library fixture) and SC-16 (numerical claims judged
by field, referent, units and provenance, using offline Checker and Studio fixtures).
E0 is a study, so its report-format FIX items are not needed.

# Round 27 — E43 keeps SC-12 only

Each review round of SC-16 found another cover or chart regression in Studio. Cover
fields are parsed from text in two places (the desktop renderer and claim preparation),
and the retry and fallback paths drop units. That needs one cover model and one claim
identity, which is a redesign, not a cleanup. All SC-16 changes go back to d50d4e8. The
checker's reproductions (Spring 2026 cover date, dated subtitle, numeric first column,
ms vs s units through retry and fallback, one cited cell, wrapped journal-club question,
citation in subtitle, idempotent metadata) move into docs/READING_RULES_NEXT.md as part
of the next project. SC-12 (topic terms by document frequency) stays.

# Round 28 — Phase C splits

Phase C covers files over ~1,000 lines that mix jobs: service.py (5.6k), api.py (1.9k),
studio/pipeline.py, agent.py and artifact_agent.py. Reading code (structure_draft,
document_model, document_structure) waits for the reading project. The recorder and the
mobile capture view model wait for the recorder redesign. Splits are pure moves along
the job map in docs/CLEANUP_ARCHITECTURE.md. No logic edits, renames or signature changes
beyond imports. A move doesn't count toward the insertion cap. The checker verifies with
`git diff --color-moved` that nothing but imports and moved blocks changed. One file per
batch, starting with service.py.
