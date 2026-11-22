# Library

Capture goes to Inbox or directly to a library. File or move an item from its detail page. A source can be filed in more than one library. The Contents tree arranges sections and source placements while keeping each source's identity.

## Contents and search

- Create, rename and nest sections. Drag or move sources into a section and order its children. A source with no placement remains in the library. Deleting a section clears those placements and keeps the sources. The API uses stable IDs and rejects conflicting orders.
- Home search crosses libraries. Use `@` to narrow it, or use search within a library. Exact text uses SQLite full-text search. The installed local embedding model adds search by meaning with sqlite-vec. Source, section and selected-source scopes restrict eligible passages.
- Topic suggestions use terms present across the library's documents. Analysis writes the overview when a model is configured. The existing overview is available while an update fails or needs a key. Use Retry after the failure is resolved.

## Readings and provenance

Each capture retains its original and provenance. The current reading consists of ordered elements, source regions and indexed passages, with a revision ID. Document structure comes from the file's own layout, reading order and tags when available. An outline may be empty. Open a passage or figure through the corresponding reading revision.

File capture queues a local reader. It recognizes PDFs, supported images, DOCX, EPUB and evidenced text formats. A BOM, file extension or declared text type admits text decoding. It uses UTF-8 or the BOM's encoding and falls back to Windows-1252 for undecodable UTF-8. The reader keeps usable text and reports skipped parts, decoding replacements or extraction limits. Formats without a reader are preserved as originals. OCR on images and applicable PDF pages runs on the backend. The source displays OCR status, provider and warnings from reading metadata, and can still show saved OCR text beside a current warning.

PDFs get an initial native reading. The offline Docling parser can publish a second reading with structure, regions and figures. Optional model review corrects the structure using the document evidence. A Read again candidate without successful review cannot displace a checked current reading. The app reports partial or failed extraction and keeps the original available. The paper card uses observed title, authors, publication metadata, abstract and outline where available. Paper abstracts used in retrieval require eligible abstract blocks.

The shared readers, leases and head guards are described in [Architecture](ARCHITECTURE.md#data-flow). For model jobs and OCR setup, see [Settings](SETTINGS.md) and [Operations](OPERATIONS.md#recovery).

## Library folders

The installed desktop defaults to `~/Gunther`. Use the Library folder setting to see, open or move it. `LIBRARY_ROOT` sets it for a backend run. A source and its local assets remain backed by the database and managed originals.

```text
<library root>/
  .gunther/                  database, originals and private workspace files
  Inbox/                     unfiled sources and notes
  Libraries/<library>/
    library.json
    Overview.md
    Sections/<section>.md
    Sources/<source>/
      source.json
      content.md
      summary.md             when available
      original.<ext>         for file captures
      recording.<ext>        for recordings
  Trash/                     readable copies of trashed material
```

Each source has a home folder. Other library placements use relative links. The folder writer links to managed originals where possible and records its own files. Treat the readable files as exports. Edit the source from Gunther, and use a workspace backup for restore. [Library folder writer](../apps/backend/gunther/library_folders.py) · [Backup](OPERATIONS.md#backup-and-restore)

## Trash

Move sources, notes and libraries into Trash with Undo. Items kept in another live library stay available there. Trashed sources leave active search and new evidence gathering. Restore inside Trash, or use Delete forever or Empty Trash to remove the items and any now-unused managed originals. The service keeps Trash for 30 days and purges expired batches. Saved answers and sealed output versions retain their citation snapshots.

[Contents API](../apps/backend/gunther/knowledge_api.py) · [Pinned reading](../apps/backend/gunther/evidence_reader.py) · [Trash service](../apps/backend/gunther/trash.py)
