# The Artifacts exam

The Artifacts exam sends fixed messages to a slide deck, a report or an empty conversation. Both fixtures use the Ask exam's demo library of cool-roof trial notes. The developer command scores intent, targeted changes, preservation of untouched parts, factual checks and cost. [Studio outputs](../../../../docs/OUTPUTS.md)

## Run it

From `apps/backend`:

```
.venv/bin/python -m evals.artifacts.run --label exam --jobs 4
```

It needs a model. Give it keys the way the Ask exam does (both are gitignored): a `.env` at the repo root, or a copy of the app's `service-settings.json` with `--settings PATH` (or the env var `SERVICE_SETTINGS_FILE`). Studio makes the fixtures and handles newly requested output builds.

Flags:

- `--models REF,REF`: default is the Outputs job's model, else Ask's. `--writer-effort LEVEL`: default is that job's effort. `--judge REF`: default is the first model (a warning is printed when the judge is also under test).
- `--only E1,S3`: only these cases. `--label NAME`. `--repeat N`: run each case N times (its result is then a pass rate; the records are kept as `E1#1`, `E1#2`).
- `--jobs N`: N cases at the same time, each app on its own copy of the library; the longest cases start first. A full run is slow because every case is a real build or revision, so use 3 or 4.
- `--reseed`: read the demo library again. `--rebuild-fixtures`: build the deck and the report again. `--fixtures-only`: build the fixtures that are missing, then stop.

The fixture records include calls, tokens and time for planning and generation. Case measurements cover the final turn, including a scripted approval when the case calls for it.

The scorecard is `evals/results/<date>-<label>/report.md`, with `raw.json` beside it (every outcome, measure and judgement; written after each case, so a run that is stopped keeps what it has). `python -m evals.artifacts.report FOLDER` writes the scorecard again from `raw.json`; with `--rescore` it first scores every case again from what the run stored (the judge's answers stay as they were), so a fix to the scoring does not cost a new run. The first `raw.json` is then kept as `raw.as-scored.json`.

## Fixtures: one deck and one report, built once

`cases.toml` begins with the two documents the messages are sent to:

- **deck**: slides, for a talk, audience student, 12 slides with 9 different layouts (big numbers, tables, cards, a flow, a quote).
- **report**: an overview for scientists, 5 sections, the third with at least two paragraphs.

Each fixture sends a `plan` turn with `setup` and a fixed `initialOutline` through the conversation stream. The runner checks that the ready proposal kept the requested rows, then sends `approve` with the returned `planRevision` and reads back the sealed version. The outline's `has` words and `paragraphs` must match the saved text. A failed attempt starts again on a fresh copy, up to three attempts. The approved snapshot is kept under `evals/results/fixture-<deck|report>-<library>-<model>-<effort>-<hash>/` with `fixture.json`, the SQLite database and retained library assets. The runner reuses it for the same library, model, effort and fixture definition. [Runner](run.py)

Each case opens a fresh SQLite backup of its approved fixture and a copy of the retained assets. It starts in the saved fixture's conversation with the original version, citations and provenance. A case can pick a sentence or introduce a typo through the exact-text Edit endpoint. Scoring uses the resulting verified head as the baseline. Cloning, the setup edit and prior conversation messages are outside the final-turn measurements.

## The cases

About 30, in ten kinds: small edits; finding a part by what it is about, by what the person points at (a selected slide, selected text, the slide on screen) and by the conversation ("shorten slide 7", then "no, the one before it"); structure (insert, delete, move, split, change a layout, change one outline line); changes upstream of the parts ("focus on cost instead", "make it for first-year students", "cut it to six slides"); content the library has and content it lacks (nothing may be invented); the whole document (translate, shorter titles); questions (answered, nothing saved); asking (a vague request must be asked about, a clear one must not, "change the costs slide" when two slides are about cost must).

The fields are listed at the top of `cases.toml`. Parts are counted from 1 in the version the message is sent on. `{typo}`, `{fixed}` and `{quote}` in a case stand for what its setup made. `tests/test_artifact_exam.py` checks that every part a case names is in its fixture and that the plan's kinds of message are all there.

## What is scored

For each case, with the version before the last message and the version it saved:

- **Intent** is ask, answer, reply, plan, make or change, as the case expects. A list accepts any listed intent. Make requires the case's explicit approval and a saved first version.
- **Asked** follows `asks`: `must`, `must_not` or `may` (a question is then a fair answer and nothing else is asked of the case).
- **Parts.** The two documents are split into parts and matched: parts that read the same first, a part that moved, and then the rest in order by likeness (a translated part is still that part). A part counts as **changed** when its text differs or it is gone.
  - **Precision**: every changed part was allowed to change (`changed` and `may_change`). **Recall**: every part in `changed` did change. Both are counted over parts, not cases, and a case where anything may change (`may_change = "all"`) is left out of precision, because it has no part that must stay.
  - **Untouched parts identical**: the parts outside `changed` and `may_change` read the same afterwards. Saving renumbers the source numbers of the whole document in reading order, so a part nobody touched can read `[2]` where it read `[3]`. Parts are therefore compared with every `[n]` replaced by its passage's identity (source id and a hash of the quote). **Byte-identical** is reported beside it, as information only.
  - **Count** (`count_delta`; left out it is 0, unless anything may change), **layout** (a slide's own `<!-- layout: … -->` line, else its outline), **order** and **new_at** (after a move or an insert), **shorter** (fewer words).
- **Strings**: `must_contain` and `must_not_contain` are looked up in what the assistant produced (the new text, and any reply or question), ignoring case, source numbers, curly quotes and dashes. **Cites**: a changed or new part cites one of the named notes.
- **No numbers from nowhere**: every number a changed or new part (or a reply) states is in the document before, in a note the documents cite, in a cited passage, or in what the person wrote. Whole numbers up to 12 without a percent sign (slide numbers, "3 bullets") and numbers written as words ("fifteen") are not counted as inventions.
- **Checked**: every changed or new part has a check. Whether the check was clean is reported (supported, flagged as unsupported or contradicted, not checked) but does not fail a case, because the documents are model-written and the Checker flags some claims in every part.
- **Judge**: one call for a case that has a `judge` question, answered yes or no. The judge sees the conversation, the document before (with what the Checker found and the sources it cites), and the document after with unchanged parts left out, or the answer or question.
- **Calls, tokens, seconds** of the last message (the history before it is not counted). Calls are counted where the gateway talks to the provider.

A case passes when every check holds and the judge says yes. The scorecard (`report.md`) has one row of numbers per configuration, "passed by kind of request", and **one line for every case that failed, saying why**.

## Conversation turns

The exam sends history and final messages to `POST …/artifact-conversations/{id}/messages/stream` with any focus, model and effort under test. Empty conversations can ask, reply or plan before a first save. Cases with a `plan` field send structured Setup on a Plan turn. A case with `approve = true` reviews the returned proposal and explicitly approves its revision.

The final `done` event records the outcome and any saved version. The scorecard uses the verified version before the measured turn and records calls, tokens and seconds for that turn.

## Tests

`tests/test_artifact_exam.py` covers the case file, the scoring on hand-made before and after documents (including a part that was only renumbered), the matching of parts, the scorecard, the call counter, and runs with stand-in models (fixtures, side-by-side cases, repeats, the cache); it needs no keys.
