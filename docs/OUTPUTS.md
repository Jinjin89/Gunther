# Outputs

The Outputs workspace makes saved reports and slide decks. Start a conversation in a library, choose the whole library or selected sources and saved knowledge, and describe the work. A selected saved answer or discussion carries its eligible pinned evidence. A selected note can inform the work when its linked source is in Trash. Its trashed passage stays out of the citations.

## Build in Studio

Studio prepares a verified evidence pool, reads the material, writes a memo or study map, plans the storyline and drafts the report or slides. Slide building adds format repair, factual verification, fitting, design, and optional rendered review. For scientific decks it plans coverage of the study and figures, reads supplied pixels when it can, and records limitations and review status. The study workflow ties the explanation of methods, results and figures to their passages.

Planning uses structured requested count and duration fields. An approved outline supplies stable part IDs, headings, goals and order. Rebuild from Approach or Outline sends a conversation work action with the approved outline. Studio checks format and heading and count requirements before accepting a draft. Progress, skipped steps and incomplete checks remain visible. Missing models and failed required work are reported in the workspace.

Direct and conversation builds share the Studio pipeline, prepared material, source and figure readers, and assembly. Direct builds start a fresh pipeline run. Conversation builds save stage checkpoints with the work and reuse completed stages when their inputs still match. Stop is checked on entry to each stage. [Studio coordinator](../apps/backend/gunther/studio/pipeline.py) · [Study](../apps/backend/gunther/studio/study.py) · [Drafting](../apps/backend/gunther/studio/drafting.py)

## Conversations and versions

The agent reads a compact view of the brief, evidence and work. It chooses bounded actions such as Understand, Plan, Write, Edit, Check, Review, Answer and Save. Action gates check selection, part positions and readiness. Part IDs remain stable through reordering or heading edits. The reader can show changes and jump to the affected section or slide.

Saving seals content and provenance as an immutable version, with a lineage head, outline and citation snapshots. The version owner stages the candidate, conversation and workspace together and publishes them on success. A failed or stopped publication keeps the prior stored work and head. A replay of an already published turn returns that version. Older saved versions still open through their stored format and seal. Interrupted old turns reopen with the conversation state they stored.

The reader and backup verification use the same seal rules. A broken seal or malformed saved outline reports an integrity conflict (HTTP 409). Version edits start from the current head. The desktop has history, typed Edit, conversation revisions, Check again, deck style and visual review. Direct Revise uses the same Studio part-writing and format validation. An optional fitting failure can keep a valid writer reply. An invalid reply leaves the parent current.

[Action gates](../apps/backend/gunther/artifact_actions.py) · [Workspace](../apps/backend/gunther/artifact_workspace.py) · [Version commit](../apps/backend/gunther/artifact_versions.py) · [Verified reads](../apps/backend/gunther/artifact_read.py)

## Checks, decks and exports

The Checker judges claims against their cited evidence. Each verdict is keyed by claim hash, evidence hash and stage. Coverage and incomplete or unavailable states come from that one snapshot. An incremental edit rechecks changed paragraphs where the format allows, keeps findings on untouched text, and checks the whole part where needed. Retry and sync merge the same way. Saved legacy checks are read into the current shape. Typed edits can be saved with unresolved checks.

Charts are drawn only when their rows are checked and supported by the label, values and citations. Tables and rows use their positions and each header belongs to its own table. Unsupported or unchecked rows use the table fallback. [Studio verification](../apps/backend/gunther/studio/verification.py) · [Verdicts](../apps/backend/gunther/checks.py)

Decks can carry a guarded HTML design over their Markdown. Real rendering and measured geometry support Look and manual Review. A design correction keeps the verified text and evidence. Report and deck readers support copy or download of Markdown, PDF and the applicable DOCX or PPTX export. [Studio format and exports](STUDIO_FORMAT.md) · [Slide eval](../apps/backend/evals/slides/README.md)

## API boundaries

The conversation turn stream is owned by [artifact_conversations_api.py](../apps/backend/gunther/artifact_conversations_api.py). [output_commands_api.py](../apps/backend/gunther/output_commands_api.py) owns direct build, revise, restyle, visual review, edit and check. Direct operations use the shared version writer. [artifact_api_common.py](../apps/backend/gunther/artifact_api_common.py) owns workspace headers and error mapping. Streaming supports progress, Stop and follow, with completed Activity following the saved result.
