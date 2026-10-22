# Rendered-slide review and bounded repair: batches 3–5

Implemented 18 October 2026 on top of batches 0–2. Slides skill v11 keeps reveal.js, the
1280 × 720 stage, existing checked Markdown, managed source figures, citation identities,
and scene v1/v2 permissions.

Open the [before/after gallery](../artifacts/validation/presentation-batches-3-5/comparison.html)
or [paired outlines](../artifacts/validation/presentation-batches-3-5/outlines.md). The gallery
contains all 15 comparisons, full-size images, remaining findings, original source quotations,
and the actual exported PDFs. Examples use synthetic sources, never the user's library.

## Production capture

The independent render entry point imports the production SlideFace and PrintRoot/PrintSlides.
The backend captures the assembled candidate after final citation numbering. It waits for
font loading, image decoding, scene/graphic validation and stable fitting, with explicit
timeouts. One capture worker runs at a time per backend process.

RenderSnapshot v1 records candidate, content, scene, design, renderer and source-asset digests;
slide and block identities; full-size PNGs; a contact sheet; effective text sizes, bounds,
fit, overflow and block overlaps; requested/resolved fonts; missing assets and scene fallback
reasons; and a PDF from the real print path. The exported PDF is opened and checked for
text and source titles as well as checking its print DOM.

PDF glyph comparison uses the published
[Unicode 17 Equivalent_Unified_Ideograph data](https://www.unicode.org/Public/17.0.0/ucd/EquivalentUnifiedIdeograph.txt)
to recognize equivalent CJK glyph encodings. Raw extraction differences and the extracted-text
hash remain in the snapshot. This comparison never normalizes or rewrites source text,
checked Markdown, citation markers, or PDF contents.

The worker serves only the compiled renderer, this candidate and its referenced images
through an authenticated private loopback host. It uses a temporary browser profile and
cleans up the browser and host. Private render caches stay separate from original sources.
Render downloads check artifact/workspace ownership and file hashes.

The desktop build includes the compiled host and a pinned Chromium headless shell. Chromium
is copied and signed as a separate runtime; PyInstaller must not rewrite its dylib paths.
No Node, Vite, Playwright installation or main Outputs screen is needed at application runtime.

## Review and repair

The final skill step uses the configured Outputs model and its actual image capability.
Each request contains a contact sheet and at most three full-size slides, so it uses at most
four images. Findings name a stable slide and optional block, severity, reason, correction
and the appropriate layout/write/plan route.

The limits are one repair cycle, at most four affected slides, and eight physical model
requests including JSON retries. Nested request budgets retain the enclosing turn's limit.
Geometry and image findings remain distinct from factual checks and outline review.

A layout repair can return only a scene tree and/or supported composition. Code validates
block coverage, view eligibility, locks and existing graphic licenses. Checked wording,
speaker notes, numeric values, units, qualifications, citation tokens and source identities
must remain exactly unchanged. The title marker and selected theme remain intact.

Changed pages are rendered and reviewed again. A rejected repair, failed capture, unavailable
provider, exhausted budget or severe remaining defect keeps the previous usable candidate.
Clean pages do not change merely to demonstrate a repair. Without image capability the
same configured model is retained; geometry results remain available and image review is
explicitly unavailable.

Workspace reintegration carries accepted layout metadata back to pool-numbered drafts,
rebinds existing graphic hashes through the trusted citation map and preserves freshness
and factual-check coverage. Tests cover both ordinary text and licensed graphics.

## Product workflow and compatibility

The Outputs preview has a separate rendered-slide review panel. It shows actionable page
findings, captured images and the inspected PDF. Diagnostics never enter slides or speaker
notes. Wording and sequence findings can enter the existing checked editing/planning tools.

Review current renders runs only the visual workflow and saves a new immutable version,
even when no wording changes. It uses the existing progress/stop stream and idempotency
mechanism. The previous version stays recoverable. Picking a style still performs a code
edit without model calls; content, style, outline, use or citation changes mark an earlier
review stale. Source-asset or renderer changes also invalidate its current status.

Old artifacts and skill records load without fabricated review results. No database
migration or reveal.js replacement is required. Render/review metadata is additive.

The workflow defaults on in desktop builds. A development checkout prepares its renderer with:

    npm run build:renderer --workspace @gunther/desktop
    node scripts/prepare-slide-browser.mjs

SLIDE_REVIEW_ENABLED=false disables capture. SLIDE_RENDER_DIR and SLIDE_BROWSER_PATH can
set explicit absolute cache/browser paths. Missing dependencies produce an unavailable
review result while preserving the deck; nothing downloads at application runtime.

## Before/after findings

The six source packets run in both speaking and reading modes. Three additional dense
scene fixtures cover Briefing, Editorial and Keynote. Original inputs, per-case model
settings, source hashes, images and findings are retained under the final comparison folder.

| Result | Cases |
| --- | ---: |
| Clean, no automatic change | 7 |
| Accepted layout repair | 5 |
| Previous candidate retained; needs review | 3 |
| Capture unavailable | 0 |

The final comparisons used DeepSeek Flash, medium review effort and 30 physical model
requests. Earlier diagnostic probes are separate from this total. Every comparison preserves
exact wording, notes, citations, source bindings, slide identities, page count and order.
All 15 final PDFs passed content/source checks; all 45 exported pages were rasterized and
visually inspected through the saved contact sheets.

All three dense examples accepted a repair in three requests each. Their main qualification
text grows from about 18 px to about 32 px (Briefing/Editorial) or 39 px (Keynote), with the
same checked wording and references. This demonstrates observable readability and grouping
changes; it is not evidence of measured audience comprehension.

The retained cases are deliberate partial outcomes. The reading intake example received an
invalid scene proportion and kept its original. Both numeric cases need readable scale/zero
information; the attempted table repairs still collided with the qualification and were
rejected. These cases are labelled needs_review, never completed or repaired.

Free story planning remains the separate six-packet comparison from batches 0–2, whose
sources and planner inputs are unchanged. The new visual fixtures are fixed subsets;
they do not constitute a new full extraction/writing benchmark.

## Packaging, native comparison and tests

The frozen helper successfully captured a Chinese fixture using its own renderer, Unicode
data and signed browser. Measured capture time was 2.33 seconds; full helper invocation
was 8.83 seconds. Sampled process-tree RSS peaked at 718 MiB, which can count shared pages
more than once. The prepared browser cache is about 216 MB. See
[packaged feasibility](../artifacts/validation/presentation-batches-3-5/packaged-feasibility.json).
The smoke build explicitly excluded the separate document-parser runtime; normal release
builds retain their existing parser requirement.

Nine decks / 18 slides were also rendered in macOS WKWebView, the engine used by Tauri.
Seven cases matched the 3 px geometry tolerance. Two had small line-box differences
(3.5 and 3.81 px) with the same fitting, wrapping, source images and scene views; inspection
found no clipping or lost content. These are engine checks, not proof of exact pixel
parity or a full installed-application UI test. Other OS/architecture combinations remain
unverified. See [native comparison](../artifacts/validation/presentation-batches-3-5/webview-parity.json).

Validation includes:

- Full frontend: 793 tests passed; the final Outputs check passed 313 tests after the
  native-safe image/PDF controls were added. TypeScript and production web build passed.
- Full backend: 1,186 passed, two skipped; final affected boundary run: 219 passed.
- Additional final workspace repair/glyph/budget/review API checks: 34 passed after that run.
- Native desktop: 34 passed, one ignored.
- Ruff, whitespace checks, packaged capture and deep code-signature verification passed.

One earlier unconstrained backend run passed assertions but aborted during native-library
teardown. The repeated full run exited successfully with OMP_NUM_THREADS=1 and
VECLIB_MAXIMUM_THREADS=1. Focused final checks also exited successfully with ordinary settings.
The web build retains its pre-existing large-chunk warning.

The implementation is enabled with conservative completion status. The three partial
comparison cases remain visible for checked follow-up; automatic visual review is not a
guarantee of factual correctness or a substitute for a presenter reviewing the final deck.
