# Settings → Developer

For seeing how Gunther produced something, without reading the code.

## How answers are made (traces)

Off by default. With **Keep how answers are made** on, every new answer keeps a
trace, shown in a conversation's right panel under **Trace** (select the answer
first). Answers made while it was off have none.

A trace is the agent's steps in order, each with its time:

| Step | What it shows |
|---|---|
| plan | the action and query the planner chose (or that its reply could not be read) |
| search | the tool, the query and every result it returned |
| grade | which results the relevance check kept |
| write | the sources the writer could cite, and the answer style |
| audit | claims the writer left without a source |
| check | a claim, and which results support or contradict it |
| cite | the final numbering, and sources gathered but not cited |

Under each step, every model call it made: the model and effort, the exact system
prompt and messages, the reply, its reasoning, the time and, when the provider
sends them, token counts. **Copy** puts the whole trace on the clipboard as JSON.

How it works: `gunther/trace.py` holds the trace for the answer being written in
a context variable; `agent.py` marks steps and `ModelGateway.complete` records
each call into the open step. With no trace current nothing is recorded, so
answering costs the same with the switch off. Traces live in `message_traces`
(migration 18), deleted with their message, and only the latest 200 are kept.
Long texts are cut at 20,000 characters.

API: `GET/PUT /settings/developer` · `GET /sessions/{sid}/messages/{mid}/trace`.

## Logs and background jobs

**Logs** lists the local service's recent lines from Gunther's own loggers,
newest first (the last 400, INFO and up), filtered to everything, warnings and
errors, or errors. Before this nothing configured Python logging, so INFO lines
such as a failed planning step were dropped; warnings still go to stderr, which
is `backend.log` in the desktop app.

**Background jobs** lists the latest reading, search-by-meaning, paper and
summary jobs with their state, attempts and last error.

API: `GET /developer/logs?level=info|warning|error` · `GET /developer/jobs`.
Only the desktop owner can use these; a paired phone cannot.
