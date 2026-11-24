# Library

Capture goes to Inbox or directly to a library. File or move an item from its detail page. A source can be filed in more than one library.

## Library, Chat and Studio

- The **Library** column has separate collapsible Chats, Sources and Key results sections, with Add source at the bottom.
- **Chats** shows pinned chats plus the four most recent others, with a New chat button, search, and an All chats disclosure that includes archived chats. Each row shows its relative update time, pin state, and branch lineage, and its menu can rename, pin or unpin, and archive or restore. The section remembers whether it is collapsed, and reports its own chat loading error with Retry.
- **Sources** has the library's sections and sources. Create, rename and nest sections. Hover or focus a row to reveal its drag handle; the handle also supports keyboard dragging. A source's actions menu offers Rename, Move to…, Take out of section for placed sources, and Move to Trash with Undo. Each action targets that row and leaves the scope selection intact. A source with no placement remains in the library. Deleting a section clears those placements and keeps the sources. The API uses stable IDs and rejects conflicting orders.
- Tick sources to choose material for Chat and new Studio outputs. A section tick selects its descendant sources, including those under collapsed sections. All sources includes additions to the library. The library remembers its selection across chats. Select at least one source to send a question or start an output from a selection.
- Use **Add source** to capture a document, photo or scan, web page, recording, or note or pasted text directly into this library.
- Click a source title to see its summary and paper details in the middle. The Inspector's Evidence tab shows key results supported by that source; choosing one opens its original passages. Key-point citations open Evidence at the digest's source revision. Choose a term or open question to put it in the Chat composer, or use its send button to ask with the current scope. Open full page opens the source reader. Back to chat restores the open conversation, draft and scroll position. The right-hand Inspector contains Evidence and Studio tabs; Studio tiles start builds, and Studio cards open saved or running outputs. [Output guide](OUTPUTS.md)
- Home search crosses libraries. Use `@` to narrow it, or use search within a library. Exact text uses SQLite full-text search. The installed local embedding model adds search by meaning with sqlite-vec. Source, section and selected-source scopes restrict eligible passages.
- Suggest sections groups unplaced papers by shared terms when enough papers are available.

The app sidebar starts collapsed in a library. Drag the inside edge of Library or Inspector to resize it, or focus the separator and use the arrow keys. Double-click the edge to restore the starting width. The desktop remembers each width on this device. Inspector starts on Studio and collapses to Evidence and Studio rail buttons below 1080 px; either opens a slide-over. Escape returns from a passage or Trace to the Evidence list before closing that slide-over. Library collapses below 840 px; Escape or choosing a chat or source closes its slide-over. Clicking elsewhere inside the workspace closes either open slide-over; menus and dialogs outside the workspace leave it open. Opening slides below 1200 px also collapses Library. The desktop window can shrink to 800 px.

Opening an empty Chat requests an overview of the library's current sources. Analysis uses each source's current digest overview, paper abstract or opening passage, so notes, web pages and recordings can contribute. The job appears in Activity and under Background jobs in Settings → Developer. The saved overview is reused until the live source set or its material changes. If the job fails, choose Retry in Chat to try again on the same material. [Ask](ASK.md#ask-a-question)

## Key results

Analysis can keep up to five findings from a saved answer or output. Each result is a sentence backed by original library passages. The Checker must mark the sentence supported. A later answer or output can add checked evidence to the same result.

**Key results** is a separate section below Sources and counts the kept results supported by the current source selection. A result appears when all the passages checked together by at least one origin are eligible and come from selected, live sources with unchanged originals. Unticking one source hides a comparison that depended on it unless another origin checked the sentence using sources that are still selected. Re-reading a file keeps results tied to the same original. If the original text of a note or pasted source changes, results that depended on the earlier text disappear. Restoring a source from Trash brings back the results it still supports.

Empty lists and lists of more than three results start collapsed; the section remembers a manual choice for each library. Lists of more than five have their own filter. Select a result to open Evidence and read the source, locator and quote. The passage picker in Evidence switches between the passages in the currently usable evidence groups.

Chat searches key results during library lookup, and Studio searches them while preparing a build. A result can help the model find and frame evidence within the current scope. The model cites the numbered passages from the source reading. [Ask evidence](ASK.md#evidence-and-checking) · [Build in Studio](OUTPUTS.md#build-in-studio)

Remove takes one sentence out of the list. Undo in the toast restores it. The kept count under an answer or output has its own Undo, which removes that origin's contribution and preserves other origins' evidence on their results. Failed jobs offer Retry and appear in Activity. Setting up an Analysis model resumes jobs that were waiting for one.

While trusted saved answers await checking, Key results offers a read-only link to view them. Each can contribute once. Their stored revisions remain available to the output versions that pinned them.

The key results API uses `/api/knowledge-bases/{libraryId}/key-results` as its base path.

| Method and suffix | Result |
| --- | --- |
| `GET` on the base path | Lists the library's kept, usable sentences with their creation origins and original passages. |
| `POST` on the base path with `sourceIds` | Lists kept sentences whose evidence supports them within the specified source selection. |
| `GET /pending-saved-answers` | Lists trusted saved answers whose key results jobs have yet to finish. |
| `POST /{resultId}/remove?withUndo=true` | Removes one sentence and returns the time of that removal. Later distillation keeps the sentence suppressed. |
| `POST /{resultId}/restore?removedAt=…` | Restores the sentence from that removal while an evidence group remains. |
| `GET /origins/{kind}/{originId}` | Returns the job state, kept count and kept IDs. This data also appears on saved Ask messages and output version details. |
| `POST /origins/{kind}/{originId}/undo` | Removes the sentences created by that origin and deletes its evidence contributions. |
| `POST /jobs/{jobId}/retry` | Queues a failed job again. |

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
    Key results.md
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

`Key results.md` is a read-only export with one line per kept, usable result and its source titles and locators. **Export library** in the library actions includes the same results under `keyResults` in the JSON workbook. Both exports use the full live library scope.

## Trash

Move sources, notes, libraries and outputs into Trash with Undo. Sources kept in another live library stay available there. Trashed sources leave active search and new evidence gathering. Outputs go with their version history. The Trash page shows the output type, version count and when it was moved. Restore inside Trash, or use Delete forever or Empty Trash to remove the items and any now-unused managed originals. The service keeps Trash for 30 days by default and purges expired batches. Saved answers and sealed output versions retain their citation snapshots. [Output Trash](OUTPUTS.md#trash)

Permanently deleting a source removes each key result evidence group that required that source. The sentence stays if another group still supports it.

[Contents API](../apps/backend/gunther/knowledge_api.py) · [Pinned reading](../apps/backend/gunther/evidence_reader.py) · [Trash service](../apps/backend/gunther/trash.py)
