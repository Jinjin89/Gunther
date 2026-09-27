# Gunther product research

Status: research synthesis  
Reviewed: 2026-08-27

This document separates observed product behavior from Gunther's design decisions. Official documentation was used for capabilities; community reports are directional evidence for friction, not representative statistics.

## Executive finding

Current tools are good at one or two parts of the knowledge lifecycle, but none treats these four things as one continuously connected system:

1. immutable source evidence;
2. small, revisable claims;
3. human-authored knowledge units that synthesize those claims;
4. reusable outputs whose elements remain linked to the knowledge and revision that produced them.

That gap is Gunther's opportunity. It should not compete by adding another chat box or prettier global graph. It should make knowledge **traceable, maintainable, composable, and publishable**.

## Session and knowledge-workspace research

The modern pattern is no longer “one chatbot with a file upload.” It is a durable project or knowledge space containing several focused conversations:

- [ChatGPT Projects](https://help.openai.com/en/articles/10169521-projects-in-chatgpt) keeps chats, files, project instructions, and project memory together while allowing multiple separate chats inside the project.
- [Perplexity Sessions](https://www.perplexity.ai/help-center/en/articles/10354769-what-is-a-thread) preserves the initial question, follow-ups, responses, and sources as one coherent research trail.
- [NotebookLM](https://support.google.com/notebooklm/answer/16215270) lets a user explicitly select which notebook sources participate in a chat, making retrieval scope a visible interaction rather than a hidden backend choice.
- [Claude Projects](https://claude.com/docs/cowork/guide/projects) similarly treats project knowledge and instructions as durable context that each new session can inherit.

These products converge on four useful separations:

| Layer | Durable responsibility | Gunther interpretation |
| --- | --- | --- |
| Knowledge base | Sources, instructions, accepted knowledge, shared memory | A living field made of sources, claims, Knowledge Units, and revisions |
| Session | One line of inquiry and its working context | A titled, pinnable, archivable conversation with a chapter focus and source scope |
| Turn | User intent plus the generated response | Two persisted messages plus the retrieval snapshot used for that answer |
| Citation | A route back to supporting material | A claim-level link, or a bounded preserved-source passage when no structured claim is available, with source, quote, locator, status, and confidence |

### Implications for Gunther

1. **A knowledge base can have many sessions.** A session is not a duplicate knowledge base and must not silently fork accepted truth.
2. **Scope must be visible before sending.** “All sources” and an explicit selected subset are both first-class states shown next to the composer.
3. **Every answer keeps its retrieval snapshot.** The library can evolve later, but the user must still be able to inspect what an earlier answer searched and cited.
4. **Conversation is a staging area, not the truth store.** Useful chat output becomes a proposal for a Knowledge Unit or claim revision; it is never accepted merely because the model said it.
5. **Evidence inspection belongs beside the answer.** A three-pane layout—session history, conversation, evidence/source inspector—keeps continuity, focus, and verification visible without opening nested modals.
6. **An evidence gap is a valid result.** When selected sources do not support an answer, Gunther should expose the gap and suggest widening scope or adding a source instead of silently using general model knowledge.

## Search-first home and live-learning research

Tencent ima's current public product description reinforces two behaviors that belong together: one search/answer surface across public information and a personal knowledge base, plus recording notes that keep both the transcript and a generated summary. Its App Store listing also emphasizes multi-language recordings of up to two hours and direct ingestion of audio into personal knowledge. Tencent Meeting's recorder adds a useful lifecycle detail: the recording remains a first-class file after transcription, rather than being discarded once a summary exists.

Gunther adopts the low-friction entry point but keeps stronger boundaries:

| Observed pattern | Risk if copied literally | Gunther decision |
| --- | --- | --- |
| One prominent search field | Local evidence and open-web claims can become visually indistinguishable | Explicit **My knowledge**, **Knowledge + web**, and **Web** scopes; results remain in separate labeled layers |
| Automatic web answer | A provider failure can make the whole search feel broken | Run local and online retrieval independently; retain local results and display an honest web status |
| Record → transcript → summary | The generated notes can replace or distort the original | Preserve the audio locally, keep the transcript editable, and store the summary beside the full transcript |
| AI-generated meeting notes | Confident structure can hide missing speech or uncertainty | Include open questions, keep the transcript visible, and send the resulting source through the existing review inbox |

The implementation follows OpenAI's current realtime-transcription contract: 24 kHz PCM input, incremental transcript events, completed-turn events, and item IDs for ordering. Browser audio reaches OpenAI only through a backend WebSocket proxy, so the API key never enters the WebView. The online path uses the Responses API web-search tool and returns source URLs alongside the answer. macOS packaging includes `NSMicrophoneUsageDescription` and the audio-input entitlement; microphone access is requested only after the learner explicitly starts a recording, with bounded permission waiting, cleanup, System Settings guidance, and Retry.

Primary references: [Tencent ima App Store listing](https://apps.apple.com/cn/app/ima-%E8%85%BE%E8%AE%AFai%E7%9F%A5%E8%AF%86%E7%AE%A1%E5%AE%B6/id6737188438), [Tencent Meeting recorder](https://meeting.tencent.com/support/topic/2214/index.html), [OpenAI realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription), [OpenAI web search](https://developers.openai.com/api/docs/guides/tools-web-search), and [Tauri macOS bundle configuration](https://v2.tauri.app/distribute/macos-application-bundle/).

## Product benchmark

| Product | What it gets right | Recurring limitation | Gunther response |
| --- | --- | --- | --- |
| Tencent ima | Very low-friction collection from files, web, WeChat, images, and audio; grounded Q&A; notes, mind maps, podcasts, and shared libraries. | The dominant mental model remains a library of documents queried by RAG. Early reviews also found notes and the knowledge library felt separated. | Preserve its capture simplicity, but promote extracted claims and curated knowledge units to first-class data. Writing and publishing use the same units rather than copying chat output into notes. |
| NotebookLM | Excellent source-grounded synthesis and rapid generation of reports, maps, audio, video, and slide decks. Officially exports PDF and PowerPoint. | Users repeatedly ask for folders, source organization, better native source viewing, clearer limits, and a way for authored notes to become source-aware knowledge. Generated outputs are products of a notebook, not durable objects connected to evolving claims. | Keep output generation, but make each output element traceable to a unit, claim, source fragment, and revision. Allow live or pinned bindings. |
| Obsidian | Durable local files, fast writing, links/backlinks, an open canvas format, and web publishing. | Users must invent and maintain structure. Large global graphs become hairballs; plugins and metadata workflows create maintenance burden; export and media handling are frequent complaints. | Default to capture-first organization, typed relationships, local neighborhood views, and gradual schema suggestions. No plugin is required for the core knowledge lifecycle. |
| Heptabase | Strong spatial sensemaking with cards, whiteboards, mind maps, PDF reading, and recent agent/CLI access. | Large whiteboards can lag; users report card granularity and scattered AI entry points; a spatial layout alone does not encode evidence or claim validity. | Keep canvases as views over knowledge, not the knowledge store. A card can display a whole unit or an embedded block, and large maps can be query-backed projections. |
| Tana | Supertags, fields, live queries, voice capture, and schema-aware AI turn outline nodes into typed objects. | The power depends on learning and maintaining a schema; supertags and queries impose a steep setup cost. | Ship useful domain packs and infer candidate schemas from real content. The user accepts a schema delta instead of designing everything before capture. |
| Capacities | Its object model is intuitive, visually calm, and better structured than page-only systems; export is human- and machine-readable. | Object types can still become containers without evidence semantics. Community reports mention missing workflows, performance/version-history concerns, and friction around import or integration. | Separate object identity, authored explanation, atomic claims, and evidence. Make full history and open export core requirements. |
| RemNote | Combines notes, concept links, PDF annotation, active recall, and spaced repetition. | Its strongest abstraction is the flashcard/outliner, so broader knowledge synthesis and publishing can feel secondary. Visual content extraction and collaborative review have reported gaps. | Treat flashcards and quizzes as generated learning views over verified units, not the canonical form of knowledge. |
| Notion | Flexible blocks/databases, collaboration, connected-app search, and source citations in enterprise search. | Flexible workspaces drift into duplicate pages, inconsistent schemas, stale documentation, and expensive maintenance. A page can have properties without explaining why a statement is true. | Give every important claim provenance, freshness, ownership, and conflict state. Let LLMs propose cleanup and schema alignment as reviewable diffs. |

## Pain points that repeat across tools

### 1. Capture grows faster than understanding

Saving is easy. Synthesis is not. Users accumulate documents, web clips, notes, chats, and generated summaries, then cannot tell what changed their understanding. RAG improves retrieval but does not create a maintained knowledge model.

**Design requirement:** every import produces a visible knowledge delta: new, reinforced, contradicted, superseded, or unresolved.

### 2. Organization becomes unpaid maintenance

Folders are rigid, tags sprawl, database schemas drift, and canvases require manual gardening. Flexible tools transfer ontology design to the user; rigid tools fail across domains.

**Design requirement:** one global library, flexible domain lenses, suggested types and relationships, and progressive structure. Capture must work before the user defines a taxonomy.

### 3. Graphs visualize links, not meaning

A graph of note-to-note links often becomes visually impressive but operationally weak. Edges usually lack type, context, evidence, confidence, or validity time.

**Design requirement:** typed claims are the graph. Default to a focused neighborhood, path, comparison, or question-specific subgraph instead of an unfiltered global hairball.

### 4. AI outputs are detached copies

Summaries, notes, mind maps, and decks are commonly generated as snapshots. When knowledge changes, the output silently becomes stale; editing the output does not improve the knowledge base.

**Design requirement:** artifact elements bind to knowledge revisions. Gunther shows upstream changes and lets the user refresh, pin, or intentionally override them.

### 5. Truth and provenance are page-level

Most tools cite a document for an answer, but they do not maintain evidence at the level of each claim. Contradictions, scope qualifiers, and changed facts are flattened into prose.

**Design requirement:** use statement-level qualifiers, references, stance, history, and validity. The graph must preserve disagreement rather than selecting one answer invisibly.

### 6. AI is powerful but not inspectable

Users report unpredictable cost, opaque limits, source blindness, and anxiety when agents change large spaces. A generic chat surface also fragments the workflow.

**Design requirement:** LLMs produce typed proposals and patches with preview, scope, evidence, estimated work, and an undoable commit. AI should appear at the object being changed.

### 7. Retrieving is mistaken for learning

Fast summaries and generated study media can create passive familiarity without the active work of organizing, explaining, testing, and correcting.

**Design requirement:** ask the user to confirm structure, explain relationships, compare contradictions, and optionally generate retrieval practice from trusted units.

### 8. Export is an exit, not a living projection

PDF, Markdown, or PowerPoint export usually breaks the connection to the source system. Canvas backup formats may preserve geometry but not reusable semantic intent.

**Design requirement:** export an artifact manifest alongside HTML/PDF/PPTX, recording unit IDs, revisions, citations, bindings, theme, and renderer version.

## What Gunther should copy—and what it should avoid

### Copy

- ima's low-friction, multi-channel capture.
- NotebookLM's grounded transformation into useful formats.
- Obsidian's local ownership and open formats.
- Heptabase's spatial thinking and focused visual organization.
- Tana and Capacities' typed-object ergonomics.
- RemNote's active review loop.
- Notion's polished block editing and composable views.

### Avoid

- document folders presented as a knowledge model;
- global graph views with untyped links;
- mandatory schema design before useful capture;
- AI that directly rewrites trusted knowledge;
- generated outputs that immediately become stale copies;
- proprietary-only storage or export;
- one abstraction forced onto biology, mathematics, and management.

## Standards and technical lessons

- [Wikidata's data model](https://www.wikidata.org/wiki/Help:Data_model) demonstrates why statements need qualifiers, references, and rank rather than bare subject-predicate-object edges.
- [W3C PROV-O](https://www.w3.org/TR/prov-o/) provides a useful vocabulary for entities, activities, agents, derivation, revision, quotation, and primary sources.
- [Nanopublications](https://nanopub.net/) show the value of packaging an atomic assertion with its provenance and publication metadata.
- [ProseMirror's document model](https://prosemirror.net/docs/guide/) demonstrates a schema-constrained JSON tree that can render to multiple formats; Gunther should store structured content rather than raw HTML.
- [Open Canvas Interchange Format](https://github.com/ocwg/ocif-spec/blob/main/spec/v0.7.0/spec.md) and JSON Canvas show that spatial layouts should have an open, inspectable representation.
- [PptxGenJS HTML-to-PowerPoint](https://gitbrent.github.io/PptxGenJS/docs/html-to-powerpoint/) only converts HTML tables with important CSS limits. Gunther should map its own scene components to native PowerPoint objects rather than promise universal HTML-to-PPT conversion.
- [Reveal.js](https://revealjs.com/pdf-export/) and [Slidev](https://sli.dev/guide/exporting.html) validate HTML-first presentation delivery, but Gunther still needs its own semantic binding and revision model.

## Primary product sources

- Tencent ima: [Google Play product listing](https://play.google.com/store/apps/details?id=com.tencent.ima), [Tencent 2024 SSV report](https://static.www.tencent.com/attachments/ssv/2025/tencent-ssv-report-2024.pdf), and an [early hands-on review](https://hub.baai.ac.cn/view/41554).
- NotebookLM: [product help](https://support.google.com/notebooklm/answer/16164461), [source support](https://support.google.com/notebooklm/answer/16215270), [slide deck export](https://support.google.com/notebooklm/answer/16757456), and [organization/source-viewing reports](https://www.reddit.com/r/notebooklm/comments/1sjmkk4/whats_your_biggest_frustration_with_notebooklm/).
- Obsidian: [Canvas](https://obsidian.md/canvas), [Publish](https://obsidian.md/help/publish), and [large-vault workflow complaints](https://www.reddit.com/r/ObsidianMD/comments/1jwchcg/whats_your_biggest_frustration_with_obsidian/).
- Heptabase: [product overview](https://heptabase.com/), [performance guidance](https://support.heptabase.com/en/articles/11430704-troubleshooting-performance-and-lag-issues-in-heptabase), and [community product feedback](https://www.reddit.com/r/heptabase/comments/1vliyvi/heptabase_community_letter_ama/).
- Tana: [supertags](https://outliner.tana.inc/learn/features/supertags), [fields](https://outliner.tana.inc/learn/features/fields), and [input API](https://outliner.tana.inc/learn/features/input-api).
- Capacities: [object model](https://capacities.io/product), [export](https://docs.capacities.io/reference/export), and [offline behavior](https://docs.capacities.io/misc/offline-support).
- RemNote: [knowledge and learning model](https://help.remnote.com/en/), [flashcard lifecycle](https://help.remnote.com/en/articles/8663109-flashcard-basics), and [visual-source limitations reported by users](https://www.reddit.com/r/remNote/comments/1oqs55s/does_anyone_use_remnote_as_a_holistic_learning/).
- Notion: [enterprise search](https://www.notion.com/help/enterprise-search) and [staleness/maintenance reports](https://www.reddit.com/r/Notion/comments/1n7km3t/weve_tried_using_notion_for_guides_heres_what/).
