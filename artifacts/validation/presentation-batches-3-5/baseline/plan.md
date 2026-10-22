# Gunther slide structure and visual design implementation plan

18 October 2026. Status: batches 0–2 implemented; batches 3–5 planned. See the [implementation record and review artifacts](PRESENTATION_BATCHES_0_2.md).

Build a stronger planning and rendering workflow on the existing reveal.js viewer, shared `SlideFace`, evidence cards, and versioned artifacts. The first release should produce a clearer sequence of supported ideas, a coherent visual style, and a review of the actual rendered slides. Each change must preserve the relationship between claims and their sources.

Basis: [structure research](PRESENTATION_STRUCTURE_RESEARCH.md) and [public slide skills research](PRESENTATION_SKILLS_RESEARCH.md). This plan extends the current working tree, including its existing presentation changes.

## Implementation decisions

- Keep reveal.js and the 1280 × 720 design stage. Viewer scaling remains separate from reducing type inside a slide.
- Use the same React slide components for preview, thumbnails, presentation, capture, and PDF preparation.
- Keep original sources, evidence cards, checked Markdown, and layout metadata distinct. Presentation planning can reorder ideas while retaining the sources' actual organization and provenance.
- Reuse the existing goal, card, transition, identity, lock, and freshness mechanisms. Add structured fields only where validation or persistence requires them.
- Use the configured Outputs model and its capability information. The gateway already accepts image input; rendered-slide review needs a capture integration and an explicit image-capable review path.
- Deliver in the six batches below. Each batch ends with a concrete artifact and acceptance evidence.

## Source and citation preservation

These conditions apply to every batch:

| Operation | Required behavior |
| --- | --- |
| Reorder or merge slides | Preserve the source identity and passage behind each claim. Let assembly assign visible citation numbers in the new reading order. |
| Change layout or style | Preserve checked wording, numeric values, units, qualifications, citation bindings, and source assets. Only presentation metadata changes. |
| Rewrite or split content | Use the existing writing and fact-checking path. Preserve essential qualifications and record which cards support the resulting pages. |
| Change a chart or diagram | Keep the existing graphic eligibility checks, per-block permission, and content fingerprints. A design step cannot grant itself permission. |
| Edit content or change sources | Reuse the current freshness rules and invalidate affected design/render results. A previous visual review cannot certify a changed page. |
| Save, reopen, or export | Preserve source mappings and the selected design. Old artifacts load with their existing defaults. Incomplete extraction or unresolved support stays visible as an issue. |

Use the current citation identity machinery, including the conversation pool and saved-version mapping. Visible `[n]` markers are display numbers, not source identities. Extend the existing evaluation normalization when necessary instead of creating a competing citation system.

Retain all original sources and evidence cards. Optional content excluded from the displayed story remains recorded and recoverable; explicit requirements and protected citations stay covered. When a user requires all original content or citations on the slides, a conflicting size limit needs a constraint result rather than an omission.

## Batch 0 Establish the baseline and rendering feasibility

**Deliverable:** a small comparison pack and a working capture experiment using the actual slide renderer.

1. Select six evaluation packets covering English and Chinese, prose without numeric data, a source figure, numeric series with negative values or unequal intervals, conditional processes, and conflicting findings or necessary caveats. A packet can cover several conditions. Use varied source organizations.
2. Save the exact inputs, model/settings, outline, checked text, citation map, scene, and current renders. Include a dense page and a fixed-count request.
3. Separate two comparisons: unchanged checked content for visual evaluation, and free outline generation from the same material for structure evaluation.
4. Prototype a dedicated render host importing `SlideFace`. Confirm it can load local assets, wait for fonts and layout, and return images and measurements without the main Outputs screen being open.

Preferred capture implementation: a local browser worker loading a render-only frontend build. Prove its packaged desktop startup, fonts, assets, memory use, and distribution requirements before enabling automatic visual review. The development Playwright harness is an evaluation tool until that packaging check succeeds.

**Accept when:** the comparison inputs are reproducible; captures correspond to the recorded content and style; asset or font failures are reported; and the production capture choice has a measured feasibility result. The structure and style batches can proceed while packaging work is resolved.

**Integration:** existing graphics/composer fixtures, `SlideFace.tsx`, `PrintSlides.tsx`, desktop build scripts, and a proposed `evals/slides/` developer harness.

## Batch 1 Improve outline reasoning and preserve coverage

**Deliverable:** an outline that explains what each page establishes, why it appears there, and how the evidence supports the intended outcome.

1. Update the slide skill, `story.md`, and `writing.md` to plan the audience's question and supporting reasoning before choosing layouts. Treat the seven story patterns as adaptable guidance. Support unresolved conclusions and appropriate setup/definition titles.
2. Keep `goal` as the communicative job of the page. Add a separate short `message` where the title or goal cannot express what its evidence establishes. Add optional `question` and prerequisite item identities for cases that need them. Carry these fields through planning, workspace state, saved outlines, API contracts, and rebuild/edit paths.
3. Add a coverage record mapping explicit requirements and essential supporting/limiting cards to planned pages. Mark inferred priorities separately from user requirements. Coverage records guide planning; they do not replace claim verification.
4. Add one outline review before detailed design. It reports specific unsupported messages, repeated ideas, unexplained prerequisites, missing required content, and transitions whose reasoning is unsupported.
5. Use at most one targeted outline repair by default. Reuse existing insert/move/delete and invalidation behavior for structural changes. Respect user-edited choices and locks.
6. Replace positional page truncation in `structure()`. Replan by merging compatible ideas or removing optional detail with recorded reasons. If required content still cannot fit, retain the candidate and its constraint issue without presenting it as a completed plan.

Keep the current supported page limits for this release. Distinguish an explicit requested total from a default range; an unsupported request must produce a clear constraint result. Apply the same planner checks to ordinary builds and workspace replanning.

**Accept when:** required content and essential limitations survive compression; invalid prerequisite/card references are rejected; semantic findings identify the relevant pages and evidence; a failed repair preserves the previous valid work; and saved/reopened outlines retain the new information.

**Integration:** `skill_runner.py`, `skill_checks.py`, `artifact_steps.py`, `artifact_workspace.py`, `schemas.py`, the shared TypeScript contracts, and `OutlineEditor.tsx`.

## Batch 2 Build complete visual styles and real previews

**Deliverable:** three design directions demonstrated on the same checked material, then available through the existing style control.

Extend Briefing, Editorial, and Keynote into complete visual recipes: dedicated slide font roles with Chinese/English coverage, title/body hierarchy, spacing, grouping surfaces, chart treatment, and image placement. Preserve the existing Classic/legacy behavior through a versioned design specification.

For each direction, render a cover and a representative evidence page with real content. Include a figure page where the packet supplies a suitable image. Use these renders in the style picker instead of relying only on the current `Aa` swatch. Choosing a style remains optional; an existing user choice takes precedence.

Persist only the added controls needed to reproduce the design, such as font-set and type/spacing-scale identifiers. Keep existing theme/accent and talk/read fields authoritative for their current meanings. Resolve the final tokens once and share them across all rendering surfaces. Bundle or use available fonts that cover the required scripts, and report substitutions.

Use current scene primitives first. The comparison pack should determine whether an integrated title, a spanning region, or explicit image-fit control is necessary. Add only demonstrated capabilities, with a new scene version if its contract changes. Maintain v1/v2 decoding and the rule that each source content block appears exactly once.

**Accept when:** each direction has a usable cover and evidence page, required content and citations are preserved, final text is readable at the intended viewing size, and save/reopen, presentation, and print reproduce the selected recipe. Style changes do not initiate model calls by themselves.

**Integration:** `themes.ts`, `slides.css`, `SlideStylePicker.tsx`, `SlideFace.tsx`, `ComposedSlide.tsx`, and the design metadata contracts. Scene changes additionally touch `slide_scene.py`, `scene.ts`, and their shared fixtures.

## Batch 3 Return actual renders and measurements to generation

**Deliverable:** a versioned rendering result the backend can consume and the user can inspect.

Promote the Batch 0 capture host into the generation workflow after its packaging check. Render the assembled candidate with its final citation numbering, theme, design specification, and scene. Wait for font loading, image decoding, scene/graphic validation, and fit measurements to settle; use an explicit timeout with a recorded incomplete result.

Define a proposed `RenderSnapshot` containing:

| Information | Purpose |
| --- | --- |
| Candidate, slide, and block identities | Attach findings to the correct content. |
| Content, scene, design, asset, and renderer digests | Reject stale measurements and screenshots. |
| Full-slide images and a contact sheet | Support page inspection and deck-wide review. |
| Bounds, fit scale, effective text sizes, and overlap/overflow findings | Identify concrete layout and readability issues. |
| Resolved fonts, asset failures, and fallback reasons | Explain differences or incomplete rendering. |

Calibrate text-size thresholds by semantic role, language, and speaking/reading use using the Batch 2 samples. Measure sizes after internal fitting. Preserve useful existing overflow checks; a fit value below one is a diagnostic rather than an automatic design failure or success.

**Accept when:** a snapshot matches the assembled candidate, outdated results are rejected after edits or style changes, full-size captures are available, and capture failure preserves the deck with an accurate review status. Verify the saved candidate matches the inspected candidate. Inspect the exported PDF as well as its print DOM.

Cross-check the capture worker against the packaged Tauri WebView for font resolution, wrapping, SVG graphics, and layout. Shared components alone do not establish rendering parity across browser engines; record and resolve material differences before claiming the captured output matches the product.

**Integration:** shared slide renderer and graphic readiness reporting; a new capture host/worker and backend render-result adapter; artifact orchestration and build packaging.

## Batch 4 Add bounded visual and density repair

**Deliverable:** an automatic review that can identify a visual defect and improve the affected page without changing its evidence.

1. Send full-size slide images, the design brief, and measurements to the configured image-capable model. Use the contact sheet for deck context. Batch up to four full-slide images per review request, subject to the provider's actual limits.
2. Return findings with slide/block identities, severity, reason, and a proposed correction. Prioritize unreadable text, clipping, broken graphics, and misleading emphasis before cosmetic preferences.
3. First repair scene/style data only. Validate block coverage, content fingerprints, citation identity, and graphic permissions. Re-render affected pages and recheck their measurements.
4. If the defect requires less or different display wording, return it to writing and fact-checking. If it requires a different sequence or split, return it to the outline planner. Recompose and render the changed content. Preserve qualifications that affect the conclusion.
5. Start with one repair cycle affecting at most four pages, followed by review of the changed pages. Report remaining defects if the budget is exhausted; make no changes merely to prove that a repair ran.

Reuse `ModelGateway.complete_json(..., images=...)` and model capability flags. With a model that cannot inspect images, provide text and geometry findings and record image review as unavailable. Model/provider changes remain explicit settings choices.

Keep factual-check, outline-review, render-check, and visual-review results separate internally. In the product, show actionable messages such as “Slide 4 labels are too small,” alongside the affected page. Diagnostics stay outside the slides and speaker notes.

**Accept when:** layout-only repair leaves checked text and citation bindings unchanged; content repair reruns the appropriate checks; visual status becomes stale on relevant changes; clean pages remain untouched; failed repair retains the previous usable candidate; and actual requests, including retries, remain within the recorded budget.

**Integration:** `llm.py` capability/image support, new slide-review schemas, the slide skill/configuration, artifact orchestration, `SlideFace`/scene validation, and the existing Outputs review interface.

## Batch 5 Verify quality and enable the improved workflow

**Deliverable:** a comparison report and a release candidate with evidence of structural, visual, and citation correctness.

Use meaningful checks for each changed boundary:

- Backend: required-content retention under page limits; outline repair and locks; citation identity after moves/merges/splits; freshness after upstream changes; failed model/capture behavior; truthful status with and without image input.
- Frontend: actual type and graphic fit; invalid/stale scene fallback; real style previews; citation clicks; save/reopen; presentation; and PDF output using the shared renderer.
- Cross-language contracts: shared scene fixtures, version compatibility, block coverage, and graphic permissions when the scene contract changes.
- End-to-end: run the six packets from Batch 0 with matched inputs/settings. Evaluate structure before styling, then compare design with checked content held constant. Cover both speaking and reading use.

Reuse the existing artifact edit evaluation for affected move/edit/save behavior. Its fixed-outline fixtures cannot measure autonomous story planning; the new slide evaluation must also start from material without a supplied outline. Existing domain examples remain fixtures, never production rules.

Run targeted regressions with each batch. Run broader frontend/backend checks and the packaged desktop smoke check at integration. Use a small live-model smoke run during development and the complete comparison at the release checkpoint, reusing saved results when inputs have not changed.

The release is ready when explicit requirements and source bindings survive, completed pages have no unresolved clipping or unreadable required content, visual inspection covers all final slides and PDFs, old versions reopen correctly, and paired review finds the intended structural and visual improvements without factual regressions. Record qualitative findings, model/effort, calls, latency, and fallback rates. Model self-evaluation alone does not establish audience comprehension.

Enable the new stages through a versioned Slides skill rollout. Keep the previous valid output recoverable, record which stages completed, and reuse existing partial-failure behavior. Motion and native PPTX can follow after this HTML/PDF workflow meets these criteria.

## First implementation delivery

Make the first reviewable delivery the Batch 0 comparison pack, Batch 1 outline improvements, and the Batch 2 style prototypes. It should contain:

1. Before/after outlines using identical sources, with the content-coverage and citation comparison.
2. An over-limit case that replans without silently removing its supporting middle.
3. Three rendered visual directions using identical checked wording, including an evidence page.
4. Results of relevant regressions and the rendering feasibility experiment.

This delivery demonstrates the structural and visual direction before the automatic screenshot-review loop becomes a release dependency.
