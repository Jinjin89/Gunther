# Outputs

The Outputs workspace makes saved reports and slide decks. Start a conversation in a library, choose the whole library or selected sources and saved knowledge, and describe the work. A selected saved answer or discussion carries its eligible pinned evidence. A selected note can inform the work when its linked source is in Trash. Its trashed passage stays out of the citations.

## Build in Studio

Studio prepares a verified evidence pool, reads the material, writes a memo or study map, plans the storyline and drafts the report or slides. Slide building adds format repair, factual verification, fitting, design, and optional rendered review. For scientific decks it plans coverage of the study and figures, reads supplied pixels when it can, and records limitations and review status. The study workflow ties the explanation of methods, results and figures to their passages.

Each output type has a recipe for what to write and how to organize it. Ask for a type in the conversation or choose one for the active report under Details → Brief.

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

Planning uses structured requested count and duration fields. An approved outline supplies stable part IDs, headings, goals and order. Rebuild from Approach or Outline sends a conversation work action with the approved outline. Studio checks format and heading and count requirements before accepting a draft. Progress, skipped steps and incomplete checks remain visible. Missing models and failed required work are reported in the workspace.

The Report and Slides shortcuts send build turns with the selected sources and the draft as focus. Studio starts directly from those options. Choose at least one source, saved answer or discussion when building from a selection. Failed or stopped builds keep completed stages with the draft. The conversation brief, scope and evidence pool are published with the version. Retry uses the saved build options and reuses the stages whose inputs still match. Stop is checked on entry to each stage. [Studio coordinator](../apps/backend/gunther/studio/pipeline.py) · [Study](../apps/backend/gunther/studio/study.py) · [Drafting](../apps/backend/gunther/studio/drafting.py)

## Progress and Activity

Conversations can build at the same time, including two in the same library. Each conversation runs one turn at a time. The Activity panel shows the current stage and counts when available, such as “Checking every claim against its source · 4 of 11 sections checked.” Closing Outputs leaves its runs working. Reopen a conversation to follow it, or use Stop in the conversation or its Activity row. A completed row opens the version that run saved.

## Conversations and versions

The agent reads a compact view of the brief, evidence and work. It chooses bounded actions such as Understand, Plan, Write, Edit, Check, Review, Answer and Save. Action gates check selection, part positions and readiness. Part IDs remain stable through reordering or heading edits. The reader can show changes and jump to the affected section or slide.

Saving seals content and provenance as an immutable version, with a lineage head, outline and citation snapshots. The version owner stages the candidate, conversation and workspace together and publishes them on success. A failed or stopped publication keeps the prior stored work and head. A replay of an already published turn returns that version. Older saved versions still open through their stored format and seal. Interrupted old turns reopen with the conversation state they stored.

The reader and backup verification use the same seal rules. A broken seal or malformed saved outline reports an integrity conflict (HTTP 409). Version edits start from the current head. Refine an output by opening it in the preview and sending a message. The message identifies its lineage so the conversation can work on the right output. The desktop also supports history, typed Edit, Check again, deck style and visual review. An optional fitting failure can keep a valid writer reply. An invalid reply leaves the parent current.

[Action gates](../apps/backend/gunther/artifact_actions.py) · [Workspace](../apps/backend/gunther/artifact_workspace.py) · [Version commit](../apps/backend/gunther/artifact_versions.py) · [Verified reads](../apps/backend/gunther/artifact_read.py)

## Checks, decks and exports

The Checker judges claims against their cited evidence. Each verdict is keyed by claim hash, evidence hash and stage. Coverage and incomplete or unavailable states come from that one snapshot. An incremental edit rechecks changed paragraphs where the format allows, keeps findings on untouched text, and checks the whole part where needed. Retry and sync merge the same way. Saved legacy checks are read into the current shape. Typed edits can be saved with unresolved checks.

Charts are drawn only when their rows are checked and supported by the label, values and citations. Tables and rows use their positions and each header belongs to its own table. Unsupported or unchecked rows use the table fallback. [Studio verification](../apps/backend/gunther/studio/verification.py) · [Verdicts](../apps/backend/gunther/checks.py)

Decks can carry a guarded HTML design over their Markdown. Real rendering and measured geometry support Look and manual Review. A design correction keeps the verified text and evidence. Report and deck readers support copy or download of Markdown, PDF and the applicable DOCX or PPTX export. [Studio format and exports](STUDIO_FORMAT.md) · [Slide eval](../apps/backend/evals/slides/README.md)

## API boundaries

The conversation turn stream accepts `kind="build"` with the output type and options in `build`. A refinement can specify `targetLineageId`. Conversation summaries include the active output's label, shape and lineage, plus the last turn's build options and any error. The legacy build route translates `kind`, `style` and `brief` into a build turn and replays previously saved requests. Restyle, visual review, typed edits and Check use the shared version writer. [Conversation API](../apps/backend/gunther/artifact_conversations_api.py) · [Output commands](../apps/backend/gunther/output_commands_api.py)

Use `GET /artifact-conversations/{id}/stream` under the library to follow its run and `POST /artifact-conversations/{id}/stop` to stop it. Runs and Activity share the key `output-conv:<conversation_id>`. A restyle or visual review of a deck without a conversation uses `output-lineage:<lineage_id>` and can be stopped from Activity. Output Activity targets include `conversationId`, `typeId`, `lineageId` and `artifactId` as they become available. [Output streams](../apps/backend/gunther/artifact_api_common.py)

`GET /api/output-types` lists each type's ID, label, shape, description, icon and options. New versions seal the type ID in `style`. The API projects a display label as `outputType` on summaries and details. Earlier styles and workspace structures are read with their corresponding types. A saved Field guide retains its label and opens with the report reader. [Type catalog](../apps/backend/gunther/output_types.py)
