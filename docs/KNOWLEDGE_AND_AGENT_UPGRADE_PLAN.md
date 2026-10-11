# Gunther knowledge base and agent upgrade plan

Planning handoff for GPT-6.1 Sol · 2026-10-03 · reviewed against commit `58b3450` on `main` · database schema 21.

**Recommendation:** improve the document representation and the Agent's evidence handling before adding more prompts or increasing reasoning everywhere. Keep SQLite, the current source revisions, the model gateway, the existing Skills runner, and reveal.js. Several features discussed earlier have now been implemented; this plan extends them instead of rebuilding them.

The intended result is a knowledge base that preserves a paper's structure, figures and references; an Agent that searches when the task needs evidence and accounts for what it actually checked; and Reports/PPTs that use a deliberate argument and suitable presentation forms. This document specifies future changes. No application code was changed while preparing it.

## 1. Priorities at a glance

| Priority | Problem in the current code | Recommended change | User-visible result |
| --- | --- | --- | --- |
| First | Search chunks also serve as the reader's document representation. | Preserve canonical document elements separately; derive search chunks from them. | Paragraphs, lists and references stop becoming long, reconstructed blocks of text. |
| First | The original PDF survives, but parsed figures have no retained image asset. | Export figures into the managed asset store and link them to captions and source coordinates. | Images remain visible after import, restart and backup/restore. |
| First | Bibliography text has no structured connection to mentions in the body. | Store reference entries and citation mentions, with explicit resolved/ambiguous/unresolved links. | A reference marker can open its bibliography entry without inventing a match. |
| First | The optional Docling adapter requires an external runtime; its subprocess imports Unix-only `resource`. | Prove a packaged CPU runtime on macOS and Windows early, then add tested acceleration. | Installation does not require users to set up Python, Docker or CUDA tools. |
| Next | A successful Checker call can cover only part of an answer. | Check bounded batches and persist coverage for every claim and its supporting spans. | “Checked” reflects actual coverage; omitted or failed checks remain visible. |
| Next | Search limits apply to gathering, while later claim lookups can search every tool again. | Share a run budget and retrieval policy across gathering, checking and revision. | Short tasks stay short; research stops predictably. |
| Next | Deep research counts passages as sources; source reading loses precise provenance. | Track independent works and read revision-pinned document spans. | Better comparisons, fewer duplicate sources and citations that open the actual supporting text. |
| Then | Report/PPT methods exist, but content and numeric checks need stronger guarantees. | Strengthen material cards, global editing, chart data provenance and final review. | Better argument, explanation and layout choices without changing the presentation format. |

The first implementation deliverable is the evaluation baseline and runtime feasibility check in section 8. These come before tuning models or claiming quality improvements.

## 2. What the updated repository already provides

The two latest commits materially change the starting point:

- **Ask:** `58b3450` adds sentence checking, reading further, a conversation brief, explicit `/` Skills and `/research`. Ordinary tasks can already answer without searching through `new_facts=false` and the rework path. Deep research already has framing, sub-questions, standard/deep budgets, progress and stopping behavior.
- **Outputs:** `13f9b3c` adds `understand → structure → write → edit_review → reader_test → revise`. It already creates an approach and material cards, chooses slide layouts, reviews the complete draft, revises selected sections and checks changed sections. Both Reports and PPTs can already supplement through Web when enabled.
- **Knowledge storage:** immutable original assets, `SourceRevision`, `SourceIndexHead`, `ContentBlock`, FTS5, sqlite-vec, durable processing jobs, paper metadata, work grouping and derived digests already exist. Historical citations and topic evidence can pin an old revision.

Preserve these behaviors. In particular, automatic Skill selection is currently disabled by default; the ordinary planner uses `low` and the Checker uses `off`. Ask keeps contradicted claims marked `[d:n]`, while Outputs removes contradicted claims. This plan does not silently reverse those choices.

“Skill” means the method files Gunther already loads through `skillbook.py`: instructions, steps, references, tools and checks. It does not require Claude's runtime or a provider-specific Skills API. The existing model gateway remains the provider boundary.

### Code observations behind this plan

| Location | Observation and implication |
| --- | --- |
| [document_parser.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/document_parser.py:36) | The adapter reduces Docling output to `ParsedBlock`s, slices text at 1,200 characters, retains table data and raw geometry, but does not export picture assets or caption relations. Reference labels fall through to ordinary paragraphs. Heading context propagates to children, not following siblings. |
| [processing.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/processing.py:180) | Fallback extraction runs before Docling. Reprocessing appends newly extracted text to `source_content`, which can already contain an extracted body. Fallback selection treats a page as covered when it has some structured text, then appends uncovered blocks after the structured document. This needs explicit deduplication and reading order. |
| [DocumentView.tsx](/Users/keke/Documents/git/Gunther/apps/desktop/src/items/views/DocumentView.tsx:96) | The reader rejoins indexed blocks by document reference or adjacent offsets. A figure renders as an icon and text. It cannot reconstruct information the parser discarded. |
| [asset_service.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/asset_service.py:218) | HTML extraction ignores image attributes; DOCX extraction reads text from `document.xml`; EPUB follows sorted filenames. Images, relationships and EPUB spine order need preservation. |
| [agent.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/agent.py:1549) | `check_sentences` processes the first 20 sentences, or 40 for research, and stops adding sources at 24,000 characters. Missing model verdicts are ignored. `checked` currently means a Check object was returned, not complete coverage. |
| [agent.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/agent.py:1649) | `look_up` calls each offered search tool without sharing gathering counters or deduplication. `Budget.calls` explicitly counts gathering calls, not total run calls. |
| [agent.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/agent.py:82) and [service.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/service.py:3630) | Pool identity is title/URL plus a text prefix. `_passage_block` searches by quote equality despite citations already carrying `block_id`. Expanded context is not a structured set of cited source spans. |
| [brief.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/brief.py:191) | A source-backed “settled” item requires a valid pool reference, but is not checked against the verdict of the claim it summarizes. A reference's existence alone is insufficient. |
| [skill_runner.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/skill_runner.py:876) and [skill_checks.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/skill_checks.py:227) | Whole-document review already exists. Revisions are bounded and local. The chart check detects missing citation syntax, not whether a cited passage contains the plotted value, unit and comparison. |

These are code observations, not measured failure rates on the user's library. Establish those rates with the fixtures below.

## 3. Knowledge base design

Use four layers with explicit responsibilities:

```text
Original PDF / captured page / file                    preserved bytes
                ↓ local parser
Document revision: elements + images + references     readable source of truth
                ↓ structural chunking
ContentBlock + FTS5 / sqlite-vec                       retrieval passages
                ↓ scoped search and source reading
Evidence snapshots → Ask / research / Report / PPT    attributable answers
```

### 3.1 Preserve document elements; keep ContentBlock compatible

Add a canonical document representation alongside the existing tables. **Keep `ContentBlock` as the retrieval/evidence passage type.** Do not reinterpret all existing block IDs as document nodes or replace the database. This limits disruption to retrieval, citations, topics and saved outputs.

Proposed records, with final naming following repository conventions:

| Record | Minimum contract |
| --- | --- |
| `DocumentElement` | Revision, stable ID within that revision, parent, reading-order ordinal, type, text, heading path, source-native identifier, source spans and typed payload. Types include heading, paragraph, list item, table, figure, caption, equation, footnote and reference entry. |
| `ElementAsset` | Element/revision to existing `Asset` ID, role such as figure image, media type, dimensions, source page/region and extraction provenance. Captions link to elements explicitly. Reuse content-addressed storage. |
| `BlockElementMap` | Retrieval block to one or more canonical element spans, with offsets in both the element and block text. Enforce that all mapped elements belong to the block's revision. |
| `ReferenceEntry` | Revision-local ID/label, original reference string, source spans, parsed authors/title/year/venue/identifiers when available, extractor version and extraction status. Unknown fields remain unknown. |
| `CitationMention` and its targets | Body element, exact marker text and offsets, target entries, resolution method, status and candidates when ambiguous. A range such as `[3–5]` can point to multiple entries. Validate same-revision relationships. |
| Revision manifest | Parser/adapter/model versions, options and schema version; page-level extraction status; asset and reference completeness; warnings; normalized representation hash. Add to `SourceRevision` rather than creating another competing revision system. |

Canonical text is not sliced to meet an embedding or model prompt limit. Long elements can produce multiple `ContentBlock`s. Tables preserve headers, rows, cell spans and captions; row groups used for retrieval retain their table identity and relevant headers. A numeric table must not disappear solely because a generic text heuristic excludes it from embeddings.

Specify anchors once at the normalization boundary:

- Pages are one-based. Preserve all spans for multi-page elements.
- Reuse `bboxPpm = [left, top, width, height]`, measured from the top-left in millionths of the displayed page. Convert parser coordinates using page size, coordinate origin, crop and rotation; retain raw provenance for diagnosis.
- Text offsets are end-exclusive Unicode code-point offsets into the named, immutable element text. TypeScript must convert them before slicing UTF-16 strings. Do not confuse these with legacy offsets into `sources.content`.
- Missing geometry is an explicit limitation; do not manufacture a box from text similarity.

`SourcePaper` remains metadata about the imported paper. `ReferenceEntry` describes works cited by that paper. `Work` can help recognize an already imported referenced work, but an unresolved bibliography entry must not automatically become a Source or count as evidence that its contents were read.

### 3.2 Parser contract and publication

Replace the new parser path's `list[ParsedBlock]` return with a versioned `ParsedDocument` result: elements, staged assets, reference data, page status, warnings and manifest. Retain a legacy adapter so the current text/OCR path can still produce a valid, explicitly limited result.

Recommended processing sequence:

1. Preserve and hash the original as today. Parse from those original bytes, not from a previously extracted body appended to `sources.content`. Keep user notes separate from file evidence.
2. Run the structured parser in its managed subprocess. Detect image-only and partially scanned pages; use the configured local OCR path where needed. Avoid automatically running two complete extraction passes on every PDF.
3. Normalize reading order, heading levels, lists, tables, captions and reference boundaries. Heading paths must work with the parser's actual flat/sibling output as well as nested fixtures.
4. Reconcile fallback text at page/region level. One text item does not make an entire page complete. Insert recovered elements in source order, avoid duplicate spans and record which regions remain uncertain.
5. Export figure images before the parser's temporary directory disappears. Retain embedded images or rendered figure crops as appropriate; the unchanged PDF remains the original for vector artwork. Keep page previews as bounded, regenerable caches unless needed as retained evidence.
6. Extract and link bibliography data, then derive retrieval blocks. Reference processing has a bounded time budget; failure is recorded rather than blocking all access to the document.
7. Validate all references and asset files. Under the existing job lease fence, transactionally publish the revision, mappings, bibliography and FTS entries and move `SourceIndexHead`. Queue embeddings, paper metadata and digests as today. Keyword search can work while embeddings are pending.

A failed/cancelled/stale job must not move the head. A degraded reparse must not silently replace an existing richer revision: retain the current head and report why the candidate was not promoted. A new import may publish a clearly marked fallback representation. Retrying with better extraction creates a new immutable revision.

Fingerprints include semantic parser configuration, normalized content and retained asset hashes. Exclude timestamps, temporary paths and runtime-generated IDs. Repeated identical processing must not duplicate assets or revisions. Include the parser profile in processing-job deduplication keys.

Stage files before database publication; only expose assets reachable from a committed revision. Add orphan cleanup for abandoned staging. Extend storage reservations, trash/restore, permanent deletion and backup integrity checks to cover derived assets and all owners. Saved output versions that embed a figure need their own asset ownership so deleting the source cannot break that output. Never delete a shared asset while any source revision or saved output still owns it.

### 3.3 Parser choice and desktop delivery

**Use Docling as the first structured PDF implementation.** Gunther already has its process boundary, and the official [figure export example](https://github.com/docling-project/docling/blob/main/docs/examples/export_figures.py) demonstrates retaining page/element images and exporting them. Extend the adapter, not only the dependency list. Keep text/OCR fallback. Compare MinerU on the same difficult documents only if the first evaluation exposes a material weakness; do not ship two heavy default runtimes without measured benefit.

**Treat GROBID as a bibliography-provider candidate with an early deployment gate.** Its [full-text API](https://grobid.readthedocs.io/en/latest/Grobid-service/) can retain raw reference strings and structured TEI data. Prefer full-text output when citation-to-entry links are needed; a list from `processReferences` alone is insufficient. Align its spans to Gunther's canonical elements by page/geometry and text, not by assuming two parsers produce identical offsets. Disable external consolidation in the offline path.

Native Windows GROBID is officially unsupported; its [FAQ](https://grobid.readthedocs.io/en/latest/Frequently-asked-questions/) recommends Docker. Therefore, **GROBID cannot be a mandatory dependency of the first no-setup Windows release**. In phase 0, compare a conservative local entry/marker resolver against GROBID's output. Keep the provider interface independent. If the local implementation cannot meet the reference-linking gate, that capability remains incomplete on Windows until a suitable native provider is proven. Do not disguise Docker/WSL setup or an automatic cloud upload as a solution.

Package a separate parser helper with pinned dependencies and offline model artifacts. The current [sidecar build](/Users/keke/Documents/git/Gunther/scripts/build-backend-sidecar.mjs:100) bundles semantic search and Skills, not Docling. Docling supports [model prefetching and explicit artifact paths](https://docling-project.github.io/docling/usage/advanced_options/); package the selected OCR language models too. The installer should include the CPU path. Any optional accelerator component should be acquired by the app from a versioned, checksum-verified package, without a terminal setup procedure.

| Platform | Delivery requirement |
| --- | --- |
| macOS | CPU path on every OS/architecture the release advertises; MPS only after a whole-pipeline test. Check dependency compatibility with the existing minimum OS version before changing it. |
| Windows, NVIDIA | Complete CPU path; optional CUDA acceleration with compatible drivers and a packaged runtime. Users should not install a CUDA toolkit. |
| Windows, AMD/Intel | Complete CPU path. Do not promise GPU acceleration until the chosen parser/OCR models and runtime are validated on those devices. |

Docling documents [CPU/MPS/CUDA/XPU device configuration](https://docling-project.github.io/docling/_generated/examples/run_with_accelerator/), with compatibility depending on its runtime and OCR engine. ONNX [DirectML](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html) supports a range of Windows GPUs, but that does not make a PyTorch-based parser run on it automatically. An ONNX/Windows ML adaptation would be separate work, justified by benchmarks.

The helper must have platform-specific process limits, bounded concurrency, cancellation and deadlines; replace the unconditional `resource` import. If accelerated inference fails, retry once on CPU and record the device/fallback. CPU fallback changes performance, not the documented feature contract. Measure cold/warm latency, peak RAM/VRAM and package size; no seconds-per-paper promise is established by this plan.

### 3.4 Bibliography matching and other capture formats

Preserve the raw bibliography even when metadata parsing fails. Recognize numeric, grouped/ranged, superscript and author-year citations; distinguish reference markers from equations, years, figure/table numbers and ordinary bracketed text. Use parser links first, then conservative deterministic matching. Ambiguous author-year duplicates stay ambiguous. Store method/confidence as diagnostic metadata, not as a calibrated probability unless evaluated.

Keep paper markers such as `[12]` in a namespace distinct from Gunther answer citations `[n]`. The reader resolves the former within the document revision; the answer renderer resolves the latter within its evidence snapshot. Never feed original paper numbers into the Agent's citation-renumbering logic as if they were pool IDs.

Bibliography entries remain visible and available to explicit reference searches, but are excluded from ordinary body-evidence ranking. A paper citing another work does not prove the cited work's results. Following a reference requires reading the relevant source within the allowed scope; Web-off still means no external lookup.

Apply the same document contract to captured HTML, then DOCX/EPUB:

- HTML preserves headings, paragraphs, lists, tables, figures, captions and a map from original image URLs to stored assets. Fetch images through the existing public-URL and redirect protections, with cumulative byte/time limits; retain a missing-image record when fetching fails. Opening a saved document should not depend on remote image loading.
- DOCX resolves image relationships and media, retaining table/list structure. EPUB uses its manifest and spine order and resolves relative assets. Preserve existing archive expansion limits.
- Prioritize PDFs and captured articles; DOCX/EPUB follow without delaying a useful PDF release. Unsupported features are reported, and the original remains accessible.

### 3.5 Retrieval and reader integration

Continue FTS5 plus sqlite-vec. Start with structural chunking using headings and paragraph boundaries, a configurable token cap sized to the existing embedder, and explicit element mappings. Avoid a new vector database, graph database or multimodal embedding service in this iteration.

Retrieve figure candidates through captions and nearby body text, then attach the figure asset. OCR or a model-generated image description must be labeled as derived content. A plot image is not a machine-verified data table; do not turn visually inferred values into sourced chart numbers.

Keep `/sources/{id}/structure` and its historical block lookup working. Add a paginated, revision-pinned document-elements response and reference-resolution response; use a separate endpoint or an explicit representation version rather than changing `blocks` semantics. Extend shared contracts and citation snapshots additively.

The reader consumes canonical elements for new revisions and its existing block renderer for older ones. Render retained images/captions and separate reference entries; support body-marker → entry → return-to-body navigation. Open the original at the cited page and, where available, normalized region. Reuse authenticated asset delivery and existing evidence panels. This is functional document rendering, not a search-box or general UI redesign.

## 4. Agent design

### 4.1 Make the existing decision to search explicit

Extend the existing plan schema with a small retrieval policy; do not add another permanent agent or a separate classification call for every message. Define these decisions independently of writing style and Skill selection:

| Task | Expected behavior |
| --- | --- |
| Translate, shorten, reorganize or acknowledge supplied text | No library/Web search and no automatic follow-up fact search. Preserve citations and factual uncertainty; do not add facts. |
| Answer from passages already read | Reuse eligible evidence. Search/read further only if the requested claim exceeds it. Check new factual relationships against those passages. |
| General, stable explanation without a source-grounding requirement | A direct answer may be sufficient. Preserve Gunther's distinction between source-backed and model-knowledge statements; direct mode must not fabricate citations. |
| Claims about the selected documents, comparisons, verification or current information | Gather the evidence required by the question, respecting source and Web limits. |
| Explicit `/research` | Keep the existing research method, framing and budgets; improve coverage and stopping as below. |

Rework is not a loophole for treating an old unsupported assertion as verified. New factual claims, “is that true?” and requests for latest information must not enter the transformation path. Extend the existing exam with these pairs. Keep automatic Skill matching off until a separate evaluation justifies changing it.

### 4.2 Stable evidence identity and reading further

For library passages, identify evidence by source, immutable revision, block ID and/or exact canonical spans. For Web evidence, retain canonical/final URL, retrieval time and content hash/snapshot identity. Fall back to a full quote/locator hash only for legacy records without IDs; title plus 200 characters is not sufficient.

Resolve `_passage_block` by validated `block_id` first. Check that source and revision agree, then use the legacy quote fallback only when needed. Replace anonymous context strings with a `ReadResult` that names the returned source/revision/spans, text, truncation and next cursor. Ask and Outputs should use the same read contract. It should support bounded adjacent elements, a section, a page/table/figure and a continuation read.

The supporting text used by the Checker must survive in the saved evidence snapshot. If an expanded section supports a claim, saving only the original short snippet is insufficient. Keep the original passage identity, attach its exact support spans, and let the evidence panel open them. No source reading may escape allowed source/revision/block scope.

Choose expanded contexts by the question and pending claims rather than keeping only the first three read items. Apply a prompt budget explicitly and record truncation; later evidence must not vanish silently because of insertion order.

### 4.3 Checking coverage and memory

Create a shared claim-checking result used by Ask and adapted into Outputs. Preserve existing citation markers and contradiction behavior while migrating the older Output Checker functions.

- Extract stable claim/span IDs from the answer, including factual headings, lists, table cells and slide notes. Label nonfactual spans explicitly. Do not equate “the model omitted a verdict” with “not a factual claim.”
- Batch claims with the exact evidence they cite, within the model's context limit. The existing 20/40-sentence setting becomes a batch/run constraint with visible overflow, not a silent end to checking.
- Require a result for every submitted claim. Missing verdicts, evidence omitted by the source budget, malformed responses and provider failures become `unchecked` with a reason. Retry at most once within the run budget.
- Persist `complete | partial | skipped | failed` coverage, considered/checked/unchecked counts and claim-level support results against an answer/version hash. Keep the legacy `checked` field for compatibility, deriving true only from complete required coverage on new answers. Treat old records without coverage as legacy/unknown.
- When post-check lookup supplies a new citation, check that exact claim against the actual saved passage before marking it supported. A source being retrieved is not verification.
- Revisions invalidate verdicts for changed claims/citations. Reuse a verdict only when claim text, cited evidence and evidence versions still match. Report/PPT editing cannot bypass this step.

Make `BriefKeeper` consume supported claim IDs and verdicts, not only valid pool numbers. Automatically settled factual conclusions must be supported; partly supported, disputed, inferred or unchecked statements keep their qualification or remain open. Preserve user-written constraints and agreements, labeled as user decisions rather than independently verified facts. A brief is context for the task, not a new evidence source.

### 4.4 One accounting path for all work

Add a run budget object through which searches, source reads and model calls are admitted. Keep separate gathering and total counters so the meaning of today's `Budget.calls` is not silently changed. Include framing, grading, writing, checking, claim lookups and revisions in total accounting; report brief maintenance separately when it runs after the answer.

Set explicit reserves for writing and minimum checking before gathering spends the budget. Stop gathering when its allowance or the reserve boundary is reached. Account for failed attempts and retries, deduplicate searches across phases, and apply the same Web/scope decision during verification as during gathering. Library support already sufficient for a claim should not trigger an unconditional Web lookup.

Preserve the existing first-stop behavior for research: stop gathering and answer from collected evidence. Finalization may check collected evidence within its reserve but must not start new searches after that stop. A subsequent cancel stops finalization and saves no completed answer. Apply deadlines to source reads and propagate cancellation to subprocess/network work.

Keep planner `low`, filtering as currently configured and Checker `off` as the comparison baseline. After coverage is fixed, compare `off` versus `low` for evidence-support judgments, especially numerical and contradictory claims. Record actual provider effort support, tokens, latency and quality. Adopt selective reasoning only if repeated evaluation shows a worthwhile gain.

## 5. Deep research and supplementary sources

Retain the existing `/research` Skill. Strengthen its evidence map so each sub-question records the required claim, supporting spans, independent work IDs, counter-evidence, unresolved gaps and the reason it is considered complete.

Two chunks from one paper are one source. Use validated DOI/arXiv/work identity and duplicate-content information to avoid double counting; a title match alone does not prove either identity or independence. Primary-source status and support are separate judgments. “Two sources found” must not automatically mean that the question is answered or that no further reading is needed.

Stop when the required claims are sufficiently supported, no useful new evidence arrives, the user stops, or the budget ends. Save the stop reason and remaining gaps. Research should examine material disagreements and methodological limits, not merely increase search count. Do not automatically follow or ingest an entire bibliography.

Use an explicit scope contract:

- **Primary materials:** what the user chose; they determine the question and core of an output.
- **Permitted supplements:** other material in the allowed library scope, and Web only when enabled. Store `selected | library_supplement | web_supplement`, role/reason and exact evidence identity with each used source.
- **Hard restrictions:** “only these documents,” topic/block restrictions, membership permissions and Web-off remain binding. A Skill cannot expand them.

For Report/PPT requests using the discussed “selected materials as the main basis” behavior, allow supplementary material from the same permitted library before Web. Keep an explicit strict-scope option in the service contract. Preserve existing clients' strict selection semantics unless they request the expanded policy. Apply the same contract when research explicitly permits supplements; do not silently broaden ordinary Ask's selected-source scope.

Both Report and PPT already support Web supplements. Extend the current provenance to distinguish library supplements too. A supplemental source that contradicts primary material must be reported fairly. Primary material controls emphasis, not truth: replace Skill wording that could be read as requiring outside evidence to agree with it.

## 6. Report and PPT content quality

Use the current Skills and runners. The requested improvement is how the output is conceived and written, not a PPTX generator replacement or making every document longer.

### Reports

Strengthen material cards with an evidence span, its role in the argument, scope/limitations and relevant definitions or units. Give each section a question to answer, a claim to establish and its relationship to the whole report. Require explanation and synthesis where the audience needs them; avoid sections that repeat an abstract under different headings.

Keep the existing editor and fresh-reader review. Give revision writers the relevant neighboring sections and a compact global argument/terminology map, not only the previous ending. Allow a coordinated set of section edits within a bounded revision pass. Rerun whole-document consistency checks after those edits; report unresolved editorial issues if the pass limit is reached.

Editorial mode **must not introduce new factual claims**. Preserve citation bindings when moving or rewriting existing claims and recheck changed passages. If missing evidence is discovered, use an explicit, budgeted research step and then check the addition; do not smuggle it into editing. The current `KEEP` rule permits adding a fact with an existing pool number, which is weaker than this separation.

### PPTs

Keep reveal.js, CodeMirror editing, per-slide checks, saved versions and PDF export. Existing layout choices are sufficient to start. Improve selection by content relationship: comparison, process, timeline, numbers, chart, quotation or explanation. Do not force a chart, a layout rotation or a minimum page count when the material cannot support it.

Use a story outline with one main message per slide. Distinguish presentation slides from decks meant to be read; place explanation in speaker notes for the former and in concise supporting text for the latter. Reuse imported figures when they explain the claim, retaining captions, citations and asset ownership.

Represent chart data before rendering as values with labels, units, population/time/baseline and evidence spans. Validate those fields against the cited passage or table. Derived numbers retain the formula and source operands; reject invented/mismatched numbers. A `[n]` somewhere in a chart row is not enough. For manually edited decks without structured data, parse and check what is possible and mark the rest unverified.

Apply factual checks to title-slide claims too: the current title form prohibits citations and skips `check_built`, despite allowing a core message. Permit an unobtrusive source binding or use a nonfactual title. Titles and speaker notes must not become unchecked factual channels.

Run deterministic checks again on the final revised deck: empty pages, unresolved citations, chart data, density and overflow using `slideFit.ts`. Classify decorative/title-only pages separately from factual pages. A shrink-to-fit scale is a signal, not proof of readability. Visual model review can be optional later; a text-only provider must remain sufficient for the core workflow.

## 7. Compatibility and migration rules

1. Append migrations after schema 21; never edit shipped migrations. Confirm the latest schema when implementation begins.
2. Old assets, blocks, citations, hidden pool refs, topic links, conversation briefs and output versions retain their meaning. Keep reader/API fallbacks for revisions without canonical elements.
3. Reparse through the durable job queue in controlled batches. Do not run an unbounded whole-library conversion at startup. Make progress, cancellation, retry and parser version visible through the existing processing facilities.
4. New citations store revision and support spans. Reprocessing changes the current search head, not past answers or pinned topic evidence. Never reuse a retired hidden pool number.
5. Test backup/restore and permanent deletion with shared originals, derived images, historical revisions and figures owned by outputs. File deletion remains after database commit and after checking every owner.
6. Keep digests, material cards and conversation briefs distinct from primary evidence. Their citations can lead to original passages; their generated prose is not a new independent source.
7. Keep the existing developer switches for comparing Agent/Outputs implementations. Parser rollout needs a bounded legacy fallback; returning to it must not rewrite successful new revisions in place.

## 8. Implementation sequence for GPT-6.1 Sol

Each phase should be a reviewable change with its relevant tests and updated documentation. The parser and Agent phases can share contracts without requiring a single large rewrite.

| Phase | Work and principal files | Exit condition |
| --- | --- | --- |
| 0. Baseline and feasibility | Extend [evals/README.md](/Users/keke/Documents/git/Gunther/apps/backend/evals/README.md), add parsing/output fixtures; prove packaged CPU parsing and bibliography strategy on clean macOS/Windows targets. Pin parser/OCR versions and model artifacts. | A recorded before/after protocol, reproducible fixtures and a viable local runtime. GROBID's Windows limitation has an explicit resolution or feature boundary. |
| 1. Data contracts and ownership | [models.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/models.py), [migrations.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/migrations.py), [content.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/content.py), [schemas.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/schemas.py), [contracts](/Users/keke/Documents/git/Gunther/packages/contracts/src/index.ts); asset ownership, trash and backup. | Additive migration from schema 21, same-revision validation, idempotency and historical citations pass. |
| 2. Structured PDF import and reader | `document_parser.py`, [processing.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/processing.py), [knowledge_index.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/knowledge_index.py), [knowledge_api.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/knowledge_api.py), `DocumentView.tsx`, [PdfViewer.tsx](/Users/keke/Documents/git/Gunther/apps/desktop/src/components/evidence/PdfViewer.tsx). | Figures/captions, section order, paragraphs and tables survive; fallback cannot duplicate/reorder content or publish through a lost lease. |
| 3. References and article capture | Implement a bibliography-provider boundary and mention resolver; extend `asset_service.py`, [web_capture.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/web_capture.py) and reader contracts. Add DOCX/EPUB after PDFs/HTML. | Numeric and author-year references resolve conservatively; raw entries and unresolved cases survive; imported article images work offline. |
| 4. Agent reliability | `agent.py`, `service.py`, `brief.py`, [skillbook.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/skillbook.py), [research Skill](/Users/keke/Documents/git/Gunther/apps/backend/gunther/skills/research/SKILL.md), contracts and tests. | Retrieval decisions, whole-run budgets, source identity, claim coverage, source-reading provenance and memory qualification meet the acceptance matrix. |
| 5. Output quality | `skill_runner.py`, [outputs.py](/Users/keke/Documents/git/Gunther/apps/backend/gunther/outputs.py), `skill_checks.py`, [report Skill](/Users/keke/Documents/git/Gunther/apps/backend/gunther/skills/report/SKILL.md), [slides Skill](/Users/keke/Documents/git/Gunther/apps/backend/gunther/skills/slides/SKILL.md), [SlideFace.tsx](/Users/keke/Documents/git/Gunther/apps/desktop/src/outputs/SlideFace.tsx), [slideFit.ts](/Users/keke/Documents/git/Gunther/apps/desktop/src/outputs/slideFit.ts). | Same-material comparisons show better content and reading flow, with no invented chart values or citation regressions. |
| 6. Release validation | Sidecar/parser packaging, native installation checks, data migration/reprocessing trials; update [KNOWLEDGE_BACKEND_V4.md](/Users/keke/Documents/git/Gunther/docs/KNOWLEDGE_BACKEND_V4.md), [ASK_AGENT.md](/Users/keke/Documents/git/Gunther/docs/ASK_AGENT.md), [OUTPUTS.md](/Users/keke/Documents/git/Gunther/docs/OUTPUTS.md). | Install/import/read/search/export works without a development environment. CPU and advertised GPU paths are tested separately; limitations and measurements are recorded. |

Budget accounting and honest checking coverage in phase 4 can be implemented after the baseline without waiting for every parser feature. The enriched source-reading contract depends on phases 1–2. Output figure reuse depends on asset ownership and document extraction.

### Acceptance matrix

Build a small annotated corpus of at least 12 representative PDFs, covering English/Chinese, single/two-column text, image-only and mixed scans, rotated pages, raster/vector figures, captions, multi-page tables, numeric and author-year references, no-reference documents and a large input. Include captured HTML with relative/missing images and DOCX/EPUB examples. Use redistributable fixtures or a hash-pinned benchmark manifest; do not commit a user's private library.

| Area | Required check |
| --- | --- |
| Paragraphs and reading order | Known paragraphs/list items/reference entries retain boundaries; columns and headings follow annotated order. Long-element chunking maps back exactly, including Chinese and emoji offsets. |
| Images and assets | Annotated figures remain linked to correct captions/pages after restart and backup/restore. Missing extraction is reported. Cancelled jobs leave no visible broken assets; orphan cleanup is safe. |
| References | `[1]`, `[2,4–6]`, superscripts and author-year cases map correctly; duplicate-year authors, malformed markers and equations do not produce false resolved links. Bibliography-only hits do not support substantive claims. |
| Migration | Reparse twice; interrupt a job; reclaim its lease; switch parser version. Verify stable originals, no duplicate extracted body, one current head and unchanged historical citation snapshots. |
| Scope and independence | Two chunks/copies of one paper do not count as independent corroboration. Removed/out-of-scope sources never reenter through the pool, source reader, lookup or supplement path. |
| Search decisions | “Translate this” and “shorten that answer” cause zero search calls across the entire turn. “Verify that result” and “what changed this year?” take an evidence path; Web-off causes zero Web calls in every phase. |
| Check coverage | Long answers exceeding 40 sentences, more than 24,000 source characters and omitted verdicts produce correct complete/partial states. No truncated or omitted claim becomes silently verified. |
| Identity and source reading | Same-title documents with the same text prefix remain distinct; repeated text in two sections resolves by block ID. A claim supported only by expanded context opens that exact context later. |
| Budget and stop | Include post-write lookups and retries in asserted counters. Exhaustion preserves a bounded final answer with gaps; research stop starts no further retrieval; cancel saves no completed answer. |
| Brief and revisions | Disputed/partial/unchecked claims cannot become automatically settled. A rewritten claim loses stale verification; user-edited constraints survive. |
| Reports | Blindly compare outputs from the same material/brief for argument, coverage, explanation, transitions and non-repetition. Citation support must not deteriorate. |
| PPTs | Compare the same audience/use case; test invented/mismatched chart values, unsupported factual titles, empty slides and overflow after revision. Keep editing and PDF export working. |
| Installation | On fresh target machines without Python/Java/Docker/toolkits, import a digital PDF and a scan, reopen images and search offline. Test accelerator failure followed by CPU recovery. |

Suggested quality gates, to freeze after annotating the corpus: at least 95% recall for annotated figures/caption associations; at least 98% precision among confidently resolved bibliography links and 90% recall among resolvable annotated mentions. Report results separately by document type/language and include unresolved counts. These are proposed release targets, **not current measurements or guarantees for arbitrary PDFs**. All deterministic compatibility, scope, budget and verification-coverage cases must pass.

Extend the existing Ask exam rather than replacing it. Use repeated runs of the same cases/library/model and record Skill versions, effort, provider, tokens, tool calls and latency. Prefer an independent fixed judge plus deterministic checks and a human spot check. Run a parser-only comparison with Agent settings frozen, then an Agent-only comparison with the same parsed library. This distinguishes ingestion gains from prompting/model gains. Add same-material Report/PPT examples; do not evaluate by length or citation count alone.

### Validation performed for this plan

Read the current implementation and its documentation; checked official parser/runtime documentation. Ran the following local suite using stand-in model providers, without a paid model evaluation or downloading parser models:

```sh
# Working directory: /Users/keke/Documents/git/Gunther/apps/backend
GUNTHER_DESKTOP_SIDECAR=1 SEMANTIC_SEARCH=false .venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_agent.py tests/test_structured_knowledge.py tests/test_output_skills.py tests/test_ask_exam.py
```

Result: **137 passed**, one Starlette/httpx deprecation warning, 17 seconds. The sidecar flag avoids import-time construction of the default application during test collection. Real-document extraction quality, full desktop rendering, Windows packaging, accelerator performance and live-model output quality have **not** been validated by this planning task.

## 9. Instructions to the implementing model

Read this document and compare its baseline with current HEAD before editing. Read applicable repository instructions and the current tests. Start with phase 0 and keep the existing Agent/Outputs features. Implement additive data contracts and explicit evidence provenance; do not solve document rendering by further merging retrieval chunks.

Use the existing processing queue, source revisions, asset store, model gateway, Skills and evaluation harness. Introduce only the focused modules needed for document normalization, bibliography resolution and shared run/check accounting. Keep routes as transport and avoid moving more logic into the large service file.

Deliver each phase with the changed behavior, relevant tests, measurements where required and any unmet exit criteria. Update the behavior docs when implementation changes them. If a runtime/provider cannot meet the desktop requirement, record the demonstrated limit and continue work that does not depend on it; do not replace a required local capability with an unrequested cloud service or call the phase complete.

Leave search-box/UI redesign, a new agent framework, automatic Skill activation, a new vector database and a PPTX migration outside this work. The release is successful when the imported evidence is faithful, the Agent's search/check behavior is accountable, and the same materials yield better reports and presentations.
