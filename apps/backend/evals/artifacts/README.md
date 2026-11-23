# The Artifacts exam

A fixed set of 32 messages sent to a slide deck and a report that were built from the Ask exam's demo library (ten notes about cool roofs in a made-up city, "Velmora"), and scored: did the right part change, did everything else stay exactly as it was, did the Checker look at what changed, did the assistant ask when it should, and what did it cost. It is the before/after measure for changes to how Artifacts understand a message ([Outputs](../../../../docs/OUTPUTS.md)), so every number comes from the same library, the same two documents, the same messages and the same judge. It is a developer tool: nothing in the app or the sidecar uses it.

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

**Time and cost.** Earlier runs on `deepseek/deepseek-flash` at high effort took about 7 minutes and 90 model calls to build the retained deck fixture and about 8 minutes and 45 calls for the report. The scorecard records calls, tokens and time for each run.

The scorecard is `evals/results/<date>-<label>/report.md`, with `raw.json` beside it (every outcome, measure and judgement; written after each case, so a run that is stopped keeps what it has). `python -m evals.artifacts.report FOLDER` writes the scorecard again from `raw.json`; with `--rescore` it first scores every case again from what the run stored (the judge's answers stay as they were), so a fix to the scoring does not cost a new run. The first `raw.json` is then kept as `raw.as-scored.json`.

## Fixtures: one deck and one report, built once

`cases.toml` begins with the two documents the messages are sent to:

- **deck**: slides, for a talk, audience student, 12 slides with 9 different layouts (big numbers, tables, cards, a flow, a quote).
- **report**: an overview for scientists, 5 sections, the third with at least two paragraphs.

Each is built through a conversation `kind="build"` turn with a **fixed outline**, so a case can name a part by what it is about. The outline's `has` words (and `paragraphs`) must be found in the built text, or the document is built again (up to 3 times): they are what the cases rely on. The built version is kept as `evals/results/fixture-<deck|report>-<library>-<model>-<effort>-<hash>.json`, for the library, model, effort and definition it was built with, and is reused until one of them changes.

**Why slide 8 is a table and not a chart.** The deck was first planned with a `bar_chart` on slide 8. The real build failed three times in a row, each time at its last step and after about ten minutes of work ("Chart claims and values must be verified before saving. Its verification is incomplete", then "Numbers with no source"): a chart slide is only saved when the Checker finds nothing at all in it, speaker notes included, and every other slide of the two fixtures has several findings. So slide 8 is a `compare` table, which built in one try. When chart slides can be built, put a `bar_chart` back in the outline and run with `--rebuild-fixtures`.

**Each case starts fresh.** The fixture is saved again as version 1 of a new lineage (`artifact_versions.commit_version(parent_id=None, …)`, with its content, citations, outline, checks, scope, inputs and skill provenance), so no case sees another's changes. A case may first break a word of its copy (`setup`) or pick a sentence from it; that is how "fix the typo" and "change this sentence" get a real typo and a real sentence.

## The cases

About 30, in ten kinds: small edits; finding a part by what it is about, by what the person points at (a selected slide, selected text, the slide on screen) and by the conversation ("shorten slide 7", then "no, the one before it"); structure (insert, delete, move, split, change a layout, change one outline line); changes upstream of the parts ("focus on cost instead", "make it for first-year students", "cut it to six slides"); content the library has and content it lacks (nothing may be invented); the whole document (translate, shorter titles); questions (answered, nothing saved); asking (a vague request must be asked about, a clear one must not, "change the costs slide" when two slides are about cost must).

The fields are listed at the top of `cases.toml`. Parts are counted from 1 in the version the message is sent on. `{typo}`, `{fixed}` and `{quote}` in a case stand for what its setup made. `tests/test_artifact_exam.py` checks that every part a case names is in its fixture and that the plan's kinds of message are all there.

## What is scored

For each case, with the version before the last message and the version it saved:

- **Intent** is ask, answer, make or change, as the case expects (a list means any of them). A question saves no version; a change or a new document does.
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

The exam imports the case's starting document into a conversation as one saved turn. A case with no document starts with an empty conversation. It sends the history and final message to `POST …/artifact-conversations/{id}/messages/stream` as `kind: "message"`, with any focus, the model and the effort under test.

The final `done` event records a question, an answer, or a saved version. The stored outcome includes the version before the last message and the calls, tokens and seconds for that message.

## Tests

`tests/test_artifact_exam.py` covers the case file, the scoring on hand-made before and after documents (including a part that was only renumbered), the matching of parts, the scorecard, the call counter, and runs with stand-in models (fixtures, side-by-side cases, repeats, the cache); it needs no keys.
