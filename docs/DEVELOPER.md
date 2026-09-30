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
errors, or errors. For anything older, or from the app itself, use the log files.

## Log files

The installed app keeps everything it does in files, so a problem can be traced
after it happened. **Settings → Developer → Logs → Log files → Show** opens the
folder; the startup error screen has **Show log files** too.

```
~/Gunther/.gunther/logs/            (inside the library folder, wherever it was moved)
  2026-09-30.app.log                the app shell and its windows (ui)
  2026-09-30.backend.log            the local knowledge service
  2026-09-30.backend.2.log          the same day, once the first file passed 20 MB
```

- One file per process per day, named by date first, so the folder sorts by day.
- A day's file past 20 MB goes on in `.2.log`, `.3.log`…; files older than 14 days
  are deleted when the app starts, and the folder never grows past 500 MB.
- If the library folder cannot be written (a disconnected disk), logs go to the
  system's log folder for the app (`~/Library/Logs/com.gunther.knowledge` on macOS).
- Files are private to your user (0600). Tokens, API keys, `Bearer` credentials and
  signed-URL signatures are masked in every line; request lines never include the
  query string.

Every line starts the same way in both files, so one day's files read as one story
(`sort -m 2026-09-30.*.log` merges them):

```
2026-09-30 14:03:22.481+08:00 INFO  app Connected to the knowledge service after 2706 ms
2026-09-30 14:03:25.102+08:00 INFO  ui read-aloud: part 1/3: 181244 bytes, audio/wav, 2210 ms
2026-09-30 14:03:25.300+08:00 WARN  backend/tts_service Speech job-… part 2/3: … header disagrees: …
```

| Source | What it records |
|---|---|
| `app` | start (version, data, library and log folders), starting and stopping the service, how long connecting took, the service stopping on its own (with its exit status and the reason it logged), menu choices, capture phase changes, moving the library, quitting, panics |
| `ui` | each window opening and connecting, failed calls to the service (method, path, status, reason), uncaught errors and rejections, `console.warn/error`, and read aloud step by step: what was fetched (size, type, time) and what the audio element reported (length, playing, waiting, stalled, errors) |
| `backend/…` | startup steps with timings (lock and port, data folder check, setup, ready), Gunther's own loggers at INFO, uvicorn, each speech part with the supplier's time and a check of its WAV header, unhandled errors in any thread, and a crash in native code |
| `api` | one line per request that changes something, is slow (over 1.5 s), fails, or is a WebSocket: `POST /api/… 200 812 ms` |

When the service cannot start, it logs one `FATAL … knowledge service stopped: <reason>`
line with the traceback below it and exits. The app notices at once (it no longer
waits out a timeout), and the startup screen shows that reason, e.g. *port 28787 is
already in use by another program* or *another Gunther knowledge service is still
running for this data folder*. **Try again** starts the service again.

How it works: the shell uses `tauri-plugin-log` with a daily-file writer
(`src-tauri/src/app_log.rs`); the web layer writes through `@tauri-apps/plugin-log`
(`src/log.ts`); the service gets the folder as `--log-dir` and writes with
`gunther/app_log.py`. The shell also points the service's stdout and stderr at its
daily file, so output from before logging starts is kept. In development the
backend runs on its own and logs to its terminal; the shell logs to the terminal
and to its system log folder.

**Background jobs** lists the latest reading, search-by-meaning, paper and
summary jobs with their state, attempts and last error.

API: `GET /developer/logs?level=info|warning|error` · `GET /developer/jobs`.
Only the desktop owner can use these; a paired phone cannot.
