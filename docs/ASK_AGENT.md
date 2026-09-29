# Ask agent

Ask used to retrieve the best-matching claims and passages for the raw question
and hand them to one model call ("answer only from this evidence"). That fails
in exactly the cases people actually hit: small talk, follow-ups ("shorten
that"), questions that need the outside world, and questions the library only
partly covers, where the reply was a canned "I couldn't find a claim…".

Ask is now an agent (`apps/backend/gunther/agent.py`). One question goes through
three stages, all by the model the conversation picked.

1. **Plan** (low effort, one small JSON call). The planner sees the question,
   the last six turns, the tools on offer and what has been found so far. It
   decides the *intent*: `chat`, `followup`, `library`, `web`, `both` or
   `clarify`, and the next action: `search_library`, `search_web` or `answer`.
   It writes a standalone search query (resolving "it", "that paper"). Small
   talk and clarifying questions are answered by the planner itself, so they
   cost one call.
2. **Gather.** The tool runs; results are numbered into one evidence list
   (library passages and web pages together); the planner is asked again. A
   thin search can be reworded once or aimed at the other tool. At most four
   tool calls; a repeated search ends the loop.
3. **Answer** (the chosen effort). One call writes the reply from the numbered
   evidence. It leads with the answer, cites `[n]`, separates supported from
   inferred, may add general knowledge *marked as such*, and, when nothing
   was found, says what was looked for, answers as far as it can, and suggests
   next steps (turn on Web, add a source, ask more narrowly). It is never asked
   to say only "not found".

Citations are checked afterwards: numbers that do not exist are removed, and
the ones used are renumbered `[1]`, `[2]`… in order, so the sources attached to
an answer are exactly the ones it cites. Small talk carries none.

## Tools

| Tool | What it does | Offered when |
| --- | --- | --- |
| `search_library` | Claims and passages of this library (keyword + meaning, the topic or source scope, paper abstracts for large libraries) | The library has sources |
| `search_web` | Tavily search; each page becomes a citation with its URL | A Tavily key is set (Settings → Web search) **and** the Web switch is on for the question |

The web is never searched unless the switch is on, and the reply says when it
was left out. The steps taken ("Searched your library for …, 3 results";
failures too) are kept with the reply and shown above it.

## Why JSON plans, not provider function-calling

Every provider Gunther supports (DeepSeek, Kimi, GLM, Qwen, OpenAI, self-hosted)
can return JSON through the existing gateway, which already maps effort and
keeps reasoning per model. Native tool calling differs between them, and for
thinking models needs their reasoning replayed between calls. An agent
framework (LangChain, PydanticAI, the OpenAI Agents SDK) would bypass that
gateway for a loop of about 200 lines. If the planner's JSON cannot be read,
the library is searched with the question as asked and the model still writes
the answer.

## Failure is visible

No model set up → the reply says Ask needs a model (Settings → Models) and any
library quotes found stand in. A model that fails → its error is shown and the
library quotes stand in; nothing invents an answer. A failed web search is shown
as a failed step, and the answer says the web was not reached.

## Streaming

`POST /api/sessions/{id}/messages/stream` runs the same turn and reports it as
server-sent events: `intent`, `step` (`running`, then `done` with the result
count), `text` (the answer as it is written) and finally `done` with the saved
turn, or `error`. The text shown while streaming carries the writer's own
`[n]` numbers; the saved answer replaces it with the checked, renumbered one.
Closing the connection stops the work and nothing is saved, so a stopped
question goes back into the box.
