# The quality evaluation

Builds a talk deck and a written report from real public material through the real app path,
renders them, lints them for hard defects, and has a judge model rate them. It is the measure for
`docs/ARTIFACT_QUALITY_GOAL.md` (section 6). A developer tool: nothing in the app or the sidecar
uses it.

## Run it

From `apps/backend` (keys as for the Ask exam: the repo-root `.env`, or `--settings PATH`):

```
.venv/bin/python -m evals.quality.run --sets velmora --methods skills --label dev
.venv/bin/python -m evals.quality.run --sets rag-papers,ev-outlook --methods skills,studio --jobs 3
.venv/bin/python -m evals.quality.report evals/results/<folder>      # re-render report.md from raw.json
```

Flags: `--sets` (ids from `materials.toml`, default all 7), `--methods skills,studio`, `--kinds deck,report`,
`--model REF` (writer; default the Outputs job's model), `--judge REF` (default `deepseek/deepseek-flash`),
`--effort`, `--jobs N` (builds at once), `--label`, `--reuse` (skip a build whose set definition, brief,
method, model and a hash of `gunther/` already have a result in an earlier run), `--reseed`,
`--no-render`, `--no-judge`, `--visual-judge REF` (a vision model, e.g. `deepseek/deepseek-flash`,
scores the rendered slide PNGs 1-10 with a ship / minor edit / rework verdict per slide; it fills the
report's Visual column, the text judge is unchanged).

Methods: `skills` runs the app with `developer.output_skills = true`; `studio` sets
`developer.output_method = "studio"`. A studio build whose content is not in the studio format
(no `design: v2` / `<!-- report: v2 -->`) is reported as an error; nothing falls back.

## Material

`materials.toml`: 7 sets, each with a library question, sources and two briefs. Sources are read with
the app's web capture. Two situations need a fallback, recorded per source in the report:

- On a machine without IPv6, hosts that resolve to IPv6 first (IEA, behind Cloudflare) fail in the
  capture with "The web page could not be fetched". The eval then fetches the HTML itself and files
  it as a note (`fallback: HTML fetched by the eval...`). Extraction of those pages is therefore
  cruder than the product's.
- The capture reads only HTML and text, so a PDF (the Fed transcript) is turned into text with
  `pdftotext` and filed as a note.

Each set is seeded once into `evals/results/quality-template-<id>-<hash>/` (with the real processing
worker) and every build runs on a fresh copy. `evals/results/` is gitignored; downloaded material
stays there.

## What is measured

- `lint.py` (no model): overflow, text under 18px, empty half (read from the renderer's
  `measure.json`), slides over the STUDIO_FORMAT budgets (CJK = 2 units), topic-word titles,
  disclaimer phrases on slides, numbers without a citation on the same line, talk slides without notes.
- `judge.py`: rubric 1-10 (insight, storyline, titles, evidence, clarity, correctness, visual when the
  judge can see images), a ship / minor edit / rework verdict for every slide or section, the
  titles-only test (a fresh call retells the message from titles alone, a second call compares), and a
  blind pairwise comparison with the order swapped (a result counts only if it survives the swap).
  Ship rate = (ship + minor edit) / all slides or sections.
- Rendering calls `node apps/desktop/evals/deck/capture.mjs --input content.md --citations citations.json --out DIR`
  (`--kind report` for reports); without the script the run says so and goes on. PNGs and `measure.json`
  are kept in `builds/<set>/<kind>-<method>/render/`.

Results: `evals/results/<date>-quality-<label>/` with `report.md`, `raw.json` and `builds/`. Unit tests:
`tests/test_quality_eval.py`.
