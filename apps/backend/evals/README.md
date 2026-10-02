# The Ask exam

A fixed set of 25 questions over a small demo library (ten notes about cool roofs in a made-up city, "Velmora"), asked through the real Ask path with real models and scored. It is the before/after measure for changes to Ask, so every number comes from the same library, the same questions and the same judge. It is a developer tool: nothing in the app or the sidecar uses it.

## Run it

From `apps/backend`:

```
.venv/bin/python -m evals.ask.run --label baseline
```

It needs a model, and for the two web questions a Tavily key (without one those cases are skipped and listed). Give it keys in one of two ways, both gitignored:

- a `.env` at the repo root: `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, `TAVILY_API_KEY`; or
- a copy of the app's `service-settings.json`: `--settings PATH` (or the env var `SERVICE_SETTINGS_FILE`). It carries the models, the job-to-model choices and the Tavily key as saved in Settings.

Useful flags: `--models REF,REF` (default: the Ask job's model), `--writer-effort LEVEL`, `--plan-effort off,low` (default `low`, as in the app), `--check-effort off,low` (default `off`; every combination is run), `--judge REF` (default: the first model; a warning is printed when the judge is also under test), `--only L1,C2`, `--label NAME`, `--reseed`, `--auto-skills` (let Ask pick a skill itself, as `ASK_AUTO_SKILLS=true`; the auto-skill cases SK2 and SK3 are skipped and listed without it), `--repeat N` (run each case N times on the same copy; a case's result is then its pass rate, the scorecard shows means over all runs and lists the cases whose runs disagreed, and the raw results are kept as `L1#1`, `L1#2`; one run is noisy, so use `--repeat 2` or more to compare before and after).

A full run is about 28 questions, 8 of them with earlier messages asked first (47 Ask turns in all); each turn makes roughly 4 to 12 model calls, plus one judge call per scored answer. Expect on the order of 300 to 600 calls and 10 to 30 minutes per configuration, depending on the model. Seeding the library the first time adds a few minutes of reading and indexing.

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
