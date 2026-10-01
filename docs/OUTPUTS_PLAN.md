# Outputs rebuild: implementation plan

> **Status 2026-10-01: all seven phases are built (uncommitted) and each has a progress log at the end of this file.** How Outputs works now is described in `docs/OUTPUTS.md`; this file keeps the plan and the record of what was built, changed and left unverified.

Agreed with the user on 2026-10-01. Written by Claude Opus for the agent that implements it; Opus reviews each phase afterwards. Line numbers are from 2026-10-01 and will drift: grep for the names.

## 0. How to work

- Edit directly on `main` in `/root/git/Gunther`. Never create a git worktree.
- Don't commit or push unless the user asks. If asked, make one commit per phase, named after the phase.
- Don't build the app (no `tauri build`, sidecar or `.deb`). Run only quick, targeted checks:
  - Backend: `cd apps/backend && .venv/bin/python -m pytest tests/<file>.py -q` and `.venv/bin/ruff check gunther tests`. `uv` is not on PATH; use the venv.
  - Desktop: `cd apps/desktop && npx vitest run <paths>` and `npm run typecheck`.
  - Rust (Phase 6 only): `cd apps/desktop/src-tauri && ~/.cargo/bin/cargo check`. If it can't run, say so.
- **Model output or error.** If no model is set up or a model call fails, show the error and a Retry. Never fill in with template or heuristic text, and never save a half-built output.
- Use established libraries (react-markdown, CodeMirror 6, reveal.js). Don't hand-roll a slide engine, Markdown renderer or PDF writer.
- UI: white background, black text, black primary buttons, colour only for identity and status. Reuse the tokens in `apps/desktop/src/design` (see `docs/DESIGN_SYSTEM.md`) and existing classes before adding CSS.
- User-facing text: short and plain ("Saved as knowledge", not "Proposal accepted").
- Refactoring shared Ask code must not change Ask: `tests/test_agent.py` and the Ask tests in `tests/test_api.py` must still pass.
- If part of this plan turns out wrong or impossible, stop and tell the user instead of inventing a workaround (especially native macOS code).
- Work phase by phase, in order. After each phase, run its checks and write a few lines on what changed and what you couldn't verify.

## 1. Decided with the user (don't reopen)

1. Saving an Ask answer as knowledge is the only review step. There is no Inbox review. A saved answer can be removed later ("Remove from knowledge").
2. Outputs are built by agents from what the user picks: the whole library, or a hand-picked mix of sources, saved knowledge and Ask discussions. The user can add an optional brief.
3. Two kinds: **report** and **slides**. Report styles: Overview (default), Field guide, Teaching path, Decision brief. Audience: Scientist, Student, Collaborator.
4. The agents are Planner → Researcher → Writer → Checker. Slides use a slide-writing mode of the Writer (the "Slide designer").
5. A build runs straight through, with no pause for outline approval. The outline is shown, and the user can edit it and rebuild.
6. Outputs are read in Gunther. Reports show on the page; slides show in a reveal.js viewer with thumbnails, speaker notes and Present (fills the app's window; changed from the screen's full screen after the first trial).
7. Editing in Gunther works two ways: type directly (CodeMirror beside a live preview), or tell the agents what to change. Every save is a new version. Typed text is marked "Not re-checked" until the Checker runs again.
8. Export is PDF only, plus the existing Markdown copy and download. No Word or PPTX in v1.
9. Slides library: reveal.js 6 with `@revealjs/react` (both MIT). Rejected: PPTist (Vue app, AGPL), open-slide (slides are compiled code), DiceUI PPTX (too young), Marp React (inactive).

## 2. Calls the planner made (the user may still override)

- "Whole library" means every live source in the library plus every saved knowledge unit. Ask discussions are used only when picked.
- Saved knowledge and discussions guide the build but aren't cited themselves. The passages they cite are loaded into the output's source pool first, and their text is shown to the Planner and Writer as notes. The Writer cites passages only, so the citation contract stays the same as Ask's.
- Outputs search the library only, not the web, in v1.
- Existing pending and held proposals become saved knowledge (Phase 1 migration), because proposing an answer was already the user's decision.
- A new "Outputs" job is added in Settings → Models. When it isn't set, Outputs use Ask's model.
- PDF goes through the system print dialog ("Save as PDF"). On macOS `window.print()` does nothing in the app's webview, so a one-line Tauri command calls Tauri's own `WebviewWindow::print()`.
- "Check again" doesn't create a new version; check results are stored beside the version.
- Only one build or revision runs per library at a time.

## 3. What exists today

| Piece | Where |
|---|---|
| Outputs page (old) | `StudioView` in `apps/desktop/src/pages/KnowledgeBaseWorkspace.tsx` (~342–532), rendered for `mode === "outputs"` (~569); test `src/pages/StudioView.test.tsx` |
| Old builder (no model; pastes units under headings) | `KnowledgeService.create_artifact` and `_compose_artifact_content` in `apps/backend/gunther/service.py` |
| Integrity of saved versions | `_verified_artifact_payload` (service.py) and `scripts/backup_gunther.py` (~299) |
| Tables | `Artifact`, `ArtifactUnitBinding` in `gunther/models.py` (~589) |
| Migrations | `gunther/migrations.py`: `MIGRATIONS` (latest 18) and helper `add_column_if_missing` |
| Save as knowledge | `create_knowledge_proposal` (service.py ~2303); already accepts at once (see §4) |
| Proposal status changes | `update_knowledge_proposal`: accepted → unit `trusted`, rejected → unit `deprecated` |
| Units for accepted proposals | loop at the end of `seed_if_empty` (service.py ~3366). It runs only when `seed_demo` is on (`main.py` ~275), so not on a normal start |
| Inbox suggestions | proposals block in `list_inbox` (service.py ~999–1022); `InboxPage.tsx` (~62, 69, 229, 303); `items/SuggestionItem.tsx`; `items/itemRef.ts` (~19); `items/ItemPage.tsx` (~12, 135); `KnowledgeBaseOut.pending_proposal_count` |
| Ask's save button | `ConversationMessage` (~281) and `promoteMessage` (~924) in `src/pages/SessionWorkspace.tsx`; "Knowledge units" list in `ContextInspector` (~443) |
| Ask agent | `gunther/agent.py`: `AskAgent`, `Evidence`, `Tool`, `Toolbox`, `check_citations`, `renumber_citations`, `_relevant`, `_mark_unsourced`, `_check_leads`; design in `docs/ASK_AGENT.md` |
| Library search tool | closure `search_library` inside `create_session_turn` (service.py ~1969–2084) |
| Saved citations → evidence | `conversation_pool(messages)` (service.py ~228) |
| Streaming | `gunther/answer_runs.py` (`AnswerRuns`); `api.py` `_sse` and `/sessions/{id}/messages/stream` (~1082–1161); desktop `src/sse.ts`, `src/api.ts` (~700–760), `src/pages/liveAnswer.ts` |
| Rendering with citations | `src/pages/AnswerBody.tsx` (`AnswerBody`, `AgentSteps`, `LiveAnswer`, `withoutMarks`, `citationNumbers`); `src/components/evidence/EvidencePanel.tsx` |
| Markdown editor | `src/components/markdown/MarkdownEditor.tsx`, `CodeMirrorMarkdown.tsx` |
| Model jobs | `ROLES` in `gunther/model_registry.py` (~82); role id union in `packages/contracts/src/index.ts` (~385); `ModelGateway.for_role` in `gunther/llm.py` |
| Fake model for tests | `apps/backend/tests/fake_models.py` (`FakeProvider`); examples in `tests/test_agent.py` |
| Native detection | `"__TAURI_INTERNALS__" in window` (see `src/log.ts`); `invoke` from `@tauri-apps/api/core` |

## 4. Already done, not committed (keep it)

- `create_knowledge_proposal` sets the proposal to `accepted` and creates the trusted unit straight away.
- Ask button labels are "Save as knowledge", "Saving…" and "Saved as knowledge".
- The Outputs empty-state text mentions "Save as knowledge".
- `tests/test_api.py`: two tests were adjusted. The Inbox test reopens the proposal to `pending`; Phase 1 changes it again.
- The full backend suite passed (384 passed, 1 skipped).

## Phase 1: Saving knowledge without review

Goal: one click saves, one click removes, and Inbox no longer shows knowledge suggestions.

**Backend**
1. `create_knowledge_proposal`: if the message already has a proposal that isn't `accepted` (for example, it was removed), accept it again through the same path as `update_knowledge_proposal`: unit back to `trusted`, plus a `Revision` row. An accepted one is returned unchanged.
2. Move the "ensure a unit for every accepted proposal" loop out of `seed_if_empty` into `ensure_saved_knowledge_units()`. Call it on every start in the `main.py` lifespan, before the demo seed. `seed_if_empty` keeps calling it.
3. Migration 19 `knowledge_saved_without_review`: `UPDATE knowledge_proposals SET status = 'accepted' WHERE status IN ('pending', 'held')`. The startup step from item 2 creates their units.
4. `list_inbox`: remove the proposals block. Remove `"knowledge_suggestion"` from `InboxItemType` and `"held"` from `InboxItemState` (only proposals used it), and from the query patterns in `api.py` (~898–902). Remove `proposal_id` and `proposal_status` from `InboxItemOut` if nothing else uses them.
5. Remove `pending_proposal_count` from `KnowledgeBaseOut`, the service and the contracts, unless grep finds a UI that still needs it.
6. Keep `PATCH /proposals/{id}`. "Remove from knowledge" sets status `rejected`, which makes the unit `deprecated`.

**Desktop**
1. `SessionWorkspace.tsx`:
   - Keep a map from message id to proposal (id, status) instead of the `proposalMessageIds` set.
   - Button states: "Save as knowledge" → "Saving…" → "Saved as knowledge", with a quiet "Remove" button beside it once saved.
   - `promoteMessage` toast: "Saved as knowledge." It currently says "Answer added to Inbox…" or "This answer already has a accepted knowledge proposal.", a bug left by the auto-accept. Remove the `gunther:proposal-created` event and its listeners.
   - Remove calls `knowledgeApi.updateProposal(id, { status: "rejected", reason: "Removed from knowledge" })`, shows "Removed from knowledge.", then refreshes the units.
   - The "Knowledge units" list in `ContextInspector` shows only `trusted` units.
2. Inbox: delete the suggestion branch in `InboxPage.tsx`, `items/SuggestionItem.tsx`, and the `"suggestion"` item type in `items/itemRef.ts` and `ItemPage.tsx`. Quick notes and sources stay exactly as they are. **Don't touch topic suggestions** (`TopicManager.tsx`, `library_topics.suggest`); they are a different feature with a similar name.
3. Update `packages/contracts/src/index.ts` to match the backend.

**Tests**
- Backend:
  - saving accepts at once;
  - saving a removed answer accepts it again;
  - removing deprecates the unit;
  - Inbox lists no proposals;
  - migration 19 flips pending and held (follow `tests/test_migrations.py`);
  - accepted proposals get units on a normal start.
- Desktop: update `InboxPage.test.tsx`, `ItemPage.test.tsx` and `SessionWorkspace.test.tsx`, and add Save → Saved → Remove.

**Done when** Save and Remove work in Ask, Inbox has no knowledge items, and the targeted tests, ruff and typecheck pass.

## Phase 2: Backend, outputs built by agents

### 2.1 Model job
- Add `Role("outputs", "Outputs", "Builds reports and slides from what you choose.", "high")` to `ROLES`, and `"outputs"` to the contracts role union. Settings → Models should draw roles from the API; grep for hard-coded role lists and add the new role to any you find.
- The model is `for_role("outputs") or for_role("ask")`. If neither is set, fail with "Outputs need a model. Set one up under Settings → Models." and save nothing.

### 2.2 Data (migration 20 `agent_outputs`)
Extend `artifacts`. Legacy rows stay valid; use `add_column_if_missing`.

| Column | Definition | Legacy rows | New rows |
|---|---|---|---|
| `kind` | `VARCHAR(16) NOT NULL DEFAULT 'report'` | `report` | `report` or `slides` |
| `style` | `VARCHAR(40)` (nullable) | set to the old `format` | `overview`, `field_guide`, `teaching_path`, `decision_brief`; NULL for slides |
| `brief` | `TEXT NOT NULL DEFAULT ''` | `''` | the brief |
| `origin` | `VARCHAR(16) NOT NULL DEFAULT 'legacy'` | `legacy` | `build`, `rebuild`, `edit`, `revise` |
| `scope_json` | `TEXT NOT NULL DEFAULT '{}'` | `{}` | the scope as asked |
| `inputs_json` | `TEXT NOT NULL DEFAULT '{}'` | `{}` | what was used (below) |
| `outline_json` | `TEXT NOT NULL DEFAULT '[]'` | `[]` | `[{heading, goal}]` |
| `citations_json` | `TEXT NOT NULL DEFAULT '[]'` | `[]` | citation list in reading order |

- New rows write the kind into the old `format` column. Widen `ArtifactFormat` to also allow `report` and `slides`.
- `inputs_json`: `{"sources": [{"id", "title", "revisionId"}], "units": [{"id", "revisionId"}], "sessions": [{"id", "title", "messageIds": []}]}`. Units used are also pinned with `ArtifactUnitBinding`, as today.
- Citations store copies (quote, title, locator, ids) with no foreign keys to sources. A deleted source never breaks an output; the evidence panel just says the source is gone.
- New table `artifact_checks`, append-only, where the latest row per artifact wins. Columns: `id`, `artifact_id` (FK to `artifacts.id`, `ON DELETE CASCADE`, indexed), `content_hash`, `checks_json`, `created_at`.
  - `checks_json`: `{"sections": [{"hash", "issues": [{"claim", "verdict", "note"}]}]}`.
  - Check that deleting a library (`trash._delete_library_records`) and the backup tool handle the table.
- Integrity: new rows use provenance `schema_version: 2`, `generator: "gunther.output-agents.v1"` and `accepted_only: false`. Provenance also records `kind`, `style`, `audience`, `brief`, `origin` and `model {ref, label, effort}`.
  - Manifest hash v2: sha256 of canonical JSON (same `sort_keys` and separators as today) of `{kind, style, audience, title, brief, origin, contentHash, citations, outline, scope, inputs, acceptedUnitIds, revisionSnapshot, provenance}`.
  - `_verified_artifact_payload` and `scripts/backup_gunther.py` branch on `schema_version`. Version 1 keeps today's checks unchanged. Version 2 checks the content hash, the v2 manifest, and the bindings of the units listed.
  - Add a v2 case to `tests/test_backup_tool.py`.
- Move the row-writing half of `create_artifact` into one helper used by build, rebuild, edit and revise. It covers the workspace check, `client_request_id` idempotency, lineage head and version number, hashes and bindings.

### 2.3 Shared pieces from Ask (no behaviour change)
- Move the `search_library` closure out of `create_session_turn` into a method that returns a `Tool` for given scoped source ids, with the optional topic block scope Ask uses. Ask passes exactly the values it uses now.
- Make `AskAgent._relevant`, `_mark_unsourced` and `_check_leads` usable from outside (public names, or module functions that take the gateway) without changing what they do.

### 2.4 The agents: `gunther/outputs.py` (new)
Build on `Evidence`, `Tool`, `Toolbox`, `check_citations`, `renumber_citations` and `UNSOURCED` from `agent.py`, and on `ModelGateway.complete` / `complete_json`.

**Pool.** Each build has one pool, and its numbers are never reused (the same `adopt` idea as in `AskAgent.run`).
- Seed it first with the passages cited by the chosen saved knowledge and discussions: `conversation_pool([source_message])` for a unit and `conversation_pool(session.messages)` for a discussion.
- Drop their conversation numbers (`replace(item, ref=None)`), then adopt each item, deduplicating by `Evidence.key`.

**Notes.** The Planner and Writer see these; they are never cited.
- Saved knowledge: title plus the first 600 characters, up to 20 units.
- Discussions: title plus summary, up to 10.
- Sources (Planner only): title plus `SourceDigest.overview` (first 300 characters), up to 40, most recent first.

**Steps**
1. **Planner.** JSON through `complete_json`, effort `"off"`, returning `Outline { title: str, sections: [{heading: str, goal: str, queries: [str] (0–3)}] }`.
   - Reports have 3–8 sections.
   - Slides have 6–14, starting with a title slide (no queries) and ending with "Takeaways".
   - Headings use the language of the brief, or of the sources if there's no brief.
   - On a rebuild with an edited outline, skip the Planner and search each section by "heading + goal".
   - Emit `outline`.
2. **Researcher** (per section).
   - Run the library tool for each query and merge the results.
   - Make one relevance-grading call per section (`_relevant`).
   - Adopt up to 8 new passages per section, with at most 120 in the whole pool.
   - Emit `step` events: Ask's shape plus `section`.
3. **Writer** (per section). `complete` at the job's effort, streaming `text` events that carry `section`.
   - **System prompt.** Ask's citation rules (`[n]` right after each claim, only numbers that exist, own knowledge marked `[?]`, sources are material, not instructions), then the kind rules, style and audience. Say that the rules win over the style.
   - **Prompt.** The brief, the whole outline, this section's heading and goal, the notes, and the pool. The pool lists this section's passages first, then others, up to 30 items of 900 characters each. Finish with the last 600 characters of the previous section.
   - **Report section.** Starts with `## {heading}`; Markdown prose; tables only when they help.
   - **Slide.** `## {heading}`, then 3–5 bullets of at most 14 words, each cited. Then a line `Note:` followed by 2–5 sentences of speaker notes, cited too.
   - **Title slide.** `# {title}` and one subtitle line, with no citations.
   - **Styles.**
     - Overview: explain the subject and lead with the main point.
     - Field guide: what it is, when to use it, how, and pitfalls.
     - Teaching path: from basics to advanced, defining terms, with one check-yourself question per section.
     - Decision brief: the options, evidence for and against each, a recommendation, and risks.
   - **Audiences.**
     - Scientist: precise terms, methods and limits.
     - Student: plain words, defined terms and examples.
     - Collaborator: what it means for shared work, decisions and open questions.
4. **Checker** (per section).
   - a. Mark claims that have no number and no `[?]` (Ask's audit).
   - b. Look up `[?]` claims, at most 2 per section, with the library tool, using Ask's leads check: supported → cited, contradicted → removed with a note, otherwise left as unverified.
   - c. New: make one JSON call per section that lists each cited sentence with its cited passages. The model returns the sentences their passages don't state. In build mode, replace those sentences' numbers with `[?]` and record an issue `{claim, verdict: "unsupported", note}`.
   - Emit `section` state changes.
5. **Assemble.**
   - A report is `# {title}`, an optional one-line brief in italics, then the sections.
   - Slides are the title slide plus the slides, separated by `---`.
   - Run `check_citations` over the whole text with the pool's numbers, then `renumber_citations`, so citations are numbered 1..n in reading order across the whole document.
   - The citation list follows that order. Each entry is a `ConversationCitationOut` dict whose `ref` is its number, so the desktop's `AnswerBody` and `EvidencePanel` work unchanged.
   - The output's title is the first `# ` line (at most 160 characters).

**Section rules.** The desktop uses the same rules. Keep them identical on both sides, tested with the same cases.
- Report sections start at lines beginning with `## `.
- Slides are separated by lines that are exactly `---`, outside code fences.
- Speaker notes start at a line beginning with `Note:`.
- A section's hash is the sha256 of its text, trimmed, with `\n` line ends.

**Limits** live in a frozen dataclass like `agent.Limits`:
- sections: 8 (report), 14 (slides);
- queries per section: 3;
- new passages per section: 8;
- pool size: 120;
- `[?]` claims checked per section: 2.

**Failures.**
- A failed Planner or Writer call fails the build with that error, and nothing is saved.
- A failed grading, audit or check call is logged and skipped, as Ask does.

**Database use.** Read everything inside one session, as Ask does. Write the new version in a fresh `session_scope` after the agents finish, so no write lock is held while models run.

### 2.5 Service and API
Every method validates the workspace and the live library, as `create_artifact` does.

- **`build_output(base_id, workspace_id, payload, events)`**
  1. Resolve the model.
  2. Resolve the scope. Selected ids must belong to this library and be live; units must be `trusted`. Otherwise fail with 400.
  3. Seed the pool and notes, then run the agents.
  4. Save a version. Without `supersedesArtifactId` it starts a new lineage (origin `build`); with it, it becomes the next version (origin `rebuild`).
  5. Write the first `artifact_checks` row and return `ArtifactOut`.
- **`revise_output(base_id, workspace_id, artifact_id, payload, events)`**
  - The parent must be the lineage head (409 otherwise, as today).
  - The pool is the parent's citations as evidence numbered 1..n, plus new research on the instruction within the parent's `scope_json`.
  - The Writer gets the target section's current text (every section when none is chosen) and the instruction. The Checker runs in build mode on revised sections only.
  - Untouched sections keep their text. Renumber citations over the whole document at the end.
  - Origin `revise`.
- **`edit_output(base_id, workspace_id, artifact_id, payload)`**
  - The parent must be the head. The content is saved exactly as typed (at most 2.5 MB), and the citation list is copied from the parent.
  - Nothing is renumbered or rewritten; a number with no citation is just not linked.
  - Copy the parent's checks for sections whose hash didn't change into a new checks row.
  - Origin `edit`.
- **`check_output(base_id, workspace_id, artifact_id)`**
  - Run Checker steps a and c in report-only mode (it never changes the text) on sections that have no check for their current hash.
  - Append a checks row and return `ArtifactOut`.

**Output shapes**
- `ArtifactOut` gains `kind`, `style`, `brief`, `origin`, `outline`, `citations`, `scope`, input counts `{sources, units, sessions}` and `modelLabel`.
- It also gains `sections: [{index, heading, checked, issues: [{claim, verdict, note}]}]`. These are computed from the content and the latest checks row; `checked` means a check exists for the section's current hash.
- `ArtifactSummaryOut` gains `kind`, `style` and `origin`.

**API.** These go beside the artifact routes in `api.py`, and all take the `X-Gunther-Workspace-Id` header.
- `POST /knowledge-bases/{id}/outputs/build/stream` (SSE). Body `BuildOutputInput`: `{clientRequestId, kind, style?, audience, brief?, title?, scope: {mode: "library" | "selection", sourceIds, unitIds, sessionIds}, outline?: [{heading, goal}], supersedesArtifactId?}`.
- `POST /knowledge-bases/{id}/artifacts/{artifactId}/revise/stream` (SSE). Body `{clientRequestId, instruction, sectionIndex?}`.
- `GET /knowledge-bases/{id}/outputs/build/stream` follows a running build or revision (204 when there is none).
- `POST /knowledge-bases/{id}/outputs/build/stop` returns 204.
- `POST /knowledge-bases/{id}/artifacts/{artifactId}/edits` returns 201 with `ArtifactOut`.
- `POST /knowledge-bases/{id}/artifacts/{artifactId}/check` returns `ArtifactOut`.

**Runs** reuse `AnswerRuns` and `_sse` with the key `outputs:{knowledge_base_id}`. A second build in the same library gets 409 "Gunther is still building an output here." Leaving the page doesn't stop a build; Stop does, and nothing is saved.

**SSE events**
- `outline` `{title, sections}`
- `section` `{index, state: "researching" | "writing" | "checking" | "done"}`
- `step` (Ask's shape plus `section`)
- `text` `{section, text}`
- `done` (the saved `ArtifactOut`)
- `error` `{status, detail}`
- `stopped`

**Tests.** Put them in `tests/test_outputs.py` (new), using `FakeProvider` scripted replies as `tests/test_agent.py` does.
- Planner → research → write → check gives a report whose citations are renumbered in reading order and match the list.
- Slides content splits into the planned slides with their `Note:` blocks.
- A selection scope searches only the chosen sources; ids from another library get 400.
- Saved knowledge seeds the pool: its passages can be cited without a search.
- An unsupported citation becomes `[?]` and an issue is recorded.
- No model gives an error and no row; a Writer failure gives an error and no row.
- A rebuild with an edited outline skips the Planner and makes version n+1 in the same lineage.
- An edit keeps the text verbatim, copies checks for unchanged sections, and leaves changed ones unchecked.
- `check_output` never changes content.
- v2 integrity passes; tampering with the content or citations fails; legacy v1 rows still verify.
- The build stream ends with `done`, and a second concurrent build gets 409.

**Done when** these tests and the existing artifact and Ask tests pass, and ruff is clean.

## Phase 3: Desktop, the new Outputs page (reports)

**Files.** New folder `apps/desktop/src/outputs/`:
- `OutputsPage.tsx`, which replaces `StudioView`; render it from `KnowledgeBaseWorkspace.tsx`;
- `ScopePicker.tsx`;
- `liveOutput.ts` plus a test, modelled on `liveAnswer.ts`;
- `ReportView.tsx`;
- `sections.ts` plus a test, using the same rules as the backend;
- `outputs.css`.

The API functions go in `src/api.ts` next to the session stream functions; the types go in the contracts.

**Left column.** Keep today's two-column layout and look.
- "Use": Whole library | Choose…, which opens ScopePicker and then shows a summary such as "12 sources · 3 saved · 1 discussion".
- "Brief": an optional textarea, placeholder "What should it cover, and for whom?".
- "Make": Report | Slides.
- "Style" (reports only) and "Audience".
- The black Build button. With a version open it reads "Rebuild", and "New" in the history header starts a fresh lineage, as today.
- Output history below: kind and style labels, and an "Edited" or "Revised" tag from the origin.

**Right side while building**
- The outline appears first. Then each section fills in live: text with the marks hidden (`withoutMarks`), and `AgentSteps` under the section being worked on.
- A Stop button.
- On error: the message and Retry, which sends the same request.

**Right side for a finished report**
- A header line: kind · style · vN · date · model.
- The title.
- A collapsed "Outline" row. Its "Edit outline" opens an editable list of headings and goals with "Rebuild with this outline".
- The body through `AnswerBody` with `citationNumbers(citations)`; clicking a citation opens `EvidencePanel`.
- Per section, a quiet "Not re-checked" tag and the issues, when there are any.

**ScopePicker** is a dialog with three groups (Sources, Saved knowledge, Discussions), a filter box, checkboxes and "Select all" per group. Its data comes from `knowledgeApi.sources(baseId)`, `knowledgeUnits(baseId)` (trusted only) and `sessions(baseId)`. Reuse the classes of Ask's source-scope list where you can.

**Empty and other states**
- If the models list shows neither an Outputs nor an Ask model: "Outputs need a model. Set one up under Settings → Models." with a button to open Settings.
- A library with no sources and no saved knowledge says so and offers "Add sources".
- Returning to the page while a build runs follows it (`GET .../outputs/build/stream`).
- Legacy versions (origin `legacy`) render their Markdown through `AnswerBody` without citations.

**Tests**
- Replace `StudioView.test.tsx` with `OutputsPage.test.tsx`: the stream shows the outline, then the sections; an error shows Retry; history opens versions; the scope summary is right.
- Add `liveOutput.test.ts` and `sections.test.ts`.

## Phase 4: Slides viewer (reveal.js)

**Install.** `npm i -w @gunther/desktop reveal.js@^6 @revealjs/react`. The wrapper is 0.2.x and needs React ≥ 18. If it misbehaves with React 19, use reveal.js core directly: `new Reveal(element, config)`, `initialize()`, and `destroy()` on unmount.

**`slides.ts`** (plus a test) parses the deck Markdown into slides `{heading, body, notes}` using the rules in 2.4.

**`SlidesView.tsx`**
- `<Deck>` with one `<Slide>` per slide. Each slide body goes through `AnswerBody`, so citations behave as in reports.
- Config: `embedded: true, hash: false, history: false, keyboardCondition: "focused", width: 1280, height: 720, controls: true, progress: true, slideNumber: "c/t", transition: "fade"`.
- Remount (React `key`) when the version changes.

**Theme.**
- Import `reveal.js/reveal.css` and `reveal.js/theme/white.css`.
- Override fonts and colours under a wrapper class to match Gunther (white background, black text).
- The theme sets `.reveal-viewport` styles and `:root` variables; make sure none of it restyles the rest of the app.

**Thumbnails.** A strip of small slide renders (the same slide component, scaled with CSS) that moves the deck with `deck.slide(i)`. Reveal's overview mode is also acceptable; pick whichever looks cleaner.

**Notes.** Show the current slide's notes under the deck. Don't use reveal's speaker-notes popup window; popups don't work in the app.

**Present.**
- In the app, call `getCurrentWindow().setFullscreen(true)` from `@tauri-apps/api/window` and show a fixed, full-window deck. Esc leaves both.
- Add `core:window:allow-set-fullscreen` to `src-tauri/capabilities/default.json`.
- In the browser build, use `requestFullscreen()`.

**During a build**, show slides as a simple list as they're written, and switch to the deck when the build is done.

**Tests.** `slides.test.ts`, plus a render test that the deck has N slides and shows the current slide's notes (mock reveal if jsdom can't run it).

## Phase 5: Editing

- **Edit.** Opens `MarkdownEditor` (CodeMirror) beside a live preview of the draft (ReportView or SlidesView).
  - "Save as new version" sends `POST .../edits`; "Cancel" discards.
  - Only the latest version of a lineage can be edited; older ones show "Open latest".
- **"Ask Gunther to change…"** is an instruction box with "Which part": the whole output, or one section or slide. It submits to `revise/stream`, shows progress like a build, and opens the new version.
- **"Check again"** appears when any section is not re-checked. It sends `POST .../check`, then refreshes; issues show per section.

**Tests.** An edit saves a version and marks changed sections; a revision streams and opens the new version; a check clears "Not re-checked".

## Phase 6: PDF export

**Print layout.** Always mounted, hidden on screen: `<div id="print-root">`, rendered for the open version through a portal into `body`.
- `@media screen { #print-root { display: none } }`
- `@media print { body > :not(#print-root) { display: none } #print-root { display: block } }`
- **Report**: the document typeset for A4 (`@page { size: A4; margin: 18mm 16mm }`), with citations as superscript numbers and a "Sources" list at the end (title, locator, short quote).
- **Slides**: one slide per page (`@page { size: 13.333in 7.5in; margin: 0 }`), each slide with `break-after: page`. No notes in v1.
- Inject the `@page` rule for the kind that's open.

**"Export PDF" button**
- In the macOS app, call `invoke("print_output")`. Add this command to `src-tauri/src/lib.rs` and register it in `invoke_handler`:
  ```rust
  #[tauri::command]
  fn print_output(window: tauri::WebviewWindow) -> Result<(), String> {
      window.print().map_err(|error| error.to_string())
  }
  ```
  `WebviewWindow::print` exists in Tauri 2.11 and 2.12. On macOS (11 or later) it opens the print sheet through `printOperationWithPrintInfo`, and the sheet's PDF button saves the file.
- On Windows, Linux and in the browser, use `window.print()`.
- Wait for `document.fonts.ready` before printing.

**Verification.** This can't be checked on Linux beyond `cargo check`. Tell the user exactly what to try on the Mac: margins, page breaks, Chinese text, and slide pages. If WebKit ignores the `@page` margins, report it; don't write Objective-C.

## Phase 7: Remove the old builder and write the doc

- Remove `_compose_artifact_content`, `POST /knowledge-bases/{id}/artifacts`, `CreateArtifactInput`, `knowledgeApi.createArtifact`, the old StudioView code and its local-storage "generation attempt" helpers. Legacy versions stay readable.
- Write `docs/OUTPUTS.md` in the manner of `docs/ASK_AGENT.md`: scope, pool, agents, checks, versions, editing, export, limits and how failures show.
- Change the page intro to "Turn what you've gathered into a report or slides."

## Review checklist (Opus, after each phase)

- Ask is unchanged: its tests pass, and the extracted search tool returns the same evidence.
- There is no heuristic fallback text anywhere, and a failed build saves nothing.
- Every number in a saved output exists in its citation list, numbers follow reading order, and the evidence panel opens the right passage.
- v1 and v2 rows both verify in the service and in `backup_gunther.py verify`.
- No passage comes from outside the chosen scope or from another library.
- The UI is white and black with no new tinted surfaces, and empty and error states are written plainly.
- Inbox quick notes and sources are untouched, and so are topic suggestions.

## Progress log (implementer)

### Phase 1: built 2026-10-01, uncommitted, awaiting review

What changed
- Backend: `_set_proposal_status` is the one place that records a decision on a saved answer. `update_knowledge_proposal` and `create_knowledge_proposal` both use it. `ensure_saved_knowledge_units()` runs in the `main.py` lifespan, and `seed_if_empty` calls it too. Migration 19 `knowledge_saved_without_review` makes pending and held proposals accepted, and also puts a provisional unit that belonged to one of them back to trusted (a held answer that had been accepted before). It skips tables that don't exist, because `test_migrations.py` runs against a one-table schema.
- Inbox: the proposals block, `knowledge_suggestion`, `held`, `proposal_id`, `proposal_status` and `pending_proposal_count` are gone from the backend, the contracts and the desktop. The Held tab, `SuggestionItem.tsx`, the `"suggestion"` item type, `ItemPage`'s `onOpenSession` prop and the `gunther:proposal-created` event are removed. Topic suggestions and quick notes are untouched.
- Ask: save and remove live in a new hook, `pages/savedAnswers.ts` (`useSavedAnswers`), so they can be tested without rendering the whole workspace. `ConversationMessage` is exported for the same reason. A message counts as saved only while its proposal is `accepted`, so a removed answer shows "Save as knowledge" again. The "Knowledge units" list shows trusted units only.
- Tests: new backend tests for re-saving a removed answer, startup repair and migration 19; the Inbox test was rewritten; desktop tests for Save → Saved → Remove, offline and error cases; Inbox, ItemPage, fixtures and `api.test.ts` were updated.

Left as it was, on purpose
- `ProposalStatus` still allows `pending` and `held`, because `PATCH /proposals/{id}` accepts them. Nothing in the app sets them now.
- The workbook export (`exportBase` in `App.tsx`) still writes `proposals` and `reviewModel: "durable-unified-inbox"`. It is a file format; changing it wasn't part of this phase.

Checks run
- Backend: `tests/test_migrations.py`, `test_api.py`, `test_trash.py`, `test_backup_tool.py` and `test_structured_knowledge.py` pass, and so does the full suite (387 passed, 1 skipped). `ruff` is clean on the changed files. Four older E501 findings remain in `gunther/tts_providers.py` and `tests/test_tts.py`, which this phase didn't touch.
- Desktop: `npm run typecheck`, and the whole `npx vitest run`.

Not verified
- Nothing was clicked through in the running app. The Remove button's look (a quiet text button beside "Saved as knowledge") has not been seen on screen.

### Phase 2: built 2026-10-01, uncommitted, awaiting review

What changed
- **Agents** are in `gunther/outputs.py` (new): `OutputAgents` (`plan`, `research`, `write`, `check_built`, `check_unbuilt`, `assemble`, and the whole runs `build`, `revise`, `check`), `Pool`, the section rules (`split_document`, `section_hash`, `heading_of`, `title_of`) and the prompts. It reads nothing from the database; the service hands it the tool, the pool and the notes.
- **Ask, unchanged in behaviour**: `AskAgent._relevant`, `_mark_unsourced` and `_check_leads` are now `relevant`, `mark_unsourced` and `check_leads` (the audit is split into `unsourced_claims` + `place_marks`; `check_leads` can also report what it dropped). The `search_library` closure became `KnowledgeService._library_scope` + `_library_tool`; Ask passes exactly what it passed. `evidence_from_citation` is shared by `conversation_pool` and the outputs. All 30 tests in `test_agent.py` passed straight after the refactor.
- **Model job**: `Role("outputs", …, follows="ask")`. `effective()` gives an unsaved Outputs job Ask's model, and `models_api._still_following` keeps it unsaved when other jobs or providers are saved, so it keeps following until someone picks a model for it. The contracts role union has `"outputs"`. Settings → Models draws its jobs from the API, so no desktop change was needed.
- **Data**: migration 20 `agent_outputs` (8 columns on `artifacts` + table `artifact_checks`); `ArtifactCheck` model; legacy rows get `style = format`. **Migration 12 now ignores those 8 columns** when it compares a stored table with the models. Without that, a database still at version 10 or 11 would have had its Outputs set aside as `artifacts_legacy` by migration 12 (the existing v11 repair test caught it). `trash._delete_library_records` needed no change: `artifact_checks` goes with its version through `ON DELETE CASCADE` (tested, with `PRAGMA foreign_key_check`).
- **Integrity**: `manifest_hash_of(artifact)` (service.py) seals version 1 exactly as before and version 2 with kind, style, brief, origin, citations, outline, scope and inputs. `_verified_artifact_payload` and `scripts/backup_gunther.py` branch on `schema_version`; version 2 also checks that kind, format, style, audience, brief and origin match the provenance. `OutputProvenanceOut` is a subclass served with `SerializeAsAny`, so a version 1 output's JSON is byte-for-byte what it was.
- **Service**: `build_output` (also the rebuild), `revise_output`, `edit_output`, `check_output`, all writing through one helper, `_save_output`. Everything is read first; the version is written in a fresh session only after the agents finish, and a `{"type": "saving"}` event is the last place a Stop can still land.
- **API**: the six routes of the plan, with Ask's `AnswerRuns` and `_sse` under the key `outputs:<library>`. A model failure is 502, a missing model, bad scope or empty library is 400, a stale version or a reused request id is 409. `AnswerRun._replay` joins the text of one section only.
- **Tests**: `tests/test_outputs.py` (28 tests: section rules, shaping, build report and deck, selection scope, seeding from saved knowledge and discussions, unsupported/unverified/contradicted, no model / writer failure / unreadable plan, idempotency, rebuild with an edited outline, edit, check, revise, tamper, busy + follow, stop, library deletion), a migration test, a v2 case in `test_backup_tool.py`. `tests/fake_models.py` gained `output_replies`.

Where this differs from, or adds to, the plan
- The row-writing helper is used by build, rebuild, edit and revise. `create_artifact` was **not** moved onto it: it is deleted in Phase 7, and moving it risked version 1. It now also sets `style = format`.
- Section rules, kept identical for the desktop: a `## ` or `---` inside a code fence does not count (for both kinds, not only slides); a deck separator is a line whose trimmed text is `---`; empty slides are dropped; a report's text before its first `## ` is the preamble and belongs to no section.
- Limits added to `OutputLimits`: `seed` 40 (of the 120, so research is not starved by a big library of saved answers), `results` 40 (what one grading call sees), `shown` 30, `sentences` 40. Research runs for every section before any writing, and the space left in the pool is shared by the sections still to search.
- The Checker drops numbers the pool does not have before the audit, as Ask does, so a sentence left uncited is seen as uncited. In report-only mode (`check_output`) a number with no passage behind it is "unsupported".
- Notes (saved knowledge, discussions) have their own `[n]` and `[?]` marks stripped, so a writer cannot copy one as if it were a citation. Seeds from a library passage are kept only when its source is still a live source of this library.
- A revision may answer a section with exactly `UNCHANGED`; that section keeps its text and its check. Sections are matched by hash for edits, and by index for revisions (renumbering changes the hash but not the finding).
- A deck's title is its first slide's heading, so the title slide is always `# {title}`. A planner that returns more sections than allowed is cut to the limit, keeping the closing one.
- Edit, revise and check refuse a version from the old builder (400): it has no citations or scope. Building on it (a rebuild) is allowed. `check_output` writes a checks row only when it checked something.
- `ArtifactBuilderFormat` in the contracts names the old builder's three formats; `ArtifactFormat` also allows `report` and `slides`. The old `StudioView` and its test were adjusted only to compile; Phase 3 replaces them.

Checks run
- Backend: full suite 417 passed, 1 skipped (387 before). `ruff` is clean on every file this phase touched; the older E501 findings in `tts_providers.py`, `test_tts.py` and two lines of `scripts/backup_gunther.py` are not mine.
- Desktop: `npm run typecheck`.

Not verified
- Nothing ran against a real model. The prompts (outline, writer, support check) are untested on a real provider, so the quality of plans and sections, the 120/30 limits, and the cost of one build are unknown. The fake model returns whatever the tests script.
- The streams were exercised through `TestClient`, not through the desktop app.

### Phase 3: built 2026-10-01, uncommitted, awaiting review

What changed (all under `apps/desktop/src/outputs/` unless noted)
- `OutputsPage.tsx` replaces `StudioView`. Left column: **Use** (Whole library | Choose…, with "12 sources · 3 saved · 1 discussion"), **Brief**, **Make** (Report | Slides), **Style** (reports only), **Audience**, the black **Build** button (it reads "Rebuild" with a version open, and "Open latest version N" on an older one), and the history ("Edited" and "Revised" tags from the origin; **New** starts a new lineage). Right side: the live view while building, the error with **Retry**, or the open version.
- `ScopePicker.tsx`: a dialog with Sources, Saved knowledge (trusted only) and Discussions, a filter box, checkboxes and Select all / Clear per group. `describeScope` words the counts.
- `liveOutput.ts` (+ test) is the reducer for the stream (`outline`, `section`, `step`, `text`, `resumed`) and `useLiveOutput`; `LiveSections.tsx` shows the plan, then each section with its state, searches (`AgentSteps`) and text (`withoutMarks`), and a Stop button.
- `ReportView.tsx`: the body goes through `AnswerBody` with `citationNumbers(citations)`; a number opens `EvidencePanel`. Each section is rendered on its own with a quiet "Not re-checked" tag and what the Checker found in it. A legacy version's Markdown is shown without citations. `SlideList` shows a deck as a plain list (with the speaker's notes) until Phase 4.
- `OutlineEditor.tsx`: a collapsed "Outline" row; "Edit outline" opens headings and goals (add, remove, move) and "Rebuild with this outline" sends `outline` + `supersedesArtifactId`.
- `sections.ts` (+ test): the same section rules as the service (`splitDocument`, `joinDocument`, `sectionHash`, `headingOf`, `titleOf`, `splitNotes`); the tests use the same cases as `tests/test_outputs.py`, including the same SHA-256 vectors.
- `outputs.css`: white and black, hairlines, serif for the reading column; no tinted surfaces (red only on an unsupported or contradicted finding's rule).
- `api.ts`: `buildOutputStream`, `reviseOutputStream`, `followOutputBuild`, `stopOutputBuild`, `editOutput`, `checkOutput`, the `OutputEvent` type and `OutputStoppedError`. Contracts: the request types are in `packages/contracts` (Phase 2).
- Page behaviour: opening the page follows a build that is still running (a 204 means none); leaving it only stops listening; a failed or stopped build saves nothing and the page says so; with no model it says "Outputs need a model. Set one up under Settings → Models." and opens Settings → Models; with an empty library it offers Add sources.
- **Removed early** (the plan put this in Phase 7): `StudioView`, its local-storage "generation attempt" helpers and `StudioView.test.tsx`, plus the imports they left. The backend route, `knowledgeApi.createArtifact`, `createArtifactSchema` and the old studio CSS in `atlas.css` / `design/legacy.css` are still there for Phase 7. The page intro already reads "Turn what you’ve gathered into a report or slides."
- Tests: `OutputsPage.test.tsx` (13 tests: plan → sections → saved, slides and rebuild, Retry with the same request, Stop, following a running build, scope summary and picker, filter, no model, empty library, history, findings and evidence, legacy version, outline rebuild), `liveOutput.test.ts`, `sections.test.ts`.

Where this differs from, or adds to, the plan
- The page reads a running build through `GET …/outputs/build/stream` on mount, and a retry sends the same `clientRequestId` (nothing was saved, so it is safe). It does not keep request ids across a reload; a build that was running is followed instead.
- "Rebuild" is refused on an older version (the button opens the latest), and so is "Rebuild with this outline", because a rebuild must supersede the lineage head.
- Opening a version sets the controls (kind, style, audience, brief, scope) to what it was built with, so Rebuild repeats it unless something is changed.

Checks run
- Desktop: `npm run typecheck`; the whole `npx vitest run`: 51 files, 269 tests passed (249 before; the old page's 4 tests are gone, 24 new).

Not verified
- Nothing was opened in the running app: the layout, spacing and the picker dialog are unseen, and `EvidencePanel` was only exercised for a web citation (a library citation needs the source reader).
- The live view's look while text streams (re-rendering the Markdown per event) has not been measured on a long report.

### Phase 4: built 2026-10-01, uncommitted, awaiting review

What changed
- Installed `reveal.js` 6.0.2 and `@revealjs/react` 0.2.2 (`npm i -w @gunther/desktop`). The wrapper works with React 19, so the plan's fallback (`new Reveal(...)` by hand) was not needed. `npm audit` lists only the existing vitest advisory.
- `SlidesView.tsx`: a `<Deck>` with one `<Slide>` per slide, config `embedded, hash/history off, 1280×720, controls, progress, slideNumber "c/t", transition "fade"`; each slide's body goes through `AnswerBody`, so a number opens the evidence panel as in reports. It is remounted (`key`) for another version and starts at the first slide.
- **Thumbnails**: a strip of the same slide face scaled by CSS (`SlideFace` is used by the deck and the strip, so they cannot disagree); clicking one calls `deck.slide(i)`. Reveal's own overview (`o`) also works. **Notes** of the slide in view show under the deck (no pop-up window). What the Checker found in that slide shows under the notes.
- **Present**: the page's own layer, fixed over the window. In the app it calls `getCurrentWindow().setFullscreen(true)` (dynamic import of `@tauri-apps/api/window`); in a browser `requestFullscreen()`. Esc (through the shared `useEscape` stack), the ✕ button and, in a browser, leaving full screen all leave it. While presenting, Reveal's keys work without focus (`keyboardCondition` becomes `null`); otherwise they work only while the deck has focus. `core:window:allow-set-fullscreen` is added to `src-tauri/capabilities/default.json`.
- `sections.ts` gained `parseSlides` (heading, body, notes), with a test. The plan's `slides.ts` is this; no separate file.
- During a build, slides still appear as a list (`LiveSections`); the viewer opens when the version is saved. The plain `SlideList` of Phase 3 was removed.

Where this differs from the plan
- **No `reveal.js/theme/white.css`.** That file is 575 KB, almost all of it fonts embedded as base64 (which this app would override), and it declares `:root` variables. The app imports only the core `reveal.css` (54 KB; its few unscoped rules only apply to `html.reveal-print`, the `.r-overlay` layer and `.zoomed`, none of which the app uses) and defines the look in `outputs.css`: the `--r-*` variables are set on `.outputs-stage`, not on `:root`, so nothing outside the deck is restyled. A slide is always white paper and black ink, also in the dark theme, because it is shown to a room.
- reveal.js and its wrapper are split into their own chunk in `vite.config.ts` and the viewer is `React.lazy`, so they load when a deck is opened (118 KB + 54 KB CSS), like the PDF viewer. `vite build` (frontend only, to `/tmp`) succeeded; no Tauri build was made.

Checks run
- Desktop: `npm run typecheck`; `src/outputs` tests (31 passed). `SlidesView.test.tsx` runs the **real** reveal.js under jsdom (it initializes there): 3 slides in the deck and 3 thumbnails, notes follow the slide in view, a number on a slide opens its evidence, a new version starts at slide 1, Present in a browser and through the app's window (mocked `setFullscreen`), Esc and ✕ leave, an empty deck.

Not verified
- None of it was seen in a real WebKit window: the deck's scaling inside the page, the arrow and progress controls, the thumbnails' look, and the type sizes (38 px body on a 1280 px canvas, 88/58 px headings) are my reasoning, not a screenshot.
- Full screen through Tauri on macOS (and whether Esc reaches the page while the window is full screen) is untested. If Present does not fill the screen, the permission or the window call is the first thing to look at.

**Changed after the first Mac trial (2026-10-01).** The user found Present (the whole window went full screen) and the slide size wrong. Now: Present fills the app's window and no longer calls Tauri's `setFullscreen`, so `core:window:allow-set-fullscreen` is gone from `capabilities/default.json` (and the browser's `requestFullscreen` is no longer used). Looking at it in real Chrome (a seeded demo library, headless Chrome over DevTools) found three things the jsdom tests could not:
- The page's entrance animation (`.page-enter`, fill mode `both`) leaves `transform: matrix(1, 0, 0, 1, 0, 0)` on the page, and a `position: fixed` layer inside a transformed element fills that element, not the window. Present began under the sidebar and ran off the right and bottom. It is now a portal on `document.body`, with its own deck that starts at the slide in view and keeps the page's slide in step.
- Reveal takes Esc for its overview and cancels the event, so `useEscape` (a window listener) never ran and Esc did not leave Present. Esc is taken in the capture phase while presenting.
- Slide text ran off the bottom of the slide (and under Reveal's arrows). The slide's type is a little smaller, the arrows sit at the two edges, and a slide whose text is too long is scaled down to fit (`slideFit.ts`, down to 55%), the same in the deck, the thumbnails and the printed page.
The deck's page now uses the whole width (`.outputs-page.is-deck`; reports keep their reading column), and its stage is never taller than the window. The small preview beside the editor has no arrows. Report citation numbers had default button chrome and are now quiet superscripts, as in Ask.

### Phase 5: built 2026-10-01, uncommitted, awaiting review

What changed (all under `apps/desktop/src/outputs/`)
- **Edit** (`EditPane.tsx`): the existing `MarkdownEditor` (CodeMirror) beside a live preview of the draft (`ReportView`, or the slide viewer for a deck). "Save as new version" sends `POST …/edits` with a fresh request id; "Cancel" (or Esc) discards; ⌘↵ saves. The bar says "No changes yet" / "Unsaved changes · 1 part to re-check". If saving fails, the banner says why and the draft stays where it was typed.
- The preview uses `withDraft` (in `sections.ts`, with tests): a section that still reads as it did keeps what the Checker found in it, wherever it moved; a section that was typed in shows "Not re-checked". The draft keeps one id (`<version>:draft`), so a deck is not rebuilt on every key.
- **Ask Gunther to change…** (`RevisePanel.tsx`): "Which part" (the whole output, or "Section 2: Heading" / "Slide 2: …") and "What should change". It sends `reviseOutputStream` and shows the agents at work in the same live view as a build; for one section the others show "Kept as it is". On success the new version opens and the history refreshes; on failure the banner says "The change wasn’t saved" with Retry (the same request id).
- **Check again** shows when any section is "Not re-checked". It calls `checkOutput`, replaces the open version's data with the result (a check is not a version) and the flags go.
- Only the latest version of a lineage can be changed: an older one shows **Open latest** instead of Edit and Ask. A version from the old builder (origin `legacy`) shows "This version came from the old builder. Rebuild it to edit it." and none of the three. While editing, checking or building, the controls, the history and "New" are disabled.
- Errors now carry their own title (`The output wasn’t saved`, `The change wasn’t saved`, `The edit wasn’t saved`, `It couldn’t be checked`, …).
- New shared piece: `LazySlides.tsx` (the lazily loaded viewer, used by the page and by the edit preview).

Tests
- `OutputsPage.test.tsx` now has 20 tests; the 7 new ones: typing over a version → marked → saved as the next version; a failed save keeps the draft and Cancel discards; one section changed as asked (others "Kept as it is"); the whole output changed and a failed change retried; check again clears the flags; an older version opens the latest instead; a legacy version has no editing. The Markdown editor is replaced by a textarea in those tests. `sections.test.ts` covers `withDraft`.

Checks run
- Desktop: `npm run typecheck`; the whole `npx vitest run`: 52 files, 285 tests passed. `vite build` (frontend only, output in `/tmp`) succeeds.

Not verified
- CodeMirror itself, ⌘↵ and Esc inside it, the side-by-side layout, and how the live preview copes with a long document are untested; the tests use a textarea and jsdom.
- A real revision or check has not run against a model.

### Phase 6: built 2026-10-01, uncommitted, awaiting review

What changed
- **Shell** (`apps/desktop/src-tauri/src/lib.rs`): `print_output(window: WebviewWindow)` calls `window.print()` and is registered in `invoke_handler`. `cargo check --offline` passes on this Linux box (Tauri 2.12.0; `Webview::print` is `#[cfg(desktop)]` and documented as macOS-only, which is why other systems use `window.print()`). The app is not sandboxed (`Entitlements.plist` has only audio input), so printing needs no new entitlement.
- **Print layout** (`PrintRoot.tsx`): a `<div id="print-root" aria-hidden>` portalled into `body` for the open version, always present and hidden on screen (`@media screen { #print-root { display: none } }`). Printing shows only it (`body > :not(#print-root) { display: none }`).
  - Report: the title, the brief, then each section through `AnswerBody` (numbers are superscripts, not buttons), then **Sources** (title, locator, a 220-character quote). `@page { size: A4; margin: 18mm 16mm }` is injected as a `<style>` in the root. Type is the serif at 11 pt, headings avoid being stranded at the foot of a page, and tables, code and quotes are not split.
  - Deck: one slide per page, `@page { size: 13.333in 7.5in; margin: 0 }`; a slide is exactly the 1280 × 720 px it is drawn on (96 dpi), `break-after: page`; no notes.
  - The light palette is pinned on `#print-root`, so a dark theme never prints light text on white, and `html, body` are forced white when printing.
- **Export PDF** button (any version, not while editing): it waits for `document.fonts.ready`, then in the macOS app calls `invoke("print_output")`; on Windows, Linux and in a browser it calls `window.print()`. A short toast says "In the print window, choose PDF to save it." A failure shows "It couldn’t be printed".
- `SlideFace` moved to its own file (the deck, the thumbnails and the printed page share it, and printing does not load reveal.js); `withoutTitle` moved to `sections.ts`.

Where this differs from, or adds to, the plan
- A printed deck ends with one **Sources** page when it has citations. The plan said nothing about it; without it the numbers on the slides would mean nothing on paper.
- The print layout shows the saved version, not an unsaved draft (Export is disabled while editing).
- The print root is always mounted (as planned). A lazily mounted layout would be gone before a macOS print sheet's user confirms, which is why it stays; the cost is that the open version's text is in the page twice, one hidden (tests scope their queries to the visible `article`).

Tests
- `OutputsPage.test.tsx` (now 25 tests): the report layout (A4 style, title, superscript numbers, Sources, no buttons), the deck layout (13.333 in page, one section per slide + a sources page, no notes), printing from the page in a browser, the shell command on a Mac (and its failure), and a test that `outputs.css` keeps the three print rules (screen hides, print shows only the root, a slide is 1280 × 720 with a page break after). jsdom cannot lay out paper, so these check structure, not appearance.

Checks run
- Desktop: `npm run typecheck`; `src/outputs` tests (45 passed). Rust: `cargo check --offline` in `apps/desktop/src-tauri` (finished, no warnings).

**What to try on the Mac** (nothing here could be seen)
1. Open a report, press **Export PDF**: the print sheet should open (this is `WebviewWindow::print`, macOS 11 or later). Choose PDF → Save as PDF. Look at: the paper is A4 with about 18 mm top and bottom and 16 mm sides; no heading alone at the foot of a page; the Sources list at the end; Chinese text and the serif font; numbers as small superscripts; "unverified" marks where a claim has no source.
2. Open a deck and export: one landscape slide per page with no margin and no speaker notes, white background (also in the dark theme), the last page "Sources". If the sheet insists on A4 or Letter and shrinks the slides, WebKit is ignoring `@page { size }`.
3. If WebKit ignores `@page` margins or size, tell me; do not write Objective-C for it (the plan's rule).
4. If **Export PDF** does nothing in the app, `print_output` is the place to look (the toast appears either way).

### Phase 7: built 2026-10-01, uncommitted, awaiting review

What changed
- **Removed**: `KnowledgeService.create_artifact` and `_compose_artifact_content`, `POST /api/knowledge-bases/{id}/artifacts`, `CreateArtifactInput` and `OldArtifactFormat` (backend); `knowledgeApi.createArtifact`, `createArtifactSchema`, `CreateArtifactInput`, `artifactFormats` and `ArtifactBuilderFormat` (desktop and contracts). The old page, its local-storage "generation attempt" helpers and its test were removed in Phase 3. Versions from the old builder stay readable, sealed and verifiable (service and backup tool), and can be rebuilt; they cannot be edited, revised or checked.
- **Tests that used the old route** now insert a legacy version directly: `tests/legacy_artifacts.py` seals one the way the old builder did, with its own copy of the version 1 hashing so the code that reads it is not checked against itself. The old artifact-history test became "a version from the old builder stays readable, sealed and bound to its library" (exact version 1 provenance, list order, library and workspace boundaries, tamper detection, edits refused); the Trash and backup-restore tests build their versions the same way. A new test checks that a real legacy version refuses edit, revise and check, and that a rebuild of it is the next version of the same lineage.
- `docs/OUTPUTS.md` is the design document, in the manner of `docs/ASK_AGENT.md`: scope, notes and pool, the four agents, findings, section rules, versions and what seals them, rebuild / edit / revise / check, reading, slides and PDF, streaming, limits, failure, known limits. `README.md` (Outputs and the review step), `docs/REPOSITORY_STRUCTURE.md` and the header of this plan are updated; older dated reports (acceptance report, product plans) are left as the record of their time.
- `api.test.ts` covers the new calls (workspace header and encoded ids on every route, the build stream's events and its done / error / stopped / closed endings, a revision's address, following a build and a 204).

Found by reading my own code once more, and fixed
- A planner that ran long (a heading over 160 characters, a goal over 400, more than 3 searches, an empty title) failed the whole build on schema limits. It is now cut to size afterwards (title falls back to the first heading), with a test.
- The Checker ran its claim audit on a deck's title slide, which only has a title and a line. It no longer does, in a build, a revision or a check (with a test that checking a typed title slide makes no model call).
- A whole-output revision that leaves a section alone made the page show the literal word `UNCHANGED` as that section's text; it now shows "Kept as it is" (with a test).

Left on purpose
- Dead CSS from the old page (`.studio-*` in `atlas.css`, `design/legacy.css`, `styles.css`) is still in the stylesheets. Those rules share long lines with live ones, so removing them was not worth the risk of breaking the library workspace; nothing refers to them.
- `ProposalStatus` still allows `pending` / `held` and the workbook export still writes `proposals` (from Phase 1).

Checks run (final)
- Backend: full suite, `ruff` on every file I touched, `cargo check --offline`.
- Desktop: `npm run typecheck`, the whole `npx vitest run`, `vite build` (frontend only, to `/tmp`).
(Numbers are in the summary I give with this hand-over; see the log of each phase for what each check covered.)

Not verified (all phases)
- Nothing was run against a real model, and nothing was opened in the real app on a Mac: prompt quality, the cost of a build, the look of the page, the slide viewer, CodeMirror editing, and PDF output are all untested. Each phase's "Not verified" says what in particular.
