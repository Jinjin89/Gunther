# Outputs

Studio makes saved reports, slide decks, flashcards, quizzes and mind maps in a library. Click a tile to start a build from the library's Sources ticks. Click its pencil to set options such as focus, audience, length, language and slide use when offered. Each tile build starts a new conversation. Studio disables its build tiles until a model is available and links to Models settings when setup is needed.

## Build in Studio

Studio prepares a verified evidence pool, reads the material, writes a memo or study map, plans the storyline and drafts the report or slides. Slide building adds format repair, factual verification, fitting, design, and optional rendered review. For scientific decks it plans coverage of the study and figures, reads supplied pixels when it can, and records limitations and review status. The study workflow ties the explanation of methods, results and figures to their passages.

Flashcards, Quiz and Mind map use the direct pipeline. Studio reads the sources, writes a memo, drafts the whole output, checks its format, verifies the claims against their citations and assembles the version. An invalid draft gets one format repair. If it still fails, Studio reports the format error and leaves the saved version intact. [Cards format](STUDIO_FORMAT.md#3-cards-design-v1) · [Mind map format](STUDIO_FORMAT.md#4-mind-maps-design-v1)

Studio searches checked key results using the build brief and source selection. A usable result brings its passages into the evidence pool from the original reading. Studio sees the result sentence as orientation with pointers to those passages. Citations and checks use the original passages. Rebuilding an older output with saved answers in its scope keeps those answers as notes. [Key results](LIBRARY.md#key-results)

Each output type has a recipe for what to write and how to organize it. Choose the type on its Studio tile.

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

Report and Briefing doc open with a summary box and use exhibits for comparisons that rely on numbers. The Study guide teaches concepts in order, the FAQ uses questions as headings, and the Timeline follows events by date. The output types share verification of claims and cited passages. Each shape determines the text that reaches the Checker. [Studio format](STUDIO_FORMAT.md)

The build options `short`, `standard` and `long` set targets. Reports aim for 800–1,200, 1,500–3,000 or 3,000–5,000 words. Slides aim for 5–7, 8–12 or 12–14 slides. Studio uses report and slide outline limits as planning guidance and can finish a longer model-generated outline. Card sets aim for 10, 20 or 40 cards and mind maps for 15, 30 or 60 nodes when the brief gives no count. An explicit language takes priority, then Studio reuses the language chosen during analysis. If analysis supplies none, the prompts ask for the language of the request or the material. Slides default to a talk. Choose `read` for a deck intended for reading on its own.

Planning uses structured requested count and duration fields. An approved outline supplies stable part IDs, headings, goals and order. Rebuild from Outline in the output's actions sends the approved outline. The outline editor uses the type's submitted outline limit, including when a saved output uses a legacy style. Studio checks format, headings and counts before accepting a draft. The reader shows incomplete checks. Studio reports missing models and failed required work on the output card.

Failed or stopped builds keep completed stages with the draft. The conversation brief, scope and evidence pool are published with the version. Retry uses the failed build's saved options and reuses the stages whose inputs still match. Stop is checked on entry to each stage. [Studio coordinator](../apps/backend/gunther/studio/pipeline.py) · [Study](../apps/backend/gunther/studio/study.py) · [Drafting](../apps/backend/gunther/studio/drafting.py)

## Progress and Activity

Conversations can build at the same time, including two in the same library. Each conversation runs one turn at a time. Studio cards show recent outputs, their versions, progress and failures. Click a running card or its tile to follow that build. Runs continue while you use Chat or another library. Use Stop in the open output or its Activity row. A completed Activity row opens the version that run saved.

## Conversations and versions

The agent reads a compact view of the brief, evidence and work. It chooses bounded actions such as Understand, Plan, Write, Edit, Check, Review, Answer and Save. Action gates check selection, part positions and readiness. Part IDs remain stable through reordering or heading edits. The reader can show changes and jump to the affected section, slide or card.

Saving seals content and provenance as an immutable version, with a lineage head, outline and citation snapshots. The version owner stages the candidate, conversation and workspace together and publishes them on success. A failed or stopped publication keeps the prior stored work and head. A replay of an already published turn returns that version. Older saved versions still open through their stored format and seal. Interrupted old turns reopen with the conversation state they stored.

The reader and backup verification use the same seal rules. A broken seal or malformed saved outline reports an integrity conflict (HTTP 409). Version edits start from the current head. Open a card in the middle to read the output, see its folded change history and send a Refine message. In older conversations that built several outputs, the change history marks turns that saved a different output. Refine targets that output and uses its own stored scope. When the library ticks differ, the scope bar offers an explicit update for future refinements. The output actions include Versions, Outline where available, Edit, Check again, Style for decks, and Export. Leaving an unsaved Edit asks for confirmation.

[Action gates](../apps/backend/gunther/artifact_actions.py) · [Workspace](../apps/backend/gunther/artifact_workspace.py) · [Version commit](../apps/backend/gunther/artifact_versions.py) · [Verified reads](../apps/backend/gunther/artifact_read.py)

## Study a card set

Click a flashcard or press Space on its flip button to reveal the answer. Use Previous, Next or the arrow keys while focused in the reader to change cards. Activate a citation number to open its passage in Evidence.

In a quiz, choose one answer to see the result, the correct choice and the explanation with citations. The score counts correct choices among the questions you have answered. Start again clears those choices. List view shows every question and answer so you can review the full set. Versions opens earlier saved sets, and Refine this card points the shared Refine box at that card.

Download Markdown saves the version's stored text. Export PDF opens the print dialog with all the cards, including the choices, marked correct answer and explanation for each quiz card. The printout includes numbered citations and a source list. [Cards format](STUDIO_FORMAT.md#3-cards-design-v1)

## Explore a mind map

The root and its first-level branches start expanded. Click a branch or its chevron to expand or collapse it. Use Tab to reach nodes and Enter on a branch control to toggle it. Scroll in either direction to reach the rest of the tree. Below 1200 px, Sources collapses to give the map room.

Hover over or focus a node to reveal Ask. It opens Chat and asks about the node along its full path using the current Sources selection. A citation number opens its passage in Evidence. The Checker reads a cited node with its ancestor labels and uses that node's own citations. Refine rewrites the selected node and its descendants together. Rewriting an ancestor rechecks any cited descendants whose path changed.

Download Markdown saves the nested list. Export PDF prints the full indented outline with its citations and sources. [Mind map format](STUDIO_FORMAT.md#4-mind-maps-design-v1)

## Audio overview

The backend accepts `audio_overview` builds. The type offers focus, audience, length, language, and two voice picks. The catalog's `voices` gives the choices from Settings → Read aloud and defaults to its selected voice plus a different listed voice when available. The selected voices can be sent in `build.voices` in A, B order. [Script format](STUDIO_FORMAT.md#5-audio-overview-script-design-v1)

Studio checks the dialogue in batches and saves the script before recording. The artifact response's `audio` contains the script's `contentHash`, the recording state, and, when ready, the `audioAssetId`, voices, and timings. Timings count lines from one and give start and end in seconds. The WAV streams through `GET /api/assets/{id}`. Delete forever releases its asset links.

An unconfigured Read aloud provider gives `needs_tts`. A failed recording gives `error` with a reason. `POST /api/knowledge-bases/{libraryId}/artifacts/{versionId}/audio/stream` retries the recording for that exact script version. Pass `{}` to keep the current picks or `{ "voices": ["voice-a", "voice-b"] }` to choose new ones. The audio record lives alongside the sealed script and carries its hash so both operations keep the script version and seal.

## Trash

Choose **Move to Trash** in the open output's actions or the menu on its Studio card. The entire version history moves together. Stop a running turn before moving its output or conversation. Trashed versions disappear from Studio, conversation history, Activity links and exports. Use Undo in the toast or Restore on the Trash page to get the versions back with their seals intact.

Moving an output conversation to Trash also moves the outputs it owns. The batch preserves outputs that were moved to Trash separately. Deleting the conversation for good also removes those separately trashed outputs. Restore the conversation before restoring one of its outputs separately. Files stay available while another record can still use them, including items that can be restored from Trash. [Trash service](../apps/backend/gunther/trash.py)

## Checks, decks and exports

The Checker judges claims against their cited evidence. Each verdict is keyed by claim hash, evidence hash and stage. Coverage and incomplete or unavailable states come from that one snapshot. An incremental edit rechecks changed paragraphs where the format allows, keeps findings on untouched text, and checks the whole part where needed. Retry and sync merge the same way. Saved legacy checks are read into the current shape. Typed edits can be saved with unresolved checks.

Supported claims from new builds, revisions and rebuilds can contribute key results through their library citations. Check again makes supported claims from a typed edit eligible. A revision or rebuild with the same supported claims and passages as its parent skips this work, including a restyle that keeps the checked wording. The reader shows the open version's job status, Retry or kept count with Undo. The scope label still shows saved answers pinned by an older version. [Key results](LIBRARY.md#key-results)

In report and slide readers, hover over or focus a citation number to preview the source title, location and a short quote. Activate the number to open that passage in Evidence.

Charts are drawn only when their rows are checked and supported by the label, values and citations. Tables and rows use their positions and each header belongs to its own table. Unsupported or unchecked rows use the table fallback. [Studio verification](../apps/backend/gunther/studio/verification.py) · [Verdicts](../apps/backend/gunther/checks.py)

Decks can carry a guarded HTML design over their Markdown. Real rendering and measured geometry support Look and manual Review. A design correction keeps the verified text and evidence. Report and deck readers support copy or download of Markdown, PDF and the applicable DOCX or PPTX export. [Studio format and exports](STUDIO_FORMAT.md) · [Slide eval](../apps/backend/evals/slides/README.md)

## API boundaries

The conversation turn stream accepts `kind="build"` with the output type and options in `build`. A refinement can specify `targetLineageId`. A conversation PATCH with that lineage and `scope` stores the scope for future refinements of that output. Conversation summaries include the active output's label, shape and lineage, plus the last turn's build options and any error. The legacy build route translates `kind`, `style` and `brief` into a build turn and replays previously saved requests. Restyle, visual review, typed edits and Check use the shared version writer. [Conversation API](../apps/backend/gunther/artifact_conversations_api.py) · [Output commands](../apps/backend/gunther/output_commands_api.py)

Use `GET /artifact-conversations/{id}/stream` under the library to follow its run and `POST /artifact-conversations/{id}/stop` to stop it. Runs and Activity share the key `output-conv:<conversation_id>`. A restyle or visual review of a deck without a conversation uses `output-lineage:<lineage_id>` and can be stopped from Activity. Output Activity targets include `conversationId`, `typeId`, `lineageId` and `artifactId` as they become available. [Output streams](../apps/backend/gunther/artifact_api_common.py)

`GET /api/output-types` lists each type's ID, label, shape, description, icon, options, part limit and legacy style aliases. `capabilities` supplies the unit label, outline and design availability, and export formats. The desktop selects the reader by shape and uses the catalog to show its controls. New versions seal the type ID in `style`. The API projects a display label as `outputType` on summaries and details. Earlier styles and workspace structures are read with their corresponding types. A saved Field guide retains its label and opens with the report reader. [Type catalog](../apps/backend/gunther/output_types.py)

Under the library, post to `/artifacts/{versionId}/trash` for a lineage or `/artifact-conversations/{id}/trash` for a conversation. Both return an `output` entry with the Trash batch ID for Restore and Delete forever. An active run returns HTTP 409. Reading a trashed version returns HTTP 404. [Trash API](../apps/backend/gunther/trash_api.py)
