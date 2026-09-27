# Knowledge session workspace

Status: implemented foundation  
Reviewed: 2026-08-27

## Product boundary

A Knowledge Base is durable shared context. A Session is one focused line of inquiry inside that base. A message is immutable conversation history. An assistant answer can create a Proposal, but it cannot directly mutate accepted knowledge.

```text
Knowledge Base
  ├─ Sources, accepted claims, and versioned Knowledge Units
  ├─ Session A
  │    ├─ source scope + chapter focus
  │    ├─ user and assistant messages
  │    └─ per-answer retrieval snapshots and citations
  ├─ Session B
  └─ Reviewable proposals
```

## Implemented interaction model

- Searchable session history with create, open, rename, pin, and non-destructive archive.
- Archived sessions remain inspectable and exportable but are read-only until explicitly restored.
- Recoverable archive view plus Markdown session export with citations and scope metadata.
- Copy-answer and stop-waiting controls; an interrupted question is restored to the composer.
- Branch-from-answer creates a new session with copied history, explicit parent/message lineage, and immutable evidence snapshots while preserving the parent.
- A responsive session drawer keeps history and creation available when the desktop sidebar is hidden; at phone width the application rail becomes a bottom navigation bar and the Knowledge Base chrome compacts without horizontal overflow.
- Backend-persisted Knowledge Base metadata and a real create-base flow that opens an empty Overview workspace.
- Editable Knowledge Base identity with revision records, without changing its source, session, or unit history.
- One chapter focus per session.
- “All sources” retrieval or an explicit selected source set.
- Grounded local synthesis when no model key is configured.
- Evidence-constrained DeepSeek synthesis with a deterministic local fallback.
- Claim- or source-passage citation cards with source title, quote, locator, status, and confidence; raw-source citations explicitly carry `assertionId=null`.
- Hyphen-aware retrieval keeps concepts such as “T-cell” intact and rejects a response when only a generic word overlaps, so an explicit evidence gap wins over an irrelevant citation.
- A read-only source drawer that exposes the complete preserved original plus each extracted subject–predicate–object claim, review status, confidence, and source locator behind a citation.
- Immutable retrieval metrics stored on every assistant message.
- Offline preview that remains usable without pretending its responses are persisted.
- Promotion of an assistant synthesis into a durable review proposal.
- Inbox acceptance/hold decisions synchronized back to the proposal revision log.
- Acceptance materializes a durable Knowledge Unit and immutable first revision; repeated acceptance is idempotent.
- Knowledge Units retain proposal, source-session, source-message, chapter, and evidence-count lineage and can reopen the exact originating retrieval snapshot.
- Search-first dark home with explicit Personal, Web, and Combined scopes. Multi-word local search spans curated chapters, accepted units, indexed sources, sessions, and Notebook notes; natural query framing is removed before matching, source results open the preserved original, and unit results reopen their source turn.
- A first-class Notebook holds unclassified fragments without requiring a Knowledge Base or running extraction. Notes auto-save, can be pinned or archived, appear in personal search, and become immutable source snapshots only through an explicit filing action.
- Optional sourced online answers through the backend Responses API web-search provider. Missing or failed online search never removes personal results.
- A complete `.gunther.json` workbook export containing metadata, original source contents, Knowledge Units, all session turns and citation snapshots, proposals, and accepted changes.
- A locally editable learner profile included in complete workbook exports.
- Local text-file capture for TXT, Markdown, CSV, TSV, and JSON with explicit size/type validation.
- Live lecture capture with a real microphone level meter, pause/resume, original-audio persistence, low-latency transcript events when configured, editable fallback text, structured summaries, and import into the existing review workflow.
- Failed imports remain in a visible local retry queue and cannot be accepted before indexing succeeds.
- Accepting a captured source reviews all of that source's candidate claims in one backend transaction and records an immutable status revision per claim.

## API surface

| Method | Route | Responsibility |
| --- | --- | --- |
| `GET` | `/api/knowledge-bases` | List durable base metadata with source, session, and proposal counts |
| `POST` | `/api/knowledge-bases` | Create a base with a title, question, boundary description, and accent |
| `PATCH` | `/api/knowledge-bases/{baseId}` | Edit durable base identity and record a revision |
| `GET` | `/api/search` | Search notes, units, sources, and sessions across base boundaries |
| `GET` | `/api/search/web` | Return a current online answer with source URLs, or an explicit configuration/failure state |
| `GET` | `/api/notes` | List or search Notebook notes by inbox, filed, or archived state |
| `POST` | `/api/notes` | Create a lightweight note without requiring knowledge attribution |
| `PATCH` | `/api/notes/{noteId}` | Auto-save content, pin a note, archive it, or restore it |
| `POST` | `/api/notes/{noteId}/file` | Snapshot a note as a source, attach it to one base, and run reviewable extraction |
| `POST` | `/api/recordings` | Preserve original lecture audio in the local recordings directory |
| `GET` | `/api/recordings/{recordingId}` | Retrieve a locally preserved original recording |
| `WS` | `/api/recordings/live` | Proxy 24 kHz PCM audio to realtime transcription without exposing the provider key |
| `POST` | `/api/lectures/summarize` | Produce overview, key points, actions, questions, and terms from a transcript |
| `GET` | `/api/sources/{sourceId}` | Inspect the preserved source, extracted entities, claims, and timestamps |
| `PATCH` | `/api/sources/{sourceId}/assertions/status` | Review every candidate claim from one captured source atomically |
| `GET` | `/api/knowledge-bases/{baseId}/sources` | List only sources indexed into one base |
| `GET` | `/api/knowledge-bases/{baseId}/units` | List accepted Knowledge Units and their revision heads |
| `GET` | `/api/knowledge-bases/{baseId}/sessions` | List active sessions, pinned first |
| `POST` | `/api/knowledge-bases/{baseId}/sessions` | Create a session with optional focus and source scope |
| `GET` | `/api/sessions/{sessionId}` | Load messages and session state |
| `PATCH` | `/api/sessions/{sessionId}` | Rename, pin, archive, or change future context |
| `POST` | `/api/sessions/{sessionId}/messages` | Persist a turn and its grounded response |
| `POST` | `/api/sessions/{sessionId}/messages/{messageId}/branch` | Create an independent session branch at a chosen message |
| `POST` | `/api/sessions/{sessionId}/messages/{messageId}/proposal` | Promote one assistant answer for review |
| `GET` | `/api/knowledge-bases/{baseId}/proposals` | Load proposal inbox state |
| `PATCH` | `/api/proposals/{proposalId}` | Accept, hold, or reject a proposal |

## Retrieval and answer rules

1. Use the session's explicit source IDs when present; otherwise search only sources attached to its Knowledge Base. An empty base returns an evidence gap; it never falls back to another base or the global library.
2. Reject unknown source IDs instead of persisting a scope the retriever cannot honor.
3. Reject cross-base source IDs; selecting a source never silently attaches it to a new Knowledge Base.
4. Rank atomic assertions against normalized query terms.
5. Prefer matched claims and never pad citations with unrelated high-confidence claims. If no supported claim matches, rank a bounded set of relevant passages from the preserved text of in-scope sources and cite them with `assertionId=null`; generic one-word overlap is insufficient.
6. Pass only ranked evidence, recent session history, and the current question to the responder.
7. Require inline citation numbers from the model and fall back locally if any cited index is missing or out of range.
8. Persist citations and retrieval counts with the message before returning the turn.
9. If neither a supported claim nor a sufficiently relevant preserved-source passage exists, return a visible evidence gap instead of general model knowledge, and block promotion until evidence exists.
10. Report the responder that actually produced the answer; a DeepSeek failure is labeled as local fallback.

## Deliberate next steps

- Move chapter bodies and domain-specific curated content into backend revisions; base metadata and source membership are already persisted.
- Add streaming responses and propagate cancellation through the backend provider request; the current control cancels the desktop wait safely.
- Add a branch ancestry map; direct parent lineage is already visible and the complete workbook is exportable as structured JSON.
- Let reviewers edit, split, or merge a proposal before acceptance.
- Add editable Knowledge Unit revisions, claim selection, and split/merge operations; accepted proposals already create immutable first revisions.
- Add embeddings or full-text indexing while preserving the current evidence-first ranking contract.
