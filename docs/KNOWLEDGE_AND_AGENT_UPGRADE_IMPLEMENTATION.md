# Knowledge and Agent upgrade: implementation notes

Updated 2026-10-11. Based on the plan prepared against `58b3450`. This implements the core storage, ingestion, Agent reliability and output safeguards. It is not a completed cross-platform release qualification or a measured improvement in live-model writing quality.

## What changed

| Area | Implemented behavior |
| --- | --- |
| Document storage | Schema 22 adds canonical elements, element-to-chunk mappings, retained asset ownership, bibliography entries and body citation mentions alongside existing source revisions and retrieval blocks. Canonical paragraphs remain whole; retrieval slices retain exact Unicode code-point offsets. |
| PDF parsing | Docling recovers the source's headings and uses bookmarks and typography for hierarchy. Caption-associated panels, tables and visual annotations form a complete page crop. Page furniture and metadata stay separate from prose. Fallback recovers missing pages; it cannot append raw chart labels to recognized layout pages. A basic import is explicitly partial. |
| Other imports | HTML retains image positions, captions, lists and tables; article images use the existing public-URL and redirect policy. DOCX retains paragraph/image relationships; EPUB follows its spine and preserves embedded images. Oversized structured tables fail explicitly instead of silently losing cells. |
| References | Raw bibliography entries and separately stored printed list markers remain available apart from prose. Numeric markers, groups/ranges, HTML numeric superscripts and conservative author-year matches have resolved, ambiguous or unresolved status. Missing metadata and uncertain links remain unknown. Reference entries are excluded from substantive retrieval. |
| Reader | The canonical reader shows source-defined heading levels and contents, document details, complete figures, table cell spans and citation/bibliography navigation. Figure annotations and repeated page furniture are inspectable separately. Old revisions still use the existing structured-block reader. |
| Agent retrieval | The planner distinguishes transform, reuse and evidence tasks. Transform mode admits no searches during gathering or post-answer lookup. A shared run budget counts planning, writing, checking, revisions, source reading, claim lookups, JSON repair and provider-setting retries. |
| Agent verification | Bounded batches cover headings, prose, tables and notes. Missing verdicts and failed or oversized checks remain unchecked. Coverage and answer hashes are saved; unchecked/disputed claims cannot become automatically settled conversation memory. |
| Source identity/readback | Evidence uses source/revision/block identity or a full-text fallback digest. Available DOI/arXiv identifiers group copies. Reading further retains supporting text and revision-pinned spans, which the evidence panel can reopen. |
| Report/PPT | Existing repository Skills are version 2. Material cards include role/scope/units; global review and revision use the argument and neighboring sections. Editorial changes cannot introduce numeric facts, and an explicit fidelity verdict is required before applying other rewrites. Changed sections are checked again. |
| Charts and figures | Chart values must occur in their cited passages; newly generated chart pages cannot be saved with unresolved verification. Imported figures can be reused with a managed asset reference and citation. Saved outputs own those images, so deleting an original source does not break its report. |
| Supplementary material | `librarySupplements: true` explicitly permits additional sources from the same library. Selected material remains primary. Web supplementation still obeys `web`. Citation origin and the supplementation option persist with the version. |

The existing gateway, SQLite/FTS/vector index, job queue, source revisions, reveal.js viewer, Markdown editor and PDF export remain in use. The search composer was not redesigned. Automatic Skill selection and the existing model/effort defaults remain unchanged.

## Storage and rollout

Migration 22 is additive; shipped migrations retain their behavior. Existing block IDs, citations and historical output versions keep their meaning. New imports publish canonical elements and derived retrieval blocks in one revision transaction. Old revisions can be reparsed through the existing reprocess queue; startup does not convert an entire library at once.

Derived images are staged under the parsing job's lease. A cancelled or stale attempt cannot publish a revision; abandoned staging is collected while committed document/output owners retain their bytes. Reprocessing is idempotent. A parser failure cannot silently replace an existing richer Docling revision with a degraded text revision.

`GET /api/sources/{id}/document` accepts pagination and a pinned revision. Its response includes canonical elements, references, mentions, processing state and the parser manifest. The old `/structure` endpoint remains available.

The new output option is an API field, not a new composer control:

```json
{
  "clientRequestId": "output_example_0001",
  "kind": "report",
  "audience": "scientist",
  "scope": {"mode": "selection", "sourceIds": ["src_example"]},
  "librarySupplements": true,
  "web": false
}
```

Images in saved Markdown use `gunther-asset:<asset-id>`. Renderers resolve this to the authenticated local asset URL at display time; credentials are not written into document content. Remote images remain blocked in answer/output rendering.

## Local parser packaging

`tools/document-parser/pyproject.toml` and its lock file define a separate Python 3.12 CPU parser environment with pinned Docling, RapidOCR/ONNX and PyInstaller dependencies. The backend itself does not import PyTorch. The release builder downloads models once; the installed helper disables network connections and uses the bundled models.

From the repository root:

```sh
uv sync --frozen --project tools/document-parser
uv run --frozen --project tools/document-parser python scripts/build-document-parser.py
node scripts/build-backend-sidecar.mjs
```

The builder emits `tools/document-parser/.build/bundle/` with the frozen runtime, offline models and a manifest containing file hashes, package versions, the lock hash, model profile and parser/build source hashes. Build outputs are gitignored. Release builds include the parser by default and reject a missing or stale bundle. `--without-document-parser` is an explicit reduced build; its imports report incomplete layout. With models already fetched, the parser builder accepts `--offline`. Its cache distinguishes parser-enabled and reduced builds. Development discovers the prepared model bundle and uses the current pipeline in the pinned helper environment. Explicit `DOCLING_PYTHON` and `DOCLING_ARTIFACTS_PATH` remain supported. Preserve the virtual-environment interpreter path rather than resolving its symlink to the base Python binary.

Packaging preserves PyInstaller's relative native-library links and keeps only the selected model variants: Heron with Transformers/PyTorch, accurate TableFormer, and RapidOCR with ONNX Runtime. The pipeline explicitly pins the same layout/table choices that were previously defaults. The unused ONNX layout export, fast table weights and PyTorch OCR weights are omitted; active weights, precision and inference settings are unchanged. PyInstaller's build cache stays under the parser's `.build` directory. The standalone parser is now approximately 1.4 GiB on disk (including 397 MiB of models), down from 2.2 GiB. All 50 retained model/config files matched the earlier bundle's SHA-256 hashes.

Run the packaging regression checks with `tools/document-parser/.venv/bin/python -m unittest discover -s tools/document-parser/tests -v`. They verify native-library links, preservation of active models/configuration/vocabulary, failure before trimming an incomplete bundle, and equivalence with the previous model defaults.

The rebuilt macOS ARM64 app contains 2.309 GB of regular files (2.151 GiB), compared with 3.173 GB in the previous installed copy. The compressed `Gunther_0.1.3_aarch64.dmg` is 1.041 GB (993 MiB). These are measured local artifacts, not Windows/Linux estimates. The app passed `codesign --verify --deep --strict`; the DMG passed `hdiutil verify`. This is an ad-hoc signed local build, without Apple notarization. Rebuilding a bundle does not replace `/Applications/Gunther.app`; install the new DMG to update that copy.

The normal Tauri command completed the app but `hdiutil create` failed with `Device not configured` under the restricted build runner. Running the generated DMG step with macOS disk-device access completed the installer. No notarization credentials were needed for this local build. For an otherwise opaque DMG failure, use `npm run tauri:build -w @gunther/desktop -- --verbose` to expose the failing disk-image command rather than treating the notarization warning as its cause.

After trimming, digital, scanned, illustrated and 16-page sample PDFs produced identical canonical elements and image SHA-256 hashes to the previous installed parser. Scanned and full-paper comparisons also passed using the parser embedded inside the rebuilt app. The packaged backend smoke passed authentication, upload/worker processing, document hierarchy, scoped retrieval, pinned citations and ONNX semantic search. Four packaging tests and 25 document regressions passed. These checks preserve observed extraction behavior; they are not a controlled throughput benchmark.

Build on each target OS/architecture. This change proves the local macOS ARM64 helper, not the full signed DMG/MSI installation. CPU is the shipping baseline for this helper; no AMD/NVIDIA acceleration is advertised by this implementation.

## Verification completed

| Check | Result |
| --- | --- |
| Full backend suite | 601 passed, 1 skipped; one Starlette/httpx deprecation warning. Localhost access is required for the gateway and helper integration tests. |
| Full desktop suite | 365 passed across 61 files; existing React test synchronization warnings remain. An intermittent Home research test passed in isolation and in the full two-worker rerun. |
| Desktop production build | TypeScript and Vite passed. |
| Backend lint / sidecar script syntax | Ruff passed; Node syntax check passed. |
| Native CPU parser | Digital PDF, scanned PDF and a PDF containing a chart, caption and bibliography parsed with network connections disabled. |
| Frozen CPU helper | Built and executed without the development Python environment. Digital/scan text recovered; figure PNG/caption retained; numeric body citation resolved to bibliography. |

The October 11 review fixed two regressions before committing. Missing-database recovery
prefers the newest valid matching workspace snapshot, and a completed migration cannot
reuse the stale legacy database. Output revisions retain complete coverage for unchanged
charts; older chart checks without coverage are rerun before saving. Eight additional
regression cases cover these paths and reject chart saves without complete verification.
Native Rust tests passed (33 passed, one ignored), as did all four parser packaging tests.

Regression coverage includes long paragraphs and Chinese/emoji mappings, reference ranges and ambiguity, page fallback, DOCX/EPUB images, actual native PDF raster export, reparse/restart, lease cancellation/reclaim, failed-parser preservation, all-claim batching, omitted verdicts, retry budgets, zero-search transforms, memory qualification, editorial fact rejection, invented chart rejection, source scope, article-image URL restrictions and output image ownership through permanent deletion.

On this Mac the initial frozen-helper launch took about 130 seconds; subsequent small parses took about 4 seconds. Before the packaging cleanup, the uncompressed runtime was about 1.5 GiB plus 730 MiB of models. These are local observations, not general performance guarantees. First-launch verification/startup remains release optimization work.

## Source-defined structure follow-up

`AGENTS.md` records the general-capability constraint. No publisher, paper, DOI or fixed academic outline is used as a production special case. Explicit headings retain their wording; content without headings stays unheaded. Partial PDF bookmarks calibrate matching fonts on other pages, without guessing section names. Metadata distinguishes publishers/publications from file creators, and published/received/accepted dates from file timestamps. Declaration origins, conflicts, unknown values and geometry are retained. Explicitly unresolved date notices receive a bounded offline OCR check against page pixels; uncertain OCR is not promoted to a fact. Page furniture and visual labels are excluded from substantive keyword and vector retrieval.

The reported 16-page Systema PDF was read from the user's original, without changing their library. Its stored import used the basic fallback (`gunther-asset-v3`), presenting 17 embedded rasters as figures and 423 paragraphs. The updated CPU parser recovered five caption-associated figures (including vector graphics), 36 source headings, two structured tables, 35 numbered bibliography entries and 147 body paragraphs. Printed reference labels 1–35 were retained; trailing publisher, licence and copyright notices were kept as metadata. Publication metadata included the XMP publisher, journal, DOI and eight authors. The offline metadata check recovered the printed publication date hidden over a native-text placeholder. The final frozen helper imported this source through the leased background worker into a temporary library, retained all five image assets, and returned the correct document and reference labels through the API without a parsing warning. That first import took 139.5 seconds on this Mac. These are checks on one real example, not a corpus accuracy score. Native PDF superscript reference resolution remains limited.

Reproduce application checks:

```sh
# apps/backend
GUNTHER_DESKTOP_SIDECAR=1 SEMANTIC_SEARCH=false .venv/bin/python -B -m pytest -q -p no:cacheprovider
.venv/bin/ruff check gunther tests

# apps/desktop, with Node 22
node ../../node_modules/vitest/vitest.mjs run
npm run build
```

## Remaining release and quality work

1. Run the plan's annotated, representative PDF corpus and record figure/caption recall, citation-link precision, reading order and extraction completeness. Synthetic smoke PDFs do not establish accuracy for arbitrary papers, rotated/cropped pages, vector artwork, or multi-column/multi-page tables.
2. Measure same-material Report/PPT results and the existing Ask exam with live providers and a fixed independent judge. Mock-provider regressions prove safeguards and workflow behavior; they do not prove better prose or argument quality.
3. Qualify clean Windows/macOS installations, signing/notarization, minimum OS versions, startup performance, cancellation and runtime limits. Windows and GPU paths have not been exercised.
4. Extend conservative bibliography parsing and native superscript support. Full author/title/venue extraction, a bibliography-provider boundary and GROBID are not implemented. A resolved link identifies the cited entry; it does not mean the cited paper was read or independently verified.
5. Improve portable-format fidelity: merged HTML/DOCX cells, DOCX numbering, caption association and native PDF crop geometry are limited. Native PDF fallback retains raster images without claiming a reliable caption association.
6. Research coverage now counts identifiable works rather than chunks, but two works remain a heuristic for “covered”; claim-driven research completion and title-based work deduplication need further evaluation. Whole-output call accounting remains separate from the new Ask run budget.
7. Perform backup/restore trials with enriched revisions and output-owned figures on real installation data. The generic backup machinery includes the new tables/assets and its regression suite passes; a representative release trial is still required.

These boundaries should remain explicit when reviewing or shipping the change. Do not claim a full release gate, arbitrary-document reference accuracy or better writing quality from the unit-test totals.
