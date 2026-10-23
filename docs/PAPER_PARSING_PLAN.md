# Paper parsing — plan

Agreed 2026-10-23. Evidence: three open-access papers (Attention, ResNet, PLOS ONE) run
through the real import pipeline, fallback vs Docling; reports in the job scratch dir
`/root/.claude/jobs/011436d7/tmp/codex/parse-run/` (`report.md`, `*-docling.md`,
`docling-build-notes.txt`; harness `run.py` / `run-docling.py`).

Goal: every document comes out with its own structure (recovered, never imposed — see
AGENTS.md), readable by a person (clean outline, figures in place with captions, page
furniture hidden but available) and by the machine (passages know their section; furniture
and references never feed search, Ask, Outputs or summaries).

## A. Let Docling finish (biggest)
Docling took 75–175 s per paper on this CPU box and the shipped limits killed it, so imports
silently fell back to the native reader (figures lost, footnotes in text).
- The quick native extraction stays the first, immediately usable revision.
- Docling then runs as a background upgrade with a limit that scales with page count
  (wall clock and CPU; e.g. base + per-page, capped), with the worker lease heartbeat kept
  alive, and replaces the revision when it succeeds; a failure keeps the earlier revision
  and records why.
- The reader shows "Reading layout…" while it runs and says plainly when layout parsing
  failed (with Try again), instead of only "Partly readable".
- Files: processing.py, document_parser.py, asset_service.py, the worker/lease code it uses,
  and the desktop reader (apps/desktop/src/items/views/CanonicalReader.tsx and its status).

## B. One shared rule for page furniture
Footnotes, page headers/footers, page numbers and sidebars/margin notes are kept as their
own elements (original labels preserved), hidden by default in the reader, and excluded from
search (keyword + embeddings), Ask, Outputs, digests/summaries, abstract/gist assembly and
section expansion — through ONE policy function in document_structure.py that every
consumer uses.
- Files: document_structure.py (eligibility policy only), knowledge_index.py, digest.py,
  papers.py (abstract/gist assembly only), service.py (output passages ~3651, section
  expansion ~4184), document_parser.py label mapping only if needed to keep a label.

## C. Captions, title, references
- When Docling links a caption to a picture/table, keep that link; our geometric guess
  (document_structure.py ~408) only fills gaps, never overrides; numbered-caption matching
  must not cross columns. (ResNet: Figure 6 got Table 7's caption.)
- Title: not a running header / journal name repeated on many pages ("Plos One"); prefer
  PDF metadata title or the first-page title element.
- References: the reference section ends at the next real heading; an appendix after it is
  body text again. (ResNet: 24 appendix paragraphs filed as references.)
- Files: document_structure.py (caption association + reference span functions only),
  papers.py (title + reference detection only).

## Not now
LLM review pass (decide after A–C, for reading order around sidebars).

## Rules for all builders
General rules only — no special cases for these three papers (AGENTS.md). Keep text and
provenance unchanged. No real model calls; do not commit; do not edit package files.
Tests: synthetic fixtures in tests/test_documents.py, test_document_structure.py,
test_papers.py (or a new test file per part).
