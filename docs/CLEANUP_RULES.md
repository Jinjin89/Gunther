# Cleanup review — rules

Goal: make Gunther's code small and simple. Two equal priorities:

1. **Abstraction** — find the few base building blocks the code keeps re-inventing
   (model calls, background jobs + progress, settings, file/asset paths, retries,
   JSON-from-model parsing, API route boilerplate, UI panels/lists/forms, …).
   Propose one shared function/module/component per block, so feature code becomes
   short calls into it.
2. **Simplicity** — delete what is dead, duplicated, over-built or not worth keeping.

This round is a **review only**. You write a report. You change no code.

## Output

Write `docs/CLEANUP_REVIEW.md`. Write it **as you go** (one area at a time), so
nothing is lost if your context is compacted. Structure:

1. **Summary table** — area, findings count, estimated lines removed.
2. **Base layer proposals** (the most important part). For each building block:
   name, one-paragraph purpose, the exact function/class/component signature,
   every current place that would use it (file:line), lines saved, risk.
3. **Findings**, grouped by area, highest value first. Each finding ≤ 8 lines:
   ```
   ### B-12 · dead code · low risk · −140 lines
   Where: apps/backend/gunther/foo.py:120-260, bar.py:40
   What: `old_export()` and its 3 helpers have no callers (rg shows only the def + one test).
   Change: delete them and test_old_export.py.
   Evidence: `rg -n "old_export" apps` → 2 hits, both listed above.
   Behaviour change: none.
   ```
   Types: `dead code` · `duplicate` · `missing abstraction` · `over-engineered` ·
   `useless feature` · `special case` · `hand-rolled (use a library)` · `bug`.
   IDs: B- backend, D- desktop, R- rust, M- mobile, S- scripts/tools/docs.
4. **Useless features** — separate list. Removing a feature is the owner's call,
   so give: what it is, where it is reachable in the UI (or "unreachable"),
   code size, what removing it saves.
5. **Bugs seen along the way** — only ones you can show with file:line and a
   concrete failing case. Not a full bug hunt.
6. **Execution plan** — the findings put into batches of ≤ ~500 changed lines each,
   ordered so base-layer pieces land first. Each batch names the tests that
   prove it (existing test files, or the test to add).

## Scope and order

1. `apps/backend/gunther` (largest: `service.py` 6.4k lines, `skill_runner.py`,
   `studio/pipeline.py`, `agent.py`, `api.py`, `artifact_*`, `outputs.py`, …)
2. `apps/desktop/src` (`LectureRecorder.tsx`, `SessionWorkspace.tsx`,
   `AtlasUtilities.tsx`, `api.ts`, `liveTranscript.ts`, …)
3. `apps/desktop/src-tauri/src`
4. `scripts/`, `tools/`, `packages/contracts`, `docs/` (stale plan docs that describe
   things already built or abandoned)
5. `apps/mobile` — lighter pass: whole dead screens/features and duplication with
   the desktop/backend only.

Tests (`apps/backend/tests`, `*.test.tsx`): flag tests that only cover dead code or
duplicate each other. Never propose deleting a test just because it is in the way.

## What counts as a good finding

- An abstraction must replace duplication in **3 or more places**, or make a large
  module's public surface clearly smaller. No abstraction for its own sake: no new
  frameworks, plugin systems, dependency injection, base-class hierarchies,
  "manager"/"factory" layers.
- Prefer a well-known library over hand-rolled code when the library already
  exists in the project or is the obvious standard (say which one and why).
- Giant files: propose a concrete split (new file names + what moves) only when the
  parts are truly separate concerns — not just to make files shorter.
- Special cases: hard-coded publishers, document names, example questions or test
  fixtures inside production code break `AGENTS.md`. Always report them.
- Dev tools inside the app (evals, benchmarks, debug panels) belong in repo
  commands, not in the app — report them.
- Every finding needs evidence: an `rg` result, a call graph, or the two duplicated
  snippets side by side (file:line). No "probably unused".

## Do not

- **Do not edit, move, delete or format any file** except `docs/CLEANUP_REVIEW.md`.
  The working tree has uncommitted work: no `git stash`, `checkout`, `reset`,
  `commit`, `clean`, no branch switching.
- Do not change core behaviour or data. Core features stay: library + import
  (PDF parsing, web capture, documents), recording + live transcript + dictation,
  Ask agent, Contents, Outputs/studio (reports, slides, journal club decks), read
  aloud, settings (models, services, usage), Trash, backups, mobile gateway.
- Do not propose changes that break existing users' data: database migrations are
  append-only; on-disk library layout, saved file formats, the HTTP API used by the
  mobile app and `packages/contracts` stay compatible (or the finding must say how
  to migrate).
- No style-only churn: renames, import order, formatting, comment rewording, type
  annotations for their own sake.
- No model calls, no API keys, no network, no app builds (`tauri build`,
  PyInstaller, `.deb`), no package installs, no lockfile changes. Static reading,
  `rg`, `git log/blame`, and already-installed linters (`ruff`, `npm run typecheck`)
  are fine. Running existing unit tests is allowed but not needed.
- **Do not ask questions and do not stop to wait for approval.** When something is
  unclear, pick the safer reading, write the assumption in the finding, and keep
  going. Stop only when every area in the scope is covered and the report is
  complete. If you run low on context, save the report first, then continue.

When the report is complete, end with one line: `CLEANUP_REVIEW DONE`.

---

# Round 2 — top-down (architecture)

Round 1 (`docs/CLEANUP_REVIEW.md`) was too local: ~230 lines out of 65k backend
lines, and base-layer pieces that save 4–60 lines each. Keep it as it is. This round
looks for the big wins: **whole systems that exist two or three times**, and god
modules that should be a few small ones.

Known example to start from: `service.py:4204` chooses between three output makers
(`studio.pipeline.StudioAgents` — the default, `skill_runner.SkillAgents` — "the earlier
skill steps", and a plain path), switched by Settings → Developer → Method
(`output_method`). Also check where `artifact_agent`, `artifact_steps`,
`artifact_workspace` and `outputs.py` fit in.

Write `docs/CLEANUP_ARCHITECTURE.md` (as you go, like round 1):

1. **Job map.** For every user-facing job — make an output (report, slides, journal
   club deck), revise/edit an output, Ask answer, import + parse a document, web
   capture, record + live transcript + dictation + retranscribe, provider settings —
   list every code path that can do it today. For each path: entry point
   (file:line), modules it owns, total lines, whether it is the default, reachable
   from the normal UI, only behind a developer switch / fallback, or unreachable.
2. **Target design per job.** One path. Name the few base pieces it is built from,
   with concrete signatures (e.g. one model-call-with-JSON helper, one step runner
   with progress + resume, one source pool / evidence reader). Say exactly which
   modules and functions are deleted, which merge, and what stays only to **read**
   old saved data (old artifacts, old settings values must still open). Lines saved.
3. **God modules.** `service.py` (6.4k), `skill_runner.py`, `studio/pipeline.py`,
   `agent.py`, `api.py`, `artifact_agent.py`; desktop `LectureRecorder.tsx`,
   `SessionWorkspace.tsx`, `AtlasUtilities.tsx`, `api.ts`. For each: the concerns
   mixed inside (with line ranges), the split into named files, and which parts
   disappear once the job map collapses to one path. Only split true separate concerns.
4. **Owner decisions.** Removing an older method or a reachable feature is the
   owner's call. Mark each one `Owner decision — recommended: remove/keep` with one
   line of why, and the lines it frees.
5. **Merged execution plan.** One ordered list of batches covering both rounds.
   Big deletions of retired paths go first (they shrink everything after them), then
   base pieces, then the round-1 small items that still apply. Each batch ≤ ~800
   changed lines (deletions may be bigger), with the tests that prove it. Note which
   round-1 findings become unnecessary.

Same "Do not" rules as round 1: edit no file except `docs/CLEANUP_ARCHITECTURE.md`,
no git commands that change the tree, no model calls, builds or installs, **no
questions — decide, note the assumption, keep going.** Evidence for every claim
(`rg`, call graph, file:line). End with the line `CLEANUP_ARCHITECTURE DONE`.
