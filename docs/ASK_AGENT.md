# Ask agent

Ask is a source-grounded agent (`apps/backend/gunther/agent.py`). The model is the
judge (what is relevant, whether the sources are enough, whether they disagree);
the *facts* come from sources. Every claim in an answer carries a source number, or
is visibly marked as not sourced.

## One question

1. **Plan** (low effort, small JSON call). The planner sees the question, the last
   six turns, the tools offered and the *pool* (below). It returns an action: a
   tool name with a standalone `query`, or `answer`. There is no intent category
   and no special path for small talk or follow-ups; with nothing worth
   searching it simply answers.
2. **Gather.** The tool runs, a model drops off-topic results (for tools that ask
   for it), new results are added to the pool, and the planner is asked again. At
   most four searches; a repeated search ends the loop.
3. **Write** (the chosen effort, in the chosen style). The writer sees only the
   pool. It cites `[n]` right after each claim. Anything it adds from its own
   knowledge is marked `[?]`.
4. **Audit, then check the model's own claims.** A low-effort model call lists
   sentences that state a fact with neither a number nor `[?]`; they get `[?]`
   too (it finds them in any language; code only places the mark). Each `[?]` claim (at most three) is searched
   for with the tools, and a model reads the results:
   - a source states it → the marker becomes that source's number;
   - a source states the opposite → the claim is left out and a note says why;
   - no source (or the web is off) → it stays `[?]`, shown as *unverified*.
5. Citations to numbers that are not in the pool are removed.

## The source pool

A conversation holds one pool of sources: everything its answers cited. A source
gets a number the first time it is cited and keeps it for good, in every later
answer, so `[3]` means the same thing throughout a conversation.

- A new search is compared with the pool (by kind, URL or title, and the start of
  the text). Known results reuse their number; only new ones are numbered, from the
  highest number ever given. The planner is told what the pool already holds, so a
  follow-up ("shorten that") can answer with no search.
- Each saved answer stores the sources it cites, each with its `ref`. The pool is
  rebuilt from those, so there is no separate table.
- A library source that is out of scope for a question (another library was picked
  with `@`, the source was removed) is left out of the pool for that question, but
  its number is never reused.
- Answers from before the pool have no `ref`; their `[n]` count within the
  answer, and their numbers are removed from what the model reads.

## Tools are a registry

`Toolbox` is a list of `Tool(name, about, run, where, grade)`; a tool turns a query
into `Evidence`. The agent knows nothing about which exist. Today: `search_library`
(the scoped library, keyword and meaning, graded for relevance) and `search_web`
(Tavily; offered only when a key is set **and** the Web switch is on). Adding a tool
means registering one in `KnowledgeService.create_session_turn`. Facts about what is
unavailable (no web, no library) travel as plain notes to the planner and writer.

## Style

Only the writer's wording changes. A small fixed set (`STYLES`: balanced, concise,
detailed, academic) is appended to the fixed rules, and the prompt says the rules win
if they conflict. The reader picks a style beside the model chips; it is remembered
on the device and sent as `style`. No style can turn off citing.

`Limits` (searches, new sources per question, pool size in view, claims checked)
are plain defaults on `AskAgent`.

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
`step` (`running`, then `done` with the result count), `text` (the answer as it is
written; it carries the pool's numbers and any `[?]`) and finally `done` with the
saved turn, or `error`. Claim checks run after the text ends and show as steps; the
saved answer replaces the streamed one. Closing the connection stops the work and
nothing is saved.

## Conversation sources panel

The context panel lists every source the conversation has cited, once each, under its
pool number, with the ones the selected answer cites in bold.

## Known limit

Whether a sentence is a claim is a model judgement (the writer's, then the audit's),
never a rule, so it can miss one. Answers under 40 characters skip the audit.
