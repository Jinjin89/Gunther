# Slide rendering and visual review

Six synthetic source packets supply fixed slides and citations for visual evaluation.

Start the isolated render host from the repository root:

```sh
npm exec --workspace @gunther/desktop -- vite --config evals/slides/vite.config.ts
```

Then capture from another terminal at the repository root:

```sh
node apps/desktop/evals/slides/capture.mjs --design 1
```

Open `http://127.0.0.1:5184/evals/slides/`. Select `theme=briefing`, `editorial`, or `keynote`.
Use `design=0` for legacy rendering; `all` shows every fixed visual example. The host imports
the actual `SlideFace` component and serves the original synthetic source figure locally.
The capture script uses installed Google Chrome through Playwright. It prints the path to a fresh
`slide-capture-*` directory under the git-ignored `apps/backend/evals/results/`. Use `--output DIR`
to choose a destination. Captures include fonts, sizes, citation markers, failures, elapsed time,
and browser process memory. `--pdf` also exercises the `PrintRoot` and `PrintSlides` path.

Build the standalone render host for the feasibility check:

```sh
npm exec --workspace @gunther/desktop -- vite build --config evals/slides/vite.config.ts \
  --outDir /tmp/gunther-slides-render-build
npm exec --workspace @gunther/desktop -- vite preview --config evals/slides/vite.config.ts \
  --outDir /tmp/gunther-slides-render-build --port 5185
```

Run the capture with `--origin http://127.0.0.1:5185` to use the compiled renderer host.

Original Markdown/source inputs remain authoritative. Compare wording with layout whitespace
normalized and compare citation sequences and managed image identities separately.

## Rendered review and repair

The production worker and v11 visual step are exercised with fixed source-backed material.
Each run saves exact input/output content, source hashes, snapshots, requests and fidelity checks.
The six packets run in speaking/reading modes; three dense fixtures demonstrate bounded repair.

    npm run build:renderer --workspace @gunther/desktop
    node scripts/prepare-slide-browser.mjs
    PYTHONPATH=apps/backend apps/backend/.venv/bin/python apps/backend/evals/slides/render_review.py --output artifacts/validation/presentation-batches-3-5/final
    apps/backend/.venv/bin/python apps/backend/evals/slides/review_report.py --input artifacts/validation/presentation-batches-3-5/final --output artifacts/validation/presentation-batches-3-5

Saved results are reused only for matching inputs, model, review protocol and compiled renderer.
Use `--only` for a subset, `--force` for an intentional rerun, and `--geometry-only` to capture
geometry findings alone.

The macOS engine probe in tools/slide-browser/webview-probe.m compares the same candidate in
WKWebView. The packaged helper exposes --slide-render-smoke for explicit fixture input, without
opening a database or contacting a model. See docs/PRESENTATION_BATCHES_3_5.md for the measured
packaging result, test limits, raw PDF glyph-extraction differences and remaining findings.
