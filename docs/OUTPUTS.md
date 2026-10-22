# Outputs

An output is a **report** or **slides** written by agents
(`apps/backend/gunther/outputs.py`) from what the person chooses, and saved as a version
that can be reopened, read in Gunther, changed, and printed to PDF. It follows Ask's rule
(see ASK_AGENT.md): every claim carries a passage number, or is visibly marked as not
sourced. The model plans and writes; the *facts* come from the library.

## What goes in

- **Scope.** The whole library (every live source and every saved answer), or a hand-picked
  mix of sources, saved knowledge (trusted units) and Ask discussions. A pick that is not in
  this library, or was removed, is refused (400). An empty library is refused too.
- **A brief** (optional, up to 2000 characters), a **kind** (report or slides), a report
  **style** (Auto, Overview, Field guide, Teaching path, Decision brief), a deck's **use**
  (Auto, a talk, reading) and an **audience** (Scientist, Student, Collaborator).
- **Web** (optional, when web search is set up): the shared Web switch lets a skill add web
  results where the chosen material has a gap.
- **Library supplements** (API opt-in, `librarySupplements: true`): additional live sources
  from the same library may support the selected material. Citation origin is labeled and
  recorded with the version; the option does not expand into another library.
- **Notes and sources are different things.** Saved knowledge and discussions are *notes*:
  they shape the plan and the writing, but are never cited. The passages they cite seed the
  pool instead, so the text can cite the passage itself.
- The model is the **Outputs** job in Settings → Models. Until a model is chosen for it, it
  follows Ask's. With neither, a build fails with "Outputs need a model. Set one up under
  Settings → Models." and nothing is saved.

## Skills

By default an output is made by following a **skill**: the method for one kind of output,
kept as files in `apps/backend/gunther/skills/<name>/` and shipped with the backend. There is
one for reports and one for slides. Settings → Developer → *Make outputs with skills* turns
them off; outputs are then made by the agents alone, as in "One build" below (Auto counts as
Overview), so the two can be compared on the same material. A version records which way it was
made (`provenance.generator`: `gunther.output-skills.v1` or `gunther.output-agents.v1`).

A skill folder holds:

- `skill.toml`: its name, the kind it makes, its limits, the structures and (slides) layouts it
  knows, and its **steps** in order. Each step names what it does (a kind the runner knows), the
  tools it may use, the references it reads, the checks that run on its work and its effort
  (`job` is the Outputs job's).
- `SKILL.md`: the instructions, in Chinese. The text before the first step's `## <id>` is the
  preamble; each step reads the preamble and its own section.
- `references/`: tables and examples. `layouts.md#{layout}` reads only the layout of the slide
  being written.

`skillbook.py` loads and checks a skill when it is first used (an unknown step kind, tool or
check, a missing section, a structure no reference describes are errors that name the file);
`skill_runner.py` runs it. **Gunther's rules come first in every step and win over the skill:**
citing, the `[?]` mark, the language, the JSON a step returns. No skill can turn the Checker
off: every section written or rewritten is checked exactly as in "One build".

The steps (`SkillAgents.make`):

1. **understand.** A small loop (at most `tool_calls`): the model picks an action and a query
   from what the step offers: `search_library`, `search_web` (only with the Web switch on and
   a key), `read_source` (a passage and the two blocks on each side, as a new passage). Then one
   call writes the **approach** (core question and answer, who it is for and why, the structure
   and why, a deck's use and size, web supplements with their role, what the material cannot
   answer) and **material cards** (claims with pool numbers). A report style the person picked
   fixes the structure; a deck's use they picked is kept; an approach they edited keeps their
   words.
2. **structure.** The outline: a claim as each heading, a goal, its cards, a few searches, and a
   slide's layout. The step's checks (`section_count`, `page_count`, `first_and_last`,
   `layout_run`) run on it; if they find something the model gets one more try. A deck's first
   slide is always `title` and its last `takeaways`. A `figure` slide whose cards' passages
   have no figure kept with them becomes a `text` slide, and the version says so.
3. **design (slides only).** One bounded call designs the pages from the outline, material
   cards and shared evidence. Each page keeps its intent, visual explanation, composition,
   emphasis and note guidance. The designer may suggest a supported layout, while keeping
   the page identity, order, evidence and explicit user choices. One repair is allowed for an
   invalid response. Failure records a skipped design and keeps the original plan; later
   writing chunks do not retry the same failed plan. Reports go straight to writing.
4. **write.** Sections with fewer than two passages are researched as in "One build", and may
   then use the step's tools; each section is written from its cards' passages with the skill's
   instructions and its layout's reference, then checked. A deck's slide starts with a
   **layout marker**, a line `<!-- layout: compare -->` (the title slide's may also say
   `; accent: blue; theme: editorial`): code writes it from the plan, the model never does, and neither the
   Checker nor the models that read the deck see it. The slide's body must take the form its
   layout asks for (`layout_shape`: a table for `compare`, 2 to 6 numeric rows for a chart, an
   ordered list for `flow` and so on); one that does not is written again once with the problem
   named, and drawn as `text` if it still does not (`provenance.skill.skipped`, "Slide 3 drawn
   as text"). The section hash covers the marker, and the desktop reads it the same way
   (`sections.ts`); a deck made before markers falls back to its outline's layouts.
5. **compose.** After the body has passed the Checker, each `composed` slide receives one
   bounded scene call (and at most one structural repair). Python splits its title, content
   blocks, fenced examples and `Note:` footer into an exact numbered inventory. The response
   contains only a nested row/column tree that references those block ids; it carries no prose.
   Code rejects unknown, duplicate, missing or type-mismatched blocks, excessive depth/size and
   unsupported values. Graphic views are offered only for an ordered 2–6 item list, a valid
   cited DAG table, or a two-column table with 2–6 numeric rows and one unit. Code grants a
   graphic view only after checking that exact block against its own citations and coverage.
   Every chart row must have a supported claim bound to its label, value and row citations. A
   failed block returns to its complete original list/table and records the reason, while the
   rest of the page keeps its scene. A structural failure keeps the checked Markdown and records
   a skipped step.
   The version stores a base64url scene comment directly after the layout marker. Version 1
   remains readable with only the original six block views. Version 2 stores `sourceHash`, a
   citation-sensitive `graphicsHash`, code-written per-block graphic licenses and the element
   tree. Its source hash covers visible Markdown while ignoring citation numbers and speaker
   notes; the graphics hash retains citation markers. Code carries a graphic license across
   citation renumbering only when the caller supplies the exact reference map and the visible
   source still matches. A manual citation edit invalidates the license. An unchanged valid
   scene is reused; an explicit re-composition request makes one new call even when the body
   returned `UNCHANGED`.
6. **edit_review.** Code checks (`heading_length`, `filler_phrases`, `density`, `table_size`,
   `chart_numbers_cited`, `layout_run`, `layout_shape`; settings in `skill.toml`; `text` is the
   last resort, so `layout_run.max_text` keeps it to 2 slides and never two in a row), then an
   editor lists problems per section. Nothing is changed.
7. **reader_test.** A fresh reader, who gets no preamble and no approach, reads the text without
   numbers (a talk's speaker notes as what the speaker says) and says what it understood, what
   was unclear, where it jumped and what it would ask.
8. **revise.** One call picks at most `revise_max` sections, each with an instruction; each is
   rewritten (or answered `UNCHANGED`) and checked again. One round only. Revision receives
   the argument and neighboring sections. New numeric facts are refused; other edits
   require an explicit fidelity verdict preserving factual assertions and citation bindings.

understand, structure and write failing fail the build (nothing is saved). design, edit_review,
reader_test and revise failing are **skipped and recorded**: the live view marks the step, and
the version lists it under Approach → Skipped (`provenance.skill.skipped`).

What a version made by a skill keeps, sealed in its provenance: `skill` (name, version, skipped
steps), `approach` (web supplements only when the text cites them) and a deck's `use`. Outline
items keep their `layout`, `link`, page `design` and explicit choice flags. Revising such a
version ("Ask Gunther to change…") uses the skill's
revise instructions and its approach; edits and revisions carry the approach on.

A `composed` slide's scene is presentation metadata, not body evidence. The service, Checker,
outline and writer omit it from visible Markdown. A trusted code citation map can rebind its
version-2 graphic hash; any body or manual citation change makes graphic licenses stale until
recomposition. Manual Markdown editing hides
the scene payload behind a short per-slide retained marker and restores it to the same page on
preview/save; inserted, deleted or duplicated markers are not silently attached to another page.

On the page, **Approach** sits above the outline: question, answer, who it is for, structure,
deck, web supplements, gaps and skipped steps. *Edit approach* changes the question, answer or
purpose and rebuilds with those words kept; the outline is planned again. The outline shows a
slide's layout, which can be changed before *Rebuild with this outline*.

The skill files also go into the packaged backend (`--add-data` in
`scripts/build-backend-sidecar.mjs`; a test checks the line is there).

## One build

Without a skill, everything is read first; the version is written only after the agents finish, so a failed
or stopped build leaves nothing behind.

1. **Planner** (low effort, small JSON call). It sees the brief, the notes (up to 20 saved
   answers, 10 discussions) and what each source is about (title and overview of up to 40,
   most recent first), and returns a title and the sections, each with a goal and up to
   three searches. A report has 3–8 sections; a deck has 6–14 slides, the first a title
   slide and the last "Takeaways". A rebuild with an outline the person edited skips it,
   and each section is searched by its heading and goal.
2. **Researcher** (per section, every section before any writing). The searches run on the
   library tool (the same one Ask uses, over the chosen sources only), one relevance call
   keeps what is about the section, and up to 8 new passages join the pool. What is left of
   the pool (120 in all) is shared by the sections still to search.
3. **Writer** (per section, at the job's effort, streaming). Ask's citation rules, then the
   format (a report section is `## Heading` and prose; a slide is `## Heading`, 3–5 bullets
   of at most 14 words and a `Note:` of speaker notes, all cited; the title slide is `# Title`
   and a line), the style and the audience, with the rules winning over the style. It sees
   the brief, the whole outline, this section's goal, the notes, up to 30 passages (this
   section's first) and the end of the previous section.
4. **Checker** (per section, right after it is written):
   - numbers the pool does not have are dropped, then a low-effort call marks sentences with
     neither a number nor `[?]`;
   - each `[?]` claim (at most two per section) is looked up in the library: a passage that
     states it becomes its citation, one that states the opposite removes the claim, and
     otherwise it stays `[?]`;
   - bounded batches list the cited sentences whose cited passages do **not** state
     them; each loses its numbers and becomes `[?]`;
   - shared claim checking records coverage across headings, prose, tables and notes,
     including title-slide facts. Missing or failed verdicts remain unchecked.
5. **Assemble.** The report is `# Title`, an optional one-line brief in italics, then the
   sections; a deck is the slides separated by `---`. Citations are renumbered 1, 2, 3 in the
   order the whole text first cites them, and the version stores the passages in that order,
   so a number in the text is the number of the passage beside it. The title is the first
   `# ` line (at most 160 characters).

## The pool

As in Ask, a passage keeps its number for good during a build and is adopted once by
source/revision/block identity, or a full-text fallback digest. Only cited passages are kept. A version
stores **copies** of what it cites (quote, title, locator, ids), with no link to the source,
so a source that is deleted later never breaks an output; the evidence panel then says the
source is gone. A passage from a source that has left this library is not used as a seed.
Imported figures can be reused with a cited managed asset reference. Outputs retain image
ownership so permanent source deletion does not break the saved figure. Credentials are
resolved by the viewer and are not stored in Markdown.

## Findings

What the Checker finds is stored beside the version, never inside it (table
`artifact_checks`, append-only; the latest row wins), so checking again changes no version.
Each finding has a claim, a verdict and a note:

| Verdict | Meaning |
| --- | --- |
| `unsupported` | the passages it cites do not state it (or its number has no passage) |
| `unverified` | from the model's own knowledge; no source was found |
| `unsourced` | no source is cited (found when checking, not building) |
| `contradicted` | left out of the text because the sources disagree |
| `unchecked` | a verdict was omitted, failed or exceeded the context/check budget |

Findings belong to a **section's hash**: the sha256 of its text with `\n` line ends,
trimmed. A section that changed is "not re-checked" until the Checker looks at it again.
New checks mark a section checked only when coverage is complete. Chart pages require
cited numeric values and completed verification before saving. Revisions carry complete
coverage for unchanged sections into assembly. Older charts without coverage records
are checked again without rewriting them. Final Skill checks run again after revision;
these safeguards do not establish measured writing quality.

See [implementation notes](KNOWLEDGE_AND_AGENT_UPGRADE_IMPLEMENTATION.md) for test
results, optional parser packaging and the remaining quality/release evaluation.

## Sections

The service and the app split a document by the same rules (`split_document` in
`outputs.py`, `sections.ts` on the desktop, tested with the same cases):

- A report's sections start at lines beginning with `## `; what comes before the first one
  (the title, the brief) is the preamble.
- A deck's slides are the parts between lines that are only `---`; empty ones are dropped.
- A `## ` or `---` inside a code fence does not count.
- A slide's speaker notes start at a line beginning with `Note:`.

## Versions and what seals them

Every build, rebuild, edit and revision is a new immutable version of one lineage
(`supersedesArtifactId`); only the **latest** version can be built on (409 otherwise).
`clientRequestId` makes a retry safe: the same request returns what it saved, a different
request with the same id is refused.

A version records its kind, style, audience, brief, origin (`build`, `rebuild`, `edit`,
`revise`; `legacy` for versions from before agents), the scope as asked, what was in it (ids
and revisions of sources, saved answers and discussions), the outline, its citations, the
model that wrote it, and pins the saved answers it used (`artifact_unit_bindings`).

Its **manifest hash** (schema version 2) covers all of that plus the content. Versions from
the old builder (schema version 1, which pinned accepted knowledge only) are still verified
exactly as they were, by the service and by `scripts/backup_gunther.py verify`. A version
that fails its check is refused (409). Legacy versions are read-only apart from rebuilding
them: they have no citations or scope to edit.

## Changing a version

- **Rebuild.** The same controls, optionally with an outline edited in Gunther. The result is
  the next version.
- **Edit** (`POST …/artifacts/{id}/edits`). The person types in the Markdown editor beside a
  live preview; the text is saved exactly as typed (up to 2.5 MB). Nothing is renumbered or
  rewritten and no model runs. Findings stay with sections whose text did not change; the
  others show "Not re-checked".
- **Ask Gunther to change…** (`…/revise/stream`). An instruction, for the whole output or one
  section or slide. The writer gets each target section's current text and the instruction;
  it may answer `UNCHANGED`. The pool is the version's own citations (numbers kept) plus new
  research on the instruction within the scope the output was built from. Sections that
  change are checked as in a build; the others keep their text and their findings.
- **Check again** (`…/check`). Runs the audit and the support check on the sections that have
  no finding for their current text, **without changing the text**, and stores the result.

## Reading, slides and PDF

- Reports show on the page, section by section, with each section's findings. A number opens
  the evidence panel at its passage.
- Slides open in a reveal.js 6 deck (`@revealjs/react`): arrows at the two edges and progress
  inside the page, thumbnails to jump by, the presenter's notes under the deck, and **Present**,
  which fills the app's window with the deck (a layer of the page; the screen and the window's
  size are left alone, and Esc or ✕ leaves). Reveal's pop-up notes window is not used. Only the
  core `reveal.css` is imported; the deck's visual theme controls its appearance independently
  of the application's light or dark mode.
- A deck gets the whole width of the page (a report keeps its reading column), and its stage is
  never taller than the window.
- A slide is drawn on a 1280 × 720 canvas. When its text is too long for it, the type and
  spacing are scaled down together (`slideFit.ts`, down to 55%). Extreme content can still
  overflow. The viewer warns when text is substantially reduced or remains outside the content
  bounds, with suggestions to split the slide or move detail to notes. These messages are outside
  the slide and do not print. The fit cache includes the content and visual theme, so the deck,
  its thumbnails and the printed page agree.
- **Each layout has its own drawing** (`apps/desktop/src/outputs/slides/`): `parseSlide.ts`
  reads a slide's body for its layout's form (the rules of the service's `layout_shape`, with
  the same cases in `parseSlide.test.ts` and `tests/test_skillbook.py`) and one component per
  layout draws it: a title with a rule, a big number, a grid for `compare`, panels for
  `two_column`, numbered circles for `flow`, dots on a line for `timeline`, bars or a line for
  the charts, cards, a quote with a hanging mark, the managed image for `figure`, and large
  numerals for `takeaways`. Every piece of text is the slide's own Markdown, drawn by
  `AnswerBody`, so a citation stays a number that opens its evidence. The composed renderer
  (`scene.ts`, `ComposedSlide.tsx`) validates the source hash and renders only exact block
  references; stale or invalid trees fall back to the complete Markdown body with an outside-canvas
  update notice. A slide with no layout, or
  whose body does not take its layout's form, is drawn as its text, as before layouts; a table
  that cannot be a chart (a value that is not a number, rows in different units, fewer than two
  or more than six rows) is drawn as the table it is. A chart draws only what the table says:
  the row whose label is bold is in the accent and the rest in grey, a bar's value is at its tip,
  a line has its value at the end only and no numbers on its scale, and pointing at a mark shows
  its value and its source. `gallery.fixture.md` there has one slide of every layout.
- **The accent** is the deck's one colour: `ink` (the default, no colour), `blue`, `teal`, `red`,
  `amber` or `violet`, each at least 4.5:1 against white. It is written in the title slide's
  marker (`<!-- layout: title; accent: blue -->`) and applies to every slide. The Accent menu in
  the slides toolbar saves a change as an edit version (`withAccent` in `sections.ts`), for the
  latest version only.
- **Style** offers Classic, Briefing, Editorial and Keynote, with visual samples. The choice
  changes typography, paper, ink, spacing and cover treatment. It saves a new edit version by
  changing only the title marker's optional `theme` field; the accent, words, citations and notes
  are retained. Decks without a theme and unknown theme IDs render as Classic. The shared theme is used by
  preview, thumbnails, Present, edit preview and printing. Dark themes use corresponding light
  accent tones and contrasting text on colored marks. Rewrites, reopened work and rebuilds retain
  the chosen theme. The first release's selector is available after a deck exists.
- **Story relationships** are kept as an optional `link` in outline items. Slides after the first
  describe how they follow the preceding slide; these relationships reach the writer and editor,
  survive saving/reopening, and can be changed in the outline editor. Missing links and repeated
  headings are recorded as structural review findings, not proof of semantic correctness.
- **Page design** is a collapsible, editable part of the outline. It records what a page should
  communicate, how to show it, what to emphasize and what belongs in notes. Compatible layouts
  support `standard`, `split` and `spotlight` compositions, stored in each slide's marker.
  Theme changes, edits and printing retain these choices. Design prose guides writing and
  editing; it is never evidence. Automatically generated design prose omits temporary citation
  numbers, while the outline's source and card bindings retain the provenance.
- **Visual language** gives content roles a shared treatment across themes: equal groups,
  supporting text, structure, connections and local focus. The Slides skill's
  `references/visual-design.md` guides design, writing and editing through hierarchy, grouping,
  reading order and density. Peer columns stay aligned row by row; cards retain equal weight;
  flow steps can separate existing bold names from their explanations, with matching rows
  across two to four steps and a vertical sequence for five or six. Diagram origins identify
  the reading entrance without adding relationships or preferring a destination. Surfaces and
  lines derive from the chosen Theme and Accent. Design prose is realized through supported
  layouts, compositions and Markdown; it does not execute arbitrary positioning instructions.
- **Relationship diagrams** use a three-column Markdown table in start / relationship / end
  order, with a source citation on each relationship. Native nodes and arrows render supported
  small directed acyclic graphs: 2–6 nodes, 1–8 edges, up to three layers and three nodes per
  layer, with readable labels and no overlapping relationship labels or skipped layers.
  Conditions and directions must be supported by evidence; a source need not itself contain
  a drawing. Invalid or unverified graphs retain the complete table/text and record a fallback.
  The linear `flow` layout remains available for ordinary sequences.
- **Export PDF** prints a layout made for paper (`PrintRoot.tsx`): a report on A4 with
  superscript numbers and a list of sources; a deck as one 13⅓ × 7½ in page per slide, no
  notes, with a final page of sources. macOS's web view ignores `window.print()`, so the app
  calls its own `print_output` command; the print sheet's PDF button saves the file.
  Markdown can still be copied or downloaded.

## Streaming

`POST /api/knowledge-bases/{id}/outputs/build/stream` and
`…/artifacts/{id}/revise/stream` run as server-sent events: `outline`, `section`
(`researching`, `writing`, `checking`, `revising`, `done`), `step` (a search or a reading, with
its section), `text` (a section as it is written; the page hides its numbers until the finished
text replaces it), with a skill also `stage` (a step: `running`, `done` or `failed` with a
`detail`) and `approach`, then `done` with the saved version, `error` (`status`, `detail`) or
`stopped`. They use
Ask's `AnswerRuns` under the key `outputs:<library id>`:

- one build or revision per library at a time (a second gets 409);
- leaving the page only stops listening; `GET …/outputs/build/stream` follows a running one
  (204 when there is none);
- `POST …/outputs/build/stop` ends it, and nothing is saved.

## Limits

Plain defaults in `OutputLimits`; nothing else depends on them.

| Limit | Value |
| --- | --- |
| Sections in a report / slides in a deck | 8 / 14 |
| Searches per section | 3 |
| New passages per section | 8 |
| Passages in a build (of which seeded from saved answers and discussions) | 120 (40) |
| Search results graded together | 40 |
| Passages a writer sees | 30 |
| `[?]` claims looked up per section | 2 |
| Cited sentences in one support check | 40 |

## Failure is visible

No model → an error and nothing saved. A planner or writer that fails (a key refused, an
unreadable plan) fails the build with that message (502) and nothing is saved; the page
shows it with **Retry**, which sends the same request. A failed relevance, audit or support
call is logged and skipped, as in Ask. Nothing in Outputs fills in text when a model cannot.

## Known limits

- Whether a sentence is supported is a model judgement (the writer's, the audit's, the support
  check's), so it can miss one. A section under 40 characters skips the audit.
- Without a skill, outputs search the library only, not the web.
- A skill's layouts are written as Markdown the slide viewer already draws (tables, lists,
  quotes); charts are tables of their numbers until a renderer draws them.
- A typed edit is never checked until **Check again**.
- Printing depends on the web view honouring `@page`; macOS's does not always honour every
  margin or size.
