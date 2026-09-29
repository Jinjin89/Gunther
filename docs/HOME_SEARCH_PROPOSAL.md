# Home search: search first, answer on demand

Status: built (2026-09-29). Before this, Home ran a fast keyword/meaning search
(optionally with web results and a short Tavily answer). Asking a question there
felt like "search and return", when the person often wants an *answer*.

## What Home is for

One box, two intents:

- **Find**: "where did I put the note about X" → wants a ranked list, instantly.
- **Ask**: "what do my papers say about X, and is that still current?" → wants an
  answer with sources, across everything, without choosing a library first.

## Proposal: search first, answer on demand

1. **Enter runs the fast search** exactly as now: instant, no model, works
   offline, results grouped by library. `@library` still scopes it.
2. **An Ask card sits above the results.** It shows the question's likely intent
   and one action: *Ask Gunther*. Pressing **⌘/Ctrl+Enter** does the same
   without a mouse. Questions phrased as questions (ends in ?, starts with
   what/why/how/是否…) get the card expanded and focused; keyword queries get it
   collapsed to a single quiet line.
3. **Asking runs the same agent as a library's Ask** (see ASK_AGENT.md) with
   every library as scope (or the `@` ones), the Web switch, and the model
   picker already on Home. The answer streams into the card, its citations
   link to the passages in the results below.
4. **The conversation is kept.** It appears under *Recent* on Home and can be
   continued, or **filed into a library** (moving its sources scope with it).
   Nothing is saved as knowledge without the existing "Propose as knowledge"
   decision.

## Decisions to make

| Question | Recommendation |
| --- | --- |
| Where do Home conversations live? | A built-in "Home" scratch space, listed in Recent, fileable into a library. Avoids inventing a library per question. |
| Answer automatically on Enter? | No. A model call costs time and money and search should stay instant; the Ask card is one keystroke away. Revisit after seeing how often it is used. |
| Which libraries does the agent search? | All by default, or the `@` ones. The agent picks the query; the scope stays the person's. |
| Web on Home | Same shared Web switch as Ask. Off means the answer says the web was not searched. |

## What it takes

Backend: sessions currently belong to one library, so a `scope` of many
libraries (or none = all) on sessions, and `search_library` running over that
scope. Frontend: the Ask card and streaming. Streaming also helps library Ask,
whose agent takes several calls: progress ("Searching your library…") arrives
as the steps happen instead of after them.

## As built

- `⌘/Ctrl+↵` in the Home box, or the **Ask Gunther** card under a search, asks
  the agent. The card is prominent for question-shaped searches (a `?`, a
  question word, or seven or more words) and a quiet dashed line otherwise.
- Conversations belong to the reserved scope `@home` (library ids never contain
  `@`), are listed under **Recent questions** on Home, and can be **filed** into
  a library with `POST /api/sessions/{id}/file`; they keep their messages and
  citations.
- Scope is every live library plus unfiled captures, or the libraries picked
  with `@` (`knowledgeBaseIds` on the message). Web follows the same switch.
- Answers stream: `POST /api/sessions/{id}/messages/stream` sends `intent`,
  `step` (a search running, then done) and `text` events, then `done` with the
  saved turn. Closing the connection stops the work and saves nothing. Ask in a
  library uses the same stream.
- Home conversations are not found by search and never become trusted
  knowledge without the usual "Propose as knowledge" decision (after filing).
- The plain search also finds sources by meaning: after the exact matches it
  adds up to six sources whose passages match most of the words, or match by
  meaning when Search by meaning is on (the retrieval Ask uses). They are
  labelled "related by meaning" (or "its words"), keep the library scope from
  `@`, and never repeat an exact match. A sentence like "Which marker
  identifies T cells?" therefore finds the note that answers it.
