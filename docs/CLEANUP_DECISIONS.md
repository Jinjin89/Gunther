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
