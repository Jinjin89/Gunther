# A library that reads like a library: arranged contents — implementation plan

> **Status 2026-10-12: L1 and L2 built and reviewed, including on the real screen (see the progress log); not yet tried on the Mac.** Written by Claude Opus for Sonnet agents, one phase per agent; Opus reviews each phase. Line numbers drift: grep for the names. Runs after `docs/TRANSCRIPT_UPGRADE_PLAN.md` Phase U1 (that one owns migration 24; this one is migration 25).

The user's request (2026-10-12), in short: a library can hold very many sources, and its Overview shows them in a way that is hard to read and to organise. They want to see it **like a real library**, arranged under their own headings. Their examples:

1. **A course.** A heading for the course (or a part of it), then lectures in order — Lecture 1, Lecture 2, … — each with its recording, and PDFs (slides, handouts) beside them. A second lecture unit is another heading with its own lectures and PDFs.
2. **Many papers.** Papers arranged under a few big headings and smaller headings beneath them, each paper put where it belongs.

Decided with the user (2026-10-12):

1. **Headings and sub-headings that the user arranges**, with any mix of sources (recordings, PDFs, papers, notes, links) under them, **in the order the user chooses**. No paper template, no forced section names (`AGENTS.md`).
2. **One place per source** in a library — like a book on a shelf or a file in a folder. Moving a source takes it out of where it was.
3. **One "Contents" tab** replaces both Overview and Sources. The library's tabs become **Contents · Ask · Outputs**.

---

## The design

### What it looks like

```
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ ← ◼ COURSE  Machine Learning 2026      Contents  Ask  Outputs    ⚙ Export │
 ├───────────────────────────────────────────────────────────────────────────┤
 │  Lectures, slides and readings for ML 2026, autumn term.                  │
 │                                                                           │
 │  [⌕ Filter]                        + Section    Import folder   Add source │
 │                                                                           │
 │  ▾ Part A · Foundations                                         ⋯         │  ← heading 1 (serif)
 │      Course outline.pdf                         PDF · 4 pages             │
 │    ▾ Lecture 1 · Introduction                                   ⋯         │  ← heading 2
 │        🎙 Lecture 1                             Oct 3 · 1:12:04           │
 │        📄 week1-slides.pdf                      PDF · 24 pages            │
 │    ▾ Lecture 2 · Linear models                                            │
 │        🎙 Lecture 2                             Oct 10 · 58:40            │
 │        📄 week2-slides.pdf                      PDF · 31 pages            │
 │  ▸ Part B · Deep learning                       8 sources                 │  ← collapsed
 │  ▾ Readings                                                               │
 │    ▸ Optimisation                               12 papers                 │
 │    ▾ Generalisation                                                       │
 │        📄 Understanding deep learning requires… Zhang et al. · 2017       │
 │                                                                           │
 │  Not placed yet · 3                             Suggest sections          │  ← always last
 │      🔗 A blog post about Adam                  arxiv.org · Oct 11        │
 │      📝 My notes on week 3                      Oct 12                    │
 └───────────────────────────────────────────────────────────────────────────┘
```

- **Sections** are the headings. Any depth is stored; the screen shows level 1 as a serif heading, level 2 as a semibold line, level 3+ as a smaller semibold line, each indented one step (16 px). A section can have a one-line note under its heading (its existing `description`).
- **Rows** are sources: the kind icon (its identity colour, as in the Sources list today), the title, and one grey detail at the right: recordings `date · duration`, papers `authors · year`, PDFs/files `PDF · N pages` (or the file type), links the site name and date, notes the date. A source still being read shows `Reading…`; a failed one `Couldn't read`.
- **Order** is the user's: sections and sources share one order inside their parent (the course outline PDF above Lecture 1 in the example). New things go at the end.
- **"Not placed yet"** is always last and lists the library's sources that are in no section, newest first. New sources land here. It hides when empty.
- **Collapse/expand** with the triangle; a collapsed section shows its count ("8 sources", or "12 papers" when all are papers). Collapse state is remembered per library on this device.
- **Click a row** opens the source page, as today (with J/K through the visible rows in order).

### Arranging

- **+ Section** (toolbar) adds a top-level section at the end, its title field already editing. In a section's **⋯** menu: *Add sub-section*, *Rename*, *Add sources here…*, *Sort by name*, *Ask this section*, *Overview*, *Delete section*.
- **Drag** a row or a section by its handle (shown on hover at the left). Drop **on a section's heading** → into that section, at the end. Drop **between rows** → at that place, in that row's section. Drop in **Not placed yet** → out of its section. A section can't go into itself or its own sub-sections (the drop shows as not allowed).
- **Select several**: a checkbox appears on hover at the left of a source row; Shift-click selects a range, ⌘-click toggles. With a selection, a bar shows `3 selected · Move to… · Take out of section · Clear`. **Move to…** opens a small picker: a filter field and the section tree, plus "Not placed yet". This is also the keyboard-friendly way to move one row (row ⋯ menu: *Move to…*, *Take out of section*).
- **Add sources here…** opens the same picker the other way round: the library's sources (Not placed yet first, then the rest with their current section shown), with a filter and checkboxes; *Add* moves them into this section.
- **Sort by name** orders a section's children (sections and sources together) by title, numbers by value: Lecture 2 before Lecture 10 (`Intl.Collator(undefined, { numeric: true, sensitivity: "base" })`).
- **Delete section** asks: `Delete "Part B" and its 3 sub-sections? Their 8 sources go back to Not placed yet; no source is deleted.` 
- **Suggest sections** (the existing paper grouping, `TopicSuggestionsPanel`) appears in the Not placed yet header when at least 6 papers are not placed; accepting a group makes a section at the end with those papers.
- **Ask this section** opens Ask scoped to the section and everything under it (the existing topic focus). **Overview** shows the section's written overview under its heading (the existing `TopicOverviewPanel`, with Write/Rewrite).
- **Filter** (toolbar) narrows the rows to titles/details that match, keeping their headings (and showing sections whose title matches with all their rows); collapse is ignored while filtering. Esc clears.

### Words

The interface says **section**, never "topic". "Not placed yet". "Take out of section". "Ask this section". "Sort by name". Backend names (`TopicNode`, `/topics`) stay.

---

## How to work (for the implementer)

- Read this whole document and your phase before editing. Follow `AGENTS.md`.
- Match the surrounding code: naming, comment density, the design tokens in `apps/desktop/src/design/tokens.css` and `docs/DESIGN_SYSTEM.md` (white surface, black text, colour only for identity; no tinted panels; black primary buttons).
- Tests: backend `cd apps/backend && /tmp/uvbin/uv run pytest tests/<files> -q` and `/tmp/uvbin/uv run ruff check gunther tests`; desktop `cd apps/desktop && npx vitest run <files>` and `npm run typecheck` (not `npx tsc --noEmit`). No app builds.
- Stop at the end of your phase; add a progress-log entry at the bottom.

## 1. Decided with the user (don't reopen)

1. User-arranged sections and sub-sections holding any kind of source, in the user's order.
2. One place per source per library.
3. One Contents tab replacing Overview and Sources; tabs Contents · Ask · Outputs.

## 2. Calls the planner made (the user may still override)

1. **Sections are the existing topic tree** (`TopicNode`: `parent_id`, `position`, `title`, `description`, `version`) and placements are the existing `TopicSourceLink` rows. Ask's topic focus, section overviews, suggestions and the library folder keep working on the same data. No new tables.
2. **One shared order per parent**: `TopicNode.position` and a new `TopicSourceLink.position` are in the same space among a parent's children, renumbered 0…n−1 on every change.
3. **One place**: `topic_sources` gains `knowledge_base_id` and a unique index on `(knowledge_base_id, source_id)`. Filing a source anywhere in a library removes its other placement in that library.
4. **New sources are not placed** (nothing changes in the capture or import paths). Bringing them into a section is *Add sources here…*, a drag, or *Move to…*.
5. **Delete section** deletes its whole subtree; their sources become not placed; overviews of deleted sections go with them (cascade); passage links (`topic_evidence_links`) go with them; Ask conversations focused on a deleted section fall back to the whole library (clear `focus_chapter_id`).
6. **Trash**: a trashed source keeps its placement row, so Restore puts it back where it was; the contents hide trashed sources.
7. **Drag and drop with `@dnd-kit/core` + `@dnd-kit/sortable`** (pointer events; the established React choice). Native HTML5 drag-and-drop is avoided because Tauri's window drag-drop handling can swallow it.
8. **The library folder** follows the contents: `Overview.md` becomes the contents (headings → list of sources with links, then "Not placed yet"); per-section files move from `Topics/` to `Sections/` (the folder writer's manifest removes the old managed files).
9. **No virtual list** for now: rows are light, and collapsed sections render no rows. Revisit if a 1 000-source library is slow.
10. **The source-detail drawer** in the old Sources view (used only when no `onOpenSource` was given; the app always gives one) is dropped with the view. `SourceEvidence` stays as a component with its tests; its "topic" wording becomes "section".

## 3. What exists today (read before starting)

- Backend topic tree: `apps/backend/gunther/topics.py` (`TopicService.list/save/_file/file_sources/unfile_source/link`, `subtree_ids`, `topic_source_ids`, `topic_block_ids`), routes in `apps/backend/gunther/knowledge_api.py` (`TopicInput`, `/knowledge-bases/{id}/topics…`, `topic-suggestions`, `topics/{id}/overview`), suggestions and overviews in `apps/backend/gunther/library_topics.py` (unfiled papers = not in any topic of the library), models `TopicNode`, `TopicSourceLink`, `TopicEvidenceLink`, `TopicSynthesis` in `models.py`, Ask's focus in `service.py` (search `focus_chapter_id`), trash in `trash.py` (library purge deletes topics), folder files in `library_folders.py` (`_plan_knowledge`, `Overview.md`, `Topics/`). Tests: `tests/test_structured_knowledge.py`, `tests/test_papers.py`, `tests/test_trash.py`, `tests/test_library_folders.py`, `tests/test_migrations.py`.
- Source details for rows: `Source` (`kind`, `title`, `created_at`, `asset_id`), `Asset` (`original_name`, `media_type`), `SourcePaper` (`authors`, `year`), recording duration in the content's first `Duration: …` line (`RECORDING_META` in `apps/desktop/src/items/sourceContent.ts`), PDF page count where the parsed revision records it (`document_parser.py` writes `pageCount` into the manifest; check `SourceRevision.manifest_json` of the head) — omit when unknown. Processing state: `KnowledgeIndex.processing()`.
- Library membership: `KnowledgeBaseSource`; the sources list endpoint behind `knowledgeApi.sources(baseId)`.
- Desktop: `apps/desktop/src/pages/KnowledgeBaseWorkspace.tsx` (`BaseHeader`, `modeLabels`, `OverviewView`, `MaterialsView`, the topics loading, `askTopic`), `apps/desktop/src/components/TopicManager.tsx` (`TopicSuggestionsPanel`, `TopicOverviewPanel`, `TopicManager`), `apps/desktop/src/components/BulkImport.tsx`, `apps/desktop/src/atlas.ts` (`AtlasMode`), `apps/desktop/src/App.tsx` (`DEFAULT_KNOWLEDGE_BASE_MODE`, `openBase`, `KnowledgeBaseWorkspace` props), `apps/desktop/src/pages/HomePage.tsx` (`onOpenBase(baseId, "sources")`), `apps/desktop/src/api.ts` (`knowledgeApi.topics/saveTopic/fileTopicSources/…`), contracts in `packages/contracts/src/index.ts` (`KnowledgeTopic`, `TopicInput`, `SourceSummary`), the CI smoke `scripts/smoke_desktop_ui.mjs` (clicks "Sources", expects `button.material-row`), styles in `apps/desktop/src/design/legacy.css` (`.material-*`, `.base-overview*`) and `apps/desktop/src/knowledge.css`.

## 4. Contracts

### 4.1 Migration 25 `library_contents`

- `topic_sources`: add `knowledge_base_id` (String(160), backfilled from the topic's library, NOT NULL after backfill) and `position` (Integer, default 0).
- One place: for each `(knowledge_base_id, source_id)` with several rows keep the earliest (`created_at`, `id`) and delete the rest; log how many.
- Unique index `ux_topic_sources_base_source` on `(knowledge_base_id, source_id)`.
- Renumber, per library and parent: sections by `(position, created_at, id)` get 0…k−1; then each section's sources by `(created_at, id)` get k…k+m−1 (sections first, as existing data had no shared order).
- Model fields added in `models.py`. Test in `tests/test_migrations.py` (duplicates collapsed, order kept, re-run safe).

### 4.2 `GET /api/knowledge-bases/{id}/contents`

```json
{
  "sections": [{ "id": "topic_…", "parentId": null, "title": "Part A · Foundations",
                 "description": "", "position": 0, "version": 3, "passageCount": 0 }],
  "placements": [{ "sourceId": "src_…", "sectionId": "topic_…", "position": 1 }],
  "sources": [{ "id": "src_…", "title": "Lecture 1", "kind": "recording", "createdAt": "…",
                "durationSeconds": 4324, "authors": null, "year": null, "pages": null,
                "mediaType": "audio/webm", "host": null,
                "processing": "ready" | "reading" | "failed" }]
}
```

`sources` = every live source in the library (placed or not). The desktop builds the tree. 404 for an unknown or trashed library.

### 4.3 `POST /api/knowledge-bases/{id}/contents/move`

```json
{ "items": [{ "kind": "source" | "section", "id": "…" }], "parentId": "topic_…" | null, "index": 2 | null }
```

Moves the items, in the given order, to be children of `parentId` starting at `index` among the remaining children (null = at the end). `parentId: null` means top level for sections and **not placed** for sources (index ignored for those). Validation: every item belongs to this library (422), a section can't move into itself or a descendant (422), unknown ids (404). Renumbers every parent it touched; bumps moved sections' `version`. `BEGIN IMMEDIATE` like `TopicService.save`. Returns the full contents (§4.2).

### 4.4 `PUT /api/knowledge-bases/{id}/contents/order`

```json
{ "parentId": "topic_…" | null, "children": [{ "kind": "section" | "source", "id": "…" }] }
```

Sets the order of exactly the current children of `parentId` (null = top-level sections; not-placed sources have no stored order). Any other set → 409 "The contents changed. Reload and try again." Returns the contents. Used by *Sort by name*.

### 4.5 `DELETE /api/knowledge-bases/{id}/topics/{topic_id}`

Deletes the subtree (§2.5) and returns the contents. Children before parents (`parent_id` is `ON DELETE RESTRICT`).

### 4.6 Changed behaviour of existing routes

- `POST /topics` (create): with no explicit position, goes to the end of its parent; `sourceIds` (an accepted suggestion) are **moved** into it (§2.3).
- `POST /topics/{id}/sources`: moves (removes other placements in this library); appended at the end.
- `DELETE /topics/{id}/sources/{source_id}`: takes it out (not placed).
- `GET /topics`: unchanged shape; `sourceIds` in `position` order.
- `library_topics.suggest`: "not filed" now means not placed in this library (same query through `knowledge_base_id`).

## Phase L1: contents API (backend)

### Tasks

1. Migration 25 + model fields (§4.1).
2. `topics.py`: one-place filing in `_file` (delete the source's placement in the same library before adding; new rows get `knowledge_base_id` and the next position); `contents(base_id)`, `move(...)`, `order(...)`, `delete(...)` on `TopicService` with a shared `_renumber(session, base_id, parent_id)`. Section creation gets the next position when none was given (`TopicInput.position` stays accepted; treat an omitted position as "end" — use `model_fields_set`).
3. Row details for `sources` (§4.2) in one or two queries per kind, not one per source: durations parsed from the content's `Duration:` line (`h:mm:ss` / `m:ss`), paper `authors` (first author + "et al." when several, as the folder writer's style) and `year` from `SourcePaper`, `pages` from the head revision's manifest when present, `host` from a link's web snapshot URL, `processing` from `KnowledgeIndex.processing()` collapsed to ready/reading/failed.
4. Routes in `knowledge_api.py` (§4.2–4.5), errors through `structure_error`.
5. `service.py`: deleting sections clears `focus_chapter_id` of sessions pointing into them; and `topic_block_ids`/`topic_source_ids` on an unknown topic must not break Ask (treat as whole library).
6. `library_folders.py`: `Overview.md` = the contents (sections as `##`/`###`/`####` headings in order, their sources as list items in order, then `## Not placed yet`); section files under `Sections/`. Keep "Written by Gunther; edits here are not read back."
7. Contracts are added by Phase L2 (desktop); don't edit `packages/contracts` here.

### Tests (`tests/test_library_contents.py` + updates)

- Contents shape; trashed sources hidden; sources of another library absent.
- Move: sources into a section at an index, to the end, to not placed; several at once keep their order; sections into another section / top level; into itself or a descendant → 422; another library's ids → 422/404; positions contiguous after every move; versions bumped.
- One place: filing into B removes the placement in A; the same source in two libraries keeps one place in each.
- Order: exact set → saved; different set → 409.
- Delete: subtree gone, sources not placed, overview rows gone, Ask session focus cleared, nothing else deleted.
- Suggestion accept moves papers; `suggest` only offers not-placed papers.
- Trash and restore keep the placement.
- Folder: `Overview.md` lists the arranged contents in order and "Not placed yet"; `Sections/` files.
- Existing topic tests still pass (adjust only where the one-place rule intentionally changed behaviour, and say so in the log).

### Done when

Tests and ruff pass; progress-log entry.

## Phase L2: the Contents tab (desktop)

### Tasks

1. `npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities` in `apps/desktop` (record versions).
2. Contracts: `LibraryContents`, `ContentsSection`, `ContentsPlacement`, `ContentsSource`, `ContentsMoveInput`, `ContentsOrderInput`. `api.ts`: `contents`, `moveContents`, `orderContents`, `deleteSection`.
3. New `apps/desktop/src/contents/`:
   - `contentsTree.ts` (pure, tested): build the tree from §4.2 (children sorted by position; not-placed sources newest first), `visibleRows(tree, collapsed, filter)` (flat rows with depth, for rendering and J/K order), `dropTarget(rows, overId, position)` → `{parentId, index}` or null if not allowed, `isDescendant`, `naturalOrder(children)`, `sectionCount(node)` ("8 sources" / "12 papers"), row detail text (§"What it looks like").
   - `LibraryContents.tsx` (the tab), `ContentsRow.tsx` (section heading and source row), `SectionMenu.tsx` (⋯), `MovePicker.tsx` (Move to… / Add sources here…), `contents.css` (tokens only).
   - Move `TopicSuggestionsPanel` and `TopicOverviewPanel` here as `SectionSuggestions` / `SectionOverview`, wording "section"; delete `TopicManager.tsx` when nothing uses it.
4. `KnowledgeBaseWorkspace.tsx`: tabs **Contents · Ask · Outputs**; Contents renders the library description, the toolbar (Filter, + Section, Import folder via `BulkImport`, Add source with the existing kinds menu: Document, Photo or scan, Web page, Recording → `onAdd(kind)`), and `LibraryContents`. Remove `OverviewView`, `MaterialsView` and the drawer. Keep the bundled references of demo libraries (`base.sources`, shown when the library has no indexed sources) as a read-only "References" list at the bottom. Refresh on `gunther:sources-updated` and `gunther:topics-updated`.
5. `atlas.ts`: `AtlasMode = "contents" | "ask" | "outputs"`; anything stored or passed as `"overview"`/`"sources"` maps to `"contents"` (search App/Home for both). `HomePage`'s `onOpenBase(baseId, "sources")` → `"contents"`.
6. Ask this section: the existing `askTopic` flow (create a session with `focusChapterId`, switch to Ask).
7. Optimistic moves: apply the move to local state at drop, send it, replace with the server's contents; on error, restore and show the error in the toolbar line (`role="alert"`).
8. `SourceEvidence.tsx`: "topic" → "section" in the UI text.
9. `scripts/smoke_desktop_ui.mjs`: click "Contents" instead of "Sources" and find the filed note's row in the new markup (keep a stable class like `button.contents-source` with the title in `strong`).
10. Remove now-unused CSS for `.material-*` / `.base-overview*` only if nothing else uses it (grep).

### Tests

- `contentsTree.test.ts`: tree from positions; not placed newest first; collapse hides children; filter keeps headings; natural order (Lecture 2 < Lecture 10, case-insensitive, Chinese titles keep a stable order); drop on a heading → end of it; drop between rows → that parent and index; a section onto its descendant → null; counts.
- `LibraryContents.test.tsx` (mock `knowledgeApi`): renders headings, rows, details and Not placed yet; + Section creates and focuses the title; Move to… sends §4.3 with the chosen section; multi-select bar; Sort by name sends §4.4 in natural order; Delete section shows the counts and calls §4.5; collapsed state persists; a failed move restores the rows and shows the error.
- Workspace: three tabs; `"sources"` mode opens Contents.
- `npm run typecheck`; existing desktop tests pass (update tests of removed views).

### Done when

Tests and typecheck pass; Opus looks at the real screen (seed a demo library with sections, lectures, PDFs and papers per the "look at the UI" recipe) before reporting; progress-log entry.

## Pitfalls

- Positions are per parent and shared by sections and sources; renumber inside the same transaction as the move.
- `parent_id` is `ON DELETE RESTRICT`: delete deepest sections first.
- Don't load every source's full `content` to read a duration — select only the first line (`substr`) or the `Duration:` match; libraries can hold 1 000+ sources.
- dnd-kit: give every draggable a stable id (`section:<id>` / `source:<id>`), use a `PointerSensor` with a small activation distance (so clicks still open a source) and the `KeyboardSensor`; render the drag overlay in a portal on `document.body` (fixed layers inside `.page-enter` misplace, see the "fixed layers" note in the design doc).
- The filter and collapsed sections must not hide the drop targets of an ongoing drag in surprising ways: disable drag while filtering.
- Keep "Not placed yet" out of Ask scoping: it is not a section.

## Review checklist (Opus)

- [ ] One place per source per library, enforced by the database.
- [ ] Order is the user's and survives reloads; Sort by name is numeric-aware.
- [ ] No source is ever deleted by section operations.
- [ ] Ask this section, section overviews and suggestions still work.
- [ ] Words: "section", never "topic", in the interface.
- [ ] White/black, colour only for kind identity; calm headings; no tinted panels.
- [ ] Library folder `Overview.md` mirrors the contents.
- [ ] Smoke script updated.

## Progress log

(Add entries below: date, phase, who, what changed, tests, deviations.)

### Phase L1 — 2026-10-12 (Sonnet)

Backend contents API done. Files: `gunther/models.py` (`TopicSourceLink.knowledge_base_id`, `position`, unique index `ux_topic_sources_base_source`), `gunther/migrations.py` (migration 25 `library_contents`: backfill, keep earliest of repeated placements and log the count, unique index, renumber sections first then sources), `gunther/topics.py` (one-place `_file`, `_children`/`_renumber`, `contents`, `move`, `order`, `delete`, row details), `gunther/knowledge_api.py` (`GET /contents`, `POST /contents/move`, `PUT /contents/order`, `DELETE /topics/{id}`; create uses `exclude_unset` so an omitted position means "end"), `gunther/library_topics.py` (unfiled = not placed in this library, via `knowledge_base_id`), `gunther/service.py` (unknown focused section means whole library), `gunther/library_folders.py` (`Overview.md` = arranged contents with `##`/`###`/`####` headings, `## Not placed yet` (or `## Sources` when there are no sections); files under `Sections/`).

Tests: new `tests/test_library_contents.py` (15), new migration test in `tests/test_migrations.py`. Full backend suite 734 passed, 1 skipped; ruff clean.

Existing tests changed on purpose: `test_migrations.py` (expected history gains 25; three `== 24` version asserts from the transcript-upgrade phase became `== 25`), `test_papers.py` (`0 sections`, `Sections/Rockets.md`, new Overview wording).

Details and deviations:
- Row details come from five grouped queries over the library (durations use `substr(content, 1, 2000)` of recordings only; page count read inside SQLite with `json_extract`; no per-source queries). Duration also accepts `Duration: N seconds` (what `lecture.py` writes) besides `h:mm:ss`/`m:ss`.
- `processing` mirrors `KnowledgeIndex.processing()` (latest `parse_asset` job unless completed, else head revision state): queued/running/pending -> `reading`, failed -> `failed`, rest `ready`. A source with no job and no head therefore reads `reading` (as the app shows elsewhere).
- Trashed sources keep their placement row and their position; `index` in move counts only visible (live) children, and `order` appends hidden ones after the requested list.
- `authors` is the short form ("Ada Lovelace et al."; one author as is); `null` when unknown.
- In `migrations.py` the repeated-placement delete is row by row after a select (a bulk `DELETE ... IN (window query)` failed on old test databases whose `sources` table did not exist, because of foreign keys).
- An unknown section for a move target is 404; a section/source of another library is 422; a trashed source is 404.
- Delete clears `focus_chapter_id` inside `TopicService.delete` (same database) rather than in `service.py`.
- Not done (per phase scope): contracts and desktop. Moving sources does not bump the version of the target section; only moved sections are bumped.

### Opus review of L1 — 2026-10-12

Checked against the plan and the checklist; one change: `Overview.md` printed a section's own sources after its sub-sections when that was the user's order, so in Markdown they read as part of the last sub-section (no way back out of a sub-heading). The folder file now lists a section's own sources first, then its sub-sections, each in the user's order; the app keeps the exact order. `test_library_contents.py`, `test_library_folders.py`, `test_papers.py`: 35 passed; ruff clean. Left as built: `processing` mirrors `KnowledgeIndex.processing()` (sources without a head are indexed by the start-up backfill, so "Reading…" doesn't stick); authors split on commas, as `SourcePaper.authors` is written.

### Phase L2 — 2026-10-12 (Sonnet)

The Contents tab is built on the desktop; nothing was looked at on a real screen (jsdom tests only), and drag and drop has not been tried with a real pointer.

Packages (installed with `npm install` in `apps/desktop`, `package.json` and the root `package-lock.json` updated): `@dnd-kit/core` 6.3.1, `@dnd-kit/sortable` 10.0.0, `@dnd-kit/utilities` 3.2.2.

Files:
- `packages/contracts/src/index.ts`: `ContentsSection`, `ContentsPlacement`, `ContentsSource`, `LibraryContents`, `ContentsItemRef`, `ContentsMoveInput`, `ContentsOrderInput`; `TopicInput.position` is now optional (omitted = end of its parent, as the backend does).
- `apps/desktop/src/api.ts`: `knowledgeApi.contents`, `moveContents`, `orderContents`, `deleteSection`.
- New `apps/desktop/src/contents/`: `contentsTree.ts` (pure: `buildTree`, `visibleRows`, `dropTarget`, `applyMove`, `isDescendant`, `naturalOrder`, `sectionCount`, `rowDetail`, `subtreeStats`, `sectionList`), `LibraryContents.tsx`, `ContentsRow.tsx`, `SectionMenu.tsx` (also the generic `PopMenu`), `MovePicker.tsx`, `SectionPanels.tsx` (`SectionSuggestions`, `SectionOverview`, moved from `TopicManager.tsx`, wording "section"), `contents.css`, and tests `contentsTree.test.ts` (24), `LibraryContents.test.tsx` (21).
- `pages/KnowledgeBaseWorkspace.tsx`: tabs Contents, Ask, Outputs; `OverviewView`, `MaterialsView` and the source drawer removed; `ContentsView` = library note, toolbar (Filter, + Section, Import folder, Add source menu), `LibraryContents`, and a read-only "References" list when a library has no sources but bundled references. New `pages/KnowledgeBaseWorkspace.test.tsx` (5).
- `atlas.ts`: `AtlasMode = "contents" | "ask" | "outputs"` and `normalizeAtlasMode` (maps "overview"/"sources" to "contents"; used by `App.openBase` and the workspace). `HomePage` opens `"contents"` (test updated).
- `components/TopicManager.tsx` deleted. `StructuredKnowledge.test.tsx` lost its TopicManager tests (moved into `LibraryContents.test.tsx`); `SourceEvidence.tsx` says "section".
- `components/BulkImport.tsx`: optional `compact` (one "Import folder" button for the toolbar; status below it).
- `knowledge.css`: topic-manager / topic-suggestion / topic-overview rules removed (evidence rules kept).
- `scripts/smoke_desktop_ui.mjs`: clicks "Contents" and finds the note in `button.contents-source strong`. Only `node --check` was run.

Tests: `npx vitest run` over the whole desktop suite: 71 files, 488 tests passed; `npm run typecheck` clean. One unhandled rejection is reported from `src/services/singleFlight.test.ts` (a file this phase did not touch).

Details and deviations:
- Drag and drop uses `useDraggable`/`useDroppable` from `@dnd-kit/core` only (rows are a flat list with custom drop rules, which fits that better than `SortableContext`); `@dnd-kit/sortable` and `@dnd-kit/utilities` are installed as asked but not imported. Drag starts from the handle only; `PointerSensor` activation distance 6 px, `KeyboardSensor` too (arrow keys move 25 px; the Move to… picker is the real keyboard path). The drag chip is a `DragOverlay` portalled to `document.body`. Drag is off while filtering. A multi-row selection is dragged together when a selected row is picked up.
- Drop position: on a section heading, the top quarter means "before it", the rest "into it (at the end)"; on a source row, the upper half is before, the lower half after. A drop beside a top-level section is refused for sources (they cannot sit at the top level). `dropTarget` returns the index among the children that stay, as the backend's `move` expects.
- A section added with + Section or Add sub-section is created at once as "New section" and its name field opens focused with the text selected; Esc on a never-named new section deletes it again. Rename also has a one-line note field.
- Delete section is an inline confirmation under the heading (no modal), with the counts from the loaded tree.
- `Overview` of a section writes from papers only (as the backend does), so the Write button needs at least one paper below the section.
- The old `.material-*`, `.base-overview*` and `.source-detail-*` CSS (in `atlas.css` and `design/legacy.css`) is now unused by any component but was NOT removed: those selectors are grouped with live ones in many rules and I could not check the screen. Safe to clean later.
- The workspace still loads `knowledgeApi.topics` for Ask's section choices (reloaded through `onChanged` and `gunther:topics-updated`).
- Not verified without a real screen: layout and spacing of rows, the hover-only handle/checkbox/⋯, drop line positions while dragging, menu placement near the window edge, the Move to… dialog look, dark mode, the toolbar wrapping with the import status, and the keyboard drag.

### Opus review of L2 — 2026-10-12

Looked at the real screen (demo library: a course with parts, lectures with recordings and slides, readings with papers, two unplaced sources; backend + Vite + headless Chromium at 1440×900). The contents read like a table of contents: serif part headings, semibold lecture headings, durations beside recordings, "Not placed yet" last; the section ⋯ menu, the selection bar and Move to… look right. Headless Chromium reports `hover: none`, which shows every row's handle, checkbox and ⋯ (the `@media (hover: none)` rule for touch screens); with that rule overridden, they appear only on the hovered row, as intended for the Mac.

One change: dragging measured the drop from dnd-kit's translated rectangle of the dragged row, which came out ~80 px off after the page had scrolled, so the drop line said "after Lecture 11" while the drop put it before; the `DragOverlay` chip trailed the pointer by as much. Now the pointer decides: its position is followed during a drag (from the activator event, then `pointermove`), the target row's rectangle is read from the DOM at that moment, and the chip is a fixed element beside the pointer (portalled to `body`). Checked with real mouse events: between rows and onto a heading, the line or box shown is where the item lands, and the saved order matches.

Also fixed, not from this phase: `runOnce` (`services/singleFlight.ts`, live transcript) left an unhandled rejection when its body failed, which makes `npx vitest run` exit 1 (CI's desktop job). Full desktop suite: 71 files, 488 passed, no errors; typecheck and lint clean. Chrome smoke (`scripts/smoke_desktop_ui.mjs`): passed, 42 checks.

