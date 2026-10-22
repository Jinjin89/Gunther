# Slide structure and design batches 0 to 2

Implemented 18 October 2026. See the [comparison page](../artifacts/validation/presentation-batches-0-2/comparison.html) for recorded before/after outlines, three full-size visual directions, the over-limit repair, and original evaluation sources.

## Structure

Slides skill v10 plans supported messages and audience questions before layout details. Saved outlines retain stable page identities, messages, questions, and optional prerequisites through the workspace, API, editor, and rebuild paths. The editor prevents rebuilding an order that loses a prerequisite. Newly added pages receive fresh identities.

One bounded outline review checks the reasoning and evidence coverage. Its record distinguishes explicit content requirements from inferred priorities and maps supporting cards to the resulting pages. Factual messages on covers and summaries also need assigned evidence. Observations, interpretations, and proposed actions remain distinguishable; missing evidence must not become a negative finding or a policy mandate.

The planner can repair once. It validates page limits, prerequisites, card references, and retention of previously assigned evidence. It no longer cuts out the middle of an overlong outline to keep the ending. An unresolved constraint retains the candidate in the planning diagnostic and fails the new plan; an existing workspace outline is replaced only after planning succeeds.

Layout/card assignments guide writing. The existing claim checks still verify headings, body content, values, and speaker notes. Planning coverage does not certify the final claims.

## Visual design

Briefing, Editorial, and Keynote now have complete recipes for type hierarchy, spacing, evidence framing, and chart treatment. They use available local fonts with Chinese/English fallbacks. The style chooser shows the current deck's actual cover and evidence page, using the same `SlideFace` as presentation and print.

The title marker stores the recipe version as `design: v1`. Older artifacts without it continue to render with their previous styles. Rewrites, theme/accent changes, save/reopen, editor previews, thumbnails, and print preserve the version. An explicit Ink choice remains neutral. Miniature previews use measured scaling without requiring container-query support.

Style/composition changes preserve the checked wording, values, qualifiers, managed image references, and citations. Scene and graphic permission rules remain in use. The overflow check now distinguishes visible glyph ink from clipped content and measures text ranges to catch real overflow.

## Comparison and verification

The [slide evaluation tools](../apps/backend/evals/slides/README.md) keep six synthetic packets outside production. They cover English and Chinese, speaking and reading use, text without numeric data, negative values and unequal time intervals, conditional procedures, conflicting evidence, and an original source diagram.

The saved planning runs use the same configured Outputs model and fixed source-backed cards. Original source packets, card content, and input hashes are retained. A separate seeded seven-page candidate exercises live review and repair: six pages result, with all five assigned evidence cards retained.

Visual comparisons hold wording constant. All 21 samples preserve wording and citation sequences after excluding layout whitespace, with no detected overflow or missing images. Their content and platform fonts match between the development and compiled render hosts. Actual font resolution on this Mac includes Avenir Next, Iowan Old Style, PingFang SC, and Songti SC.

Three PDF exports use the actual `PrintRoot`/`PrintSlides` path. All nine pages, including source pages, were rendered and visually inspected. Text extraction also confirms the principal values, qualifications, and source names.

Detailed counts and results are in [validation](../artifacts/validation/presentation-batches-0-2/validation.json) and [fidelity checks](../artifacts/validation/presentation-batches-0-2/fidelity.json). The frontend full run passed 789 tests. Backend integration found three stale assertions, including two migration expectations from the existing working tree; those were corrected and passed their recheck. Final checks cover the affected output, workspace, planner, scene/style, and API behavior. TypeScript, Ruff, and the production web build pass; the build retains its existing large-chunk warning.

## Capture feasibility and remaining scope

The standalone render host imports production components, loads the original local fixture asset, and works from compiled frontend assets without the main Outputs screen. Captures record actual fonts, text sizes, citation markers, timing, and browser process RSS. See [capture feasibility](../artifacts/validation/presentation-batches-0-2/capture-feasibility.json).

This developer host uses installed Chrome through a development dependency. Browser distribution, packaged Tauri WebView parity, and automatic screenshot review/repair remain batches 3–5. The model runs evaluate planning from fixed cards; they do not establish performance for a full real-library extraction/writing run or a proprietary remote system. The comparisons are review material, not an automatic aesthetic score.
