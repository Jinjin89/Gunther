# Scientific decks from a fact sheet, made in visible steps

Status: agreed plan, unbuilt. The decisions are in §11. Read [AGENTS.md](../AGENTS.md).
The shipped study workflow and its work actions are in [Outputs](OUTPUTS.md).

## 1. Why

The current scientific build coordinates material reading, study planning, figure reading,
drafting and review from [StudioAgents.make](../apps/backend/gunther/studio/pipeline.py).
Build stages reuse the material prefix and can resume at checkpoints. The study, writer and
check steps still receive source material to work out the facts they need. This plan gives
them one verified fact sheet and makes the agent's action boundaries available for new
messages during a run. It uses the existing Understand, Plan and part writing entry points.

## 2. The target: what a deck is made from

Design from the output down. A finished slide is made of **facts** (each with the exact words of
the paper that state it), arranged by a **slide plan** that says which facts and figure panels
each slide teaches. Every later step works from these two pieces of data, not from the whole
paper.

```
paper passages + figure pixels
      │  S1 read (parallel parts)      S2 figures (vision, parallel)
      ▼
FACT SHEET  ── each fact: what it says, the exact quote, passage number, figure panel, values
      │  S3 study (one call; the only later step that sees the whole paper)
      ▼
STUDY       ── question, method, claims → fact ids, every figure accounted for
      │  S4 plan (from study + facts)  → code checks coverage BEFORE writing
      ▼
SLIDE PLAN  ── per slide: job, claim ids, fact ids, panels, layout, takeaway
      │  S5 write (from each slide's own facts) → code checks numbers, citations, figures
      ▼
SLIDES      ── cite fact ids while written; code turns them into passage numbers
      │  S6 check (one batched call)   S7 review (learner + one review + targeted fixes)
      ▼
DESIGN, LOOK, SAVE (unchanged)
```

### 2.1 The fact sheet

New module `apps/backend/gunther/studio/facts.py`. An invented example:

```json
{
  "material": "9f2c41e07a1b33d0",
  "facts": [
    {
      "id": "F12",
      "kind": "result",
      "says": "Removing factor T shifts progenitors from the red-cell branch toward the white-cell branch.",
      "quote": "loss of T shifted the progenitor population towards the white-cell lineage",
      "ref": 7,
      "figure": {"asset": "ast_3c9e", "panel": "c"},
      "values": [],
      "central": true,
      "verified": true
    },
    {
      "id": "F31",
      "kind": "value",
      "says": "The inferred network reached an AUROC of 0.82 on the held-out benchmark.",
      "quote": "reaching an AUROC of 0.82 on the held-out benchmark",
      "ref": 12,
      "figure": null,
      "values": ["0.82"],
      "central": false,
      "verified": true
    },
    {
      "id": "P3c",
      "kind": "panel",
      "says": "Panel c: arrows show the predicted shift of each cell after removing T.",
      "quote": "",
      "ref": null,
      "figure": {"asset": "ast_3c9e", "panel": "c"},
      "values": [],
      "central": true,
      "verified": true
    }
  ],
  "gaps": ["Figure 4 panel e is not legible; its values are not stated."]
}
```

- `kind`: `background | question | method | result | value | control | interpretation |
  limitation | panel`.
- `quote`: the exact words from passage `ref`. **Code** checks that the quote is in that passage
  (§6.1); a fact whose quote is not found is dropped and its count reported in the stage detail.
  `verified` is code's verdict, never the model's.
- `values`: every number in `says` must appear in `quote`, or in the figure reading's
  `confirmed_values` for that panel. Code checks this too. A number from a passage with an
  `unverified_glyphs` warning is allowed only when a figure reading confirms it (the existing
  rule in `science.value_problems`, now applied once, to facts).
- `panel` facts are made by **code** from `FigureReading.panels` (S2): one per panel, with
  `shows`, axes and legend. They have no quote; their source is the pixels.
- Fact ids are stable: a fact whose `quote` and `ref` are unchanged keeps its id across rebuilds,
  the same way `WorkSteps._keep_cards` keeps card ids.

### 2.2 The study

`science.StudyMap` and `science.CoveragePlan` merge into one `Study`, made by one model call:

```json
{
  "language": "English",
  "title": "(the paper's title)",
  "question": "Can a gene network inferred from single-cell data predict how a cell changes when one factor is removed?",
  "contribution": "...",
  "study_type": "methods",
  "method": [
    {"step": "Infer a gene network for each cell type from single-cell data", "facts": ["F4", "F5"]},
    {"step": "Simulate removing a factor by passing the change through the network", "facts": ["F6", "F7"]}
  ],
  "claims": [
    {"id": "C1", "claim": "...", "kind": "finding", "central": true,
     "facts": ["F12", "F13"], "figures": [{"asset": "ast_3c9e", "panels": ["c", "d"]}],
     "alternatives": ["..."], "limits": ["..."]}
  ],
  "figures": [
    {"asset": "ast_3c9e", "central": true, "disposition": "teach", "panels": ["a", "c", "d"],
     "role": "Shows that the simulation recovers known lineage decisions", "reason": ""}
  ],
  "dependencies": ["C1 establishes the simulation works → C2 asks whether it finds new regulators"],
  "limitations": ["..."],
  "open_questions": ["..."]
}
```

Code checks (replacing `map_problems` and `coverage_problems`): every fact id exists, every
central claim has at least one fact, every figure in `figure_inventory` appears exactly once,
`group`/`omit` carries a reason, and a central figure is not omitted. On a problem: one repair
call, then deliver with the problems listed. A study problem never stops the build.

**The study does not depend on the brief.** It is the paper's own argument. The brief shapes the
plan. So a reworded brief ("for students", "shorter") keeps the facts, figures and study, and
plans again.

### 2.3 The slide plan

Stored in the workspace outline (`artifact_workspace.ItemRec`), so the agent's existing actions
(plan insert, delete, move, write one part) work on it. New fields on `ItemRec`:

```python
class ItemRec(BaseModel):
    ...                               # existing: id, heading, goal, layout, cards, refs, …
    facts: list[str] = []             # fact ids this slide teaches, in order
    claims: list[str] = []            # study claim ids it serves
    panels: list[str] = []            # "ast_3c9e:c" — panels shown or named on this slide
    takeaway: str = ""                # what the audience should leave with
```

Code checks the plan **before anything is written** (§6.2): every central claim is on some slide,
every panel the study marks as taught is on some slide, every fact id exists, and no slide is
empty of facts unless its layout is `title`, `section` or `statement`. Problems go back to the
planner once (a small call, the plan only); what remains is listed and the build goes on.

### 2.4 Slides

A slide is written from its plan item and **only its own facts** (their `says` and `quote`), plus
the plan's titles for transitions. While writing it cites fact ids: `[F12]`. Code then:

- replaces `[F12]` with the passage number `[7]`, so citations are always right by construction;
- checks every number on the slide is in the values or quote of a fact the slide cites;
- checks every image reference is a figure in the material and every crop is a box the reader
  returned (`science.heal_images` already repairs these);
- checks every panel in the item's `panels` is named on the slide or shown as its crop
  (`science.named_panels`).

A slide that fails gets one targeted rewrite (low thinking) with the exact problems; what remains
is listed with the version.

### 2.5 Who decides what

| Decision | Owner |
| --- | --- |
| What a passage says (facts, quotes) | model (S1), quote existence checked by code |
| What a figure shows (panels, boxes, values read) | vision model (S2) |
| The paper's argument, which claims are central, figure dispositions | model (S3) |
| Which slides, in what order, teaching which facts | model (S4) |
| Wording of each slide | model (S5) |
| Citations, numbers, image refs, crops, panel coverage, scope | **code**, always |
| Whether a slide's wording is faithful to its facts | model (S6), one batched call |
| Whether a reader can learn the study from the slides | model (S7) |
| What is delivered | **code**: always the deck, with open problems listed |

## 3. The steps

| Step | Model input | Thinking | Calls (15 slides, 4 figures, 100k-token paper) |
| --- | --- | --- | --- |
| S0 purpose | brief + overview (unchanged) | low | 1 |
| S1 read → facts | one part of the paper each (~25k tokens) | low | 4 (parallel) |
| S2 figures → readings | pixels + caption (unchanged) | vision default | 4 (parallel) |
| S3 study | **whole paper once** + fact sheet | job effort | 1 (+1 repair) |
| S4 plan | study + fact sheet | job effort | 1 (+1 fix) |
| S5 write | each batch's facts + whole plan | job effort | 3–4 (parallel batches) |
| S6 faithfulness check | sentences + their facts' quotes | low | 1 |
| S7 review | learner: slide text only; review: study + learner + deck | low | 2 + fixes (≤4, parallel) + 1 re-check |
| S8 design, look | unchanged | low | 15 + look |

Estimated input for a 100k-token paper: about 0.4M tokens instead of about 2.3M. The writing
steps think at the user's chosen effort; every check, fix, review and design thinks low (as
since 2026-10-30). The shared prompt prefix for S4–S7 is the fact sheet + study (not the paper),
so it is small and cached.

**S1 read.** Replaces `StudioAgents.read` (notes) and the "whole material when it fits" path:
every paper, short or long, becomes a fact sheet. Split into parts by `PART_TOKENS` as today; each
part returns `PartFacts` (facts with quotes). The prompt asks for the facts a teacher of this
paper needs (question, method steps, setups, comparisons, controls, results with units and
conditions, limitations), not every sentence; at most about 40 per part. Merged by code, ids
assigned by code, duplicates (same quote) folded.

**S2 figures.** `read_figures` unchanged. Code adds one `panel` fact per readable panel.

**S3 study.** One call replaces `study_map` + `coverage_plan` (two high-thinking calls today).
This is the only later step that sees the whole paper: understanding the argument needs it. Its
output cites fact ids, not passage numbers.

**S4 plan.** Replaces `storyline` + its critic (today a full scientific review of the storyline,
with the whole paper). The critic's job, "did the plan cover the paper", becomes the code
coverage check in §2.3, which is instant and cannot loop.

**S5 write.** Replaces `write_deck` + `fix_format` for scientific decks. Slides are written in
batches of about four, in parallel, each batch seeing the whole plan (titles, takeaways) for
transitions and only its own facts in full. Format repair (`fix_format`) runs per slide as today.

**S6 check.** Replaces the per-slide claim checks (`verify` → `_claims` → `check_sentences`, one
call per slide with the whole paper). Numbers and citations are already code-checked in S5. One
call receives every slide's sentences with the quotes of the facts each one cites, and returns
the sentences that say more than their facts (overclaiming, causation from association, a wrong
direction). Those slides get one targeted fix. Background the paper does not contain stays
allowed, marked `[?]`, as the truth rule says today.

**S7 review.** Keeps the spec's two roles, cheaper:
- the independent learner reads **only the slides' text** (title, body, captions; no notes, no
  paper), low thinking. Rendering slides to images for it is dropped: the `look` step already
  inspects the rendered slides (decision Q3);
- one review compares the learner's answers with the study and coverage, and names problems by
  slide. It sees the study and fact sheet, not the whole paper;
- targeted fixes per named slide (parallel, low), one re-check of only those problems (low), then
  deliver what is still open as open problems. This is the round structure in place since
  2026-10-30.

**S8 design, look, assemble.** Unchanged.

## 4. The agent works in visible steps

### 4.1 Steps become actions

The studio's `make` stops being one opaque action. The steps above map onto the actions the
orchestrator already knows (`artifact_actions.Act.do`) and the step code it already calls
(`artifact_steps.WorkSteps`):

| Action | Studio step | Workspace field it fills |
| --- | --- | --- |
| understand | S0–S3: purpose, facts, figures, study | `Work.facts` (new), `Work.study` (new), `Work.approach` |
| plan whole | S4 | `Work.outline` (items carry facts, claims, panels) |
| write parts | S5 (+ S6 for the written parts) | `Work.parts` |
| check parts | S6 | `PartRec.verdicts` |
| review | S7 | parts rewritten, open problems in `Work.skipped` |
| make | runs understand → plan whole → write all → review as **separate actions** in the same turn | all of the above |

`WorkSteps.understand` and `plan_whole` already call Studio's `understand_work` and
`replan_work`. Extend those entry points to share the proposed fact sheet and slide plan.
Make `_do_make` queue the four actions as a macro. Each action emits its event, is stored,
and provides a step boundary (see §4.2). A changed brief reruns only the parts that depend
on it. `ArtifactAgent._cost` accounts for the stages and checks the turn budget at each
boundary. [Work steps](../apps/backend/gunther/artifact_steps.py) · [Studio work](../apps/backend/gunther/studio/work.py) · [Action handlers](../apps/backend/gunther/artifact_actions.py)

### 4.2 Step boundaries

After every action, the loop in `ArtifactAgent._loop` already decides again. Three things are
added at each boundary:

1. the workspace is stored (`_store`), so a Stop or a failure keeps every finished step;
2. the **inbox** is read: messages the user sent while the turn was running (§4.3);
3. if there is a new message, the next decision sees it as "New message while you were working"
   and must answer it before going on.

### 4.3 Messages while working

Today the composer turns into a Stop button while a turn runs, so the user can only stop. New:

- The composer stays usable during a run; Send and Stop sit side by side. A message sent during a
  run goes to `POST /api/knowledge-bases/{kb}/artifact-conversations/{id}/inbox`
  (add the route in `artifact_conversations_api.py` and the write in `artifact_turns.py`), stored as a user message marked `while_working`.
- The running turn reads the inbox at the next step boundary (never mid-step).
- New non-terminal action `say` (`Act.do = "say"`, field `text`): a short message to the user
  that does not end the turn, emitted as a `reply` event and stored as part of the turn. The
  orchestrator must `say` something in answer to an inbox message (code enforces it: a decision
  that sees an unanswered inbox message and contains no `say` or terminal action is sent back
  once, the way `validate` already returns problems).
- What the message changes goes through the brief (`decision.brief`), so the digests mark only
  what depends on it out of date (a new audience → plan and slides; a new focus on the method →
  plan; "stop doing X" → a constraint).

### 4.4 After a Stop or a pause

- A message after a Stop is a new turn. Its first decision must `say` an answer to it (the same
  rule as §4.3), then go on from the first out-of-date step. Nothing still valid is redone: the
  stored work digests select the next step.
- "Continue" resumes from the first out-of-date step.
- The digests of the new fields: facts ← the pool's passages; study ← facts (not the brief);
  outline ← study + the brief's goal, audience, length and use; a part ← its item, its facts'
  digest and the brief; a check ← the part's text. These replace the `resume` dictionary of
  `StudioAgents.make` (`studio_progress`), which disappears.

## 5. What stays as it is

- Source scope, the evidence pool and its numbering (`Numbering`, `version_map`).
- Figure provenance: crops only from boxes the reader returned; the whole figure on an
  orientation slide; cover metadata only from the source (`apply_source_cover`).
- Extraction warnings: an unreadable value is never stated (now enforced on facts, §2.1).
- Edits after a save: `write` with an instruction, `edit`, `redesign`, `plan` insert/delete/move.
  A rewritten slide is written from its item's facts and checked by S5's code checks and S6.
- Non-scientific studio decks and reports keep today's path in this round (Q1).
- Live progress events (`stage` with `detail`), the activity pill, and "deliver, never discard"
  from the 2026-10-30 round.

## 6. Details that are easy to get wrong

### 6.1 Finding a quote in its passage

PDF text differs from what a model copies. Before comparing, normalise both sides: Unicode NFKC;
ligatures (ﬁ → fi); all dashes and minus signs → `-`; curly → straight quotes; collapse
whitespace; join words hyphenated across a line break (`regu- lation` → `regulation`); casefold.
A quote matches when its normalised text is a substring of the normalised passage. A quote
shorter than 4 words, or containing `�`, cannot verify a value. Do not use fuzzy matching:
a quote that is not found is not a quote. Count dropped facts per part and show them in the
stage detail ("212 facts, 9 dropped: quote not found").

### 6.2 Coverage before writing

The check in §2.3 uses the study's `figures[].panels` (what should be taught) and the items'
`panels` (where). It runs on the plan, so fixing it costs a small plan call, not a slide rewrite.
After writing, S5 checks the slide actually names or shows its panels (`science.named_panels`,
`_same_box`). Today's `content_problems` becomes these two checks.

### 6.3 Fact-id citations

The writer cites `[F12]`, `[P3c]`. Code maps each to its passage `[ref]`; a panel fact maps to
the figure's own passage (the passage the figure belongs to, `figures_of`). An unknown id is a
problem for S5's fix, never silently dropped. `[?]` stays as is. Write this mapping in one
function (`facts.cite`) and test it alone.

### 6.4 Prompt cache

Keep one shared prefix per build for S4–S7: `STUDIO_BASE` + fact sheet + study, then the task.
Put the slide-specific part last. Use fake provider requests to verify that S4–S7 share
the prefix. [Prompt cache tests](../apps/backend/tests/test_prompt_cache.py)

### 6.5 Size

A long paper can produce hundreds of facts. The plan and the writers need only the ones they use:
S4 sees all facts as one line each (`id kind says`, no quote); S5 sees its batch's facts in full;
S6 sees only cited facts.

### 6.6 Old works

Works made before this change have no `facts`/`study`. They keep working for edits through the
current path; a remake builds the fact sheet. Do not migrate stored workspaces.

## 7. Files

| File | Change |
| --- | --- |
| `apps/backend/gunther/studio/facts.py` (new) | `Fact`, `FactSheet`, `PartFacts`, `normalise`, `verify_quote`, `panel_facts`, `merge`, `cite`, `values_ok` |
| `apps/backend/gunther/studio/science.py` | `Study` (merges `StudyMap` + `CoveragePlan`), `study_problems`, `plan_problems`; `content_problems` split into plan and slide checks |
| `apps/backend/gunther/studio/prompts.py` | `FACTS`, `STUDY`, `SLIDE_PLAN`, `WRITE_FROM_FACTS`, `FAITHFUL`, `LEARNER` (text-only), `REVIEW` (study-based); retire `STUDY_MAP`, `COVERAGE_PLAN`, `STUDY_STORY`, `STUDY_WRITE` and the scientific use of `CRITIC` once replaced |
| `studio/pipeline.py`, `studio/material.py`, `studio/study.py`, `studio/drafting.py`, `studio/verification.py` | expose the scientific stages as callable steps and replace their build resume data with stored work digests |
| `apps/backend/gunther/studio/work.py` | `StudioWorkAgents.write_part` writes from the item's facts; `load_built` carries facts, study and item fields |
| `apps/backend/gunther/artifact_workspace.py` | `Work.facts`, `Work.study`; `ItemRec.facts/claims/panels/takeaway`; digests for facts and study |
| `apps/backend/gunther/artifact_steps.py` | extend Understand, whole Replan, part writing and Review with facts, the slide plan and S7 |
| `artifact_agent.py`, `artifact_actions.py` | `make` as a macro of actions, `say` action, inbox reads at boundaries, orchestrator prompt and `_cost` |
| `artifact_turns.py`, `artifact_conversations_api.py`, `schemas.py` | inbox endpoint and storage; message kind `while_working` |
| `apps/desktop/src/outputs/{ArtifactComposer.tsx,OutputsPage.tsx,liveOutput.ts,ArtifactMessages.tsx}` | Send during a run; show `say` replies in the live turn |
| `packages/contracts` | event and message types for `say` and the inbox |

## 8. Phases

Each phase: one Sonnet subagent, editing `main` directly (no worktree), fake-model tests only (no
real model calls, as the user asked), then Opus review against §10. A phase is done when its
tests pass, `ruff check` and `npm run typecheck` are clean, and the full backend suite passes.

**P1 Fact sheet.** `studio/facts.py` with models, normalisation, quote verification, value check,
panel facts, merge with stable ids, fact-id citation mapping. Prompt `FACTS`. `StudioAgents.read`
returns a `FactSheet` for scientific decks. Tests: quote normalisation table (ligatures, dashes,
hyphenation, CJK, `�`); a fabricated quote is dropped; a number not in its quote is dropped;
an `unverified_glyphs` number survives only with a confirming figure reading; ids are stable
across two runs; `cite` maps `[F12]` and `[P3c]`.

**P2 Study.** `science.Study` and `study_problems`; prompt `STUDY`; one call with the whole paper
and the fact sheet; one repair. Tests: unknown fact id, a figure missing or listed twice, an
omitted central figure, a missing reason; each repaired once, then delivered listed.

**P3 Slide plan.** Prompt `SLIDE_PLAN`; plan from study + facts; `plan_problems` (central claims,
taught panels, fact ids, empty slides); one fix; items carry facts, claims, panels, takeaway.
Tests: a central claim missing from the plan is sent back with that problem; a taught panel on
no slide likewise; a reworded brief replans without re-reading facts or study.

**P4 Write from facts.** Prompt `WRITE_FROM_FACTS`; parallel batches; fact-id citations mapped;
code checks of numbers, citations, image refs, crops and panels per slide; one targeted fix; S6
faithfulness check as one batched call. Remove per-slide `verify` for scientific decks. Tests: a
number not in the slide's facts is caught and fixed or listed; an unknown fact id; a panel not
named; the S6 call is one request whatever the slide count; no request in S4–S7 contains the
whole paper (assert on the ledger).

**P5 Review.** Text-only learner; study-based review; targeted fixes; one re-check; open problems
delivered. Tests: the learner request contains no paper text and no notes; a problem fixed is not
listed; one still open is listed; review status values unchanged (`ready`, `open_problems`,
`unavailable`).

**P6 Steps as actions.** Extend Understand and whole Replan. Make `make` a macro of separate
actions. Add digests for facts and study, update the orchestrator prompt, and check the budget
at each step boundary.
Tests (through the API, fake models): a new deck shows four separate action events; a Stop after
planning keeps the plan and the next turn writes without re-reading; a reworded audience replans
and rewrites but keeps facts, figures and study; a failed write keeps the plan.

**P7 Messages while working.** Inbox endpoint and storage; read at boundaries; `say` action and
its enforcement; composer Send during a run; live turn shows `say`. Tests: a message sent during
a fake run is answered with a `say` before the next step; it updates the brief and only the
dependent steps run again; after a Stop, the first decision of the next turn answers the message;
desktop test for Send next to Stop.

**P8 Measure and tidy.** Adapt the ledger to the new steps and record the before/after table in
this doc (calls, input size, shared prefix, thinking levels, by step). Remove dead code: the
scientific `resume`/`checkpoint`, `STUDY_MAP`, `COVERAGE_PLAN`, `STUDY_STORY`, `STUDY_WRITE`,
the scientific critic. Update the [Outputs guide](OUTPUTS.md) when this workflow ships.

## 9. What is not in this round

- Reports and general (non-scientific) decks keep their path; the fact sheet is designed so they
  can move to it later (Q1).
- Real-model quality evaluation: the user runs it on the Mac. This plan measures calls, sizes and
  prefixes with fake models only.
- Showing the fact sheet in the UI (for example in Contents): later, if the user wants it.

## 10. Review checklist (Opus, after each phase)

- Does every number on every slide trace to a verified fact, by code?
- Does any request after S3 contain the whole paper? (It must not.)
- Is anything fatal? (Nothing may discard a written deck.)
- Does a check that finds a problem lead to at most one targeted fix, then a listed problem?
- Is every step reachable as its own action, with its own events and progress detail?
- Are digests right: does a brief change redo only plan and slides, a pool change redo facts?
- Are ids stable across rebuilds (facts, items)?
- Thinking: only S3, S4, S5 at the job effort; everything else low.
- Tests: fake models only; each phase's listed tests present and meaningful.
- Plain names, comments in the surrounding style, no dead code left behind.

## 11. Decisions

Locked:
- Both parts, plan first (user, 2026-10-30).
- Always deliver; checks list problems, never discard (2026-10-30 round).
- Only content-writing steps think at the job effort; checks, fixes, reviews, design think low.
- Code owns citations, numbers, image refs, crops, panel coverage and scope.
- The study is brief-independent; the plan is not.
- No real-model runs in building or review; Flash only if that ever changes.

Answered by the user (2026-11-02, one at a time):
- **Q1 Scope.** Journal-club (scientific) decks first; reports and general decks keep their path
  and move in a later round.
- **Q2 Messages while working.** Yes: the composer stays usable during a run, Send beside Stop;
  the agent reads the message at the next step boundary, answers it, and changes course (§4.3).
- **Q3 The independent learner.** Reads the slides' text only; `look` keeps inspecting the
  rendered slides.
- **Q4 Writing.** Parallel batches of about four slides, each seeing the whole plan.
