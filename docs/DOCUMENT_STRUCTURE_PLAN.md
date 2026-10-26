# Document structure: one shape, built top-down

Status: agreed with the user on 2026-10-25; being built. Supersedes the
role-guessing passes of `document_structure.py` for PDFs parsed by Docling.

## Why

Every reader problem so far (garbled formulas, page headers as headings, logos
shown as figures, affiliations in the text, references broken in two) came from
the same design: about eight passes each guess and rewrite the `kind` of blocks
in one flat list, and every feature (reader, search, paper card, Ask) filters
the result its own way. Each fix added a pass. This plan replaces that with one
target shape, one owner of structural decisions, and checks that enforce the shape.

## Principles

1. **One shape for every document.** The common core of JATS, TEI, DocBook,
   HTML5 and Pandoc: metadata; front / body / back; the body is a tree of
   sections; a closed set of blocks. Page furniture is never part of it.
2. **Document types change expectations, not the shape.** A paper, book,
   report, slide deck, web article, manual/contract or unknown document uses the
   same shape; its type decides what is expected and what counts as junk.
3. **Evidence is read-only.** Docling plus the PDF's own glyphs give numbered
   blocks with exact text and positions. Characters are repaired there, once
   (`pdf_native.py`). Nothing later edits a block.
4. **Only the plan decides structure.** Rules draft it; the Analysis model
   corrects it. Both move block ids. Neither writes or rewrites text.
5. **Assembly cannot build what the shape forbids.** A figure is image +
   caption (+ number); references are one list; every block is placed once or
   dropped with a reason.
6. **Checks are explicit.** Every plan is checked; problems are named
   violations, shown to the model once for correction and to the user if they
   remain.
7. **Dropped means gone.** Page furniture, logos, affiliations, e-mails and
   back matter such as competing interests are not stored in the document, not
   searched and not shown. The original PDF stays in the library.
8. **No model, no pretending.** Without a model the reader shows the rule draft,
   labelled "not checked"; a failed check is shown with Try again. There is no
   rule-made stand-in presented as the model's result.

## The shape

```
Document
  type:      paper | book | report | slides | article | manual | unknown
  metadata:  title, subtitle, authors, date, doi/isbn/arxiv, publisher  (from PDF metadata + plan)
  front:     abstract | summary | keywords | preface | other           (only what the type has)
  body:      sections: heading (or none) + level + blocks, in reading order
  back:      references | appendix | availability | notes | glossary | other
  figures:   number, image(s), caption, labels printed around the image
  tables:    number, table or image, caption, table footnotes
  notes:     marker + text (footnotes that are content, e.g. in books)
blocks: paragraph, list item, table, figure, formula, code, quote, note, reference
```

### Document types

| Type | Front | Body | Back | Type rules |
|---|---|---|---|---|
| paper | title, authors, abstract, keywords | sections | references, appendix, data/code availability | uncaptioned images are decoration; drop affiliations, e-mails, received/accepted lines, competing interests, author contributions, funding, acknowledgements, publisher notes, licences, reprint and peer-review notices |
| book / thesis | title, preface | chapters → sections | bibliography, appendix | footnotes are notes (kept); the printed contents and index pages are dropped (the reader builds its own contents) |
| report | title, organisation, date, summary | sections | appendices | boxes are content |
| slides | title slide | one section per slide | — | images are content even without captions |
| article (web) | title, byline, date | paragraphs, images | — | drop navigation, related links, share buttons |
| manual / standard / contract | title, version | numbered clauses (3.2.1 → level 3) | annexes | numbering is the structure |
| unknown | title if any | heading tree | — | core rules only |

## Layers and contracts

All shared types live in `apps/backend/gunther/document_model.py`.

### 1. Evidence (`document_evidence.py`)

`build_evidence(docling_json, metadata) -> Evidence` walks Docling's body and
furniture trees in Docling's reading order and gives one `Block` per text,
picture, table or formula item, ids `b0, b1, …` in that order.

`Block` fields: `id`, `label` (Docling's layout label, see `LABELS`), `text`
(native glyph text when `pdf_native` accepted it, else Docling's), `page`,
`box` (first region, parts per million: left, top, right, bottom), `regions`
(every region, for provenance), `font_size`, `level` (layout/bookmark heading
level), `inline` (sub/superscript spans), `marker` (list marker), `inside` (id
of the picture this text is printed in), `image_key` (Docling ref for picture,
table and formula crops), `table` (cells), `clues` (rule evidence, below),
`extra` (`documentRef`, extraction details).

Text printed inside a picture (`inside` set) is part of that picture: it is not
placed in the plan, never shown as text, and kept on the figure for search.

### 2. Clues and the rule draft (`structure_draft.py`)

`compute_clues(evidence)` attaches measurable facts to blocks, as short strings:

- `repeats:top` / `repeats:bottom` — same text at the same page edge on 2+ pages
- `edge:top` / `edge:bottom`, `page-number`
- `small-type` / `large-type` — font size against the body size
- `margin-column` — narrow small-type column beside the body column
- `caption:figure:3`, `caption:table:2` — a caption label and its number
- `no-caption`, `repeats-image` — pictures with no caption / at the same place on several pages
- `reference-entry` — looks like one bibliography entry
- `note-marker` — small type at a page foot starting with a note marker
- `first-page`, `before-title`
- `heading:abstract`, `heading:back-matter` — a heading naming these parts
- `continues:b87` — a sentence broken by a column or page break continues here
- `stray-glyphs` — a lone glyph or a string with no letters

`draft_plan(evidence) -> Plan` turns clues into a complete, valid plan:
type guess, title, authors, abstract, sections from headings, figures
caption-first (a caption with a number claims the picture(s) and labels next to
it), tables, one reference list, joins, notes, and drops with reasons. It reuses
the rules built on 2026-10-25 (`page_roles`, `mark_abstract`, caption matching,
sentence continuation) as clue and draft logic. The draft must always pass
`check()` without errors.

### 3. The plan (`Plan` in `document_model.py`)

JSON, the same for the rule draft and the model:

```json
{
  "type": "paper",
  "title": ["b6"], "subtitle": [], "authors": ["b7"],
  "front": [{"part": "abstract", "heading": null, "blocks": ["b8"]}],
  "body": [
    {"heading": null, "level": 1, "blocks": ["b10", "b16..b19"]},
    {"heading": "b20", "level": 1, "blocks": ["b21", "F1", "b22"]}
  ],
  "back": [{"part": "references", "heading": "b911", "blocks": ["b912..b960"]}],
  "figures": [{"id": "F1", "number": "1", "images": ["b28"], "caption": ["b29"], "labels": []}],
  "tables": [],
  "notes": [],
  "joins": [["b87", "b88"]],
  "drop": [{"blocks": ["b0..b5", "b9", "b11..b15"], "reason": "page furniture"}],
  "by": "rules"
}
```

- Id lists accept ranges `bA..bB` (inclusive, in evidence order).
- Figures and tables are defined once and placed once, by id, in a section or
  back part where they belong.
- `notes` are rendered together after the body.
- Blocks with `inside` set are not listed anywhere.

### 4. Check (`check()` in `document_model.py`)

Returns `Violation(severity, code, message, blocks)`; messages are plain words
because the model and the user read them.

Errors (a plan with errors is never assembled):
`unknown-id`, `duplicate`, `unplaced`, `heading-not-text`, `figure-without-image`,
`image-not-picture`, `caption-not-text`, `float-not-placed`, `float-placed-twice`,
`join-not-adjacent`, `bad-range`, `table-without-content`, `unknown-part` (a part name
outside the lists above).

Warnings (assembled, shown):
all types — `no-title`; paper — `no-abstract`, `no-references`,
`figure-numbers-gap`, `figure-without-caption`, `duplicate-figure-number`.

### 5. Model correction (`structure_model.py`, `document_profiles.py`)

`StructureReviser.revise(evidence, draft, source_id=…) -> (Plan, StructureStatus)`

- Input: one preview line per placeable block, e.g.
  `b21 | p2 | text | 9.5pt | x.05-.48 y.10-.35 | "Many computational approaches…" (812) | first-page`
  then the draft plan JSON, and the type guides from `document_profiles.py`.
- Output: the corrected `Plan` (same JSON). `gateway.complete_json` already
  retries once on malformed JSON.
- `check()` runs on the answer; errors go back once as a follow-up turn
  ("b88 is not placed; figure F6 has no image"). Still invalid → `StructureError`.
- Model: the Analysis role (`gateway.for_role("analysis")`), metered with
  `usage.job("analysis", "structure", ref=source_id)`.
- Budget: more than 3,000 placeable blocks or 240,000 preview characters →
  `StructureTooLong`; the draft is used and labelled. (Windowed checking of
  long books is a later phase.)
- Development and tests use fake gateways only. No live model runs on this
  machine; when one is allowed, DeepSeek Flash only.

### 6. Assemble (`assemble()` in `document_model.py`)

`assemble(plan, evidence, status) -> ParsedDocument`, elements in reading order
(front, body, notes, back; notes and back matter sit under no body heading). Stored element contract (what search, the paper card
and the reader rely on):

| kind | text | payload |
|---|---|---|
| `heading` | heading text | `level`; `role`: `title` / `subtitle` / front or back part name (e.g. `abstract`, `references`) / none |
| `metadata` | authors, keywords | `role`: `authors` (affiliation markers removed using superscript spans) / `keywords` |
| `paragraph` | exact text (joined parts for `joins`) | `role`: `abstract` / `summary` / other front-back part / none |
| `list_item` | text | `marker` |
| `code` | text | — |
| `equation` | transcription or "" | `imageKey`, `transcription` |
| `figure` | caption text | `number`, `caption`, `imageKey`, `figureText` (labels + text inside the picture) |
| `table` | caption, then cell text (so search finds both) | `table`, `number`, `caption`, `footnotes`, `imageKey` when the table is a picture (cropped like a figure) |
| `note` | note text | `marker` |
| `reference` | one entry | `reference: true` |

Every element payload also has `part` (`front` / `body` / `back` / `notes`),
`blockIds` (provenance) and `documentRef`; anchors keep page, box and regions.
Figures carry the union box of their images and labels on the first image's
page with `cropSource: "plan-figure"`, so `attach_document_images` crops them
from the sharp page render.

Manifest: `schema: 2`, `documentType`, `structure` (`state`: `checked`,
`draft`, `needs-key`, `failed`, `too-long`; `by`; `error`; `violations`),
`outline`, `metadata`, `bodyFontSize`, `pageCount`.

### 7. In the worker (`document_parser.py`, `processing.py`)

The figure crops come from page images in Docling's temporary folder, so the
whole structure step runs inside the parse job, before that folder is removed:

1. Quick native revision is published at once (unchanged).
2. Docling runs (unchanged).
3. `build_evidence` → `compute_clues` → `draft_plan`.
4. With an Analysis model: `revise` → state `checked`; on failure state `failed`
   with the error, and the draft is used. Without one: state `needs-key`.
   Too long: state `too-long`.
5. `assemble` → crops → publish once → embeddings, paper card and summary as now.

Try again = Read again (the existing reprocess endpoint). Organizing passes
(`organize_document`) do not run on schema-2 documents. Non-PDF formats keep
their current path for now.

### 8. Consumers

- Reader: renders elements in order; title, authors line, Abstract (labelled
  when the paper prints no heading), sections, figures, tables, formulas, notes,
  references; a one-line structure status with Try again when it failed.
- Search and embeddings: unchanged code; dropped blocks never reach them.
- Paper card (`papers.py`): title, authors, abstract and outline come from the
  roles above when present, instead of being detected again.

## Build order and ownership

| Package | Owner files | Depends on |
|---|---|---|
| A. Check + assemble | `document_model.py` (functions), `tests/test_document_model.py` | the types in `document_model.py` |
| B. Evidence + clues + draft | `document_evidence.py`, `structure_draft.py`, `tests/test_structure_draft.py` | types; A's `check` for its final run |
| C. Model correction | `structure_model.py`, `document_profiles.py`, `tests/test_structure_model.py` | types; A's `check` |
| D. Reader | `CanonicalReader.tsx` (+ test), `DocumentLayoutStatus.tsx` (+ test), `item.css` | the element contract above |
| E. Integration | `document_parser.py`, `processing.py`, `papers.py`, `model_registry.py`, worker wiring, their tests | A, B, C |

A–D run in parallel; E after A–C. Nobody edits another package's files.
No commits until the user asks.

## Testing

- Unit tests per package; fake model gateways only.
- Regression set (on this machine, `/tmp/gunther-papers`, parsed Docling output in
  `/tmp/gunther-harness/out/*/raw.json`): Nature Communications ×2, Scientific
  Reports, PLOS ONE, PLOS Comp Bio, Frontiers, eLife, arXiv ×2, Nature Machine
  Intelligence. For each: the draft passes `check()`; title and abstract found
  where printed; figure numbers match the PDF's captions; one reference list with
  the PDF's count; no furniture, logos or affiliations in the output.
- Later: add a book chapter, a report, a slide deck, a contract and a Chinese
  document so the core does not become paper-shaped.

## Later

- Non-PDF formats (Word, EPUB, HTML) as evidence for the same plan and assembly.
- Windowed model checking for long books.
- Cache Docling's output so Try again does not run Docling again.
- Remove the old role passes once schema-2 documents are verified on the Mac.

## Known limits (2026-10-25, rules only; no model has checked a plan yet)

- Run-in headings (a bold phrase that opens a paragraph, common in Nature
  journals) stay inside their paragraph: a plan cannot split a block below
  the printed region.
- A real hyphen that falls at a line end is removed with the line break
  ("English-to-German" reads "Englishto-German").
- A DOI or similar line that the layout parser prints inside a caption region
  stays in the caption.
- The model's plan has no explicit output-token limit; a very long document
  could exceed a provider's default and end as a failed check (the draft is
  then shown, with Try again).
