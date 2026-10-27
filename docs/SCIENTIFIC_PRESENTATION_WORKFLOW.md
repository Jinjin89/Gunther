# Journal club presentations that teach the paper

Status: agreed implementation handoff, updated 2026-10-27. Read
[AGENTS.md](../AGENTS.md). The existing scientific workflow is a baseline to extend;
the journal club requirements below still need implementation and quality verification.

The audience should be able to learn the actual paper from the deck: understand its
central method, walk through its major figures and experiments, and explain how the
evidence supports its conclusions. Sparse facts, isolated scores, generic commentary,
or images accompanied by shallow captions do not meet this requirement. Correct
citations, successful image delivery and attractive rendering cannot substitute for
substantive coverage and explanation.

## Interaction and scope

For selected research material and a request to present, explain or learn the paper,
infer the journal club purpose automatically. An explicit request for another purpose
takes precedence. Reuse the existing purpose mechanism where appropriate; this does
not require a new user-facing mode or applying a research-paper structure to other
document types.

Keep the simple request flow. Do not add a presentation-mode selector, manual figure
selection, setup questionnaire, or required approval of an internal map or outline.
Infer reasonable defaults for audience, depth and duration. Produce a complete first
draft and accept natural refinements such as “explain this method more.”

Respect the selected material scope. Background must be supported and cited; do not
silently read unrelated libraries to supply it. Report missing source information
rather than inventing an explanation. Preserve the original canonical text, files,
organization and provenance. Presentation explanations and schematics are derived
content and must not rewrite the reading version or manufacture source sections.

Essential explanations belong on the slides. Speaker notes may add detail and a
spoken walkthrough, but cannot carry the only explanation of a method, result or
control. Adapt to an explicit speaking or reading request without exposing internal
workflow configuration.

## Understand the paper and establish coverage

Before choosing slides, read the main scientific content together: results, original
figures and captions, the methods needed to interpret them, and relevant supplementary
evidence. A short abstract, digest or a few retrieved passages cannot establish that
the paper's central content has been covered. Gather the necessary material within
scope; explicitly record extraction or access gaps that prevent understanding it.

Build an internal coverage plan connecting:

- The scientific context, prior knowledge, unresolved question and contribution.
- The central method or experimental approach, how it works, why it was chosen,
  its assumptions and what its measurements can establish.
- Every main figure, its important panels, the experiments or analyses they contain,
  relevant methods, comparisons or controls, observations and supported interpretations.
- The dependencies between results: what one experiment establishes, what question
  remains, and why the next experiment follows.
- The authors' claims, the evidence supporting them, plausible alternatives and the
  agent's separately identified critical assessment.

Account for every main figure. Record where it is taught, which panels are grouped
or split across slides, and any omitted material with a scientific reason. Include
supplementary panels when they are necessary to understand or assess a central claim.
This is an internal completeness check, not a request for the user to select figures.

Prioritize the method and central results. A parameter, dataset statistic, isolated
score or routine caveat deserves space when it helps explain the science or assess a
claim. Specificity and the size of a number are not measures of scientific importance.
Keep quantities attached to units, baselines, conditions and uncertainty.

The coverage plan must reflect the source, not just repeat the writer's chosen
storyline: a central experiment omitted from the outline must still be discoverable
as an omission. Citation checking of selected sentences does not check coverage.

## Presentation sequence

Most of the presentation should teach the central method and walk through the
paper's figures and results. Use this flow as a starting point:

| Part | Content and purpose |
| --- | --- |
| Opening | Identify the paper, establish the essential background and unresolved question, and explain the overall study strategy. |
| Main body | Teach the central approach and work through the main figures, introducing experimental details where they are needed to interpret the evidence. |
| Ending | Integrate the findings, assess which conclusions are supported, explain the contribution and identify meaningful discussion questions. |

The first slide uses the paper's actual title, authors, journal or venue and publication
year where available, together with its central question and a brief explanation of
why that question matters. Do not invent missing bibliographic details, event labels
or presentation dates. Distinguish publication dates from file timestamps.

Background should prepare the audience to understand the experiments. Define necessary
concepts and show the relevant biological system or technical problem. Explain what
previous work established and the specific gap the authors address. Avoid spending
the opening on broad field summaries that do not help interpret this study.

Start with the paper's figure order and make the scientific connections explicit.
Reorder or group material when a method must be taught first or the argument is clearer
that way. Every main figure must be accounted for, but neither figure order nor a
one-figure-per-slide rule is a substitute for explanation.

For a methods paper, explain the central mechanism sufficiently before examining its
evaluation, comparisons, ablations and demonstrated applications. For experimental
work, explain the system, interventions, measurements and successive scientific
questions. Adapt to the actual argument of theoretical papers, reviews and other
research material; do not invent experiments or impose one paper template.

Length follows the content needed to teach the paper. Do not force it into a generic
eight-to-twelve-slide target. Honor an explicit duration by choosing and explaining
the central argument coherently, with further evidence in an appendix when useful;
do not silently omit central results or shrink them into unreadable slides.

## Teach the figures and their results

For each important figure or coherent group of panels, develop a teaching sequence:

**Question → method and controls → panel observations → interpretation → remaining
uncertainty → reason for the next experiment.**

Explain what the authors are trying to find out and how the experiment or analysis
addresses that question. Explain the method at the point of use: what was manipulated,
compared or measured, what each point, image or distribution represents, why the control
is needed, and what the readout can and cannot establish.

Then walk through the original evidence. Identify axes, units, groups and legend as
needed, direct attention to the important comparison, and explain the actual observation.
Explain why that observation supports a conclusion and which alternative explanations
remain. Keep the authors' interpretation distinct from the agent's assessment.

The results are taught through these walkthroughs. Do not replace them with disconnected
lists of findings or repeatedly restate the same numbers in separate summary slides.
Each step should add understanding of the experiment or its role in the argument.

A complex figure may need several slides: experimental setup, enlarged related panels,
controls and interpretation. A simple figure may need one. Do not require every figure
to use the same number of slides or every panel to receive equal time. Dense figures
may benefit from an overview for orientation followed by focused panel views.

After a result, make its connection to the next scientific question understandable.
At the end, reconstruct the integrated explanation or method and show which findings
support its parts. State what the work adds relative to the original gap.

Critical discussion must concern specific conclusions and evidence. Explain which
control, comparison or assumption affects a claim, why an alternative remains possible,
and what evidence could distinguish explanations. “More validation is needed” is not
enough. Label proposed experiments as proposals. A study's genuine scientific limitations
are content to explain; they do not themselves mean that the presentation has failed.

## Figures, layout and fidelity

Carry source figure assets, captions, panel labels, revision and region provenance,
claim links, crop quality and extraction warnings through reading, planning, writing,
editing and export. The component interpreting an image must receive its actual pixels
through a capable model interface. Captions and asset identifiers alone do not establish
that the figure was visually read.

Use the original evidence at a readable size. Keep axes, units, legends, relevant controls
and essential context visible when cropping panels. Link a crop to its original figure
and source revision, preserve panel identity, and retain the original asset. Annotations
may direct attention without changing the evidence. Clearly distinguish derived teaching
schematics from original experimental figures.

The existing whole-image layout with up to three explanation bullets may remain one
option, but must not constrain scientific planning. Support related panel groups, readable
panel views and explanatory sequences across multiple slides. Do not use decorative
graphics as substitutes for data or shrink a dense figure into a thumbnail to fit a template.

Preserve the image's intrinsic aspect ratio in the app, PDF and PowerPoint. Verify the
exported placement as well as the embedded bytes. DOM text-size and overflow checks do
not establish that labels inside an image are legible; inspect the rendered figure too.

Generation, image reading, capture and review must support the required deck length and
figure coverage, using bounded batches where necessary. Current image-count and capture
limits must not silently exclude central evidence. Do not add a figure quota to a paper
that has no useful source images. Unreadable or unavailable figures remain explicit
limitations; never invent their contents.

## Review for scientific learning

Use separate checks for coverage, learning, factual grounding and visual fidelity.

1. **Source-aware coverage.** Compare the plan and draft against the paper's central
   content and figure inventory. Identify omitted methods, experiments, important panels,
   controls and links in the scientific argument. Check that the explanation has enough
   depth to teach them, rather than merely naming them.
2. **Independent learner.** Give a separate reader only the rendered slides and their
   visible content, with an audience brief. Do not provide the source paper, evidence
   pool, study map, planning rationale or speaker notes. Ask it to reconstruct the study
   and explicitly identify what the slides do not explain.
3. **Compare understanding with the source.** Assess whether that reconstruction correctly
   describes the question and contribution, central method, major experiments and panels,
   comparisons or controls, observations, supported conclusions and unresolved issues.
   Require evidence from the slides for the reader's answers, so prior knowledge cannot
   substitute for missing teaching.
4. **Grounding and delivery.** Check factual claims, attribution, quantities, formulas,
   cover metadata, image provenance and exports. Reject association stated as causation,
   incompatible comparisons and conclusions stronger than the evidence. Inspect the final
   rendered figures and ensure that design or fitting has preserved both the evidence and
   its explanation.

The current scientific reviewer sees source material and the study map alongside the
draft. It can assess consistency, but it is not the independent learner: access to those
inputs risks filling gaps that the audience cannot fill. Keep those roles separate.

Missing central methods or results, shallow panel descriptions that do not explain the
experiment, or incorrect reconstruction are content failures. Revise them before expensive
visual polish. The final designed and exported deck must preserve the teaching and remain
readable. Do not mark a deck ready merely because images, citations and expected headings
are present. If necessary source information is missing or a blocking content problem
remains, report the incomplete outcome accurately rather than claiming successful review.
Ordinary scientific uncertainty should remain an accurately explained limitation, not
trigger an endless attempt to make the paper's evidence stronger than it is.

Verify uncertain scientific values against reliable source evidence, including the original
rendered page when needed. Do not guess replacements for damaged numbers or symbols, or
leave instructions to proofread unresolved values in presenter notes. Distinguish missing
extraction from a method, control or result that the authors did not report.

## Implementation priorities and entry points

Implement substantive coverage and teaching first, then panel presentation and independent
learning review. Preserve existing source-selection, citation, editing, resume and scope
protections. Reuse the existing agent, workspace, evidence and renderer capabilities.

- `apps/backend/gunther/service.py`: selected-source reading and sufficient coverage of
  results, methods, figures, captions and relevant supplementary evidence; preserve warnings
  and provenance in the handoff.
- `apps/backend/gunther/studio/science.py`: extend the study map and figure readings into a
  source-grounded coverage and teaching plan. Current asset-presence checks do not prove
  that important panels or their experiments were explained.
- `apps/backend/gunther/studio/prompts.py`, `pipeline.py` and `work.py`: journal club planning,
  method teaching, figure walkthroughs, scientific transitions, targeted revisions and
  separate source-aware and independent learner reviews. Preserve them through edits/resume.
- `apps/backend/gunther/studio/design.py`, `format.py`, the desktop deck renderer and
  `exportPptx.ts`: layouts that support readable panels and multi-slide explanations,
  source-image fidelity, metadata grounding and preservation through fitting and export.
- `apps/backend/gunther/slide_render.py`: capture and review of longer decks in bounded
  batches; final figure readability and PDF verification.
- `apps/backend/evals/quality/`: source coverage, depth and slide-only comprehension
  criteria, alongside factual and visual checks. Generic presentation scores are insufficient.

Measure model calls and latency by stage. Reuse readings and evidence for the same source
revision; revise affected sections and avoid repeating unchanged verification or design.
Performance improvements must preserve scientific depth, central evidence and final review.
A model change, another generic prompt or visual restyling alone is not the implementation.

## Current baseline and observed validation (2026-10-27)

The existing implementation already provides automatic scientific purpose inference,
a study map, figure metadata and extraction warnings, actual image reading, scientific
planning/review, review-status reporting and source-image preservation through edits.
Do not treat the earlier figure-transport and source-selection gaps as still unimplemented.

Local validation passed 390 targeted backend tests, 950 desktop tests, 50 UI smoke checks,
lint, type checking and the native app build. A real-model trial on one independent invented
study produced eleven slides with three source figures, successful PDF capture and no
measured overflow. The trial's review status remained `open_problems`.

That trial verifies functional behavior, not journal club quality. The user rejected the
presentation direction because it taught too little substantive scientific content and
spent attention on small details. Do not use the synthetic trial or passing test counts
as evidence that the paper-presentation goal is solved.

Confirmed additional issues from that trial:

- PowerPoint embedded the original image bytes but changed their aspect ratios through
  placement. Correct figure sizing and verify the exported geometry.
- The cover invented an event label and date absent from the brief and evidence. Prevent
  format-example metadata from becoming factual content and include it in grounding checks.
- Generation took about fourteen minutes and 69 gateway completion calls at the configured
  high effort, including repeated format repairs. Improve reuse and targeted revision.

Panel crops and layouts suited to figure shape, checking damaged values against the rendered
original page, and substantive real-paper journal club evaluation remain implementation or
verification work. The installed Mac build was exercised; protected library content and
portable files remained unchanged.

## Verification, protected material and delivery

Use independent fixtures for functional checks of multimodal inputs, panel provenance,
source scope, missing extraction, editing, resume and export. Add real-paper development
evaluations with complex methods and multiple experiments or panels. Compare the deck's
coverage against the source and inspect what the independent learner can actually explain.
Synthetic examples alone cannot establish journal club quality.

Exercise variable-length decks, dense panels, negative or conflicting results when present,
methods/benchmark explanations, source images that cannot be read reliably, and research
material with no useful figures. Verify the figure geometry and explanatory content in
PowerPoint as well as the app/PDF. Regression examples must verify general behavior and
must not become document-, publisher- or example-specific production rules.

Keep the user's evaluation papers held out. Do not search live libraries or repository
history for their identities, originals, saved parses or old screenshots. Reports and
handoffs must omit evaluation-paper names, quotations, filenames, source identifiers,
revision identifiers, page locations and screenshots exposing their identities. Use
independent development material and preserve the eventual blind evaluation.

Use isolated validation data. Preserve real libraries, especially Leadership, and do not
create data backups or reparse real material merely to run these checks. The
[remaining PDF extraction issues](../artifacts/handoffs/pdf-reader-issues/README.md)
describe upstream limitations to propagate accurately into the presentation workflow.

Deliver an implementation report covering the final behavior, scientific coverage and
learning evaluation, rendered figures and exports, latency, unresolved limitations and
any pre-existing test failures. Make the generated validation deck directly viewable and
clearly identify its role as development material; test logs alone are not the result the
user needs to inspect. No further mode selection or internal-outline approval is required.
