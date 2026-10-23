# Prompt cache — plan

Agreed 2026-10-23. Quality first: no step is merged, removed or made to see less. Only the
ORDER of what each call reads changes, so separate calls share one cached opening.

## Why

Settings → Usage shows ~12% cache hits. DeepSeek, Qwen, Kimi and OpenAI bill a request's
opening at ~1/10 when it matches an earlier request exactly; the match ends at the first
difference. Today every studio step starts with its own rules (+ brief, memo, JSON schema)
in the system message and puts the sources (10k–45k tokens) after it, so the same sources
are paid in full at every step.

## The rule

Every studio call that reads the material is built as:

```
system:  STUDIO_BASE — Gunther's shared studio rules (role, P.TRUTH, citation format,
         language). One text, identical for every step and every build.
user:    "The material:\n\n" + material      ← identical across the steps of one build
         "\n\n── Your task now ──\n\n"
         + this step's rules + brief/audience + memo/story/draft + JSON schema
```

Nothing before "Your task now" may depend on the step, the attempt, or the time.

## Changes

1. **`llm.py`** — `complete_json(..., schema_at="system" | "end")`. `"end"` appends the
   schema instruction to the end of the prompt instead of the system message. Default stays
   `"system"` so other callers do not change.
2. **`studio/prompts.py`** — add `STUDIO_BASE` (role + `TRUTH` + citation format + voice
   that every step shares today). Each step's constant (MEMO, DECK_STORY, REPORT_STORY,
   CRITIC, STORY_AGAIN, DECK_WRITE, REPORT_WRITE, FIX_REPORT, FIX_SLIDES, FIX_CLAIMS, COVER)
   becomes that step's task text only. **Do not reword rules** — move them; if a sentence
   was shared, it goes to STUDIO_BASE once; if step-specific, it stays in the task.
3. **`studio/pipeline.py`** — one helper builds `(system, prompt)` for a step from
   `(material, task)`; memo, storyline, critic, story-again, writing, report/deck repairs
   and the claim checker use it. Material order is fixed once per build (already sorted at
   P:549 — keep it) and the same `material` string is reused by every step.
4. **The critic gets the full material** (today it sees only the storyline). Quality gain:
   it can check the story against the sources. Cost is small because the material is cached.
   Same for any judging step that today sees less than the material (list them in the report).
5. **Small steps stay small**: per-slide design (`studio/design.py`), look (`studio/look.py`),
   one-slide format fix — no material added. Only make their *shared* part come first
   (designer rules + style CSS + outline before the slide number).
6. **Warm, then fan out**: where parallel workers share an opening (per-part verification,
   per-slide design), run the first item alone, then the rest in parallel, so the others hit
   the cache. Skip when there is only one item.

Not in this round: Ask (`agent.py`), artifact agent, skill runner.

## Tests (free, fake model)

`tests/test_prompt_cache.py`, using the existing fake gateway: build one report and one deck
through `StudioAgents.make`, capture every request, and assert
- all material-reading calls share the same system message and the same user-message
  prefix up to "── Your task now ──";
- the critic's request contains the material;
- design/look calls do not contain the material;
- `complete_json(schema_at="end")` puts the schema after the prompt, system unchanged;
- warm-then-fan-out: first worker request completes before the others start.
Plus the existing studio tests must still pass.

## Quality check (needs a DeepSeek key — none on this box now)

Flash only. Two builds before (code at a77fe24) and two after, same sets:
`.venv/bin/python -m evals.quality.run --sets velmora,rag-papers --methods studio --kinds deck,report --model deepseek/deepseek-flash --judge deepseek/deepseek-flash --label cache-before|cache-after`
Ship only if the judge scores are not lower; report the cache rate from model_usage
(target 60–75% on builds) and total tokens.
