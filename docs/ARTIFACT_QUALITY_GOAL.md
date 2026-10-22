# Artifact quality goal: reports and slides a professional would ship

Written 2026-10-22. Status: plan, waiting for the user's decisions in §8. Nothing is built yet.

## 1. The goal

**A Gunther report or deck, made from the user's own material, is something a demanding
professional would send to a client or show to a board after at most light edits.**

"Light edits" means: fixing a word, swapping a photo, nudging a number format. Not rewriting
titles, not restructuring the story, not fixing broken layouts.

### The bar (all must hold on the gold set in §6)

| Measure | Target |
| --- | --- |
| Ship rate: slides / sections a blind judge rates "ship as is" or "one-minute edit" | ≥ 80% |
| Head-to-head against the best outside tool on the same material (Claude.ai, Gamma, ChatGPT) | Gunther wins or ties ≥ 60% |
| Titles-only test: a fresh reader retells the core message from the titles alone | ≥ 90% of decks |
| Hard defects (clipped or overflowing text, type under 18 px at 1280×720, an empty half-slide, a made-up number, a topic-word title, disclaimer/meta text on a slide) | 0 |
| Every number and factual claim traceable to a source passage | 100% (unchanged from today) |
| Time with the writing model: deck ≤ 4 min, report ≤ 6 min | met |

## 2. What is wrong today (the honest diagnosis)

I read the whole chain: `skill_runner.py` (2,300 lines), `artifact_steps.py`, the skill files
in `gunther/skills/{report,slides}`, `slide_scene.py`, `slide_review.py`, and the rendered
validation images in `artifacts/validation/`. Eight plan documents (`PRESENTATION_*`) were
layered on top of each other in October. The problems are not in one place; they are in the
method.

### D1. Fear outranks craft — this is why content sounds "harmless and stupid"

The prompts spend most of their words on what the model must not do: "do not imply ranking",
"design fields are not evidence", "do not assert how an audience behaves", "missing evidence
does not establish a negative result", "must not add factual claims even with an existing
citation", "these visual devices may organize information without representing a new fact".
`visual-design.md` is almost entirely prohibitions. The `DESIGN` prompt has ten "do not"s.

A model given that much warning takes the safest path: hedge everything, claim nothing, and
write meta-commentary about the evidence instead of the point. The rendered validation slides
show exactly this:

> **The pilot measured time; quality remains unknown**
> "In this synthetic intake pilot… These are training data, not actual organizational results."
> "The sample does not establish an effect in other teams."

> **复核流程与样例结果需要一起阅读** ("The review process and sample results must be read together")
> "该数据不代表实际绩效" ("this data does not represent actual performance")

Those titles say nothing. A consultant's title for the same material would be: **"The pilot
cut review time from 40 to 16 hours — quality was not measured"**, with the caveat as one small
footnote line.

The writer is writing for the checker, not for the audience. Truth-checking is good; making the
writer afraid is not. Those are two different jobs and today they are mixed in every prompt.

### D2. Many small, blind steps; nobody owns "is this great?"

Slides run 11 steps: understand → structure → outline_review → design → write (one slide at a
time) → compose (an LLM builds a row/column tree) → edit_review → reader_test → revise →
visual_review. Each step returns a small JSON object and sees only a slice. The understand step
runs at effort "low" and sees 40 passages cut to 110 characters. Each slide is written alone, so
the deck has no voice and repeats itself. Each later step is told to change as little as possible.
No step is ever asked: *is this good enough to present?*

### D3. It never looks for the insight

The method goes material → cards → outline → layouts. It never asks the questions a senior
writer asks first: *What is surprising here? What would this audience not know? What is the
so-what? What decision does this change?* Without that, the outline is a summary of the
material, and the deck is a summary in boxes.

### D4. The model is doing the designer's job

Visual quality is left to the model: it picks row/column weights 1–4, sizes, surfaces and gaps
(`scene.md`). Models are bad typesetters. Result: tiny type, one narrow column and an empty half
(see `artifacts/validation/presentation-batches-3-5/dense-contact.png`), charts squeezed into
corners. Professional decks come from a small set of **designed master layouts with slots**;
the author chooses a layout and fills slots; the template guarantees the look.

### D5. The tests measure obedience, not quality

Checks count citations, density, layout runs and fit scores (a slide scoring "0.99" in the
images above is still a bad slide). The fixtures are synthetic packets whose text itself says
"these are training data, not actual results", which teaches the disclaimer habit. Nothing
compares the output with a good human or a good competitor.

### D6. The model is too weak for the writing

The default Outputs model is DeepSeek Flash; several steps run at "low" effort. No prompt fixes
that. Insight-finding and storyline writing need a frontier model.

### D7. The report is a Markdown page

No executive summary block, no numbered exhibits (charts/tables with a source line), no callouts,
no real typesetting, no DOCX. No editable PPTX for decks. A "million-dollar" product needs both.

## 3. Principles for the rebuild

1. **Write for the audience; check for the truth.** The writer gets craft guidance and one rule:
   every fact and number carries its source number. A separate verifier checks claims afterwards
   and fixes only the failing claim. Caveats appear where professionals put them: a footnote line,
   the speaker notes, or one "what we don't know yet" slide/section when it changes the decision.
   Never "this data does not represent…" on the page.
2. **Insight before structure.** First find the 3–5 things worth saying, each with its so-what.
3. **The storyline is the product.** Titles alone must tell the story (pyramid principle, SCQA
   opening). Iterate the titles with a critic before writing any body text.
4. **Write the whole thing in one go, then polish.** One strong call with full context drafts
   the whole deck or report; polish passes edit specific parts.
5. **We design; the model fills slots.** A designed layout library with typed slots and character
   budgets. Code enforces fit; the model is told "too long by 23 characters" and shortens.
6. **Look at the result.** Render every slide, show the images to a vision model with a
   rubric, fix what it finds, once.
7. **Few strong calls beat many weak ones.** About 6–8 model calls per deck, not 40.
8. **Measure what a user feels.** Blind judging against gold references and competitors on
   real material, every phase.

These are general capabilities. No rule refers to a specific document, publisher or example
(AGENTS.md).

## 4. Target pipeline

The agent and workspace from `ARTIFACT_AGENT_PLAN.md` stay: they route a conversation to steps
and track what depends on what. What changes is the steps themselves.

```
brief ─▶ insight memo ─▶ storyline ─▶ draft ─▶ verify ─▶ render + look ─▶ export
          (research)      (titles,      (whole     (claims     (vision        (viewer,
                          critic loop)  artifact)  vs sources) critic, 1 fix) PDF/PPTX/DOCX)
```

### 4.1 Brief
Who is it for, what should they understand or decide, how long (minutes or pages), talk or
read. The agent asks at most two questions, and only when audience or purpose is truly missing.

### 4.2 Insight memo (research)
Input: the selected material in full (long context; large sources chunked by their own
structure, not 110-character glances). Output, shown to the user and editable:
- **The answer** in one sentence.
- **Key facts table**: number, unit, what it is compared with, source id.
- **3–7 insights**: each a specific, non-obvious statement + why it matters to this audience
  + evidence ids. A critic rejects insights the audience would already know or that contain no
  specific (a number, a name, a comparison).
- **Tensions**: where sources disagree or a key thing is unmeasured (this is where the caveats
  live, once, not on every slide).

### 4.3 Storyline
Deck: the "ghost deck", one action title per slide (a full sentence with the point, usually with
a number), plus the slide's job and its intended layout. Report: executive summary + section claim
headings + planned exhibits. SCQA or answer-first opening chosen by purpose.
Critic loop, at most two rounds: a fresh "audience" model reads titles only and retells the story;
a "senior partner" model checks: each title is a claim, no two titles say the same thing, no
gaps in the argument, the most important point gets the most space, length fits the brief.

### 4.4 Draft
One call (two for long reports) writes everything with the storyline, memo and sources in view.
- Deck: typed JSON per slide — `layout` id, slot contents, citations, speaker notes (talk:
  what to say, 3–6 sentences). Schema validation + character budgets per slot; overflow returns
  a precise "shorten slot X by N characters" request.
- Report: Markdown with an exhibit spec (chart/table data with source ids), callouts and
  footnotes.

### 4.5 Verify
The existing claim checker, run after drafting, claim by claim. A failing claim is fixed in place
(re-cite, soften the specific claim, or drop it); everything else is untouched. Chart values must
match source numbers exactly. Unsourced model knowledge keeps the `[?]` mark.

### 4.6 Render and look
Render each slide (and report pages) in headless Chromium. Code checks hard defects (overflow,
min font size, empty regions). A vision model scores each slide on the rubric in §6 and proposes
fixes (change layout, cut text, split slide). One fix round, then show the result.

### 4.7 Export
In-app viewer and Present (exists), PDF (exists), **native editable PPTX** (charts as real
PowerPoint charts, text in real text boxes), **DOCX** for reports.

## 5. The design system (slides and report)

Built by us, once, to a professional standard — black/white plus one accent, per the user's
taste. 16:9, 1280×720 design canvas, 12-column grid, a strict type scale (title 40, body 24,
footnote 14 at the design size; nothing under 18 on the visible body).

**About 20 slide layouts, each with typed slots and budgets**, for example:
title · section divider · executive summary (answer + 3 findings) · one big number + context ·
2–4 KPI stats · chart + takeaway (bar, column, line, stacked, scatter — data-driven) · table with
a highlighted row · two-column compare · before/after · process 3–6 steps · timeline · 2×2
matrix · quote · image + caption · full-bleed image + title · agenda · recommendation + next
steps · "the question" · appendix/source list.

Each layout is a React component in the viewer **and** a PPTX builder function, from one spec,
so the screen, PDF and PPTX match. Charts: a real chart library in the viewer, native charts in
PPTX. Every slide has a small source line at the bottom (where professionals put citations).

**Report look**: cover, executive summary box, claim headings, numbered exhibits
("Exhibit 3 — Cooling savings by district", with a source line), callouts, footnotes,
recommendations, an appendix "Sources and method". Typeset for screen and print; PDF and DOCX.

Theme choice stays (three themes), but every theme is designed and tested on every layout.

## 5b. The rest of the journey (end to end)

The pipeline above is the engine. These parts make it a product, from "I have material" to
"I presented it / sent it".

**Material in.** Output quality is capped by what was extracted. Before creation, check the
selected sources: tables kept as tables (with numbers and units), figures kept with their
captions, headers/footers and references separated, extraction gaps reported. The insight
memo states which sources were thin or unreadable.

**Visuals.** Three kinds, never mixed up: (1) charts drawn from cited source numbers;
(2) the source's own figures, reused with a caption and source line; (3) neutral decoration
(icons, simple shapes) that carries no fact. No AI-generated images that could look like
evidence.

**The user's flow in the app.**
1. Pick material and say what it is for. The agent asks at most two questions.
2. See the insight memo: answer, key facts, insights, gaps. Edit or say "go".
3. See the storyline as titles (a ghost deck or a list of section claims). Reorder, rewrite,
   add or drop items, then continue. Saying "just do it" skips steps 2–3.
4. Watch progress live; see slides appear as they are drawn.
5. Review the result: viewer, slide sorter, speaker notes, a quality report (what the checker
   and the vision critic found, and what is still open).
6. Change things by talking ("make slide 4 a chart", "shorter for a 5-minute talk",
   "add the cost risk") or by editing text directly. Only the target changes. Each save is a
   version.
7. Present, or export PPTX, PDF or DOCX.

**Your own look.** Upload an existing .pptx or .potx template: colours, fonts and logo are read
from it and applied to the layouts. Default themes stay for people without one.

**Languages.** Chinese and English both work fully: CJK line breaking, fonts bundled and
embedded in PPTX/PDF, and the output follows the language of the brief.

**Robustness.** Each step saves its result, so a failure resumes from that step instead of
starting over. There are clear errors with Retry (no fake stand-in output). A cost and time
budget runs per artifact, and the time and cost are shown in the result. Without a capable
model key, the user is told which model is needed, rather than given a weak result.

**Old artifacts.** Existing reports and decks still open read-only in the old viewer. "Remake
with the new method" rebuilds one from the same material.

**Shipping.** Fonts, the PPTX/DOCX builders and the renderer are packaged into the macOS app
(sidecar), and smoke-tested on a .deb here. Then the user's Mac trial on their own material.
Done means the user says "I would send this".

## 6. How we measure (built first, used every phase)

A repo command, never app UI: `python -m evals.quality.run`.

- **Gold set: 6 real material sets**, not synthetic: (1) 3–5 research papers on one topic,
  (2) a public company annual report, (3) a government/NGO policy report, (4) a long meeting or
  lecture transcript from Gunther capture, (5) a Chinese-language source set, (6) a mixed set with
  web pages and figures. Each with two briefs (talk deck, written report).
- **References**: for each set, the same brief run through Claude.ai and one other tool (the user
  makes these on their Mac, or I do where a tool is reachable), saved as PDFs.
- **Judge**: a strong vision model, blind and order-swapped, rates each slide/section and the
  whole artifact on: insight, storyline, title quality, evidence use, clarity/density, visual
  quality (on renders), correctness (from the checker), and "would you ship it". Pairwise
  head-to-head against the references.
- **Hard-defect linter** in code (the list in §1).
- Results land in `apps/backend/evals/results/<date>-quality-*` with renders, so the user can
  flip through them.

## 7. Phases

Each phase ends with the eval run and a short note in this document. Built per the usual way:
Opus writes the detailed contract for a phase, a Sonnet agent builds it, Opus reviews.

| Phase | What | Done when |
| --- | --- | --- |
| 0. Bar and baseline | Gold set, judge, linter, references; run today's pipeline | Baseline scores recorded with renders |
| 1. Writing reset | New English skill files: insight memo, storyline, draft (craft-first, short rule list); writer/verifier split; strong writing model | Content-only (Markdown) judge scores beat baseline clearly; zero disclaimer text |
| 2. Slide design system | ~20 layouts × 3 themes, slot schema, fit validator, chart rendering, layout gallery page in the dev host | Gallery renders with zero hard defects at short/long/CJK content |
| 3. Deck pipeline | Draft → typed slots → verify → render + vision critic → fix; native PPTX export | Deck ship rate ≥ 70%; PPTX opens cleanly in PowerPoint/Keynote |
| 4. Report | Executive summary, exhibits, callouts, typeset view, PDF/DOCX | Report ship rate ≥ 70% |
| 5. App flow and editing | Memo and storyline review screens, live progress, quality report, edits by talking or typing routed to a slide/slot or section, versions | Small edits touch only the target; no regressions on eval; browser smoke covers create → edit → export |
| 6. Material, look, languages | Extraction check before creation, own .pptx template import, full Chinese support with embedded fonts | Chinese gold set passes the bar; a template deck matches its source look |
| 7. Robustness and shipping | Resume from the last step, budgets, clear errors, old artifacts open, packaged in the Mac app | .deb smoke passes; a forced failure resumes |
| 8. Push to the bar | Tune with the eval until every §1 target holds; Mac trial | §1 table all green and the user says "I would send this" |

**Removed** once phase 3 replaces them (all still in git history): the `compose` step and
`scene.md`, `slide_scene.py`, the `design` and `outline_review` steps, the layout-only
`visual_review`, `visual-design.md`, the `DESIGN`/`KEEP` prompt blocks, per-slide writing, and
the synthetic fixtures in `evals/slides`. **Kept**: source pool and citation numbers, `[?]` marks,
the claim checker, the artifact agent and workspace, versions, the viewer shell, Present, print.

## 8. Decisions for the user

1. **Writing model.** Recommend adding Claude (Anthropic) as a provider and using it for the
   insight, storyline and draft steps; keep cheaper models for verify and small edits.
   Without it, use the strongest available (DeepSeek V4 Pro or Kimi K3) and expect a lower ceiling.
2. **Native PPTX and DOCX export.** Recommend yes; people present and send these formats.
3. **Delete the old step machinery** listed in §7 once the new path passes. Recommend yes.
4. **Gold material.** Use the six kinds in §6; the user may swap in their own real material
   (better: it is what they will judge by).
5. **Caveat policy.** Caveats live in a footnote line, speaker notes, or one dedicated
   slide/section when they change the decision. Recommend yes.

## 9. Before / after (one slide, same facts)

Before (rendered today):

> **The pilot measured time; quality remains unknown** [1][2]
> In this synthetic intake pilot, median review time was 40 hours before the pilot and 16 hours
> during the pilot. The observation covered two teams over eight weeks. These are training data,
> not actual organizational results. [1] / The two-team intake pilot did not measure review
> quality or error rates. A faster review cannot establish a quality improvement… [2]

After (target, "big number + context" layout):

> **The pilot cut review time by 60% — quality wasn't measured yet**
> **40 h → 16 h** median review time
> Two teams, eight weeks
> *Source line:* [1] Intake pilot report; error rates not tracked [2]
> *Speaker notes:* Lead with the drop. Then be straight: we only measured speed. The next
> slide proposes how to measure quality in the next round.

Same facts, same honesty, but it says something.

## 10. Progress log

**2026-10-22 — decisions taken (user: "you make decisions on the key questions").** All five
§8 recommendations accepted: Claude provider for writing; native PPTX + DOCX; old step machinery
deleted once the studio path passes; real gold material (§6); caveats in footnotes, notes or one
slide/section. On this machine only DeepSeek (Flash, V4 Pro) is configured: development and the
eval run on V4 Pro; DeepSeek Flash can see images, so it serves as the vision critic/judge here.
Claude runs on the user's Mac once a key is added.

**Built so far (uncommitted while in progress):**
- `docs/STUDIO_FORMAT.md` — the deck (design v2, 15 layouts with budgets) and report v2 contract.
- `gunther/studio/` — `format.py` (grammar + budget checker that returns fix instructions),
  `prompts.py` (craft-first prompts, one truth rule), `reference.py` (format as the writer sees
  it), `pipeline.py` (`StudioAgents.make`: read → memo → storyline + critic loop → whole draft →
  format fix → claim verify with one surgical fix round → assemble). Chart values not found in
  their cited passage turn the slide into a table.
- `KnowledgeService._studio_reading`: selected sources read whole into the pool as structured
  passages (not search snippets).
- Settings → Developer `output_method` (`skills` | `studio`), default `skills` until the bar is met.
- Claude provider preset (`anthropic`, OpenAI-compatible endpoint) with a thinking-budget dialect.
- Tests: `tests/test_studio_format.py`, `tests/test_studio_pipeline.py`.
- In progress (builder agents): deck v2 renderer, themes, gallery, PPTX/DOCX export, report v2
  view; the quality eval (`evals/quality`).

**2026-10-22 — first measurements (DeepSeek V4 Pro writes and judges; text-only judge).**
Round 1 (`evals/results/20261022-0739-quality-studio-v1`), studio vs the earlier skills method
on velmora and ev-outlook: head-to-head the studio won 2 and tied 2 overall, never lost on any
criterion; it used ~22 model calls per artifact against ~69. Absolute scores were held down by
two measuring faults found afterwards, both fixed: the judge and the claim checker each saw only
the start of a passage (420 / 900 characters), so facts later in a studio passage (~1,400
characters) were called unsupported. Velmora studio: 8/8/8/8/7/7 (insight, storyline, titles,
evidence, clarity, correctness), every slide or section "ship" or "minor edit".
Committed `ce0b101`, `7a4c477` (not pushed). Round 2 runs on all seven sets.
