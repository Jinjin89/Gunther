# Public slide skills and improvements for Gunther

Research date: 18 October 2026.

Gunther can improve its slide output while continuing to use reveal.js. The strongest opportunities are richer design direction, better control of content density, and a review loop that sees rendered slides. The current application already has evidence checks, multiple layouts, themes, and a general scene composer. Those capabilities provide a useful foundation for the next changes.

The findings below compare public workflows with the current working tree, including its uncommitted presentation changes. They establish specific differences in capability. A matched pair of local and remote decks is still needed to measure the quality difference or identify the exact workflow behind the remote output.

For argument design, slide sequencing, and planning checks, see [Presentation structure research](PRESENTATION_STRUCTURE_RESEARCH.md).

The combined implementation sequence and acceptance criteria are in [Gunther slide improvement plan](PRESENTATION_IMPROVEMENT_PLAN.md).

## Public workflows worth studying

| Workflow | Output and verified behavior | Technique to adapt |
| --- | --- | --- |
| [OpenAI public Slides skill at a pinned revision](https://github.com/openai/skills/blob/e6afb0d74cc75d220df2faf3dd6c635c2dc6a108/skills/.curated/slides/SKILL.md) | Uses PptxGenJS, editable native objects where practical, layout helpers, rendered PNGs, a montage, overflow checks, and font diagnostics. This is a historical revision, not a claim about every current Codex installation. | Bundle practical layout and inspection tools with the instructions. Evaluate actual rendering before delivery. |
| [Anthropic official PPTX skill](https://github.com/anthropics/skills/blob/main/skills/pptx/SKILL.md) | Creates new PPTX decks with PptxGenJS and edits existing packages/templates. Specifies palette, typography, spacing, chart styling, template inspection, file validation, and inspection of every rendered slide. | Make design decisions concrete. Preserve a reference deck's visual system and distinguish file correctness from visual quality. |
| [Frontend Slides](https://github.com/zarazhangrui/frontend-slides/blob/main/SKILL.md) | Produces HTML presentations. Uses visual style previews, separate speaking/reading density modes, image evaluation during planning, a fixed 16:9 canvas, and browser screenshot checks. | Show alternatives using the actual material. Carry the chosen design through the whole deck and check visual overlaps as well as overflow. This is the closest design-workflow reference for Gunther's browser renderer. |
| [Baoyu Slide Deck](https://github.com/JimLiu/baoyu-skills/blob/main/skills/baoyu-slide-deck/SKILL.md) | Creates slide images from a content outline and saved per-slide prompts. Organizes style through texture, mood, typography, and density, and accepts visual references. | Use a consistent deck-wide design brief and explicit asset direction. Preserve enough generation metadata to reproduce an asset. |
| [Slidev official skill](https://github.com/slidevjs/slidev/blob/main/skills/slidev/SKILL.md) | Covers Markdown/Vue presentations, layouts, code, diagrams, math, click sequences, notes, and export. Its instructions load specialized references as needed. | Maintain a compact capability map with focused references. Presentation behavior and layout can be separate concerns. |
| [Reveal.js community skill](https://github.com/ryanbbrown/revealjs-skill/blob/main/skills/revealjs/SKILL.md) | Generates reveal.js HTML and CSS with content-informed styling, layout variation, notes, and a scaffold script. | A useful example on the same engine. Its particular font-unit and styling preferences are author conventions, not requirements for Gunther. |

Frontend Slides displayed approximately 30.8k GitHub stars, and the Baoyu skills repository approximately 26.7k, when checked. These are repository-level popularity signals, not measurements of an individual skill's slide quality. [Frontend Slides repository](https://github.com/zarazhangrui/frontend-slides), [Baoyu repository](https://github.com/JimLiu/baoyu-skills).

The [Frontend Slides style reference](https://github.com/zarazhangrui/frontend-slides/blob/main/STYLE_PRESETS.md) illustrates an important difference between a palette and a design system: its presets describe typography, spatial arrangement, and recurring visual elements as well as colors. The useful abstraction is a reusable design vocabulary. The specific fonts, decorations, and preset names can remain choices.

### Current OpenAI presentation guidance

The installed [Presentations skill](/Users/keke/.codex/plugins/cache/openai-primary-runtime/presentations/26.1017.11052/skills/presentations/SKILL.md) uses Artifact Sessions or `@oai/artifact-tool` for authoring. It should be distinguished from the historical public PptxGenJS skill above. Its local visual guidance favors a coherent slide composition, readable type, suitable assets, and reference matching. Its finalization guidance requires full-size inspection of each slide; a contact sheet serves deck-wide review.

The official [slide creation workflow](https://learn.chatgpt.com/use-cases/generate-slide-decks) also describes planning, following templates or references, and checking rendered slides. Public documentation and installed instructions do not establish which private tools, model, or asset pipeline produced a particular remote deck.

Skills package a repeatable workflow with instructions and supporting resources. Their value can come from tools, templates, and inspection steps as much as from prompt wording. [OpenAI skills documentation](https://learn.chatgpt.com/docs/skills-and-plugins#use-skills-for-repeatable-work).

### Visual fidelity and editable output

Some attractive presentation workflows produce a picture of each slide. Baoyu's [PPTX merge script](https://github.com/JimLiu/baoyu-skills/blob/main/skills/baoyu-slide-deck/scripts/merge-to-pptx.ts) places a full-slide image on each page. That preserves the generated appearance, while text and charts remain part of the image.

Slidev's current documentation distinguishes image-based `pptx` from `pptx-editable`. The latter rebuilds supported browser elements as native shapes and uses images for unsupported elements, with a possible whole-slide fallback. It would be outdated to describe all Slidev PPTX export as image-only. [Slidev export documentation](https://sli.dev/guide/exporting#pptx).

For Gunther, visual improvement in HTML/PDF and native PPTX export are separate projects. The current request can be addressed through its existing browser rendering path.

## What the current Gunther implementation already does

| Area | Current implementation |
| --- | --- |
| Viewer | `SlidesView.tsx` uses `@revealjs/react` and reveal.js on a 1280 × 720 stage. |
| Shared rendering | `SlideFace.tsx` draws the slide content used by the viewer, thumbnails, and print components. |
| Content workflow | Slides skill v9 defines understanding, structure, design, writing, composition, editorial review, audience reading, and revision. |
| Layouts | Fifteen declared layouts, including figures, diagrams, charts, and a composed scene. |
| Design controls | Four themes, accents, fixed-layout compositions, and nested row/column scenes with weights, spacing, alignment, type sizes, and surfaces. |
| Evidence | Composed leaves reference existing content blocks. Graphic views require suitable source content and checked permissions. |
| Geometry | Browser measurements detect overflow; fitting and fallbacks preserve source content and report layout failures. |

Relevant sources: [viewer](../apps/desktop/src/outputs/SlidesView.tsx), [shared slide renderer](../apps/desktop/src/outputs/SlideFace.tsx), [skill configuration](../apps/backend/gunther/skills/slides/skill.toml), [scene contract](../apps/backend/gunther/skills/slides/references/scene.md), and [graphics composer implementation record](PRESENTATION_GRAPHICS_COMPOSER_PLAN_CN.md).

The next iteration should extend these capabilities. Merely adding another planning prompt or more layout names would duplicate work already present.

## Concrete gaps

### Review receives text without rendered slides

In `skill_runner.py`, `polish()` sends the document text and check findings to the editor. The reader receives `_document(..., reader=True)`, also as text. Neither call receives screenshots, measured label sizes, or the rendered composition.

These steps can assess wording and narrative. They cannot directly observe a tiny chart label, an awkward gap, a weak focal point, or an image crop. Frontend measurements exist, but the inspected review path does not feed their results back into generation.

Source: [review implementation](../apps/backend/gunther/skill_runner.py).

### The design vocabulary remains narrow

The themes provide useful semantic colors and a display/sans heading choice. Fixed layouts expose `standard`, `split`, and `spotlight`. Composed scenes add substantial spatial freedom, but `ComposedSlide.tsx` still renders the heading above the body tree.

This supports organized evidence pages. It offers less freedom for an integrated title, a dominant source image, asymmetric typography, or a composition where the title and evidence share the same spatial system. More color options alone would leave that limitation in place.

Sources: [themes](../apps/desktop/src/outputs/slides/themes.ts), [composed renderer](../apps/desktop/src/outputs/slides/ComposedSlide.tsx), and [visual design instructions](../apps/backend/gunther/skills/slides/references/visual-design.md).

### Fitting can conceal a density problem

`slideFit.ts` permits an internal scale down to 0.55. The visual design reference directs crowded pages toward grouping followed by the existing fitting/fallback behavior. The revision instructions keep page count, order, and layout fixed.

Consequently, a crowded page can survive through smaller type even when a revised display summary or a different allocation of content would communicate better. The compose step cannot solve this alone because it must retain every supplied block.

Sources: [fitting](../apps/desktop/src/outputs/slideFit.ts), [design instructions](../apps/backend/gunther/skills/slides/references/visual-design.md), and [revision instructions](../apps/backend/gunther/skills/slides/SKILL.md).

### The existing image path centers on retained source figures

The skill plans a figure slide when a retained source image is available. This preserves provenance. The inspected slide workflow does not provide a comparable planning step for additional visual assets, such as a supplied product screenshot or an explicitly illustrative image.

Source: [slide planning instructions](../apps/backend/gunther/skills/slides/SKILL.md).

### Geometry validation does not measure communication quality

The saved [synthetic model overview](../artifacts/validation/presentation-graphics-composer/model_overview.png) shows two valid mixed compositions. In that image, chart and diagram labels are small relative to the page, and the information groups leave large gaps. Those observations suggest opportunities for better scale and attention, even though the graphics fit.

This image tests mixed graphics using synthetic material. It should not serve as a quality score for all Gunther decks. It does demonstrate why a successful fit check needs a separate visual judgment.

## Proposed improvements while retaining reveal.js

### Establish a complete design brief

Extend the current brief with a deck-wide visual specification: speaking or reading use, typography with Chinese/English coverage, type hierarchy, spacing scale, palette roles, image treatment, density, and an optional recurring visual device. Use supplied references and available assets to inform these choices.

For style exploration, render the same cover and one representative evidence page in two or three directions. An evidence page exposes whether the design works beyond the cover. A picker can remain optional; explicit user choices should carry through generation and revision.

Keep style selection contextual. Topic keywords and publisher identities should not become production lookup rules. A source's organization and evidence remain independent of the presentation's visual treatment.

Treat aesthetic heuristics as adjustable defaults. The current requirement for every title to state a conclusion and the quota on text pages can be relaxed when a definition, setup page, quotation, or typography-led statement better serves the material. Keep the checks against unsupported numbers, invented relationships, and lost citations strict. Public recipes disagree about fonts and decoration; copying every preference would produce conflicting instructions.

### Add review of actual rendering

Introduce a render checkpoint after composition, using the same `SlideFace` implementation as the product. Capture every slide after fonts, images, fitting, and graphic validation settle. Create both full-size slide images and a deck contact sheet.

Return measurements with stable slide/block identifiers: bounds, overlaps, final font sizes after fitting, missing assets, fallback reasons, and unresolved rendering. Distinguish scaling the whole stage to the viewport from shrinking content inside a slide.

A visual reviewer can then inspect screenshots against the brief. Findings should name the affected block and proposed change. A model without image input can provide text and geometry review; its result should not be represented as inspection of the screenshot.

Start with one repair pass. Permit scene and style edits within the existing content contract, then render affected slides again. A further pass should depend on unresolved material defects. Keep evidence checks and visual checks as distinct results.

### Repair density through the right stage

Apply readability thresholds to the final rendered text roles, calibrated for speaking/reading use and language. A lower fit value should trigger review rather than count as successful design by itself.

Try a better composition first. When that is insufficient, return a specific request to the evidence-checked writing or planning stage: shorten the display wording, relocate optional explanation to notes, or split the page when the requested count and user locks permit it. Preserve the original material, claim-citation bindings, and qualifications that affect the conclusion. A fixed count may require a more selective story or an explicit unresolved density issue.

### Extend scenes from demonstrated needs

Keep structured scene data and block references. Candidate additions include placing the existing title within the composition, spanning regions, image fit/crop controls, and annotations attached to an existing figure or chart. Add a primitive only after a representative design demonstrates its value.

Layout freedom does not require permission to invent evidence. An annotation should point to an existing claim or source feature. Geometry and visual-repair changes should preserve graphic permissions and content fingerprints; content changes should return through the appropriate verification path.

### Plan assets with the content

Inventory retained figures and user-supplied assets before choosing the composition. If the user enables additional sourcing, record each asset's source, intended role, and placement requirements. Distinguish evidence figures from illustrative artwork. Keep claims, numbers, and citations as structured text/data.

Generated illustrations may help explain a concept, but they cannot replace measured results or be represented as an original source figure. A typography-led page remains valid when no image improves the explanation.

### Add motion after the static page works

Reveal.js already supports layout helpers and animation between matching elements. Selected click reveals or figure highlights could support an explanation after static design is satisfactory. [Layout documentation](https://revealjs.com/layout/), [Auto-Animate documentation](https://revealjs.com/auto-animate/).

Keep a complete static state for printing. Reveal's own PDF exporter has explicit controls for fragment pagination; Gunther uses its shared print components, so those settings are reference behavior rather than a substitute for checking Gunther's exported PDF. [Reveal PDF documentation](https://revealjs.com/pdf-export/).

## Suggested implementation order

| Order | Concrete result | Acceptance evidence |
| --- | --- | --- |
| 1 | A small set of reference designs applied to existing source-backed pages through the current renderer. | Side-by-side full-size renders show a usable cover and evidence page for each direction, with citations and required content intact. |
| 2 | A persisted design brief and instructions that reproduce the selected direction. | A newly generated deck follows the chosen typography, spacing, density, and image treatment after save/reopen and in print. |
| 3 | Rendering, measurement, visual review, and bounded repair. | Record defects before and after repair, unchanged content where only composition changes, final renders, added calls, and latency. |
| 4 | Density repair and any scene/asset extensions demonstrated necessary by the comparisons. | Difficult pages improve without lost evidence, misleading charts, or unreadable text. |
| 5 | Optional presentation motion and native PPTX export. | Validate the motion's static export state; evaluate PPTX fidelity and editability as separate requirements. |

Likely integration points are the existing slide skill and references, `skill_runner.py`, the artifact orchestration steps, `SlideFace.tsx`, the composed scene contract/renderers, theme tokens, and print rendering. The render checkpoint also needs an explicit bridge between the generation workflow and browser measurements.

## How to measure improvement

Use several source sets with different organizations, not one preferred paper format: English and Chinese material, prose without numeric data, a comparable numeric series, an existing figure, a process with conditions, and a dense page with necessary qualifications. Keep these as evaluation fixtures rather than production rules.

Run two comparisons. First, hold the checked content constant and compare designs in the current renderer to isolate visual changes. Then compare end-to-end generation with the same source packet, brief, model, effort, asset access, and documented call budget. Evaluate speaking and reading use separately. Record any remote configuration that cannot be matched.

Judge readability, information hierarchy, appropriate visual explanation, narrative continuity, reference match, evidence preservation, and export consistency. Inspect each slide at its intended viewing size and compare decks with source labels hidden where practical. Record cost, latency, and fallback rates alongside quality. A model's self-assigned score alone is insufficient.

The first useful experiment is a controlled redesign of existing checked pages with a few complete visual directions, followed by review of their actual renders. It can establish which renderer and workflow changes are needed before expanding the system.
