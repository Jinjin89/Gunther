# Scientific presentations that explain the study

Status: agreed direction for the next implementation task. Read [AGENTS.md](../AGENTS.md).
The user should be able to learn a study from the slides: its question, contribution,
method, main experiments, supporting figures and limits. A collection of isolated facts,
prominent numbers and generic recommendations does not meet that goal.

## Interaction

Keep the existing simple request flow. Infer the purpose from the user's words and the
material already in scope. A request to explain or learn a paper should lead to scientific
explanation automatically. An explicit request for a different purpose takes precedence.

Do not add a presentation-mode selector, manual figure-selection step, setup questionnaire,
or required approval of the memo and outline. Infer reasonable defaults for length, depth
and audience. Produce a complete first draft and accept natural refinements such as
“explain the method more” or “focus on the experiments.” Respect the current material scope;
automatic adaptation does not authorize reading unrelated libraries.

For a learning deck, essential explanations belong on the slides. Presenter notes can add
detail, but should not carry the only explanation of an experiment. Adapt delivery to an
explicit speaking or reading request without making the user configure an internal workflow.

## Confirmed gaps

The studio prompts currently emphasize consulting-style insights, prominent numbers, action
titles, persuasive storylines and a recommendation at the end. Those choices can displace
scientific explanation even when the requested audience is scientific.

Original figure assets can exist without reaching the writer. `_studio_reading` constructs
textual evidence without attaching the available figure metadata. The studio material
serializer outputs passage text without figure references. `_figures` then tells the writer
that no figures are available. The renderer already supports source-image references;
the handoff between reading, planning and writing needs attention.

Extraction limitations also disappear from the material shown to the writer. The source
reader can report uncertain glyphs or unreliable formula text while the slide writer receives
only text. Citation presence alone cannot establish that the resulting scientific claim is
correct.

The separate source-selection problem is already addressed in the current code: the
orchestrator receives the chosen material overview before its first decision, and sending
waits for source-selection saves. Preserve those fixes and their regression tests.

## Understand the study before planning slides

Build an internal representation of the scientific argument. It should connect:

- The research question, prior gap and main contribution.
- The method's inputs, core mechanism, outputs and assumptions.
- Each central claim to its experiment or analysis, comparison or control, measurement,
  result, supporting text and relevant figure panels.
- The interpretation warranted by that evidence, competing explanations and limitations.

Distinguish the authors' findings from the agent's interpretation. Keep quantities attached
to their units, baselines, conditions and uncertainty. Preserve the original canonical source
text and provenance; slide explanations are derived content.

Use this representation to select important content. A parameter, implementation detail or
dataset statistic deserves a prominent slide only when it explains a central mechanism,
result or limitation. Specificity by itself is not scientific importance.

Let the study's actual argument determine the sequence. A methods paper, experimental study,
theoretical paper and review need different explanations. Do not impose a fixed paper
template, manufacture source section names, or require every slide to have an action title,
hero number or business recommendation.

## Use original figures as evidence

Carry available figure assets through the full workflow, with captions, panel labels,
document revision, page and region provenance, and links to the relevant claims and passages.
Retain extraction and crop-quality information alongside them. Keep asset access within the
chosen source scope.

Read important images together with their captions and the related methods/results text.
The component interpreting a figure must actually receive its pixels through a capable
model interface. An asset identifier or caption alone is not evidence that its contents were
visually inspected.

Choose figures automatically for their explanatory value. When a central result depends on
a figure, the deck should show and interpret its relevant panels. A result slide should make
clear what was tested, what the controls or comparisons are, how to read the axes and legend,
which observation matters, and what conclusion it supports.

Use legible panel crops when helpful, preserving axes, units, legends and essential context.
Annotations should direct attention without changing the evidence. Avoid shrinking a dense
full figure into an unreadable thumbnail. Distinguish original evidence from any explanatory
schematic derived by the agent; decorative graphics cannot substitute for experimental data.

Review a figure-dependent deck that uses no relevant source images as a content failure.
Do not impose an image quota on material that has no useful figures. If an image cannot be
read reliably, preserve the limitation instead of inventing its interpretation.

## Plan and review for understanding

Each planned slide should have a scientific purpose, a contribution to the overall argument,
supporting evidence, any selected figures or panels, and an explanation of the intended
takeaway. Plan the method and main results in enough depth to teach them before choosing
visual styling.

The scientific review must check whether a reader can:

1. Explain the research question and what the work contributes.
2. Describe how the central method or experiment works beyond a list of terminology.
3. Interpret the important figures and their comparisons or controls.
4. Explain which evidence supports each main conclusion.
5. State the assumptions, limitations and important unresolved questions.

Reject drafts that omit central experiments, promote peripheral details, confuse association
with causation, compare incompatible quantities, or make stronger claims than the evidence
supports. Necessary limitations belong with the claims they qualify. A titles-only review,
citation-count check or layout score is insufficient.

Verify uncertain scientific values against reliable source evidence, including the rendered
page when needed. Do not guess a replacement for a damaged number or symbol. Complete those
checks before delivery; do not leave instructions to proofread unresolved values in the
presenter's notes.

Run visual design after the scientific content and figure choices pass review. Preserve
source-image references and scientific meaning through formatting, design and export.

## Implementation entry points

- `apps/backend/gunther/studio/prompts.py`: reading, memo, storyline, writing and criticism.
- `apps/backend/gunther/studio/pipeline.py`: intermediate representations, material
  serialization, figure availability and scientific verification.
- `apps/backend/gunther/service.py`: selected-source reading, figure metadata and source
  quality/provenance handoff.
- `apps/backend/gunther/studio/design.py` and the desktop slide renderer: legible source
  figures, explanations, annotations and preservation through rendering/export.
- `apps/backend/evals/quality/`: task-appropriate scientific completeness and visual
  comprehension checks; general business-presentation conventions are not universal rules.

Reuse the existing agent, workspace, evidence and rendering capabilities where they fit.
The purpose is to improve task interpretation, scientific content selection and evidence
delivery. A model change or visual restyling alone will not repair the missing inputs or
the wrong evaluation objective.

## Verification and scope

Use independent synthetic or development fixtures to test scientific reasoning, figure
transport, multimodal inputs, scope boundaries, quality flags and rendering. Cover material
with multiple experiments, methods/benchmarks, useful multi-panel figures, and material for
which no source image is appropriate. Inspect rendered outputs and evaluate whole-deck
comprehension as well as individual claims.

Keep the user's evaluation papers held out. Do not search the live library or repository
history for their identities, originals, saved parses or old screenshots. Do not encode
document-, publisher- or example-specific production rules. Retain source-selection,
editing, citation and export behavior, and preserve user data.

The [remaining PDF extraction issues](../artifacts/handoffs/pdf-reader-issues/README.md)
describe upstream limitations. Propagate those limitations into the presentation workflow;
do not silently treat uncertain extraction as verified evidence.

Deliver an implementation report covering the changed behavior, scientific and visual
verification, unresolved limitations and any pre-existing test failures. This handoff makes
the product direction concrete; it does not require further user selection of a mode or skill.

## Implementation status (2026-10-27)

Built for decks; tested with fake models only. No real-model run yet, so the quality of the
model steps is unverified.

- **Purpose.** A small model step reads the brief and a short overview of the material and
  returns `explain_study` or `general` (`studio/science.py: Purpose`). An explicit purpose in
  the request wins. The purpose is kept with the deck, so edits stay scientific. Reports
  still take the general path.
- **Study map** (`StudyMap`). Replaces the consulting memo for `explain_study`: question,
  gap, contribution, method, claims with experiment, control, measurement, result, refs,
  linked figures and limits. A link to a passage or figure the material does not hold is
  sent back once; still wrong, the build fails.
- **Figures and warnings reach the writer.** `_studio_reading` attaches each passage's figures
  (asset, caption, page, region, crop check) and extraction warnings: `uncertain_glyph`
  (U+FFFD), `unverified_glyphs` (with the numbers that cannot be trusted), `withheld_formula`.
  `_passage` prints both, so `_figures` sees the real images.
- **Figure reading.** Up to 8 figures linked to central claims, central first, are sent as
  pixels with caption and passages to the vision model (photos job, else the writer if it
  can see). If none is set up, or a read fails, the limitation is recorded; nothing is
  invented from captions.
- **Storyline, writing, review.** Scientific prompts (`STUDY_STORY`, `STUDY_WRITE`,
  `SCIENTIFIC_REVIEW`, `STUDY_REVISE`). The figure layout takes up to three explanation
  bullets beside the image, in the app, the renderer and the PPTX export.
- **Gate before verify and design.** The reviewer answers the five reader questions. Code
  checks (`code_problems`) that the legible figures of central claims are shown on a slide,
  that no number cited to a damaged passage is stated unless clean text or a figure reading
  confirms it, and that no image comes from outside the scope. One whole-deck revision
  follows. Code problems still left fail the build. Reviewer problems still left, or a
  review that could not run, are recorded with the deck as limitations.
- **Preservation.** Automatic fixes (format, claims, fitting) may not drop a source image.
  Design may not shorten an explanation line. The look step redraws designed slides with
  their words kept, and does not cut plain slides or move their text into notes.
- **Edits.** A one-slide edit uses the study map and readings. The edited slide gets the same
  value and scope checks, is asked again once, then fails.
- **Evals.** The judge and lint use scientific criteria for `explain_study` (code only; not run).

- **Review outcome is shown.** The approach records `review_status`: `ready`,
  `open_problems` (delivered; the problems are listed) or `unavailable` (not reviewed).
  The app shows it with the limitations, under "What this deck could not check".
- **Blank model answers fail.** A study map with no claims, a review with empty answers, or a
  legible figure reading with no observation is asked for again, then treated as failed.
- **Scope and resume.** The build keeps `allowed_assets` (its own source images), and edits
  are checked against them. A saved study map is reused only for the same passages, not just
  the same count of them.
- **Edits are checked when finished.** The finished deck's code checks are attached to the
  version as notes. Fitting a slide may not bring in a damaged value or a foreign image.
- **Figure text fits its column.** With explanations, the takeaway is ≤120 units, each line
  ≤90 and all of them ≤280, for reading decks too. The same budget applies in
  `studio/format.py` and `deck/units.ts`; PPTX export shrinks type before it spills.

Reviewed on 2026-10-27 by GLM 5.3 (code), GLM 5.3 Flash (rendered slides) and gpt-6.1-sol
(checked both reviews against the code). Their confirmed findings are fixed above.

Open: panel crops and a layout that suits each figure's shape (a dense multi-panel figure gets
about half the slide), checking damaged values against the rendered page image, a real-model
run and the Mac trial.
