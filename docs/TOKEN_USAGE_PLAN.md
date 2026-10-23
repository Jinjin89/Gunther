# Token usage — plan

Agreed 2026-10-23. Record how many tokens every model request uses, per model and per
job, and show it in Settings → Usage. Tokens only: no prices, no money.

## 1. Capture — `apps/backend/gunther/llm.py`

- `_stream` asks for counts: `stream_options={"include_usage": True}`. A server that
  refuses it (`BadRequestError`) is asked again without it, like the effort setting,
  and the gateway remembers that model so it is not refused every time.
- `_usage(chunk.usage)` turns each provider's shape into one record:

  | field     | DeepSeek                   | OpenAI / Qwen / Kimi                  |
  |-----------|----------------------------|---------------------------------------|
  | input     | `prompt_tokens`            | `prompt_tokens`                       |
  | cached    | `prompt_cache_hit_tokens`  | `prompt_tokens_details.cached_tokens` (Kimi: top-level `cached_tokens`) |
  | output    | `completion_tokens`        | `completion_tokens`                   |
  | reasoning | `completion_tokens_details.reasoning_tokens` (part of output) | same |

- Every request is one record, retries included (empty-answer retry, JSON repair,
  dropped setting, broken connection). Failed requests are recorded with
  status `error`; a reply without counts is `unreported`. Nothing is estimated.
- The trace keeps the full record too (Developer → trace shows cached and reasoning).

## 2. Who spent it — new `apps/backend/gunther/usage.py`

- `ContextVar` with `job` (analysis, ask, outputs, photos), `detail` (e.g. `slides`,
  `summary`, `topic`, `title`) and an optional `ref` (conversation, artifact or source id).
- `with usage.job("outputs", "slides", ref=artifact_id):` at each place work starts;
  calls with no job are recorded as `other` so nothing is lost.
- `usage.record(...)` is called by the gateway after each request; it writes on a
  short separate DB session and never fails the answer (errors are logged only).

## 3. Storage — migration 29 `model_usage`

`id, at (UTC), provider_id, model_id, job, detail, ref, input, cached, output,
reasoning, status, ms` with an index on `at`. One row per request.

## 4. API — `usage_api.py`

- `GET /usage/summary?range=today|7d|30d|all` → totals, `byDay`, `byModel`, `byJob`.
  `byModel` lists input / cached / output / reasoning / requests for every model.
- `GET /usage/recent?limit=50`
- `DELETE /usage` clears the history.

## 5. Settings → Usage (`apps/desktop/src/usage/`)

New topic after Models. Range buttons; four numbers (Input, Cached with hit %,
Output incl. reasoning, Requests); daily stacked bars; **By model** table (each
model's input, cached, output, reasoning, requests); By job table; Recent requests;
Clear history. Black and white, colour only for the bar series.

## 6. Not now

Speech (seconds / characters), Tavily searches, money.

## 7. Tests (fake client only, no live model)

Usage parsing for DeepSeek and OpenAI shapes; `stream_options` refused then
remembered; retries counted; job tagging; summary API ranges and grouping;
migration; Usage page with seeded data.

## 8. Build notes (for the builder)

Two phases; stop after each one and report.

**P1 backend** — §1–4.
- Model calls all pass `ModelGateway.complete` → `_complete` → `_stream` in `llm.py`.
  Record in `_complete` around each `_stream` attempt (so retries count), including
  the attempt that raises. Keep `trace.update(call, usage=...)` working.
- Job tags: put `usage.job(...)` at the entry points, not at every call. Callers of
  `.complete`/`.complete_json` today: agent.py (ask), artifact_agent.py,
  artifact_steps.py, skill_runner.py, outputs.py, studio/* , slide_review.py
  (outputs), digest.py, extraction.py, lecture.py, library_topics.py, paragraphs.py,
  brief.py (analysis; brief = `brief`), digest photos path (photos), api.py,
  tts_narration.py. An inner `usage.job` overrides only the fields it sets.
- Storage: SQLAlchemy model next to the others in `models.py`, migration
  `Migration(29, "model_usage", ...)` in `migrations.py` (look at 27/28 for the style).
  The gateway must not know about the database: `main.py` gives it a recorder
  callback (default: none, so tests and tools without a DB still work).
- Router `usage_api.py`, registered in `main.py` like `developer_router`.
- Tests in `apps/backend/tests/test_usage.py` with a fake client (see
  `tests/fake_models.py`). Run: `cd apps/backend && UV_OFFLINE=1 uv run pytest
  tests/test_usage.py tests/test_models.py tests/test_migrations.py -q` and
  `UV_OFFLINE=1 uv run ruff check gunther tests`.

**P2 desktop** — §5.
- New `SettingsPaneId` `"usage"` in `components/SettingsLayout.tsx`, wired where the
  panes render (`components/AtlasUtilities.tsx`). Code in `src/usage/`, API calls in
  `src/api.ts` style. Charts: reuse `@visx` already in the bundle.
- Tests: `src/usage/*.test.tsx`. Run: `npm run typecheck -w @gunther/desktop` and
  `npm run test -w @gunther/desktop -- src/usage src/components`.

Rules: no real model or network calls; do not commit; do not edit package.json /
package-lock.json; match the surrounding code's style and comment density.
