# Library screen redesign and "search and build" — build plan

Status: agreed with the owner on 2026-11-24; being built batch by batch. Read [AGENTS.md](../AGENTS.md) first.
When a batch ships, fold what it built into the shipped docs ([Library](LIBRARY.md), [Ask](ASK.md), [Outputs](OUTPUTS.md), [Capture](CAPTURE.md)).

## 1. Agreed Decisions (Owner-approved, 2026-11-24)

- **D1 · Left column (`Library`):** One column with three collapsible sections: **Chats** (compact top: Pinned + top 4 recent, expandable for full search & archive), **Sources** (primary scrolling tree with user sections and scope ticks), and **Key results** (its own bottom collapsible section, unbundled from `SourceTree`). Sticky `+ Add source` footer below.
- **D2 · Right column (`Inspector`):** One right-hand column everywhere with two tabs: **`Evidence`** and **`Studio`**. No 4th column at any width. **Explicit-click switching only:** finishing an answer or opening an output updates the `Evidence (N)` count/content in place and never switches tabs behind the user's back; only clicking a tab, `N sources`, `[n]`, a Key result, `Trace`, or a Studio tile/card changes modes.
- **D3 · Unified Inspector detail:** Inside a library, clicking `[n]`, a cited card, a Key result, or `Trace` drills into the Right `Evidence` column (`← All sources`, `‹ ›` steppers) instead of spawning floating portals (`EvidencePanel`) or modals (`ask-trace-overlay`). `Brief` moves from the Chat header into `Evidence`.
- **D4 · Unified URL import (no site lists):** `+ Add to library` extends existing `WebCaptureService` (`POST /api/captures/web`). Direct PDFs—and HTML pages declaring `<meta name="citation_pdf_url">` or `<link rel="alternate" type="application/pdf">`—import as full PDFs via `AssetService.capture()` (Docling + `papers.py` + `WebSnapshot`). If a declared PDF fails/is paywalled, save the HTML page with a plain note. If the page itself is blocked, show the error + Retry + `Open in browser ↗` (never a fake capture).
- **D5 · Studio Web search & provenance:** Expose the existing backend `Web` toggle (default off, same Tavily check as Ask) in Studio build options and Refine. Outputs never enter `Sources` (no "Save as note"); they grow the library through **Key results** and imported web/paper finds. Expose Studio's already-saved `approach.memo` / `study_map` as `Research notes` in the output's `Evidence` view.
- **D6 · Per-run search storage, zero new tables in v1:** Store ≤20 uncited `searched` hits and `keyResultsUsed` inside `SessionMessage.context_json` and sealed `Artifact.provenance_json` (sealed before `artifact_manifest_hash`, never sent to any model). Compute `librarySourceId` ("✓ In library") at read time by canonical URL / DOI / arXiv ID against live, in-library, non-trashed `WebSnapshot` and `SourcePaper` rows. No `library_finds` table, no library-wide Found list, and no post-import key-results re-check in v1.
- **D7 · Visual discipline:** White and black monochrome `--gx-*` tokens everywhere (`session.css` migrated off `--atlas-green`); colour reserved strictly for identity (library dot, source-kind icon, errors). Continuous `44px` header across all three columns; three surface tiers (`--gx-sidebar` left, `--gx-canvas` middle, `--gx-surface-2` right).

## 2. Shared Layout & Responsive Rules

- **Widths:** `1728px` (rail collapsed `56px` → `1672px`): Left default `288px` (min `200`, max `360`), Middle `1004px` (`740px` centered measure), Right default `380px` (min `280`, max `50%` when viewing Original PDF else `440`). `1280px` (`1224px` workspace): Left `256px`, Middle `632px`, Right `336px`.
- **Breakpoints:** `<1080px`: Right collapses to a `44px` rail with stacked `Evidence` and `Studio` icon buttons; clicking either opens Right as an in-workspace slide-over (`position: absolute; right: 0; width: min(380px, 85vw)`). `<840px` (down to `800px` min): Left also collapses to a `44px` rail and opens as a left slide-over (`width: min(300px, 85vw)`). Wide readers (Slides, Mind map) still auto-collapse Left below `1200px`.
- **Esc precedence (top-most first):** (1) App modals (`App.tsx`: Capture, library sheets, shortcut sheet) → (2) inline inputs/popovers (`TitleEditor`, `MovePicker`, `PopMenu`, `BuildOptions`, `renameOpen`, `Brief` edit, focused filter clear) → (3) Right `EvidenceDetail` / `Trace` steps back to `Evidence` list → (4) narrow-width slide-over drawer closes → (5) expanded `Chats` search collapses back to compact view.
- **Migrations across P1–P4b:** **None.** All new fields live in existing JSON columns (`SessionMessage.context_json`, `Artifact.provenance_json`) with backward-compatible defaults; read-time lookup joins indexed `knowledge_base_sources.knowledge_base_id` + `web_snapshots.source_id` + `source_papers.source_id`.

- **Build batches (each one reviewed and committed before the next):**
  - **P4a1** — `searched` and `key_results_used` stored on answers and outputs, read-time `library_source_id`.
  - **P4a2** — `WebCaptureService` PDF and `citation_pdf_url` import, same-library dedupe.
  - **P4b** — desktop search and build (Add to library, Add all cited, Also searched, Key results used, Studio Web switch, Research notes).

---

## Batch P4a · Search and Build (Backend)

### 1. Goal
Persist capped uncited search hits and used key results on Ask answers and Studio outputs, project live `"In library"` source IDs at read time, and extend `WebCaptureService` so importing a PDF URL or a scholarly HTML page with standard PDF metadata ingests a full PDF paper. No new tables or migrations.

### 2. Files, Schemas, Contracts, Endpoints, Migrations
- **Add:**
  - `apps/backend/gunther/library_lookup.py` — small helper `resolve_library_sources(session, base_id, citations, searched)`: canonicalizes web URLs via `web_capture.normalize_web_url()` (make the existing `_normalize_web_url` public; one canonical-URL rule for capture and lookup), extracts optional DOI / arXiv ID via `papers.DOI` / `papers.ARXIV`, runs one query over the library's live non-trashed `WebSnapshot` (`original_url`, `final_url`) and `SourcePaper` (`doi`, `arxiv_id`) rows, and returns `{ url -> source_id }` for read-time `library_source_id` projection. Also helper `pack_searched(pool_items, used_keys, grades, cap=20)` (web hits first up to 12, then library hits, deduped by `(source_id, block_id)` or canonical URL).
- **Change:**
  - `apps/backend/gunther/schemas.py` & `packages/contracts/src/index.ts`:
    - Add `SearchedHitOut` / `SearchedHit`: `{ kind: "library" | "web"; title: string; url: string | null; sourceId: string | null; locator: string; snippet: string; query: string; kept: boolean; librarySourceId?: string | null }`.
    - Add `UsedKeyResultOut` / `UsedKeyResult`: `{ id: string; text: string; refs: number[] }`.
    - Add optional `library_source_id: str | None = None` (`librarySourceId`) on `ConversationCitationOut` / `ConversationCitation` (read-time only; `exclude_if=lambda v: v is None` or never persisted in `citations_json`).
    - Add optional `searched: list[SearchedHitOut] = Field(default_factory=list)` and `key_results_used: list[UsedKeyResultOut] = Field(default_factory=list)` on `ConversationContextOut` (`ConversationContext`) and `OutputProvenanceOut` (`OutputProvenance`).
    - Add optional `note: str | None = None` on `WebCaptureOut` / `WebCaptureResult` (plain notice when a page's linked PDF was unavailable and HTML was saved instead).
  - `apps/backend/gunther/agent.py` — record per-round search queries and grader `kept` flags for uncited items; return `searched: list[SearchedHitOut]` (via `pack_searched`, ≤20) and `key_results_used: list[UsedKeyResultOut]` (from `matches` mapped to reading-order `used` refs) on `AgentResult`. Confirm `conversation_history()` and `conversation_pool()` ignore `context.searched`.
  - `apps/backend/gunther/ask_turns.py` — store `searched` and `key_results_used` in `ConversationContextOut` on save; in `_message_out()` (when `knowledge_base_id != HOME_SCOPE`), annotate web `citations` and `context.searched` with read-time `library_source_id` via `resolve_library_sources()` (never mutating stored JSON).
  - `apps/backend/gunther/artifact_turns.py`, `artifact_versions.py`, `artifact_read.py`, `output_material.py` — collect uncited hits from `_studio_web` / `ArtifactAgent` pool + `key_results_used` from `studio_reading()` (`artifact_turns.py:375`); include `searched` and `key_results_used` in `OutputProvenanceOut` **before** `artifact_manifest_hash()` computes the seal; in `artifact_detail()` (`artifact_read.py:253`), annotate web `citations` and `provenance.searched` with read-time `library_source_id` via `resolve_library_sources()` **after** `_verified_payload()` verifies the seal!
  - `apps/backend/gunther/web_capture.py` — extend `WebCaptureService` (same `POST /api/captures/web` endpoint, same `PublicWebUrlPolicy` + `PinnedHttpFetcher` SSRF guards):
    - Broaden `Accept` header to include `application/pdf`; accept `application/pdf` or `%PDF-` magic bytes up to `asset_service.MAX_PDF_INPUT_BYTES` (64 MB).
    - **PDF path:** ingest downloaded bytes through `AssetService.capture()` (`kind="paper"` or `"file"`, letting `processing_worker` + Docling + `papers.py` parse & classify) and persist a `WebSnapshot` row linking `(source_id, asset_id, original_url, final_url, content_hash, client_capture_id, request_fingerprint)`.
    - **HTML path:** parse standard open HTML tags (`<meta name="citation_pdf_url">`, `<link rel="alternate" type="application/pdf">`, resolving relative `href` via `urljoin`). If present, attempt one policy-checked fetch (same timeout budget): if 200 and valid `%PDF-`, run the PDF path (keeping landing URL as `original_url` and PDF URL as `final_url`); if 401/403/404/timeout/non-PDF, fall back to normal HTML capture and set `WebCaptureOut.note = "Saved the web page; the linked PDF required separate access."`.
    - **Same-library dedupe:** before fetching (or after redirect), if this `knowledge_base_id` already has a live non-trashed `WebSnapshot` matching `original_url`/`final_url`, return that source (`duplicate=True`) instead of creating a duplicate.
- **Endpoints added:** None (reuses `POST /api/captures/web`, `POST /api/sources`, `GET /api/sessions/{id}`, `GET /api/knowledge-bases/{id}/artifacts/{id}`).
- **Migrations:** None.

### 3. Behaviour Rules & Edge Cases
- **No site lists (`AGENTS.md`):** Zero domain names (no `arxiv.org`, `nature.com`, etc.) in `web_capture.py`; PDF detection uses only `Content-Type` / `%PDF-` bytes and standard `<meta name="citation_pdf_url">` / `<link rel="alternate" type="application/pdf">`.
- **Old answers & sealed outputs:** Missing `searched` / `key_results_used` deserialize to `[]`. `_verified_payload()` runs on raw stored `provenance_json` / `citations_json` BEFORE read-time `library_source_id` enrichment, so every existing sealed version passes integrity verification unchanged.
- **Home chats filed later:** On `HOME_SCOPE`, `library_source_id` is omitted (`None`). Once `file_home_session` moves the session into a library, subsequent reads automatically resolve `library_source_id` against that library.
- **Trash & restore:** `resolve_library_sources` filters `Source.trashed_at.is_(None)` and `KnowledgeBaseSource.knowledge_base_id == base_id`; trashing a source clears `library_source_id` immediately, restoring brings it back.
- **Blocked / paywalled pages (403/401/timeout):** `POST /api/captures/web` returns its normal error status (502/504/422) with clear message — never creates a fake empty capture.
- **Model prompt isolation:** `searched` is never read by `conversation_history()`, `conversation_pool()`, or `artifact_workspace.pool_from()`.

### 4. Tests & Docs
- **Backend tests:**
  - `apps/backend/tests/test_web_capture.py` — add tests (faked fetcher/resolver, no network): direct PDF URL creates PDF `Asset` + `Source` + `WebSnapshot`; HTML page with `<meta name="citation_pdf_url">` fetches and stores the PDF; paywalled/403 linked PDF falls back cleanly to HTML with `note`; duplicate URL in same library returns existing live source; trashed source allows clean re-capture.
  - `apps/backend/tests/test_ask_exam.py` (or a new `apps/backend/tests/test_search_and_build.py`) — test: uncited hits capped at 20 (web prioritized ≤12) stored in `context.searched` and excluded from next turn's model prompt; `key_results_used` recorded; read-time `library_source_id` matches by URL, DOI, and arXiv ID, flips off on Trash and back on Restore, and resolves after `file_home_session`.
  - `apps/backend/tests/test_artifact_build_turns.py` — test: Studio build with `web=True` seals `provenance.searched` and passes `artifact_manifest_hash` verification, projects read-time `library_source_id`, and old sealed artifacts without `searched` still verify.
  - Run: `pytest -q tests/test_web_capture.py tests/test_artifact_build_turns.py tests/test_search_and_build.py`.
- **Docs:** Update `docs/CAPTURE.md` (PDF URLs and `citation_pdf_url` capture), `docs/ASK.md`, and `docs/OUTPUTS.md`.

### 5. Reviewer Checklist
- Confirm zero domain/publisher names in `web_capture.py` or `library_lookup.py` (`AGENTS.md`).
- Confirm `resolve_library_sources` runs AFTER `_verified_payload()` and never writes back to `citations_json` or `provenance_json`.
- Confirm `Pdf` fetch follows `PublicWebUrlPolicy` on every redirect (no SSRF bypass via `citation_pdf_url` pointing to `127.0.0.1`!).
- Confirm `conversation_history()` and `conversation_pool()` do not include `context.searched`.

---

## Batch P4b · Search and Build (Desktop)

### 1. Goal
Wire one-click `+ Add to library` (and `Add all cited`), `Also searched, not cited`, `Key results used`, Studio's `Web` toggle, and Studio `Research notes` into the Right Inspector and Studio controls. When done, the full "search and build" loop works end-to-end across Chat, Research, and Studio.

### 2. Files, Components, Contracts, Migrations
- **Add:**
  - `apps/desktop/src/pages/useImportWebFind.ts` — small hook managing per-URL import state (`idle | importing | imported | error`), calling `knowledgeApi.captureWeb({ url, title, knowledgeBaseId: baseId, clientCaptureId: crypto.randomUUID() })`, dispatching `gunther:sources-updated` on success (so `SourceTree` refreshes immediately and shows the reading/ready source), showing `result.note` via `onNotify` if HTML fallback occurred.
- **Change:**
  - `apps/desktop/src/pages/EvidencePane.tsx` — render:
    - **`Add all cited (K)`** button in `Cited` header when ≥1 cited web source lacks `librarySourceId` (runs imports one by one with live per-row progress and per-row error reporting).
    - On every `kind === "web"` card (both `Cited` and `Also searched`): show **`✓ In library · Open`** (opens `SourceSummary` in middle) if `librarySourceId` or local imported map has a source ID; otherwise show **`+ Add to library`** + **`Open ↗`**. On import failure, show the error inline + **`Retry`** + **`Open ↗`**.
    - **`Key results used (K)`** collapsible section (when `keyResultsUsed.length > 0`): each sentence links to its `[n]` passage(s).
    - **`Also searched, not cited (M)`** collapsible section (when `searched.length > 0`, showing `"M more (W online)"` in summary; auto-lists web hits first with `+ Add to library`, library hits clickable to open passage).
    - For open Outputs: collapsible **`Research notes`** block at the top of `Evidence` showing `provenance.approach?.memo?.answer` and `approach?.gaps` / `study_map?.limitations` when present.
  - `apps/desktop/src/components/evidence/EvidenceDetail.tsx` — add **`+ Add to library`** (or **`✓ In library · Open source`**) right next to `Open <host> ↗` in `WebEvidence` footer when `onImportWeb` is supplied (in Library; omitted on Home if no target library).
  - `apps/desktop/src/outputs/StudioColumn.tsx` & `OpenOutput.tsx` — add **`Search the web`** toggle (default `false`, wired to existing `useWebSearch()`, disabled with Tavily Settings tooltip when unavailable) to `BuildOptions` (sends `build.web: true`) and to `OpenOutput`'s Refine form (sends `web: true`).
- **Contracts / Backend / Migrations:** None (contracts updated in P4a).

### 3. Behaviour Rules & Edge Cases
- **Immediate state sync after import:** Importing a URL from `Evidence` immediately switches that card (and any other card in the same view with the same URL) to `✓ In library · Open`, fires `gunther:sources-updated` so the new source appears in `Sources` under `Not placed yet` (showing `Reading…` → `Ready`), and keeps the user's current `selection` ticks intact (or includes it automatically if `selection.mode === "all"`).
- **Trashed source:** After trashing a source via `SourceTree`, `gunther:sources-updated` clears any locally cached `imported` map entry for that `sourceId` and reloads the active session/artifact so the card reverts to `+ Add to library`.
- **Failed / blocked import:** Never hides errors or fakes a capture. Displays the server error message inline on that row with `Retry` and `Open ↗`. To keep something from a blocked page, the owner uses Add source → Note.
- **Studio Web toggle without Tavily key:** Visible but disabled with tooltip `"Add a Tavily key in Settings → Search to enable web search"` (matching Ask's composer).
- **Outputs with no memo or no web hits:** `Research notes` and `Also searched` sections simply do not render—zero empty clutter.

### 4. Tests & Docs
- **Desktop tests:** Update `apps/desktop/src/pages/KnowledgeBaseWorkspace.test.tsx`, `SessionWorkspace.test.tsx`, and `apps/desktop/src/components/evidence/EvidencePanel.test.tsx` — test: `+ Add to library` calls `captureWeb` with `knowledgeBaseId`, dispatches `gunther:sources-updated`, flips card to `In library · Open`; `Add all cited` imports multiple web hits; blocked import shows inline error + `Retry` + `Open ↗` and creates nothing; `librarySourceId` from server renders `In library · Open`; `Also searched` and `Key results used` render cleanly; Studio `BuildOptions` and `OpenOutput` Refine send `web: true` when Web toggle is enabled; `Research notes` displays `approach.memo`.
  - Run: `npx vitest related --run --maxWorkers=1 src/pages/KnowledgeBaseWorkspace.tsx src/pages/EvidencePane.tsx src/pages/useImportWebFind.ts src/components/evidence/EvidenceDetail.tsx src/outputs/StudioColumn.tsx src/outputs/OpenOutput.tsx` + `npm run typecheck`.
- **Docs:** Update `docs/LIBRARY.md`, `docs/ASK.md`, and `docs/OUTPUTS.md` with the shipped search-and-build workflow, Studio Web toggle, and Research notes.

### 5. Reviewer Checklist
- Verify `+ Add to library` never creates anything on error (no excerpt or placeholder source).
- Verify Studio `Web` toggle defaults to `false` and respects Tavily availability.
- Verify no `Save as note` action was added for outputs (respecting D5) and no library-wide Found tray or new tables were added (respecting D6).
- Verify `HomeAsk` and `DocumentView` still render `EvidencePanel` cleanly without broken props.
