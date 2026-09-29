# Capture: concurrent captures and AI summaries

Two changes to Capture, made on 2026-09-29.

## 1. A recording runs beside everything else

A recording used to take over Capture: the other tabs were disabled, and a new
request from the menu bar or the main window was ignored. Now the recording is
its own session inside Capture:

- It has its own title, transcript and library. The note, link, file, photo and
  table tabs keep theirs, so moving between tabs loses nothing.
- Once started, the recorder stays mounted (hidden) while another tab is open.
  The Recording tab shows its timer, and the status line says it is recording
  in the background.
- Saving a note, file or link clears only that capture. The recording keeps
  going and Capture returns to it. Saving the recording leaves an unsaved note
  open.
- A request from elsewhere (menu bar "Capture Something Else…", the main
  window, a shortcut) switches the open Capture to the requested type instead of
  being dropped. Unsaved text is never replaced.
- The menu bar keeps its recording state when a note is saved beside it
  (`captureContinues` on the saved event).

There is still one microphone, so there is one recording at a time.

## 2. Every capture is summarized

After a capture is read, a `digest` job writes a summary suited to what it is:

| Capture | Summary |
|---|---|
| Lecture / course recording | Study notes: key ideas, homework, open questions, terms |
| Meeting recording | Decisions, action items (owner and date when said), open questions |
| Voice memo | The thought restated clearly, its ideas, to-dos |
| Photo or scan | What it shows (OpenAI sees the image; otherwise its recognized text) |
| Table | What it records; per-column ranges, means and common values |
| Paper | Problem and approach, contributions, findings, limitations |
| Document, web page, long note | Overview, key points, actions |

Rules:

- **The original stays the evidence.** A summary is derived from one revision,
  labelled with what wrote it, and every key point cites the numbered passages
  it came from. Citations to passages the model was not given are dropped.
- **Nothing waits on it.** Summaries run in their own worker lane, so a slow
  model never delays reading and indexing new captures.
- **Placeholder names are replaced.** A capture still called "Untitled",
  "Lecture · 29/09/2026" or `IMG_2041` takes the summary's title. A name
  someone chose stays.
- **Only a model writes summaries.** There is no model-free stand-in: without an
  OpenAI or DeepSeek key there are none (the source page and Settings say a key
  is needed), and a model that fails is retried twice, then shown as failed with
  its reason and Try again. The recorder's "Summarize recording" follows the
  same rule.
- **Short notes are their own summary** (under 280 characters).
- **It goes into the library.** `summary.md` in the library folder holds it, and
  `Overview.md` links to it.

Where it shows: a Summary card at the top of each source page, with the
passages behind each point one click away and "Write again".

Settings (`.env`): `AI_SUMMARIES=auto|off` (auto uses OpenAI, else DeepSeek)
and `AI_SUMMARY_IMAGES=true|false`.

API: `GET /api/sources/{id}/digest` returns `reading | writing | ready | none |
failed | off` with the summary, `offReason` (`no_key | setting`) and the last
`error`; `POST` writes it again.

Not yet: summaries are not searched or used by Ask, and existing sources are
not summarized in bulk (open one and choose Summarize).
