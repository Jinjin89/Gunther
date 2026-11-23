# Outputs

Studio makes saved reports and slide decks in a library. Click a tile to start a build from the library's Sources ticks. Click its pencil to set options such as focus, audience, length, language and slide use when offered. Each tile build starts a new conversation. Studio disables its build tiles until a model is available and links to Models settings when setup is needed. Make a report from this under a research answer builds from the discussion and its eligible pinned evidence.

## Build in Studio

Studio prepares a verified evidence pool, reads the material, writes a memo or study map, plans the storyline and drafts the report or slides. Slide building adds format repair, factual verification, fitting, design, and optional rendered review. For scientific decks it plans coverage of the study and figures, reads supplied pixels when it can, and records limitations and review status. The study workflow ties the explanation of methods, results and figures to their passages.

Each output type has a recipe for what to write and how to organize it. Choose the type on its Studio tile.

| Output type | ID | Submitted outline limit |
| --- | --- | --- |
| Report | `report` | 8 sections |
| Briefing doc | `briefing` | 8 sections |
| Study guide | `study_guide` | 12 sections |
| FAQ | `faq` | 16 sections |
| Timeline | `timeline` | 16 sections |
| Slides | `slides` | 14 slides |

Report and Briefing doc open with a summary box and use exhibits for comparisons that rely on numbers. The Study guide teaches concepts in order, the FAQ uses questions as headings, and the Timeline follows events by date. All six types use the same verification of claims and cited passages. [Studio format](STUDIO_FORMAT.md)

The build options `short`, `standard` and `long` set targets. Reports aim for 800–1,200, 1,500–3,000 or 3,000–5,000 words. Slides aim for 5–7, 8–12 or 12–14 slides. Studio uses the outline limits as planning guidance and can finish a longer model-generated outline. An explicit language takes priority, then Studio reuses the language chosen during analysis. If analysis supplies none, the prompts ask for the language of the request or the material. Slides default to a talk. Choose `read` for a deck intended for reading on its own.

Planning uses structured requested count and duration fields. An approved outline supplies stable part IDs, headings, goals and order. Rebuild from Outline in the output's actions sends the approved outline. Studio checks format, headings and counts before accepting a draft. The reader shows incomplete checks. Studio reports missing models and failed required work on the output card.

Failed or stopped builds keep completed stages with the draft. The conversation brief, scope and evidence pool are published with the version. Retry uses the failed build's saved options and reuses the stages whose inputs still match. Stop is checked on entry to each stage. [Studio coordinator](../apps/backend/gunther/studio/pipeline.py) · [Study](../apps/backend/gunther/studio/study.py) · [Drafting](../apps/backend/gunther/studio/drafting.py)

## Progress and Activity

Conversations can build at the same time, including two in the same library. Each conversation runs one turn at a time. Studio cards show recent outputs, their versions, progress and failures. Click a running card or its tile to follow that build. Runs continue while you use Chat or another library. Use Stop in the open output or its Activity row. A completed Activity row opens the version that run saved.

## Conversations and versions

The agent reads a compact view of the brief, evidence and work. It chooses bounded actions such as Understand, Plan, Write, Edit, Check, Review, Answer and Save. Action gates check selection, part positions and readiness. Part IDs remain stable through reordering or heading edits. The reader can show changes and jump to the affected section or slide.

Saving seals content and provenance as an immutable version, with a lineage head, outline and citation snapshots. The version owner stages the candidate, conversation and workspace together and publishes them on success. A failed or stopped publication keeps the prior stored work and head. A replay of an already published turn returns that version. Older saved versions still open through their stored format and seal. Interrupted old turns reopen with the conversation state they stored.

The reader and backup verification use the same seal rules. A broken seal or malformed saved outline reports an integrity conflict (HTTP 409). Version edits start from the current head. Open a card in the middle to read the output, see its folded change history and send a Refine message. Refine targets that output and uses its own stored scope. When the library ticks differ, the scope bar offers an explicit update for future refinements. The output actions include Versions, Outline where available, Edit, Check again, Style for decks, and Export. Leaving an unsaved Edit asks for confirmation.

[Action gates](../apps/backend/gunther/artifact_actions.py) · [Workspace](../apps/backend/gunther/artifact_workspace.py) · [Version commit](../apps/backend/gunther/artifact_versions.py) · [Verified reads](../apps/backend/gunther/artifact_read.py)

## Checks, decks and exports

The Checker judges claims against their cited evidence. Each verdict is keyed by claim hash, evidence hash and stage. Coverage and incomplete or unavailable states come from that one snapshot. An incremental edit rechecks changed paragraphs where the format allows, keeps findings on untouched text, and checks the whole part where needed. Retry and sync merge the same way. Saved legacy checks are read into the current shape. Typed edits can be saved with unresolved checks.

Charts are drawn only when their rows are checked and supported by the label, values and citations. Tables and rows use their positions and each header belongs to its own table. Unsupported or unchecked rows use the table fallback. [Studio verification](../apps/backend/gunther/studio/verification.py) · [Verdicts](../apps/backend/gunther/checks.py)

Decks can carry a guarded HTML design over their Markdown. Real rendering and measured geometry support Look and manual Review. A design correction keeps the verified text and evidence. Report and deck readers support copy or download of Markdown, PDF and the applicable DOCX or PPTX export. [Studio format and exports](STUDIO_FORMAT.md) · [Slide eval](../apps/backend/evals/slides/README.md)

## API boundaries

The conversation turn stream accepts `kind="build"` with the output type and options in `build`. A refinement can specify `targetLineageId`. A conversation PATCH with that lineage and `scope` stores the scope for future refinements of that output. Conversation summaries include the active output's label, shape and lineage, plus the last turn's build options and any error. The legacy build route translates `kind`, `style` and `brief` into a build turn and replays previously saved requests. Restyle, visual review, typed edits and Check use the shared version writer. [Conversation API](../apps/backend/gunther/artifact_conversations_api.py) · [Output commands](../apps/backend/gunther/output_commands_api.py)

Use `GET /artifact-conversations/{id}/stream` under the library to follow its run and `POST /artifact-conversations/{id}/stop` to stop it. Runs and Activity share the key `output-conv:<conversation_id>`. A restyle or visual review of a deck without a conversation uses `output-lineage:<lineage_id>` and can be stopped from Activity. Output Activity targets include `conversationId`, `typeId`, `lineageId` and `artifactId` as they become available. [Output streams](../apps/backend/gunther/artifact_api_common.py)

`GET /api/output-types` lists each type's ID, label, shape, description, icon, options and outline part limit. New versions seal the type ID in `style`. The API projects a display label as `outputType` on summaries and details. Earlier styles and workspace structures are read with their corresponding types. A saved Field guide retains its label and opens with the report reader. [Type catalog](../apps/backend/gunther/output_types.py)
