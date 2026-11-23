# Ask

Start on Home to search across libraries or ask with selected scope. Chat opens in the middle of a library alongside Sources and Studio. Library chats share the source ticks in the Sources column. File a Home conversation in a library, branch at an answer, or archive it. Open Brief in the Chat header to see or edit the context carried across turns.

The Chat actions menu starts a new chat, opens History, renames the current chat, exports it as Markdown with citations, or archives it. In History, search by title or summary, pin sessions, or use View archive to reopen or restore one. An archived chat stays readable. Restore it to send more questions.

Empty Chat shows the library overview and up to three suggested questions when available. Choose one to send it with the current Sources ticks. While the overview is unavailable, Chat shows the library's guiding question, description and source count. Follow the Models link to set up Analysis, or use Retry if the overview failed.

## Ask a question

Select the model and effort in the composer. Use All sources or tick individual sources and sections. An empty selection disables Send. Opening saved chat history keeps the current ticks. The Web switch allows public web search when Tavily is configured. Compare and Research are explicit methods. Research frames sub-questions, gathers and grades rounds of evidence, and can stop gathering and write from the material already collected.

Ask needs a configured model. The composer links to Settings when a model or key is needed. A model or required grading failure leaves a retryable question. Optional relevance grading can retain ungraded results. Reconnect runs the same startup reads as opening the page. History, models and Ask settings each have their own Retry. Send requires a usable model, request settings and a reachable service.

## Evidence and checking

1. The planner chooses an offered tool or starts the answer. Library lookup uses the selected scope and read-more can expand a numbered source. A repeated source keeps its number in the conversation pool.
2. The writer sees the usable passages and their citations. The checker audits uncited factual statements, assesses claim support and can look up marked statements. Unsupported or unverified wording and missing evidence stay visible. Partial support, inference and dispute markers remain attached to their sources.
3. The answer stores the source revision, block or region and supporting text with its citations. Hover over or tab to a citation number for its source title, location and a short quote, then activate it to open Evidence. You can also expand the source count below an answer and choose a source there. Selecting another citation switches the open Evidence panel to that passage. The answer may read a pinned revision through the verified reader. Search and new reads respect current library membership and Trash.
4. A saved answer with a library citation queues key results for Analysis. When the answer's Checker coverage is complete, Analysis receives only its supported claims and their original passages. Filing a Home chat queues its eligible answers in the destination library. Research clarification turns, reworks and answers that cite only live web search results are skipped.

The answer actions show **Checking key results…** while Analysis works and a kept count when it finds supported results. Undo removes that answer's contribution. A failed job offers Retry, and a job waiting for Analysis links to Models settings. Open the library's Key results group to inspect the original passages. [Key results](LIBRARY.md#key-results)

## Streaming and trace

Ask uses `POST /api/sessions/{id}/messages/stream`. Attach to existing work at `GET /api/sessions/{id}/answer/stream`, or stop it with `POST /api/sessions/{id}/answer/stop`. Home and Library share the answer listener. The stream reports steps and text, then a stored answer or a terminal failure. Terminal events follow persistence and the Activity update. Clients parse SSE on CR, LF and CRLF line breaks.

Turn handling is in [ask_turns.py](../apps/backend/gunther/ask_turns.py) and [ask_api.py](../apps/backend/gunther/ask_api.py). The [agent](../apps/backend/gunther/agent.py) uses [evidence readers](../apps/backend/gunther/evidence_reader.py), [citations](../apps/backend/gunther/citations.py) and [claim checks](../apps/backend/gunther/claim_checks.py). The [Ask exam](../apps/backend/evals/README.md) runs its fixture turns through the same streaming path.

Enable Keep how answers are made in Developer settings and click Trace below a library answer. Its side panel shows the plan, searches, results, grading, writing and checks, with recorded model requests, replies and timing. Saved answers with local provenance remain readable as stored. [Developer settings](SETTINGS.md#developer)
