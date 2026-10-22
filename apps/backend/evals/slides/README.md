# Slide structure and design comparisons

These development fixtures cover six synthetic source packets. No production code loads them.
Use them to compare outline reasoning separately from visual design; keep the same model,
source packets, evidence cards, and supported page count across outline runs.

From the repository root, run the current outline planner:

```sh
PYTHONPATH=apps/backend apps/backend/.venv/bin/python -m evals.slides.plan \
  --output artifacts/validation/presentation-batches-0-2/after-outlines.json
```

The command reads the configured Outputs model and sends only the synthetic packets.
It bounds each packet to six actual requests including retries, saves each result as it
completes, and preserves source inputs and hashes. The run starts from fixed evidence cards;
it does not assess extraction, a full writing run, or real-library generation.

To reproduce the archived baseline prompt and skill, also pass:

```sh
--skill-root artifacts/validation/presentation-batches-0-2/baseline/skill \
--protocol artifacts/validation/presentation-batches-0-2/baseline/protocol.json
```

Start the isolated render host from the repository root:

```sh
npm exec -w @gunther/desktop vite -- --config evals/slides/vite.config.ts
node apps/desktop/evals/slides/capture.mjs --design 1 \
  --output artifacts/validation/presentation-batches-0-2/renders
```

Open `http://127.0.0.1:5184/evals/slides/`. Select `theme=briefing`, `editorial`, or `keynote`.
Use `design=0` for legacy rendering; `all` shows every fixed visual example. The host imports
the actual `SlideFace` component and serves the original synthetic source figure locally.
The capture script uses the installed Google Chrome through the development-only Playwright
dependency. It records fonts, sizes, citation markers, failures, elapsed time, and browser
process memory. `--pdf` also exercises the actual `PrintRoot`/`PrintSlides` path.

Build the standalone render host for the feasibility check:

```sh
npm exec -w @gunther/desktop vite -- build --config evals/slides/vite.config.ts \
  --outDir /private/tmp/gunther-slides-render-build
npm exec -w @gunther/desktop vite -- preview --config evals/slides/vite.config.ts \
  --outDir /private/tmp/gunther-slides-render-build --port 5185
```

Run the same capture with `--origin http://127.0.0.1:5185` and a separate output directory.
This verifies a compiled renderer host, not distribution of a browser inside the desktop
application or parity between Chromium and the packaged Tauri WebView.

Regenerate the portable comparison after its verification records are available:

```sh
PYTHONPATH=apps/backend apps/backend/.venv/bin/python -m evals.slides.verify \
  artifacts/validation/presentation-batches-0-2
PYTHONPATH=apps/backend apps/backend/.venv/bin/python -m evals.slides.report \
  artifacts/validation/presentation-batches-0-2
```

`python -m evals.slides.over_limit DIRECTORY` seeds a seven-page candidate from the saved
intake example, then runs the real outline review and repair with a four-request cap.
The recorded result has six pages and retains all five assigned cards. This is a seeded
repair test, separate from the free outline generation comparison.

Original Markdown/source inputs remain authoritative. Compare wording with layout whitespace
normalized and compare citation sequences and managed image identities separately.
`capture-initial/` preserves the first diagnostic records, including font-line-box false
positives and an inherited deployment API address that caused the figure to fall back.
The render host now isolates that address and checks expected images and fallback elements.
The refreshed baseline uses the corrected measurements. Warning counts are not aesthetic scores.
# Rendered review and repair

The production worker and v11 visual step are exercised with fixed source-backed material.
Each run saves exact input/output content, source hashes, snapshots, requests and fidelity checks.
The six packets run in speaking/reading modes; three dense fixtures demonstrate bounded repair.

    npm run build:renderer --workspace @gunther/desktop
    node scripts/prepare-slide-browser.mjs
    PYTHONPATH=apps/backend apps/backend/.venv/bin/python apps/backend/evals/slides/render_review.py --output artifacts/validation/presentation-batches-3-5/final
    apps/backend/.venv/bin/python apps/backend/evals/slides/review_report.py --input artifacts/validation/presentation-batches-3-5/final --output artifacts/validation/presentation-batches-3-5

Saved results are reused only for matching inputs, model, review protocol and compiled renderer.
Use --only for a subset and --force for an intentional rerun. The geometry-only mode does not
certify image review. These fixtures isolate visual behavior; free outline generation is the
separate planning comparison below.

The macOS engine probe in tools/slide-browser/webview-probe.m compares the same candidate in
WKWebView. The packaged helper exposes --slide-render-smoke for explicit fixture input, without
opening a database or contacting a model. See docs/PRESENTATION_BATCHES_3_5.md for the measured
packaging result, test limits, raw PDF glyph-extraction differences and remaining findings.
