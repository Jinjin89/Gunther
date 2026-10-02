# Ask agent

Ask is a source-grounded agent (`apps/backend/gunther/agent.py`). The model is the
judge (what is relevant, whether the sources are enough, whether they disagree);
the *facts* come from sources. Every claim in an answer carries a source number, or
is visibly marked as not sourced.

## One question

1. **Plan** (thinking set to low, a small JSON call; the checks and the brief run with it off,
   as measured on 2026-10-02, see docs/ASK_RESEARCH_PLAN.md Phase 6). The planner sees the question, the last
   six turns, the tools offered and the *pool* (below). It returns one to three
   `actions` (a tool name with a standalone `query`, or `answer`) and `new_facts`:
   whether the reply will state facts that are not in the user's message or earlier
   answers. There is no intent category and no special path for small talk or
   follow-ups; with nothing worth searching it simply answers. The planner is also
   shown the notes (below) and what is left of the budget.
2. **Gather.** The actions of a plan run in order: searches, `read_source`, and
   `answer`, which ends gathering after the actions before it. Repeats, tools that are
   not offered and actions over budget are skipped. When a round has run, **one** model
   call grades all of its results (library and web together): it drops what is about
   another subject and writes one line on what each kept result says. Kept results
   join the pool and those lines become the *notes*; the planner is asked again.
   Gathering ends when a plan says `answer`, when a round runs nothing, or when the
   budget is spent. The budget (`Budget`) is four searches, three reads and twelve
   model calls (plans and gradings); a skill may set its own. A repeated search
   is skipped. If the grading call fails, every result is kept and nothing is noted.
3. **Write** (the chosen effort, in the chosen style). The writer sees only the
   pool. It cites `[n]` right after each claim; an inference cites the sources it
   rests on. Only what it adds from its own knowledge is marked `[?]`, never a
   sentence that cites a source or one about what the sources contain. If the
   sources leave part of the question open, it ends with "Not settled by the
   sources: …".
4. **Check follows the decision.** (A skill's answer is always checked; see Skills.)
   When the search found nothing to look at and the
   planner said `new_facts` is false (a translation, a shortening, a thanks), the
   reply is not checked and nothing is cut from it; the writer is told to add no
   facts. Otherwise one model call reads the answer sentence by sentence, with the
   full text of every source it cites, and gives each fact-stating sentence a verdict:
   - *supported*: left as it is, `[n]`;
   - *inference*: `[i:n]`, shown as a small "inference · from [n]" label;
   - *partly*: `[p:n]`, shown as a hollow number; hovering says what the source does
     not cover (kept in `supportNotes`, one per `[p:n]`, in order);
   - (`[d:n]`, disputed, is only added by step 5, never by this check;)
   - *not* supported: its citations are taken off and it gets `[?]`;
   - *unsourced* (a fact with no number): it gets `[?]`, unless it has one already.
   Sentences that only say what the sources do or do not contain, or describe the
   search, get no verdict. If that call fails, the answer is kept and a note says the
   citations were not checked.
5. **Look up the `[?]` claims** (at most three; never in a rework reply, which is left as
   written). When the check
   ran, only the sentences it judged *not* or *unsourced* are looked up; any other
   `[?]` (a remark the writer marked) keeps its mark and is never searched. Each
   claim is searched with the tools, then one model call reads all the results and
   judges every claim:
   - a source states it → the marker becomes that source's number;
   - a source states the opposite → the claim is kept and marked `[d:n]` (up to three
     contradicting sources, each added to the pool and cited), shown as a "disputed · by
     [n]" label, and a note says so. (Outputs still leaves such a claim out.)
   - no source (or the web is off) → it stays `[?]`, shown as *unverified*.
6. Runs of markers are tidied (`[2] [3][2]` becomes `[2][3]`), and citations to numbers
   that are not in the pool are removed.

## Reading further

A search result is often a short passage or a snippet. `read_source` takes the number
of a source **already in the pool** (never a URL the model writes) and reads more of
it, so the answer need not rest on a summary:

- a **library** passage: its section (the blocks under the same heading, or three
  blocks each way when it has none), growing outward from the passage up to about
  6 000 characters;
- a **web** page: the page itself, up to 8 000 characters, centred on its snippet. Only
  with the Web switch on, and through the same rules as capturing a page: public
  addresses only (also after each redirect), the redirect limit, the size limit, HTML
  or text only. A refusal is a failed step with the plain reason.

What was read goes to the writer and the checker (the writer gets up to three such
sources); the saved citation keeps its short quote. Outputs has its own `read_source`
(the passage and two blocks each side), unchanged.

## Notes

What this question found: for each kept result, its pool number and one line on what it
says (and, for a research question later, which sub-question it serves). The planner
reads them to decide what is still missing. They are saved with the answer in
`context.work`; a finding about a source the answer cites carries that source's number
in the text, any other keeps its title.

## The source pool

A conversation holds one pool of sources: everything its answers cited. Inside,
each source has an id that never changes (`ref`). What a reader sees is different:

- **One answer numbers its sources 1, 2, 3 in the order its text first cites them**,
  and its source list is in that same order, so `[2]` in the text is the second card.
  Sources the text does not cite are dropped.
- Saved answers are never rewritten. A later answer that reuses a source numbers it by
  its own reading order, so the same source can be `[2]` in one answer and `[1]` in
  another; each answer's text and list always agree.
- The hidden id is what lets a later answer reuse a source without searching again and
  lets Gunther trace a number back to its source. The model always sees the ids: the
  pool is shown to it with them, and earlier answers are turned back into them when it
  reads the conversation.
- A new search is compared with the pool (by kind, URL or title, and the start of the
  text). Known results reuse their id; only new ones get one, from the highest ever given.
  The planner is told what the pool already holds, so a follow-up ("shorten that") can
  answer with no search.
- Each saved answer stores the sources it cites, in list order, each with its `ref`. The
  pool is rebuilt from those, so there is no separate table.
- A library source out of scope for a question (another library was picked with `@`, the
  source was removed) is left out of the pool for that question, but its id is never reused.
- Answers from before the pool have no `ref`; their `[n]` are removed from what the model reads.

## The conversation brief

Each conversation keeps a short brief of what the user wants: the goal, the
constraints (what to include or exclude, for whom, how long), what is settled and what
is still open. It lives in `knowledge_sessions.brief_json` (`gunther/brief.py`), and the
planner and the writer read it first, ahead of the question.

- After an answer is shown, the page asks `POST /api/sessions/{id}/brief/refresh`;
  one low-effort call (the memory step, `BriefKeeper`) folds the last question and answer
  in, using the Ask job's model (else the model of the last answer). If the page never
  asked, the next question catches up first.
- A conclusion is "settled" only when the user agreed (their words are kept, and must be
  found in their latest message) or the
  answer states it with source numbers that are in the pool. The code enforces this
  after the model answers: anything else moves to "open".
- The user can edit any line in the context panel (`PATCH /api/sessions/{id}/brief`).
  Lines they wrote are kept as written and the model is told not to touch them.
- It is model output, so there is no stand-in: if an update fails the old brief stays
  and the panel says "Couldn't update the brief · Retry".

## Tools are a registry

`Toolbox` is a list of `Tool(name, about, run, where, grade)`, plus an optional `reader` for
`read_source`; a tool turns a query into `Evidence`. The agent knows nothing about which exist. Today: `search_library`
(the scoped library, keyword and meaning) and `search_web`
(Tavily; offered only when a key is set **and** the Web switch is on). Adding a tool
means registering one in `KnowledgeService.create_session_turn`. Facts about what is
unavailable (no web, no library) travel as plain notes to the planner and writer.

## Style

Only the writer's wording changes. A small fixed set (`STYLES`: balanced, concise,
detailed, academic) is appended to the fixed rules, and the prompt says the rules win
if they conflict. The reader picks a style beside the model chips; it is remembered
on the device and sent as `style`. No style can turn off citing.

`Limits` (new sources per question, pool size in view) are plain defaults on `AskAgent`;
how many searches, reads, claims looked up and sentences checked is the `Budget` of the
question.

## Skills

A skill is a method for the same loop, not a second agent. It is a folder in
`gunther/skills/<name>/` with `skill.toml` (`kind = "ask"`, a `command`, a title, a
description, whether to frame first, and optional `[budgets.<name>]` tables) and
`SKILL.md` (a preamble and the sections `## frame`, `## plan`, `## write`, `## check`).
`ask_skills()` in `skillbook.py` loads them; a bad one names its file. Outputs' skills
(`report`, `slides`) are loaded by a different function and never see these. The skills
so far are `/compare` (Compare sources) and `/research` (Deep research, below).

- **Choosing.** Typing `/` as the first character of a message in either composer opens
  a menu of skills (`GET /api/ask/skills`); Enter or Tab picks one, Esc closes it. The
  pick shows as a `/compare ×` chip before the text and is sent as `skill`. An unknown
  command is refused with "Unknown skill: /x" before anything runs. `@` (scope) and
  the Web switch are separate; `/` lists skills only.
- **What it changes.** Its budget (else the default), and its instructions: the
  preamble and `## plan` go after the planner's rules, the preamble and `## write`
  after the writer's, each ending "If the method conflicts with the rules above, the
  rules win." No skill can turn off citing or checking.
- **Always checked.** A skill's answer is never treated as a rework reply. The one
  sentence check also reads the skill's checklist (the bullets of `## check`) and says
  for each item whether the answer passed. If an item failed, one revising call
  rewrites the answer to pass it (keeping every source number), then the sentences are
  checked again without the checklist, then the look-ups run. Never a second revision;
  if the revising call fails the answer stays as written and a note says so. The steps
  read "Checked the method" and "Revised to follow the method".
- **Shown.** The answer's context keeps `skill` (`name`, `version`, `title`, `auto`) and
  the answer says "Used: Compare sources" beside the model line.
- **Auto-match** (`ASK_AUTO_SKILLS=true`, off by default, no switch in the app). When on
  and no skill was chosen, the planner is offered the skills and may name one when the
  message clearly asks for that method. The same question is then planned again with the
  skill's method, and `skill.auto` is true. It stays off until the exam shows it picks
  well (cases SK2 and SK3, `--auto-skills`).

## Deep research (`/research`)

The same loop with a plan in front of it. Its `skill.toml` says `frame = true` and has
two budgets, **Standard** (8 searches, 4 reads, 30 model calls, 3 to 5 sub-questions)
and **Deep** (16, 8, 50, 4 to 8). The chip has a Standard | Deep toggle; the choice is
sent as `budget`.

1. **Frame.** One call splits the question into sub-questions, each with a first query,
   a one-line core question and a one-line "done when". If the answer would change a
   lot with something only the user knows, it asks one or two questions instead: the
   answer is those questions, nothing is searched or checked, and the context says
   `research.state = "asking"`. The composer then starts with the `/research` chip at
   the same depth, so the reply is framed with the question in view. If framing fails
   the answer is the error; no plan is made up.
2. **Wide first.** One library search for every sub-question (no planner call), one
   grading round that says which sub-question each result serves; then the web, only
   when the Web switch is on, for the sub-questions with fewer than two library
   findings, and one more grading round. Selected material and scope apply as for any
   question.
3. **The normal loop** then runs with the notes (sub-questions marked open, thin or
   covered), the core question and "done when" shown to the planner, until it says
   `answer` or the budget is spent. Every step event carries what is used so far.
4. **Write and check** as for any skill. The writer and the checker also read the
   plan; the skill's checklist asks that every sub-question is answered or named as
   not settled, that disagreements cite both sides, and that the opening answers the
   core question.
5. **Stop keeps the work.** The first Stop while it is still gathering ends gathering
   and writes from what was found ("Stopped early: written from what was found so
   far."; `research.state = "stopped_early"`). The button reads "Stop and write"
   then; once the text is coming, Stop is the usual Stop and drops the answer.
   Other questions are unchanged.

The page shows the plan (core question, sub-questions) above the steps with a line
"Searches 5/8 · Reads 2/4", and folds it under a saved answer as "Research plan".
Under a research answer in a library, "Make a report from this" opens Outputs with
the conversation chosen as material.

## Failure is visible

No model set up → the reply says Ask needs a model (Settings → Models) and any
library quotes found stand in. A model that fails → its error is shown and the
library quotes stand in; nothing invents an answer. A failed tool is a failed step,
and the writer is told. If the planner's JSON cannot be read, the first tool is
searched with the question as asked.

## Why JSON plans, not provider function-calling

Every provider Gunther supports (DeepSeek, Kimi, GLM, Qwen, OpenAI, self-hosted)
can return JSON through the existing gateway, which already maps effort and keeps
reasoning per model. Native tool calling differs between them, and for thinking
models needs their reasoning replayed between calls. An agent framework would
bypass that gateway for a loop of about 200 lines.

## Streaming

`POST /api/sessions/{id}/messages/stream` runs the same turn as server-sent events:
`step` (`running`, then `done` with the result count), `research_plan` (research only: the core question and sub-questions, sent once framing is done), `text` (the answer as it is
written; the reader hides its numbers and `[?]` until the finished text replaces it) and finally `done` with the
saved turn, or `error`. The sentence check and claim look-ups run after the text ends (look-ups show as steps); the
saved answer replaces the streamed one. Closing the connection stops the work and
nothing is saved.

## Conversation sources panel

The context panel lists every source the conversation has cited, once each and without
numbers, with the ones the selected answer cites in bold.

## Known limit

Whether a sentence is a claim, and whether its sources support it, is a model
judgement (the checker's), never a rule, so it can miss one. Outputs still uses the
older audit and per-claim check (`unsourced_claims`, `check_leads`), unchanged.
