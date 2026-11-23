# One library screen, Studio types and key results

Status: agreed and final (2026-11-23). Phase 1a and the build turn of Phase 1b are built; the rest is unbuilt. Read [AGENTS.md](../AGENTS.md) first. When a phase ships, move
what it built into the shipped docs ([Outputs](OUTPUTS.md), [Ask](ASK.md), [Library](LIBRARY.md),
[Studio format](STUDIO_FORMAT.md)) and delete that phase from this file.

## 1. Why

Gunther's outputs work like Google NotebookLM, but they are harder to use:

- The library page has three tabs: Ask, Contents and Outputs. Each tab has its own source picker and its own conversations.
- To make something, you type a request into a chat. The Report and Slides buttons only fill in text.
- Outputs hide behind four panes: a preview dropdown, a version dropdown, a Details drawer (artifacts, brief, approach, outline, versions) and a conversations list.
- The desktop starts outputs through the Report and Slides conversation shortcuts.
- Knowledge does not grow. Save as knowledge copies a whole answer, chatter included. Ask never reads saved knowledge, and outputs never become knowledge.

Gunther stays ahead in checked citations, versions, refining by chat, real exports and designed
slides. Keep all of these.

## 2. Decisions (agreed with the owner, 2026-11-23)

| # | Question | Decision |
| --- | --- | --- |
| D1 | Layout | One library screen: **Sources · Chat · Studio** side by side. There are no tabs. One source selection serves chat and Studio. |
| D2 | Contents tab | It becomes the Sources panel: the Contents tree with ticks, arranged in place. Clicking a source shows its summary in the middle. The library overview fills the empty chat. |
| D3 | Changing an output | The output opens in the middle in place of the chat. It has its own **Refine** box, with its change history folded above the box. Back returns to the chat. The chat is for questions only. |
| D4 | Tiles | Report (free-form), Briefing doc, Study guide, FAQ, Timeline, Slides, Flashcards, Quiz, Mind map and Audio overview. |
| D5 | Growing knowledge | After every cited answer and every saved output, Gunther writes its **key results** automatically. Each one is a checked sentence citing the original passages. The owner can remove any of them. |

These follow from AGENTS.md and are not open questions:

- Clicking a tile builds right away with default options. The pencil on a tile opens its options first. A tile build asks no clarifying questions.
- The UI stays white and black like the rest of Gunther. Tiles are neutral with a small icon. Colour marks identity only.
- Old saved outputs, conversations and saved answers keep opening.

**Not copied from NotebookLM:**

- No ephemeral chat. Keep durable, searchable, branchable, exportable Ask conversations.
- No unverified or single-shot outputs. Every shape stays Checker-verified, versioned and refinable.
- No live voice-call interruption in Audio overview and no Video overviews.
- No quiz streaks, XP badges or pastel/emoji themes.

## 3. The abstractions

### 3.1 Extend the output catalog

The existing shapes and types, build options and saved-version rules are in [Outputs](OUTPUTS.md).
Use `GET /api/output-types` for the Studio tiles in Phase 2a. Extend the type options with
`voices` when audio ships.

- **Two Studio pipelines.** Report and slides use the planned pipeline. Add the direct pipeline
  for cards, map and audio. It runs read → memo → draft → format fix → verify → assemble.
- **One general Checker.** For each new shape, add a `check_text` projection that turns one unit
  into plain checkable claims. Keep the backend split and join rules aligned with
  [desktop section parsing](../apps/desktop/src/outputs/sections.ts) through shared fixtures.
- Register the remaining shapes and types in their phases. Cards allow 40 cards, mind maps allow
  60 nodes and audio overviews allow 120 lines. Use shape metadata for outline, design and export
  capabilities when the readers use those capabilities.

### 3.2 One way to build and refine

There is one path for making and changing an output: an `ArtifactConversation` turn.

- **Tile build = `kind="build"` turn.** Clicking a tile (or submitting its options) creates a 1:1 `ArtifactConversation` and posts one turn with `kind="build"` and `build: BuildTurnInput` (`type`, `focus`, `audience`, `length`, `language`, `use`, `voices`, `theme`, `web`, `title`, `scope`).
  - `artifact_turn` (`artifact_turns.py:147-415`) runs `kind="build"` directly without calling `ArtifactAgent.run()` and without asking clarifying questions.
  - It saves `build.scope` on `row.scope_json`, loads material with `strict=True` (failing fast with 400 if empty), and honours `build.web`.
  - It creates—or on Retry reuses—a `draft_*` `Work` with `brief` and `work.spec` filled from `build`, runs Studio via `agent.build(...)` (keeping `studio_progress` checkpoints on failure), and saves v1 + sets `row.title = artifact.title` inside `commit_version`'s `publish` callback.
  - Store `build` inside the user `ArtifactMessage.payload_json` and hash it in `_turn_fingerprint` and `_earlier_turn` (`artifact_turns.py:64-106`).
  - Extend `ArtifactConversationSummaryOut` (`artifact_conversations.py:165-175`) with `outputType`, `shape`, `lineageId` and `lastTurn` (`kind`, `error`, `build`, `createdAt`). Example: if a Briefing doc fails mid-run and you reload the app, the card still shows the error; clicking Retry re-sends `lastTurn.build` with a fresh `clientRequestId` (`artifact_turns.py:397` already leaves `request_id=None` on failed turns) and resumes from saved checkpoints.
- **Refining and multi-output history.**
  - Add optional `targetLineageId` to `ArtifactTurnInput` (`schemas.py:1315-1327`). When supplied, `artifact_turn` sets `workspace.active = targetLineageId` (calling `import_work` if missing) before `ArtifactAgent` runs.
  - Make `POST .../outputs/build/stream` a thin wrapper around `artifact_turn(kind="build")`. Map old `kind`+`style` to `type` and keep `earlier_output` replay at the API edge.
  - Retire direct `revise_output` (`output_commands.py:267-403`). Keep `edit_output`, `check_output`, `restyle_output` and `review_slides`.
- **Progress and concurrency.**
  - Show no percent. Show the latest real stage label from `stage` events, with real counts where known (example: `"Writing slides · 3 of 8"` or `"Checking every claim against its source · 4 of 11 sections checked"`).
  - Replace hardcoded `"slides"` in `studio/verification.py:147,179` with `ShapeSpec.unit_label`. Remove the unused `"section"` / `progress` code in `artifact_api_common.py:79-81,107-118`.
  - Extend `ActivityItem.open` with `{ conversationId, typeId, lineageId, artifactId }` so clicking a done Activity row opens that output in the middle pane.
  - Key output runs and Activity entries by `conversation_id` (`output-conv:<conversation_id>`), falling back to `output-lineage:<lineage_id>` only for legacy direct calls (`restyle`, `visual-review`) with no conversation. Scope Follow and Stop routes to the conversation (`/artifact-conversations/{id}/stream` and `/stop`).
  - Different outputs build in parallel with no per-library cap. All Studio fan-out requests share **one process-wide pool of 12 workers** in `studio/requests.py`—covering `warm_map` and `studio/material.py:135` (which currently creates its own `ThreadPoolExecutor`). One build gets all 12 workers; two builds share them; Ask never waits on Studio, and `ModelGateway` stays unchanged. Guard the shared pool against nested re-entry (run inline if called from a pool thread) and keep smaller per-call limits (`FIGURE_WORKERS = 4` in `study.py:23`, `3` in `look.py:211`).
  - Clicking a tile directly while its first build is still running opens the running card; clicking the tile's pencil can start a new custom build.

### 3.3 Key results: one growing, checked fact list per library

A **key result** is one sentence about the subject, backed by the original passages that prove it.
Example: `"In Study A, loss of factor T shifted progenitors toward the white-cell branch [1]"`
citing Paper A, page 3. It never cites an answer or an output, only original passages.

**Storage.** Add three tables in a new migration (do not use `processing_jobs`, whose `source_id` is `NOT NULL` at `models.py:446`):

```
key_results          id, knowledge_base_id, text, status (kept | removed),
                     created_origin_kind, created_origin_id, created_at, updated_at
key_result_evidence  id, key_result_id, source_id, revision_id, block_id, region_ids_json,
                     quote, source_content_hash, source_asset_id,
                     origin_kind (answer | output | saved_answer), origin_id, created_at
key_result_jobs      id, knowledge_base_id, origin_kind, origin_id,
                     state (queued | running | done | needs_model | failed),
                     error, attempts, created_at, updated_at   -- unique (origin_kind, origin_id)
```

**Distiller pipeline.** Jobs run in the AI worker lane, one at a time per library. Each job is
keyed by `(origin_kind, origin_id)`, so a replay does nothing. When an Analysis model is set up in
Settings, any `needs_model` jobs reset to `queued`.

1. **Trigger.**
   - Ask: an answer saved in a real library with at least one non-web library citation. Ignore `@home` sessions until they are filed via `file_home_session`. Ignore `research.state == "asking"` and rework turns. When `coverage` is complete (`ask_turns.py:354-368`), pass only its supported claims and their evidence.
   - Outputs: a saved `build`, `revise` or `rebuild` version, or an `edit` version once `check_output` supports claims. Pass only supported claims with non-web library citations. On `revise`/`rebuild`, skip queueing when the set of supported `(claim_text, evidence_hash)` pairs did not change from the parent (so restyles and visual reviews do not re-distill).
2. **Input at scale.** Retrieve the top-K existing key results (25 `kept` + 15 `removed`) by hybrid search over the origin's text. Filter to `knowledge_base_id` and overlapping `source_id`s *before* applying the limit.
3. **Model writes (Analysis job).** Write at most 5 items. Each item is either a new sentence with its passage numbers, or `same_as: <id>` with extra passages for an existing kept result. Prompt for findings about the subject (numbers, definitions, conclusions, naming study or condition so conflicting studies coexist cleanly). Leave out process talk, hedges, restated questions and `[?]` model knowledge.
4. **Strict Checker gate.** Check every new or merged candidate against its cited passages using `ClaimChecks.check_claim_records()` (`claim_checks.py:242-366`). Require **raw `SentenceVerdict.verdict == "supported"`** (reject `inference`, `partly`, `not`, `unsourced`, `nonfactual`, `unchecked`) AND at least one usable library citation. Do **not** use `checks.verdict()` (`checks.py:38-40`), which maps `inference` and `nonfactual` to `"supported"`. Drop anything else; never show unchecked items.
5. **Publish and Undo.** Stage all writes, then commit in one transaction. The answer shows `"✓ 2 key results kept · Undo"`. Undo marks only key results with `created_origin_kind/id == origin` as `removed` and deletes only `key_result_evidence` rows added by that origin. Example: if Origin 1 created Result 1 and Origin 2 added `same_as` evidence to Result 1 plus a new Result 2, clicking Undo on Origin 2 removes Result 2 and drops Origin 2's extra evidence, leaving Result 1 kept.
6. **Failures.** Missing Analysis model → `needs_model` ("Key results need an Analysis model" banner). Error → `failed` with Retry.

**Eligibility (one general rule).**

- A key result is usable when at least one origin that checked it still has **all** of that origin's evidence usable.
- Evidence is usable when its source is live (not in Trash) and in scope, its `block_id` is still eligible (`evidence_eligible()`), and the source's original is unchanged (same `asset_id` for file sources, or same `content_hash` for text/note sources).
- Example: re-reading a PDF with Docling keeps its key results; editing a note drops key results from the old text; unticking Paper B hides a comparison claim that needed both Paper A and Paper B.

**Retrieval, Trash and export.**

- Index kept key results in `key_results_fts` and `key_result_vectors_<dim>` (mirroring `paper_vectors_<dim>` in `vector_index.py:210-269`). Purge both in `TrashService._delete_batch` (`trash.py:409-412`).
- In Ask and Studio, a retrieved usable key result adopts its usable backing `Evidence` passages into the pool. Its sentence goes only into a non-citable orientation block (like `Notes.knowledge` in `outputs.py:582-584`) pointing to those `[n]` numbers—never into `Evidence.text`, so answers and outputs always cite the original passages.
- Mirror kept key results to `Libraries/<library>/Key results.md` in `library_folders.py` (`WATCHED_MODELS`) and include `keyResults` in the library JSON workbook export (`App.tsx:342-386`).

**Legacy saved answers (`KnowledgeUnit`).** Remove the "Save as knowledge" button. Keep
`knowledge_units` and `knowledge_unit_revisions` tables intact so `ArtifactUnitBinding` checks
(`artifact_read.py:94-113`) keep passing. Also keep reading `scope.unit_ids` as notes when
rebuilding an old output whose stored scope pinned saved answers. Only new selection of saved
answers goes away, because the Sources tree no longer offers them (show a temporary read-only link
only while unmigrated trusted units exist). When an Analysis model is ready, run a one-time job to
distill each `status == "trusted"` unit (`origin_kind = "saved_answer"`).

## 4. Screen

```
┌ Sources ─────────┬ Middle ─────────────────────────┬ Studio ──────────┐
│ 🔍 Filter   +Sec │ chat | open output | source     │ [Report][Brief…] │
│ ☑ All (12)       │                                 │ [Slides][Cards…] │
│ ▾ Methods     ⋯  │                                 │ ──────────────── │
│   ☑ Paper A   ⋯  │                                 │ ▣ Slides         │
│ ▾ Key results(14)│ ┌ Ask your sources… ─────────┐  │   Writing 3 of 8 │
│ + Add source     │ └────────────────────────────┘  │ ▣ Briefing    ✓  │
└──────────────────┴─────────────────────────────────┴──────────────────┘
```

**Sources (left column).**

- One tree replaces both [LibraryContents](../apps/desktop/src/contents/LibraryContents.tsx) and [SourceScopeTree](../apps/desktop/src/outputs/SourceScopeTree.tsx).
- **Checkboxes control scope only** (decoupled from `LibraryContents.tsx:63,174,235-268` bulk selection). Clicking a source title opens its Source summary in the middle without changing any tick. Dragging a row handle or opening its `⋯` menu acts on that single row (Rename, Move to…, Take out of section, Move to Trash via `trash.ts`). Ticking a section ticks its sources.
- **Shared selection.** Store per library as `{ mode: "all" } | { mode: "selected", sourceIds: string[] }`. Adding a source while in `mode: "all"` includes it automatically. When `mode: "selected"` has zero sources, disable Send and Studio tiles with an inline hint ("Select at least one source") instead of sending `[]` to Ask (`ask_turns.py:642-652` treats `[]` as all sources).
- Ask turns always send the resolved source IDs (`[]` only for `mode: "all"`) and `focusChapterId: null` (preventing old section sessions from silently narrowing scope at `ask_turns.py:667-689`). Switching Ask history never overwrites the library's ticks.
- **`+ Add source`** includes Document, Photo, Web page, Recording and **Note or pasted text** (`onAdd("note")`).
- **Key results (N)** sits below the tree (phase 3a). Each row shows its source chips. Clicking opens `EvidencePanel` with its passages and origin ("from your question on 20 Nov"), whitelisted via `data-opens-evidence` (`EvidencePanel.tsx:20`). Its `⋯` menu has Remove.

**Middle column.** Switches between three views while keeping Chat mounted (hidden via CSS/state so drafts and scroll position are kept). Leaving a dirty `EditPane` asks first.

- **Chat** ([SessionWorkspace](../apps/desktop/src/pages/SessionWorkspace.tsx)):
  - Header holds the title, a compact **Brief** popover (Ask's brief, carrying context across turns), and a menu: New chat, History (search/pin/archived/restore), Rename, Export Markdown, Archive. Drop Home-only "File" and drop "Make a report" on research answers.
  - Below each answer, `"N sources"` expands an inline source list (clicking any opens `EvidencePanel`). When `traces.on`, a `"Trace"` footer button opens a slide-over.
  - Inline `[n]` buttons in `AnswerBody.tsx` show a hover popover with `sourceTitle`, `locator` and clipped `quote` (shared by Chat and all output readers). Clicking still opens `EvidencePanel`.
  - Empty chat shows library title, question, description and source counts in phase 2a. In phase 2b, a new `library_overviews` table + Analysis job (`knowledge_base_id` PK, `summary`, `suggested_questions_json`, `source_fingerprint`, `method`, `error`, `updated_at`) summarizes all live sources (from `SourceDigest.overview`, `SourcePaper.abstract` or leading snippet, so notes, web pages and recordings work too) with up to 3 clickable suggested questions. Do not reuse `TopicSynthesis`, which requires `TopicNode` FK (`models.py:958-960`) and filed paper abstracts (`library_topics.py:324-337`).
- **Open output** (with Back to chat):
  - Reader chosen by `shape`: ReportView, SlidesView, or the new cards, map or audio reader.
  - Below the reader: folded change history and the **Refine** box (`ArtifactConversation` turn with `targetLineageId`). On old multi-output conversations, turns that saved another lineage show a small output badge.
  - Refining keeps the output's own stored scope by default (delete `scopeWrites` in `artifactConversations.ts:188-236`). Example: if you built a Briefing doc from 3 sources and later tick 5 sources in the library, a small bar above Refine shows `"Using this output's 3 sources · Update to current 5"`, updating scope only when clicked.
  - `⋯` menu: Versions, Outline (if `supports_outline`), Edit, Check again, Style (if `supports_design`), Export, and Move to Trash (phase 2b).
- **Source summary** (with Back to chat):
  - Shows full `SourceDigest` (`overview`, `keyPoints` with clickable citations, clickable `terms` and `openQuestions` that pre-fill or send to Chat) beside `SourcePaper` metadata and Open full page.

**Studio (right column).**

- Top: neutral tile grid from `/api/output-types`, each with a pencil button for its options popover.
- Bottom: output cards (built, running and failed, newest first) from artifacts and conversation summaries. Each card shows title, type, version or live stage label (e.g. `"Writing slides · 3 of 8"`), or error with Retry. Clicking opens it in the middle.

**Window widths.** Update `App.tsx:107` to auto-collapse the outer app sidebar whenever `view === "base"`. Sources and Studio resize and collapse: below `1080px` Studio collapses to an icon rail; below `840px` Sources collapses too; below `1200px` opening wide readers (`slides`, `map`) auto-collapses Sources.

**Removed (phase 2a).** Remove along with their CSS and tests: Ask/Contents/Outputs tabs,
`ArtifactWorkspaceLayout`, Outputs conversations list, Outputs Brief/Approach panels and Details
drawer, preview and version dropdowns, and Ask's right-hand `ContextInspector`. Saved navigation
modes `contents`, `ask` and `outputs` all open the library screen. Home Ask is unchanged.

## 5. New shapes

Each shape adds its grammar to [STUDIO_FORMAT.md](STUDIO_FORMAT.md), a `ShapeSpec` row, a reader, a
`check_text` projection and exports.

**Cards (phase 4).** `pipeline = "direct"`, `unit_label = "card"`, exports = Markdown + PDF print.

```markdown
<!-- cards: flashcards; design: v1 -->
## What does factor T do in progenitors?
It steers them toward the red-cell branch [1].

## (quiz) Which branch grows when T is removed?
- [ ] Red-cell
- [x] White-cell
Why: removing T shifted cells toward the white-cell lineage [1].
```

- `format_check` requires non-empty Q+A for flashcards, and exactly one `[x]` option plus a `Why:` line for quiz cards.
- `check_text` projects a flashcard as `"<question> <answer>"` and a quiz card as `"<question> Answer: <checked option>. <Why>"`. Unchecked distractors are excluded so `ClaimChecks` never flags wrong choices as unsupported.
- Reader: flashcards flip; quiz scores choices and shows the explanation with clickable `[n]` citations after answering.

**Mind map (phase 5).** `pipeline = "direct"`, `unit_label = "node"`, exports = Markdown + PDF print.

- Grammar: a nested Markdown list (`- Root` / `  - Branch [1]` / `    - Leaf [2]`), one node per line with optional citations.
- `split` yields each node with its ancestor path; `check_text` projects `"<Root › Branch › Node>"` so leaf claims keep parent context when checked.
- Reader: `d3-hierarchy` tree drawn with Gunther's monochrome node cards. Clicking a branch node (or chevron) expands or collapses it. A hover/focus Ask button on each node switches Middle to Chat and sends `"Tell me about <Root › Branch › Node>"` with the current selection. Clicking `[n]` opens `EvidencePanel`.

**Audio overview (phase 6).** `pipeline = "direct"`, `unit_label = "line"`, exports = Markdown script + WAV audio.

- Grammar: dialogue lines `A: ... [1]` and `B: ... [2]`. Citations stay in the script and are stripped before synthesis.
- `check_text` strips speaker prefixes and checks lines in batches (not one LLM call per spoken line).
- **Audio storage (not `SpeechClip`).** Do not use `SpeechClip` (`models.py:620-622` has a `NOT NULL` FK to `session_messages`). Synthesize each line with `TtsProvider.synthesize()` (`tts_providers.py:59`) using the two chosen voices, measure each WAV with `wav_seconds()` (`tts_providers.py:215`), record `timings: [{ line, speaker, start, end }]`, join with `join_wav()`, and save the combined WAV via `AssetService.preserve_bytes` as an immutable `Asset` linked through `ArtifactAsset` (`models.py:390-397`). Store `{ audioAssetId, voices, timings }` in provenance. Streaming (`/api/assets/{id}`) and ref-counted Trash cleanup then work automatically.
- If no TTS provider is configured or synthesis fails, still save and open the verified script with an inline banner ("Set up Read aloud in Settings to generate audio" / "Retry audio").
- Reader: audio player beside the script. The active line highlights from `timings`, clicking a line seeks, and `[n]` opens `EvidencePanel`.

## 6. Phases

The builder may split a phase into batches. Each batch keeps every test green and is reviewed on
its own. Codex builds; Claude and the checker review each diff.

### Phase 1b — One way to build
**Adds:**
- `kind="build"` turn in `artifact_turns.py` with `BuildTurnInput`, `targetLineageId`, stored `build` payload/fingerprint, `strict=True` scope check, checkpoint-preserving Retry, and `outputType`/`shape`/`lineageId`/`lastTurn` on `ArtifactConversationSummaryOut`.
- Thin `POST .../outputs/build/stream` wrapper keeping old `kind`+`style` mapping and `earlier_output` replay.
- Conversation-keyed runs and Activity entries (`output-conv:<conversation_id>`, lineage fallback for unattached legacy calls), conversation-scoped Follow/Stop routes, and `ActivityItem.open` output fields.
- One shared 12-worker Studio pool in `studio/requests.py` covering `warm_map` and `studio/material.py:135` (with nested-call inline fallback and per-call worker caps kept intact).
- Real stage labels in `Activity` (no percent) and `ShapeSpec.unit_label` in `studio/verification.py`.

**Removes:**
- Direct `revise_output` (`output_commands.py:267-403`) and dead `"section"`/`progress` code (`artifact_api_common.py:79-81,107-118`).

**Tests (no live model calls):**
- Fake `ModelGateway` tests for `kind="build"` turns, Retry resuming failed builds from checkpoints, `targetLineageId` editing the right lineage in a multi-output conversation, parallel builds sharing the 12-worker pool without 409, stage-label Activity updates, and legacy request replays.

### Phase 2a — Core library screen
**Adds:**
- 3-column `Sources · Middle · Studio` layout with auto-collapsed outer sidebar (`App.tsx:107`) and narrow breakpoints (`1200px`, `1080px`, `840px`).
- Unified Sources tree with decoupled scope ticks (`{ mode: "all" } | { mode: "selected", sourceIds }`), empty-selection guard, single-row drag and `⋯` menu (including Move to Trash), and `+ Add source → Note or pasted text`.
- Studio tile grid from `/api/output-types`, pencil options popover, and output cards (live stage labels and failed Retry cards).
- Middle view switcher keeping Chat mounted: Chat header Brief popover + updated menu, inline `"N sources"`, Trace slide-over, Open output with Refine box + scope-mismatch bar, and Source summary.
- Updated local Chrome journey driving the new screen.

**Removes:**
- Ask/Contents/Outputs tabs, `ArtifactWorkspaceLayout`, Outputs conversations list, Outputs Brief/Approach panels, Details drawer, preview/version dropdowns, Ask's right-hand `ContextInspector`, `"Make a report"` button, `scopeWrites` queue, and their CSS and tests. (Net desktop LOC must drop.)

**Tests (no live model calls):**
- Desktop Vitest tests for shared selection (all/empty/section/Ask `focusChapterId: null`), decoupled drag/click vs ticks, Middle switching preserving Chat draft + dirty `EditPane` guard, Refine scope bar, and Retry card; local Chrome journey passes.

### Phase 2b — Library overview, parity polish and Output Trash
**Adds:**
- `library_overviews` migration and Analysis job (short summary + up to 3 suggested questions from all live source kinds), shown in empty Chat.
- Lightweight `[n]` hover popover in `AnswerBody.tsx` (shared across Chat and all output readers).
- Full `SourceDigest` (`overview`, `keyPoints` with citations, clickable `terms` and `openQuestions`) in Source summary.
- Output Trash through `TrashService`: nullable `trashed_at`/`trash_batch_id` on `Artifact` and `ArtifactConversation`, `TrashItemKind = "output"`, lineage-level batching, active-run refusal, `trashed_at IS NULL` filtering, leaf-first purge in `_delete_batch` (`trash.py:463-474`) with ref-counted `ArtifactAsset` cleanup, and `⋯ → Move to Trash` + Trash page support.

**Removes:**
- Nothing major (replaces Phase 2a empty-chat fallback when an overview exists).

**Tests (no live model calls):**
- Backend tests with fake Analysis model for `library_overviews` (PDF, note, recording, empty library) and Output Trash (trash, refuse during active run, restore, delete forever leaf-first, asset ref-count); desktop tests for hover popover, full Source summary and Output Trash; Chrome journey passes.

### Phase 3a — Key results core and migration
**Adds:**
- Migration adding `key_results`, `key_result_evidence` and `key_result_jobs`.
- Serialized per-library distiller worker in the AI lane with strict `ClaimChecks` gate (`SentenceVerdict.verdict == "supported"` only).
- Triggers for Ask answers and saved Outputs (ignoring `@home` until filed, `asking`, web citations and text-unchanged restyles).
- `"✓ N key results kept · Undo"` with merge-safe Undo (only removes results created by that origin and deletes that origin's evidence links).
- Sources `"Key results (N)"` group with `data-opens-evidence` inspection and Remove.
- One-time migration distilling `status == "trusted"` `KnowledgeUnit` rows.

**Removes:**
- "Save as knowledge" button and old saved-answer checkboxes from Sources tree (keeping `knowledge_units`/`knowledge_unit_revisions` rows intact for `ArtifactUnitBinding`).

**Tests (no live model calls):**
- Backend tests with fake Analysis and Checker models: `inference`/`nonfactual` rejected, `same_as` merge + Undo leaves prior result intact, removed results stay suppressed, `@home` queued only after filing, restyle skipped, trusted `KnowledgeUnit` migration keeps old artifact seals valid; desktop tests for Key results group, Undo and Remove.

### Phase 3b — Key results retrieval and exports
**Adds:**
- `key_results_fts` and `key_result_vectors_<dim>` indexes with `TrashService._delete_batch` cleanup.
- Two-line eligibility rule (§3.3) checking source scope/liveness, `evidence_eligible()`, and `asset_id`/`content_hash`.
- Ask and Studio retrieval adopting usable backing `Evidence` passages plus non-citable orientation notes.
- Hybrid top-K (25 kept + 15 removed) dedupe retrieval inside the distiller.
- `Libraries/<library>/Key results.md` in `library_folders.py` and `keyResults` in workbook JSON export.

**Removes:**
- Nothing (keep `KnowledgeUnit` note injection for old saved output scopes that list `unit_ids`; new selection of saved answers was already removed in Phase 3a).

**Tests (no live model calls):**
- Backend tests: Docling re-read keeps results, note edit drops them, unticking one source of a two-source claim hides it while a single-source origin keeps it, Trash purges FTS/vectors, Ask/Studio cite original `[n]` passages only, old output scopes with `unit_ids` still rebuild with those notes, and `Key results.md` + JSON export match kept results.

### Phase 4 — Cards (`flashcards` and `quiz`)
**Adds:**
- `cards` `ShapeSpec` (`direct` pipeline, format check, `check_text` ignoring quiz distractors).
- `flashcards` and `quiz` `OutputType` rows with recipes and examples; grammar in `STUDIO_FORMAT.md`.
- Cards reader (flip cards, scored quiz revealing citations after answering) and Markdown + PDF print exports.

**Removes:** Nothing.

**Tests (no live model calls):**
- Shared split/join fixture tests (backend + desktop), fake-gateway build + Checker test confirming distractors are not flagged, Refine turn on cards, and desktop reader tests.

### Phase 5 — Mind map (`mind_map`)
**Adds:**
- `map` `ShapeSpec` (`direct` pipeline, nested-list format check, ancestor-path `check_text`).
- `mind_map` `OutputType` row with recipe and example; grammar in `STUDIO_FORMAT.md`.
- Monochrome `d3-hierarchy` reader (click branch to expand/collapse, hover Ask icon sending `"Tell me about <Root › Branch › Node>"`, `[n]` opens `EvidencePanel`) and Markdown + PDF print exports.

**Removes:** Nothing.

**Tests (no live model calls):**
- Shared split/join fixture tests, fake-gateway build + ancestor-context Checker test, and desktop tests for expand/collapse vs Ask vs citation click.

### Phase 6 — Audio overview (`audio`)
**Adds:**
- `audio` `ShapeSpec` (`direct` pipeline, `A:`/`B:` format check, batched `check_text`).
- `audio` `OutputType` row with recipe, example and `voices` option; grammar in `STUDIO_FORMAT.md`.
- Synthesis via `TtsProvider.synthesize()`, `wav_seconds()` timings and `join_wav()`, saved as an immutable `Asset` via `ArtifactAsset` with `{ audioAssetId, voices, timings }` in provenance.
- Audio player + synchronized script reader, script-only fallback banner when TTS is unconfigured/fails, and Markdown + WAV exports.

**Removes:** Nothing.

**Tests (no live model calls):**
- Backend tests with fake `ModelGateway` and fake `TtsProvider` returning valid short WAV bytes: citations stripped before TTS, `timings` match durations, `ArtifactAsset` streams and cleans up on Trash, unconfigured TTS still saves verified script; desktop player highlight/seek tests.

## 7. Done when

Each phase is done when all of these hold:

- Backend, desktop and Rust tests pass. The local Chrome journey passes in the loopback namespace (see [Developer](DEVELOPER.md)). CI is green after push.
- Model and TTS behaviour are tested with fakes and recorded replies. Run no live model calls, no app builds and no worktrees.
- Saved data from before the phase opens and verifies cleanly: old reports, decks, multi-output conversations and saved answers.
- Shipped docs ([Outputs](OUTPUTS.md), [Ask](ASK.md), [Library](LIBRARY.md), [Studio format](STUDIO_FORMAT.md)) describe what shipped, and the completed phase is deleted from this file.
- Production line count is reported honestly (including new files). Phase 2a removes more desktop code than it adds.
