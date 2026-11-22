# The Ask exam

A fixed set of 29 questions over a small demo library (ten notes about cool roofs in a made-up city, "Velmora"), asked through the real Ask path with real models and scored. It is the before/after measure for changes to Ask, so every number comes from the same library, the same questions and the same judge. It is a developer tool: nothing in the app or the sidecar uses it.

## Run it

From `apps/backend`:

```
.venv/bin/python -m evals.ask.run --label baseline
```

The exam needs a model. Three cases also need a Tavily key and are listed as skipped when the key is missing. Supply keys through either of these git-ignored paths.

- a `.env` at the repo root: `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, `TAVILY_API_KEY`; or
- a copy of the app's `service-settings.json`: `--settings PATH` (or the env var `SERVICE_SETTINGS_FILE`). It carries the models, the job-to-model choices and the Tavily key as saved in Settings.

Use `--models REF,REF` to compare models or omit it to use the Ask job's model. Use `--writer-effort LEVEL`, `--plan-effort off,low`, and `--check-effort off,low` to choose effort. The exam runs every combination of model, planning effort, and checking effort. The default planning effort is `low` and the default checking effort is `off`. Use `--judge REF` to choose a judge or omit it to use the first model. The report warns when the judge is also under test. `--only L1,C2` selects cases, `--label NAME` names the run, and `--reseed` rebuilds the library template.

Use `--repeat N` to run each case N times on the same copy. The scorecard reports pass rates and lists cases whose results differed. The raw results are kept as `L1#1`, `L1#2`, and so on. Use `--repeat 2` or more to compare before and after.

A full run has 29 questions and 51 Ask turns, including the earlier messages in eight cases. Each turn makes roughly 4 to 12 model calls, with one judge call per scored answer. Expect on the order of 300 to 600 calls and 10 to 30 minutes per configuration, depending on the model. Seeding the library the first time adds a few minutes of reading and indexing.

## Why a template library

The first run reads and indexes the ten notes into `evals/results/template-<hash>/`, with the app's own background worker, so the index and the extracted claims are as in the real app. Every configuration then runs on a fresh copy of that folder, and later runs reuse it while the files in `evals/ask/library/` are unchanged. This keeps before/after numbers about the agent and not about how a note happened to be read. Use `--reseed` to read the library again.

## Reading the scorecard

`evals/results/<date>-<label>/report.md` (and `raw.json`, with every answer, check and judgement). Rows:

- **Cases passed**: every deterministic check passed, the judge found no "not supported" sentence, no unmarked fact, every "must mention" point conveyed and no "must not claim" point stated.
- **Searched correctly**: for questions that must (or must not) look something up, whether the agent did. Claim checks after writing do not count.
- **Citation support**: over all cited sentences, how many the judge found supported, partly supported, or not.
- **Unmarked claims per answer**: facts with neither a source number nor `[?]`.
- **Trap citations**: answers that cited a source they must not (the green-roof review for a question about Velmora's trial).
- **Unverified `[?]` per answer**, **mentions covered**, **mean time, tokens and model calls** (from the answer's trace).

Below the table, each case that failed has one line saying why. The judge is one model call per answer, fixed for the run and recorded in the report.

## The case file

`evals/ask/questions.toml`. Titles in `cite_any` and `must_not_cite` are note titles; `"*"` in `must_not_cite` means no library source at all. `cite_web` asks for at least one web citation; `max_searches = 0` means no limit.

## Tests

`tests/test_ask_exam.py` covers the case file, the scoring and one full run with stand-in models; it needs no keys.

## The Artifacts exam

A second exam, `evals/artifacts/`, sends messages to a slide deck and a report built from this same demo library and scores what changed and what stayed (`python -m evals.artifacts.run --path baseline`). It reuses this exam's library seeding, settings and app creation; see `evals/artifacts/README.md`.

## Comparing runs

The exam posts turns to the [Ask stream](../../../docs/ASK.md#streaming-and-trace) and
reads each saved answer from the terminal event. Use the same library, cases, provider,
effort and judge for comparisons. For document changes, keep the agent settings fixed and
check stored readings and evidence alongside the answer scores. [Library readings](../../../docs/LIBRARY.md#readings-and-provenance)
