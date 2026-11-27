# Outputs

Studio makes reports, slide decks, flashcards, quizzes, mind maps and audio overviews from a library. Work begins in Setup, continues through a plan you can revise, and opens in the same conversation as a saved reader after you approve generation.

## Build in Studio

### Set up and plan

Empty **Studio** shows all ten creation tiles. From the first work card, choose **New output** to reveal them. Selecting a tile closes the picker and opens the active unfinished work of that type, the newest unfinished work of that type, or a new Setup. **New [type] plan** starts a separate conversation. Setup keeps its first request ID and touched fields on this device, scoped to the workspace, library and plan. Opening or editing Setup makes zero Studio mutations and zero model calls.

Set the focus, audience, length, language and the options offered by the type in the centered Setup form. Slides offer use and theme. Audio takes voices in speaker A, B order. **Plan** and any source, model or voice blocker sit in the sticky action bar below the fields. Setup captures the current Sources selection. If it changes, **Use current selection** adopts the new scope. **Search the web** starts off in a new Setup and in **Refine**. It uses the Tavily key in Settings → Search.

Choose **Plan** to create the server-side conversation and work, or resume the same request after a lost response. Studio clears the submitted device draft once the server acknowledges it. Planning can read selected sources, search the web when authorized, prepare a memo or study map, and propose an outline or coverage topics. Studio uses checked key results to find useful original passages and grounds citations and checks in those passages. A rebuild can still read saved answers pinned by an older output as notes. [Key results](LIBRARY.md#key-results)

The agent can ask for a choice before presenting a proposal. Select an answer, type your own, or choose **Use current settings**. Each round stays in **Conversation history**. In a wide middle pane, the question or proposal appears beside the settings. Review the summary, angle, options, coverage and size, then edit fields directly or send feedback with **Update plan** for another round.

### Edit and approve

| Output type | ID | Part limit |
| --- | --- | --- |
| Report | `report` | 8 sections |
| Briefing doc | `briefing` | 8 sections |
| Study guide | `study_guide` | 12 sections |
| FAQ | `faq` | 16 sections |
| Timeline | `timeline` | 16 sections |
| Slides | `slides` | 14 slides |
| Flashcards | `flashcards` | 40 cards |
| Quiz | `quiz` | 40 cards |
| Mind map | `mind_map` | 60 nodes |
| Audio overview | `audio_overview` | 120 lines |

Reports and slides use an ordered outline with stable row IDs. Edit headings, goals, writer notes and any offered layouts, or change the membership and order. The number of approved rows sets the exact number of slides or sections, including the **Title slide** for Slides and the **Summary section** for Report and Briefing doc. These rows stay first. Flashcards, Quiz, Mind map and Audio overview use an ordered list of coverage topics and a count control. **Automatic** derives an aim from the length setting, **Target** sets your own aim, and **Exact** requires the decoded output to have that many cards, nodes or dialogue lines. Changing length preserves a count you set. [Plans and outlines](STUDIO_FORMAT.md#plans-and-outlines)

Direct edits save through the work PATCH with zero model calls. A complete valid edit keeps the plan approvable while its reviewed material is eligible. A change to the source scope, selected source revisions or membership, or newly enabled Web search requires a new planning pass. A completed web pass can have zero hits. Turning Web off removes web-only material. The plan can stay ready when local material is eligible and persisted outline bindings still resolve. Resolve any pending question or validation issue before approving.

**Approve & Generate** is the explicit command that can publish a first version. It uses the revision you reviewed, including any valid edits just saved. Generation writes the approved outline or topics and quantity against the reviewed material. It checks format and factual support before saving. Slides also run fitting and design, with rendered review when configured. Scientific decks can read source figures and record limitations and review status. Direct outputs get one format repair if the first draft fails the format or exact count check. The open reader and the Studio row show failed work and retry actions.

### Preferences

**What to emphasize** (`emphasize`) and **What to avoid** (`avoid`) are ordered lists in the conversation brief. Each holds up to eight short instructions. A direct addition or edit is marked `edited`. A preference attributed to your words or a selected answer is marked `you`. A model suggestion is marked `guessed` until you accept or edit it. Reordering preserves each unchanged row's mark. A suggestion can add a row within the limit. Authored rows remain under your control.

The effective preferences reach planning, writing and targeted rewrites, editorial and slide review, and repairs. They give the writer instructions about what to cover and how to present it. The factual Checker and **Check again** judge support from the content and evidence. Literal **Edit** saves the text you type. After generation, **Preferences for future changes** edits the conversation brief, while **Approved plan** shows the sealed creation snapshot. [Plan fields and provenance](STUDIO_FORMAT.md#plans-and-outlines)

## Progress and Activity

Studio rows distinguish Setup, Planning, Needs answer, Ready to approve, Generating, Failed and saved versions. The current row stays marked while you view an earlier version. Open a row or Activity entry to return to that plan or saved version. Each conversation runs one turn at a time, and separate conversations can run together while you use Chat or another library. **Stop** is available in the reader and Activity. A failed or interrupted run keeps its work and completed checkpoints. Use **Retry planning**, **Review & retry** or **Review & resume** as offered. Restarting first generation requires another explicit approval.

### Checkpoints and recovery

The Studio coordinator keeps one `work.studio_progress` record with the reviewed material manifest, the pinned evidence pool references, source notes and search provenance. Its four keys determine which stages can resume.

| Key | Reuses |
| --- | --- |
| `material_key` | Scoped source and answer identities, revisions, pinned passages and assets, and the authorized web pass. On a match, Studio uses the pinned material and citation order. |
| `prepare_key` | Compatible purpose, reading digest, memo, study coverage and figure readings. A preference or model change can rerun preparation on the pinned material. |
| `story_key` | The checked proposal and typed storyline. Approval binds edited rows by stable ID and uses the reviewed topics and quantity directly. |
| `write_key` | Matching downstream work for the approved revision and writer inputs, including the verified parent when rebuilding. |

An unchanged approval checks current material eligibility and reuses pinned evidence, the completed web pass, reading, memo and storyline work. Checking a discussion's material identity can revisit its saved messages. Compatible checkpoints survive a retry or restart. Older checkpoints are read through the work decoder and unverifiable stages are recomputed. [Studio coordinator](../apps/backend/gunther/studio/pipeline.py) · [Work decoder](../apps/backend/gunther/artifact_workspace.py)

One desktop sidecar owns the run registry. It assigns a `server_boot_id` at startup and an `attempt_id` per run. On read or admission, the service checks the live registry for the matching scope and both IDs and immediately projects orphaned attempts as interrupted. Checkpoint, Stop and publication check the stored request, boot ID, attempt ID and running state under the SQLite write lock. If publication already won the race, the reader reopens the verified version. [Run registry](../apps/backend/gunther/answer_runs.py) · [Turn service](../apps/backend/gunther/artifact_turns.py)

## Conversations and versions

The agent reads a compact view of the brief, evidence and work. It chooses bounded actions such as Understand, Plan, Write, Edit, Check, Review, Answer and Save. Action gates check selection, part positions and readiness. Part IDs remain stable through reordering or heading edits. The reader can show changes and jump to the affected section, slide or card.

Saving seals content and provenance as an immutable version, with a lineage head, outline and citation snapshots. The version owner stages the candidate, conversation and workspace together and publishes them on success. A failed or stopped publication keeps the prior stored work and head. A replay of an already published turn returns that version. Older saved versions still open through their stored format and seal. Interrupted old turns reopen with the conversation state they stored.

The reader and backup verification use the same seal rules. A broken seal or malformed saved outline reports an integrity conflict (HTTP 409). The approved plan opens its saved version in the middle pane. Reports, cards, maps and audio show the type and version above the title. Open **Refine** in the header, or use the form below a deck. A part action focuses the form with that part or selected quote targeted. The header form keeps the draft and Web choice when Escape closes it, and a pending question opens it automatically. Revisions start from the current verified head of the same lineage.

In conversations with several outputs, **Change history** marks turns that saved a different output. **Refine** uses the active output's stored scope. When the library ticks differ, the scope bar offers an explicit update for future refinements. **Output actions** includes **Versions**, **Outline** where available, **Edit**, **Check again**, **Style** for decks, and the available exports. **Outline** uses the same editor as planning and **Rebuild with this outline** sends the edited IDs, layouts and writer notes. **Edit** shows Markdown and a live preview that stack in a narrow middle pane. Leaving an unsaved edit asks for confirmation. Decks also offer rendered-slide review and restyling. Audio offers retry and voice changes against the saved script.

The Inspector's Evidence tab follows the open output version. Its **Output** card shows the version, saved scope and section coverage, including completed checks, recorded issues and unchecked sections. Cited cards show the source kind and group passages in order of first use. Numbered chips open passages, and the arrows step through all citations in reading order. When a shorter saved quote occurs exactly once in the passage, Evidence shows it first. Choose **Show context** to read the full passage with the quote marked in place, or **Show full passage** to expand a long standalone excerpt. Reports and decks with more than twelve citations and at least two parts offer **All** and **This section** or **This slide**, following the reader's position. Opening an output updates Evidence without switching away from Studio.

A Studio run seals up to 20 uncited web hits and library passages with the version, with up to 12 web hits first. It also records the key results read during the run and links them to any passages cited in the finished output. Later model prompts draw only from Studio's working evidence, so the saved search list stays out of them.

Below Cited, open **Research notes** to see the saved Studio memo, gaps and study limitations. **Key results used** links to the output's cited passages. **Also searched** shows uncited hits, their queries and which were **Filtered**, with web results first. On web cards, **+ Add to library** captures the page or PDF, and **Add all cited** imports missing cited web sources in order. A blocked import offers **Retry** on its card. [Library guide](LIBRARY.md#library-chat-and-studio)

The service checks the version's seal before resolving **In library** for its web citations and search hits. It checks the current library by URL, DOI or arXiv ID, so Trash and Restore update the match while the sealed version stays intact. Choose **In library · Open** to see the source summary.

[Action gates](../apps/backend/gunther/artifact_actions.py) · [Workspace](../apps/backend/gunther/artifact_workspace.py) · [Version commit](../apps/backend/gunther/artifact_versions.py) · [Verified reads](../apps/backend/gunther/artifact_read.py)

## Study a card set

Choose **Show answer** or tap the flashcard question to reveal the answer. With focus on the reader, Space also reveals or hides it. Use Previous, Next or the arrow keys to change cards. Activate a citation number to open its passage in Evidence.

In a quiz, choose one answer to see the result, the correct choice and the explanation with citations. The score counts correct choices among the questions you have answered. Start again clears those choices. **All cards** shows every question and answer so you can review the full set. Versions opens earlier saved sets, and Refine this card points the shared Refine box at that card.

Download Markdown saves the version's stored text. Export PDF opens the print dialog with all the cards, including the choices, marked correct answer and explanation for each quiz card. The printout includes numbered citations and a source list. [Cards format](STUDIO_FORMAT.md#3-cards-design-v1)

## Explore a mind map

The root and its first-level branches start expanded. Click a branch or its chevron to expand or collapse it. Use Tab to reach nodes and Enter on a branch control to toggle it. Scroll in either direction to reach the rest of the tree. Below 1200 px, Sources collapses to give the map room.

Select a node and choose **Ask** in its toolbar. It opens Chat and asks about the node along its full path using the current Sources selection. A citation number opens its passage in Evidence. The Checker reads a cited node with its ancestor labels and uses that node's own citations. Refine rewrites the selected node and its descendants together. Rewriting an ancestor rechecks any cited descendants whose path changed.

Download Markdown saves the nested list. Export PDF prints the full indented outline with its citations and sources. [Mind map format](STUDIO_FORMAT.md#4-mind-maps-design-v1)

## Audio overview

Choose **Audio overview** in Studio and set the focus, audience, length, language or voices in Setup before planning. The voices come from Settings → Read aloud. Studio defaults to the configured voice and a different listed voice when available. [Script format](STUDIO_FORMAT.md#5-audio-overview-script-design-v1)

The player stays above the transcript as you scroll. The line being spoken is highlighted. Select a timestamp to play from that line or select a citation to open Evidence. When the active line scrolls out of view, **Go to playing line** brings it back. The script stays readable while Activity shows which line Studio is recording.

If Studio needs a voice provider, use **Open Read aloud settings** from the reader. If recording fails, the reader shows the reason and **Retry audio**. Use **Change voices** to record the same script with different speakers. The output actions offer Markdown, **Download WAV** when the recording is ready, and **Print script**.

Studio checks the dialogue in batches and saves the script before recording. Clients can send `setup.voices` in A, B order when planning an `audio_overview`. The artifact response's `audio` contains the script's `contentHash`, the recording state, and, when ready, the `audioAssetId`, voices, and timings. Timings count lines from one and give start and end in seconds. The WAV streams through `GET /api/assets/{id}`. Delete forever releases its asset links.

An unconfigured Read aloud provider gives `needs_tts`. A failed recording gives `error` with a reason. `POST /api/knowledge-bases/{libraryId}/artifacts/{versionId}/audio/stream` retries the recording for that exact script version. Pass `{}` to keep the current picks or `{ "voices": ["voice-a", "voice-b"] }` to choose new ones. The audio record lives alongside the sealed script and carries its hash so both operations keep the script version and seal.

## Trash

Choose **Discard** on a local Setup row to remove its device draft. **Move to Trash** on an idle unfinished plan trashes the conversation. On a saved output it trashes the lineage and its entire version history. Stop a running turn before moving its output or conversation. Trashed versions disappear from Studio, conversation history, Activity links and exports. Undo and Restore use the Trash entry's stored address to reopen the selected saved version or the conversation's active draft, including after an app restart. For earlier lineage batches without a version pointer, Restore opens the lineage head. Restored work is idle.

Moving an output conversation to Trash also moves the outputs it owns. The batch preserves outputs that were moved to Trash separately. Deleting the conversation for good also removes those separately trashed outputs. Restore the conversation before restoring one of its outputs separately. Files stay available while another record can still use them, including items that can be restored from Trash. [Trash service](../apps/backend/gunther/trash.py)

## Checks, decks and exports

The Checker judges claims against their cited evidence. Each verdict is keyed by claim hash, evidence hash and stage. Coverage and incomplete or unavailable states come from that one snapshot. Readers show coverage flags, verdicts and affected claims inline. Open **Check details** for an issue's checker notes when available. An incremental edit rechecks changed paragraphs where the format allows, keeps findings on untouched text, and checks the whole part where needed. Retry and sync merge the same way. Saved legacy checks are read into the current shape. Typed edits can be saved with unresolved checks.

Supported claims from new builds, revisions and rebuilds can contribute key results through their library citations. Check again makes supported claims from a typed edit eligible. A revision or rebuild with the same supported claims and passages as its parent skips this work, including a restyle that keeps the checked wording. The reader shows the open version's job status, Retry or kept count with Undo. The scope label still shows saved answers pinned by an older version. [Key results](LIBRARY.md#key-results)

In report and slide readers, hover over or focus a citation number to preview the source title, location and a short quote. Activate the number to open that passage in Evidence.

Charts are drawn only when their rows are checked and supported by the label, values and citations. Tables and rows use their positions and each header belongs to its own table. Unsupported or unchecked rows use the table fallback. [Studio verification](../apps/backend/gunther/studio/verification.py) · [Verdicts](../apps/backend/gunther/checks.py)

Saved Slides open with a compact title, the slide toolbar and a full thumbnail strip within view on typical desktop windows. The toolbar shows the current slide's check status when it has findings or a coverage flag. Choose **Refine slide** to target that slide in the **Refine** box. The approved plan, future preferences and idle rendered review sit below thumbnails and notes. **Approved plan** shows the approved title when it differs from the reader title.

Decks can carry a guarded HTML design over their Markdown. Real rendering and measured geometry support Look and manual Review. A design correction keeps the verified text and evidence. Report and deck readers support copy or download of Markdown, PDF and the applicable DOCX or PPTX export. The document print action prepares the verified version at A4 width and waits for its local figures, equations and fonts. Print preparation errors appear in the output. Printed references use the saved citation numbers and share one numbered Sources list with the full saved quotations. [Studio format and exports](STUDIO_FORMAT.md) · [Slide eval](../apps/backend/evals/slides/README.md)

## API boundaries

Routes below are relative to `/api/knowledge-bases/{libraryId}` and scoped by `X-Gunther-Workspace-Id`. Post to `/artifact-conversations/{id}/messages/stream` with a `clientRequestId` and `planId`. The initial `kind: "plan"` carries `setup` and may carry an `initialOutline`. Later `plan` turns send the current `planRevision` and optional feedback in `text`. A planning `answer` also sends the exact `questionMessageId` and a structured choice, text or `use_settings` answer. A committed `plan` event carries the work key, plan and `editVersion`.

Use `PATCH /artifact-conversations/{id}/works/{workKey}` with `baseEditVersion` and only the fields you want to change. The response contains the updated conversation and plan revision. An equal patch leaves the version unchanged. Stale edits return HTTP 409 `edit_conflict`, and invalid fields return 422. To generate, send `kind: "approve"` with the plan ID, request ID and exact `planRevision`. A stale approval conflicts with `plan_changed` and requires a fresh review. The publisher verifies first-write authority and material pins before creating version 1. [Conversation API](../apps/backend/gunther/artifact_conversations_api.py) · [Version commit](../apps/backend/gunther/artifact_versions.py)

Studio preparation and proposal can run during planning. `StudioAgents.generate()` requires explicit `WriteAuthority`. The service supplies `FirstWrite` for the approved revision or `ParentWrite` for a verified current version. The compatibility `StudioAgents.make()` checks authority before preparation and delegates to the same writer. [Studio coordinator](../apps/backend/gunther/studio/pipeline.py)

A saved refinement can specify `targetLineageId`. A conversation PATCH with that lineage and `scope` stores scope for future refinements. Restyle, visual review, typed edits and Check use the saved-version paths. `POST /outputs/build/stream` verifies and replays an identical saved legacy request as an SSE `done` with the artifact. A fresh first-build request, including one with an outline, returns SSE `error` with status 409 and code `plan_required`. A request with a verified current `supersedesArtifactId` uses the parent-bound rebuild path. Old conversation `kind: "build"` turns follow the same replay or verified-parent rule. [Output commands](../apps/backend/gunther/output_commands_api.py)

Use `GET /artifact-conversations/{id}/stream` to follow a run and `POST /artifact-conversations/{id}/stop` to stop it. Follow returns 204 when idle. Runs and Activity share the key `output-conv:<conversation_id>`. A restyle or visual review of a deck without a conversation uses `output-lineage:<lineage_id>`. Output Activity targets include `conversationId`, `typeId`, `planId`, `lineageId` and `artifactId` as they become available. [Output streams](../apps/backend/gunther/artifact_api_common.py)

`GET /api/output-types` lists each type's ID, label, shape, description, icon, options, part limit and legacy style aliases. `capabilities` supplies the unit label, outline and design availability, and export formats. `firstPartRole` supplies the first-row label for both outline editors, including `"Summary section"` for Report and Briefing doc. The desktop selects the reader by shape and uses the catalog to show its controls. New versions seal the type ID in `style`. The API projects a display label as `outputType` on summaries and details. Earlier styles and workspace structures are read with their corresponding types. A saved Field guide retains its label and opens with the report reader. [Type catalog](../apps/backend/gunther/output_types.py)

Under the library, post to `/artifacts/{versionId}/trash` for a lineage or `/artifact-conversations/{id}/trash` for a conversation. Both return an `output` entry with the Trash batch ID. The entry's durable `open` target carries the library, conversation, type and plan or lineage and version identifiers where available. `GET /api/trash` and `POST /api/trash/output/{batchId}/restore` return that target from stored data. An active run returns HTTP 409. Reading a trashed version returns HTTP 404. [Trash API](../apps/backend/gunther/trash_api.py) · [Target projection](../apps/backend/gunther/trash.py)
