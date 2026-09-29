# Structured knowledge backend

Implemented 2026-09-27. Schema version 11; additive migration from existing workspaces.

## What ships

- Immutable source representations and evidence blocks with parent/child hierarchy,
  heading paths, text offsets, and page/OCR-region or explicit transcript-time anchors.
- An editable library-owned topic tree, separately stored from document structure.
  A block can belong to multiple topics. Linking does not copy or rewrite evidence.
- SQLite FTS5/BM25 retrieval, with shared Latin identifier and CJK-bigram tokenization.
  Original passages participate alongside extracted assertions, including conflicting evidence.
- Durable file-processing jobs with atomic claims, expiring leases, heartbeats, three
  automatic attempts, retry backoff, explicit retry/cancel, and fenced publication.
- Capture inside a library saves membership and a processing job in the same transaction.
  Desktop uploads opt into asynchronous processing. Legacy clients retain the synchronous API.
- Citation snapshots include source revision, evidence block and anchor. Historical
  revisions remain readable after reprocessing. Topics deliberately pin evidence versions.
- Desktop source processing status, structured evidence viewer, page links, topic
  creation/editing/reparenting, evidence filing, and topic-scoped conversations.

## Data boundaries

Original Assets and recording audio remain unchanged. `sources.content` remains the
original captured representation; background parsing does not overwrite it.

`source_revisions`, `content_blocks` and `source_index_heads` separate immutable
representations from the current search view. `knowledge_fts` is a derived index.
`block_embeddings` records which blocks each model version has embedded; the vectors
are in sqlite-vec. Topic evidence links
retain the exact revision selected by the user, even after a source is reprocessed.

The existing single-workspace database remains the authorization boundary. All new
routes inherit the existing API authentication and origin checks. Retrieval intersects
the conversation's selected sources with current library membership before ranking.
Topic evidence links are rejected across libraries. Tree mutations use an immediate
SQLite transaction, ancestry checks and optimistic versions to prevent cycles and
lost edits. API errors do not include underlying model exceptions or local paths.

Removing a library membership is not deletion of a shared original. No new destructive
delete operation or automatic source cleanup is introduced by this upgrade.

## Trash (schema 13)

Sources, notes and libraries can be moved to Trash (`trashed_at`, `trash_batch_id`).
Trash only marks rows: text, claims, index entries and original files stay, so Restore is
exact. Everything one action trashes shares a batch. Trashing a library also trashes the
sources filed only there (and notes promoted into them); sources also filed elsewhere stay
where they are. A recording still being captured cannot be trashed. Capturing identical
content again restores a trashed source.

Trashed items leave Inbox, search, Ask retrieval, library lists, counts, the graph and the
overview. A source's own page still opens and reports `trashedAt`.

Delete forever is the only destructive path. It deletes a batch's rows and lets foreign
keys cascade their revisions, blocks, index entries, jobs, claims and memberships; a
library also takes its topics, conversations, suggestions, trusted knowledge and outputs.
An original file (asset or recording audio) is removed only after the rows are committed
and only when no remaining source uses it. Batches older than `TRASH_RETENTION_DAYS`
(default 30) are deleted the same way, at startup and twice a day.

Schema 12 repairs Output tables left by pre-release builds: missing request identity is
backfilled, and any other old shape is kept as `artifacts_legacy` beside a fresh table.

## Retrieval and optional models

Keyword retrieval works without additional services, accounts or models. CJK bigrams
are deterministic substring terms, not a linguistic Chinese segmentation model.

Semantic search is on by default (schema 14). The model is multilingual-e5-small (MIT),
an int8-quantized ONNX export pinned by revision and SHA-256, run with ONNX Runtime and
the Hugging Face `tokenizers` library. Chinese and English share one vector space, so a
Chinese question finds an English passage. Nothing is downloaded at run time and no text
leaves the device. Queries and passages get E5's `query: ` / `passage: ` prefixes; long
blocks are encoded in 510-token windows and averaged, so nothing is truncated.

Vectors live in SQLite through the sqlite-vec extension, loaded on every connection: one
`vec0` table per width (`block_vectors_384`) keyed by block, with `source_id`,
`revision_id` and `model` as metadata columns. A query is limited to the current revisions
of the libraries being asked before ranking, and the search is exact. Measured with 10,000
vectors: about 25 ms per scoped query, 16 MB on disk. (sqlite-vec partition keys were 3 GB
for the same data, so they are not used.) `block_embeddings` rows remain as per-model
markers; their `vector_json` is empty. Delete forever removes a source's vectors;
vectors of older revisions stay until then and are never returned.

Keyword and semantic ranks combine by reciprocal rank. E5 packs cosine similarity into
roughly 0.7–0.95, so its floor is 0.80: in a bilingual spot check, relevant passages scored
0.82–0.92 and unrelated text 0.67–0.82, so the ranges touch. This is a starting point, not calibrated
confidence; raw source citations display "Source", not a percentage.

A checkout fetches the model once, into the git-ignored `apps/backend/models/`:

```sh
npm run models:fetch
```

The desktop build fetches it too, if missing, and bundles it with ONNX Runtime and
sqlite-vec (the Linux helper is 167 MB). `SEMANTIC_SEARCH=false` turns it off.
`EMBEDDING_MODEL_PATH` points at a different ONNX export (`model.onnx` +
`tokenizer.json`); give it a new `EMBEDDING_MODEL_VERSION`, because vectors are kept per
version and a new version re-embeds every source in the background. Embedding runs in
the processing worker, about 35 short passages a second on four CPU threads.

`GET /api/retrieval/status` reports whether semantic search is on and why not, the
sqlite-vec version, passages and how many have vectors, pending embedding jobs, and the
last fallback; Settings shows the same as "Search by meaning". When the model or the
extension is missing, search is keyword-only and capture is unaffected.

## Papers at scale (schema 15)

Built for a library of about a thousand papers. Measured on 11 real arXiv PDFs
(Linux, four CPU threads): upload about 1 s; reading, embedding and summarising
about 5 s per paper in the background, so a thousand papers take roughly 1.5 hours
and are searchable as they finish.

**Passages.** PDF text is reflowed before it becomes the source body: printed lines
join into paragraphs, a line-end hyphen is removed only when the document itself uses
the joined word ("mod-els" becomes "models", "open-source" stays), ligatures become
letters, and section headings ("3.2.1 Scaled Dot-Product Attention", "References")
become Markdown headings, so every passage carries its section path. A block is a
passage of whole sentences up to 600 characters instead of one sentence or one printed
line; offsets still point at exact substrings. The E5 report went from 622 blocks to
112. Passages under References are kept out of search; title pages, emails, figure
labels, table rows and fragments under six words are searchable by keyword but get no
vector, because in E5 they resemble every question a little. Parser v2 revisions
replace v1 ones: existing files are parsed again once, in the background.

**Papers** (`source_papers`, `summary.md`). After each parse a `summarize` job reads,
without a model: the title (the largest type on a PDF's first page, with small caps;
else PDF metadata; else the source title), authors (between the title and the
abstract), year (arXiv id, a dated notice, or the PDF date), DOI or arXiv id, the
abstract with the blocks it came from, and the section outline. On the 11 test papers
every title, author list, year and abstract came out right. A source still named after
its file takes the paper's title. With the local model, the title and abstract also
become one paper vector (`paper_vectors_<width>`).

**Copies** (`works`). Sources with the same DOI or arXiv id, or failing that the same
title (20 characters or more), share a work: two versions of "Attention Is All You
Need" count as one paper. Topics, overviews and paper-level answers use one copy.

**Topics.** A topic can hold whole sources (`topic_sources`) as well as passages.
`GET /knowledge-bases/{id}/topic-suggestions` groups the papers not yet in a topic:
spherical k-means (seeded, so stable) over paper vectors, or TF-IDF of titles and
abstracts without the model; about √(n/2) groups, at most 15, each named by its most
distinctive terms. Creating a topic with `sourceIds` files them. Asking a topic of
papers searches all of them.

**Overviews** (`topic_syntheses`, `Topics/<topic>.md`). `POST
…/topics/{topic}/overview` writes one page: without a model, each paper by year with
authors and the opening of its abstract, cited `[n]` to the abstract's own blocks.
With `DEEPSEEK_API_KEY`, DeepSeek writes a synthesis (overview, lines of work,
agreements, open questions) from at most 40 abstracts, the most central ones; an
answer citing a number it was not given is discarded for the local page.

**Ask.** In a library of 12 sources or more, a question also gets the abstracts of up
to four closest papers not already cited (paper vectors), so a broad question sees the
breadth of the library, not only the few best passages.

Limits: multilingual-e5-small separates Chinese questions about English papers well
when they are specific ("医学图像分割用什么网络？" finds U-Net at 0.85, the rest below
0.80) and poorly when they are vague: scores then sit together near 0.82. A larger
model or translating the question first would help. Author and title reading is
heuristic; unusual layouts can still come out wrong, and the original is unchanged.

## Optional Docling layout parsing

Set `DOCLING_PYTHON` to an absolute Python interpreter in a separately provisioned
Docling environment, and `DOCLING_ARTIFACTS_PATH` to prefetched model artifacts.
PDF layout parsing runs in that subprocess, not the API process. Downloads and Hub
telemetry are disabled. The subprocess has a 240-second timeout, 180-second CPU limit,
32-MB output limit, 64-MB input limit and 500-page input limit (macOS/Linux).

The adapter walks document reading order, preserves hierarchy and PDF geometry, and
keeps structured table cells in block payloads. Original images remain in the source
asset. Existing local OCR supplies scan text; fallback or incomplete extraction is
reported as partial, not silently presented as a complete document.

This adapter has contract tests, not a real-corpus accuracy benchmark. No model weights,
external runtime or cloud service were installed or configured by this upgrade.

## API guide

- `POST /api/captures/assets?...&deferProcessing=true`: preserve original, create source,
  assign library and queue parsing. Inspect `importResult.source.processing`.
- `GET /api/sources/{id}/structure?offset=0&limit=100`: current structured representation;
  `nextOffset` paginates up to 500 blocks per response.
- Add `revision_id` and `block_id` to resolve a historical citation exactly.
- `GET /api/sources/{id}/processing`: saved/queued/running/ready/partial/failed/cancelled state.
- `POST /api/sources/{id}/reprocess`: retry failed/cancelled processing or create a new
  immutable representation. Unchanged extraction does not duplicate revisions.
- `POST /api/sources/{id}/processing/cancel`: prevent the current job from publishing.
  In-flight bounded parser work may finish before its result is discarded.
- `GET/POST /api/knowledge-bases/{id}/topics`: list/create persisted topics.
- `PATCH /api/knowledge-bases/{id}/topics/{topicId}`: rename, edit, reorder or reparent;
  send the last observed `version` (409 means reload before saving).
- `POST .../topics/{topicId}/evidence` with `{ "blockId": "..." }`: idempotent filing.
- Create a session with `focusChapterId: topicId` to retrieve only evidence linked to
  that topic or its descendants. Default library conversations remain unrestricted
  within their library/source scope. Old static chapter IDs remain compatible.

## Operations and recovery

Run the existing development commands (`npm run dev`, or `npm run dev:backend`).
The worker starts with a file-backed backend and pauses when that backend exits.
It is not an always-on operating-system service. It backfills older sources in
bounded transactions and resumes expired leases after a crash.

Use the existing backup/verify/restore commands. New authoritative records are in
the backed-up SQLite database; original assets and recordings retain their existing
backup paths. Preserve a backup before upgrading a real workspace. Do not downgrade
a schema-11 database with an older app; restore the pre-upgrade backup instead.

In-memory database tests drive the worker explicitly to avoid concurrent transactions
on SQLite's shared in-memory connection.

## Verification on this build

- Backend: 192 passed, 1 skipped, including v10-to-v11 migration, restart recovery,
  lease fencing, source/library isolation, Chinese retrieval and pinned topic evidence.
  The skipped release-helper security test was then run against the finished native
  executable and passed separately.
- Desktop: 79 passed; Ruff and TypeScript checks passed.
- Web build and macOS ARM64 `.app` / `.dmg` packaging passed. The local build is
  ad-hoc signed and passed deep/strict signature verification; it is not
  Apple-notarized for public distribution.
- In-app browser check with disposable data: create a topic, file a source block,
  open a topic-scoped conversation, ask a question and inspect its exact citation.
- The bundled backend executable passed authentication, asynchronous upload, real
  worker processing, document hierarchy, topic-scoped FTS and pinned-citation checks:

```sh
node scripts/smoke_structured_sidecar.mjs
```

That script creates and removes only its own temporary workspace. It never launches
the installed desktop app or accesses the user's library. The installed app was not
replaced; built artifacts are under `apps/desktop/src-tauri/target/release/bundle/`.

## Remaining release gates

This is a production-oriented foundation, not a production certification.

- Benchmark retrieval/citation precision on actual bilingual papers, books and notes.
- Verify Docling, signed-app packaging and memory limits on target Macs. The bundled
  model and sqlite-vec were verified in a Linux PyInstaller helper, not yet on macOS.
- Run physical microphone, sleep/wake and four-hour recording/transcription soak tests.
- Transcript time anchors are only as precise as supplied timestamps. This upgrade
  does not invent word alignment, speaker identities, or transcribe raw audio uploads.
- PDF/OCR safety limits can produce partial results; resumable page-by-page parsing
  for very large books is not yet implemented.
- Topic merging/deletion, multimodal visual retrieval, reranking and model-written
  per-paper summaries are not enabled; topic suggestions and overviews are.
- Keep untrusted retrieved content as evidence, never as instructions to execute tools.
