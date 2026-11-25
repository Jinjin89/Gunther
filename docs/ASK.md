# Ask

Start on Home to search across libraries or ask with selected scope. Chat opens in the middle of a library alongside Library and the Inspector. Library chats share the source ticks in the Library column. File a Home conversation in a library, branch at an answer, or archive it.

Library chats live in the **Chats** section at the top of the Library column. Search there by title or summary, pin or unpin a chat, and open the archived list. The Chat actions menu starts a new chat, renames the current chat, exports it as Markdown with citations, or archives it. An archived chat stays readable. Restore it to send more questions.

Empty Chat shows the library overview and up to three suggested questions when available. Choose one to send it with the current Sources ticks. While the overview is unavailable, Chat shows the library's guiding question, description and source count. Follow the Models link to set up Analysis, or use Retry if the overview failed.

Before an answer is selected, Evidence shows the current **Scope**, including the count by source kind and whether Web is on. Choose **Change** to open and focus Sources in the Library column.

## Conversation Brief

The **Brief** card in the Inspector's Evidence tab keeps the goal, constraints, settled conclusions and open questions across turns. It opens expanded and remembers when you collapse it. A long Settled list starts with its newest three conclusions. Choose **Show all** to see the rest. Source numbers open the saved passages, including citations from earlier answers. The card quotes your agreement when Gunther recorded it and offers **Retry** if a Brief update failed.

An empty Brief explains what it keeps. Choose **Edit** to add the first goal, constraint, conclusion or open question.

For the latest answer included in the Brief, **After the last answer** summarizes the changes, even when the card is collapsed. **New** marks added lines and an updated goal. Open includes a **Closed** list for questions that answer closed. Select an earlier answer to see its conclusions marked **from this answer** and kept visible alongside the newest three.

Choose **Edit**, then select a line or use the add button in its stage. Enter or leaving the field saves your text, and Shift+Enter adds a line break while drafting. Escape cancels the active edit. Press Escape again or choose **Done** to return to reading. Use the check beside an open question to move it to Settled and phrase the conclusion. In read mode, **Ask →** puts an open question in the composer for you to review and send.

## Ask a question

Select the model and effort in the composer. Use All sources or tick individual sources and sections. An empty selection disables Send. Opening another chat keeps the current ticks. The Web switch allows public web search when Tavily is configured. Compare and Research are explicit methods. Research frames sub-questions, gathers and grades rounds of evidence, and can stop gathering and write from the material already collected.

Ask needs a configured model. The composer links to Settings when a model or key is needed. A model or required grading failure leaves a retryable question. Optional relevance grading can retain ungraded results. Reconnect runs the same startup reads as opening the page. Chats, models and Ask settings each have their own Retry. Send requires a usable model, request settings and a reachable service.

## Answer format

Gunther saves answers as Markdown. Answers and notes share the rendering rules below.

| For | Write | Gunther shows |
| --- | --- | --- |
| Code | A fenced block with a language, such as `` ```python `` or `` ~~~python `` | Code with a language label and Copy. Supported languages get syntax colours. Long blocks can be expanded. |
| Picture | An `` ```svg `` block with one self-contained, unprefixed `<svg>` root. Gunther asks the writer to include a `viewBox`. Drawings sized with `width` and `height` alone also render. | A picture card with Picture, Code, Copy, Save and Expand. |
| Diagram | A `` ```mermaid `` block | A picture card drawn by Mermaid. |
| Math | `$$…$$` inline, opening and closing `$$` on separate lines for a display formula, or a `` ```math `` block for display | KaTeX typesets the formula. Wide display formulas scroll sideways. |
| Other fence | A fenced block with any other language label or with the label left blank | A code block that preserves the source. |

A single `$` stays a dollar sign, so `$5 and $10` display as prices. Place citations and Checker marks such as `[?]` in the prose before or after a block. Citation handling and claim checks skip code, math and pictures.

Picture cards keep the original size as a maximum. Both themes use a white canvas and the same neutral Mermaid palette. Mermaid runs in strict mode. Gunther sanitizes SVG drawings, removes external references, and displays the cleaned result as a local blob image. **Save** uses that cleaned SVG. KaTeX disables commands that can load resources or add HTML.

Use fenced SVG for new drawings. An older standalone HTML block containing one valid, unprefixed `<svg>` can also become a picture. Other HTML displays as text. Read-aloud uses the answer structure to describe diagrams in words.

The desktop shares the Markdown components across answer and note views. The backend uses the same format in its writer instructions, citation checks and narration. [Desktop Markdown](../apps/desktop/src/components/markdown/rich) · [Writer instructions](../apps/backend/gunther/agent.py) · [Markup parser](../apps/backend/gunther/markup.py) · [Citations](../apps/backend/gunther/citations.py) · [Read-aloud](../apps/backend/gunther/tts_narration.py)

## Evidence and checking

1. The planner chooses an offered tool or starts the answer. Library lookup uses the selected scope and read-more can expand a numbered source. A repeated source keeps its number in the conversation pool.
2. Library lookup also finds checked key results supported by the current source selection and section focus. Their original passages join the numbered pool. The writer sees the checked sentences as orientation with pointers to those passages. The checker audits uncited factual statements, assesses claim support and can look up marked statements. Unsupported or unverified wording and missing evidence stay visible. Partial support, inference and dispute markers remain attached to their sources.
3. The answer stores the source revision, block or region and supporting text with its citations. Hover over or tab to a citation number for its source title, location and a short quote, then activate it to open the passage in the Library Inspector. The source count below an answer opens its Evidence list, where you can choose a citation and step between passages. Selecting an answer updates that list without switching away from Studio; choosing Evidence, a source count or a citation opens Evidence explicitly. Home Ask keeps its standalone Evidence panel. The answer may read a pinned revision through the verified reader. Search and new reads respect current library membership and Trash.
4. A saved answer with a library citation queues key results for Analysis. When the answer's Checker coverage is complete, Analysis receives only its supported claims and their original passages. Filing a Home chat queues its eligible answers in the destination library. Research clarification turns, reworks and answers that cite only live web search results are skipped.

For the selected library answer, **Answer check** summarizes the saved claim verdicts. Expand the summary when there are flagged claims to read what the Checker found. Open **Process** for the search and reading steps and the saved research plan, when available. **Cited** shows each source's kind, location and excerpt beside its number in the answer. A citation card marks partial support or a dispute when the Checker linked that verdict to its source.

An open citation shows the source kind and location when available. Press `[` or `]` outside a text field to step through citations, or `Esc` to return to the overview. When several passages are grouped, choose a chip for two to five passages or use the menu for six or more. Choose **Original** to view an available PDF at the cited page.

Choose **Show in answer** on a numbered **Cited** card or in its detail header to scroll to the first matching number in the selected answer and briefly outline it. In a narrow workspace it also closes the Inspector slide-over and focuses the citation.

The answer actions show **Checking key results…** while Analysis works and a kept count when it finds supported results. Undo removes that answer's contribution. A failed job offers Retry, and a job waiting for Analysis links to Models settings. Open the library's Key results group to inspect the original passages. [Key results](LIBRARY.md#key-results)

Each saved answer keeps up to 20 uncited search hits, starting with up to 12 web hits and filling the remaining slots with library hits. It records the query, a short excerpt and whether the result was kept during gathering. It also records the checked key results that pointed to passages the answer cites, using the citation numbers in the answer. The saved search list stays out of later model prompts.

In Evidence, expand **Key results used** to see the checked sentences and open their numbered passages. **Also searched** counts uncited web and library hits, with web hits first. The cards show the saved query and mark hits excluded during gathering as **Filtered**. Library excerpts open as passages. **Earlier in this conversation** shows citations from previous answers. Each section appears when it has entries.

On a web card or in its passage view, **+ Add to library** captures the page or its PDF through the library's web capture flow. Use **Add all cited** to import missing cited web sources in order. A blocked page shows the server error with **Retry** and a link to open the page in your browser. When the page is saved in place of a linked PDF, the app shows the capture notice. [Capture guide](CAPTURE.md)

**In library · Open** opens a matching source's summary. The service matches live sources by URL, DOI or arXiv ID. Trash clears the match and Restore brings it back. A Home chat can use the library actions after it is filed.

## Streaming and trace

Ask uses `POST /api/sessions/{id}/messages/stream`. Attach to existing work at `GET /api/sessions/{id}/answer/stream`, or stop it with `POST /api/sessions/{id}/answer/stop`. Home and Library share the answer listener. The stream reports steps and text, then a stored answer or a terminal failure. Terminal events follow persistence and the Activity update. Clients parse SSE on CR, LF and CRLF line breaks.

Turn handling is in [ask_turns.py](../apps/backend/gunther/ask_turns.py) and [ask_api.py](../apps/backend/gunther/ask_api.py). The [agent](../apps/backend/gunther/agent.py) uses [evidence readers](../apps/backend/gunther/evidence_reader.py), [citations](../apps/backend/gunther/citations.py) and [claim checks](../apps/backend/gunther/claim_checks.py). The [Ask exam](../apps/backend/evals/README.md) runs its fixture turns through the same streaming path.

Enable Keep how answers are made in Developer settings and choose **Trace** below a library answer or in its **Answer check** card. Trace opens in the Inspector with the plan, searches, results, grading, writing and checks, with recorded model requests, replies and timing. Back to evidence or Escape returns to the Evidence overview. Saved answers with local provenance remain readable as stored. [Developer settings](SETTINGS.md#developer)
