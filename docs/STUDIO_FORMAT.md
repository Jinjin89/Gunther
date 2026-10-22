# Studio format: how a new deck and a new report are written down

Part of `ARTIFACT_QUALITY_GOAL.md`. This is the contract between the backend writer
(`gunther/studio/`), the in-app renderer, and the PPTX/DOCX exporters. Content stays plain
Markdown (so the claim checker, citations, versions and the text editor keep working), with a
strict grammar per layout. Old artifacts keep their old format and old renderer.

## 1. Decks (design v2)

A deck is slides separated by a line `---`. Each slide starts with a marker line:

```
<!-- layout: <id>[; key: value]... -->
```

The **title slide's** marker carries `design: v2` (this is what selects the new renderer) and
optionally `theme: paper|ink|editorial` (default `paper`) and `accent: <name>` (one of the
existing accent names; default `blue`). Other slides' markers carry only the layout and the
options listed for that layout.

Every slide except `title` and `section` starts with `## <title>` (the action title: a full
sentence carrying the point). Speaker notes come last, after a line starting with `Note:`
(everything after it belongs to the notes). Citations are `[n]` (a source number) or `[?]`
(unverified model knowledge) and may appear in any text.

The renderer draws citation marks as small superscripts and adds a **source line** at the foot of
the slide listing the sources cited on that slide ("Sources: Harlow 2024, p. 3; Okafor 2025").

### Units and budgets

A "unit" is one Latin character or half a CJK character (a CJK character counts 2 units). The
budgets below are hard limits for `talk` decks; `read` decks may use 1.5× the body budgets
(titles keep theirs). The backend rejects a slide over budget and asks for a shorter one; the
renderer must lay out content at its full budget without overflow at 1280×720 in every theme
(the gallery in §4 proves this).

| Field | Units |
| --- | --- |
| Action title (`## …`) | 12–100 |
| Title slide title | 8–80; subtitle ≤ 160; meta ≤ 80 |

### Layouts

Grammar shown with an example; `[n]` positions are examples. "Item" means a list item.

**`title`** — first slide only.
```
<!-- layout: title; design: v2; theme: paper; accent: blue -->
# Cool roofs cut cooling demand — but less than the headline says
The trial's 18% saving falls to 9% once hotter summers are accounted for [1][2]
Briefing for the city energy committee · March 2026
```
Line 1 `# title`; line 2 subtitle (optional); line 3 meta (optional, no citations).

**`section`** — a divider between parts (title 8–80 units, line ≤ 160).
```
<!-- layout: section -->
# What the trial measured
How 412 buildings were compared over three summers
```

**`summary`** — executive summary: the answer and its 2–4 supporting findings.
```
<!-- layout: summary -->
## Cool roofs work, but the saving is half the headline figure
- **Savings halve after weather adjustment**: 18% raw vs 9% adjusted cooling electricity [1][2]
- **Most of the gain is in top-floor flats**: indoor peak 2.1 °C lower on the top floor [1]
- **Coatings age fast**: albedo falls from 0.82 to 0.55 within 18 months [3]
```
2–4 items; each `**lead**: detail`. Lead ≤ 60 units, detail ≤ 130.

**`stat`** — one number that carries the slide.
```
<!-- layout: stat -->
## The pilot cut review time by 60%
**40 h → 16 h**
Median review time per case, before vs during the pilot [1]
Two teams over eight weeks; error rates were not tracked [2]
```
Line 1 bold value ≤ 16 units; line 2 label ≤ 90; line 3 context (optional) ≤ 160.

**`stats`** — 2–4 key numbers side by side.
```
<!-- layout: stats -->
## The trial was large enough to trust the direction
- **412** buildings in six districts [1]
- **3** summers, 2021–2023 [1]
- **0.82** starting albedo of the coating [3]
Each treated building was matched to an untreated one of the same height and age [1]
```
2–4 items `**value** label`; value ≤ 10 units, label ≤ 60. Optional closing line ≤ 140.

**`chart`** — numbers drawn as a chart. Options: `chart: bar` (horizontal bars, default),
`column`, `line`. `unit: <text>` optional.
```
<!-- layout: chart; chart: column -->
## Savings shrink once each summer's heat is accounted for
| Summer | Raw saving (%) | Adjusted saving (%) |
| --- | --- | --- |
| 2021 | 21 [1] | 10 [2] |
| 2022 | 17 [1] | 9 [2] |
| **2023** | 16 [1] | 8 [2] |
The adjusted series is the one to plan with [2]
```
A table: first column labels (2–12 rows), then 1–3 numeric series columns (header names the
series and unit). Cells are a number optionally followed by citation marks; a number may carry
`%` or a unit word. Bold label = highlighted row (at most one). Optional closing line ≤ 140.
`line` needs ordered labels (years, months, steps). Density: rows × series ≤ 12 for `bar`, ≤ 18 for `column`. A table that is not numeric renders as `table`.

**`table`** — a comparison table.
```
<!-- layout: table -->
## Option A costs a third less over three years
| | Option A | Option B |
| --- | --- | --- |
| Upfront | €1.2 m [2] | €0.8 m [2] |
| 3-year running | €0.9 m [4] | €2.3 m [4] |
| **Total** | **€2.1 m** | **€3.1 m** |
A is dearer to start but cheaper from year two [2][4]
```
2–5 columns, 2–7 body rows, each cell ≤ 50 units and each row ≤ 100 units in total. Bold row = highlighted. Closing line ≤ 140.

**`compare`** — two sides on the same terms (before/after, option vs option, for/against).
```
<!-- layout: compare -->
## Green roofs win on stormwater, cool roofs on cost
### Cool roof
- €15–25 per m² [4]
- Needs recoating every 5–8 years [3]
### Green roof
- €80–150 per m² [4]
- Holds 50–70% of rainfall [4]
Pick green roofs only where stormwater is the binding problem [4]
```
Exactly two `### side` blocks (side name ≤ 30 units), each 2–4 items ≤ 90. Optional verdict line
after both ≤ 140.

**`process`** — ordered steps (2–6).
```
<!-- layout: process -->
## Four steps take a building from survey to savings
1. **Survey**: roof type, insulation and top-floor occupancy [5]
2. **Coat**: two layers of reflective coating in spring [5]
3. **Measure**: indoor temperature and meter readings all summer [1]
4. **Recoat**: when albedo drops below 0.6 [3]
```
Items `**step**: detail`; step ≤ 24 units, detail ≤ 90.

**`timeline`** — dated events (3–6).
```
<!-- layout: timeline -->
## Interest grew after the 2019 heatwave
- **2019** Heatwave; 31 excess deaths in top-floor flats [6]
- **2021** Trial starts in six districts [1]
- **2024** Final report published [1]
- **2026** Climate plan adopts cool roofs for public housing [7]
```
Items `**when** event`; when ≤ 16 units, event ≤ 90.

**`quote`**
```
<!-- layout: quote -->
## Building managers care about complaints, not kilowatt-hours
> We stopped getting calls from the top floor. That's what sold it to the board.
— Building manager, river district [8]
```
Quote ≤ 240 units; attribution line ≤ 90.

**`figure`** — a figure kept from the sources (`gunther-asset:` reference, copied exactly).
```
<!-- layout: figure -->
## Indoor peaks fell most on the hottest days
![Figure 3: Peak indoor temperature, treated vs control](gunther-asset:ast_3f9a1c)
The gap opens above 30 °C outside [1]
```
Image line, then takeaway line ≤ 160.

**`statement`** — one big sentence (a key question, a turning point, the core message).
```
<!-- layout: statement -->
## The real question is ageing, not the first summer
Coatings lose a third of their reflectivity in 18 months, so lifetime savings depend on recoating [3]
```
Statement ≤ 180 units. Optional up to 3 items ≤ 80 below it.

**`points`** — up to 4 parallel points. The fallback; use sparingly.
```
<!-- layout: points -->
## Three things limit what the trial can tell us
- **No quality data**: only electricity and temperature were measured [1]
- **One city**: river-district blocks are older than the city average [9]
- **Short horizon**: three summers, so ageing beyond 18 months is projected [3]
```
2–4 items, `**lead**: detail` (lead optional); lead ≤ 40, detail ≤ 120.

**`actions`** — the close: decisions or next steps (2–4).
```
<!-- layout: actions -->
## Approve cool roofs for top-floor public housing, with a recoating budget
1. **Approve** coatings for the 1,200 top-floor public flats [7]
2. **Budget** recoating every six years [3]
3. **Measure** comfort complaints, not only electricity
```
Items `**verb/lead** detail`; lead ≤ 24, detail ≤ 100.

### Notes

```
Note: Lead with the drop from 18% to 9%. Pause. Then explain why: 2021 was the coolest of the
three summers. Hand over to the next slide on where the savings concentrate.
```
Talk decks: every content slide has notes (3–6 sentences, what to say). Read decks: optional.

## 2. Reports (design v2)

A report is Markdown: `# Title`, an optional italic line, then `## ` sections. A report in this
design starts with a line `<!-- report: v2 -->` before the title. Two block extensions:

**Executive summary**: the first section is `## <heading>` immediately followed by the marker
line `<!-- block: summary -->`. The renderer draws it as a summary box. It holds 1 short
paragraph (the answer) and 3–5 bullets `**finding**: detail [n]`.

**Exhibit** (numbered chart or table with a source line):
```
:::exhibit chart=column
Exhibit 2 — Savings by summer, raw vs weather-adjusted
| Summer | Raw (%) | Adjusted (%) |
| --- | --- | --- |
| 2021 | 21 [1] | 10 [2] |
| 2022 | 17 [1] | 9 [2] |
Source: Harlow 2024; Okafor 2025 [1][2]
:::
```
First line inside: caption. Then a table (same rules as the deck `chart` / `table`). Last line
`Source: …`. `chart=` is `bar|column|line`, or absent for a plain table. Exhibits are numbered by
the writer in order.

**Callout**:
```
:::callout Why the adjusted figure matters
A city budgeting on 18% would overestimate savings by half [2].
:::
```

Headings are claims. Sub-headings use `###`. Footnotes are not used; citations are `[n]`.

## 3. Exports (desktop)

- **PPTX** (pptxgenjs): one slide per deck slide, the same layout geometry, real text boxes,
  native charts for `chart`, tables as real tables, speaker notes as notes, the source line as
  a small text box, fonts declared (system fonts per theme; CJK falls back to the platform's
  CJK font). Citations become superscript numbers.
- **DOCX** (docx): title, summary box as a shaded table cell, headings, paragraphs with
  superscript citation numbers, exhibits as real tables (charts as a table plus caption — Word
  charts are out of scope), callouts as shaded boxes, a "Sources" list at the end.
- **PDF**: the existing print path draws the same components.

## 4. Gallery

A development page (not in the app UI) renders every layout × theme at short, budget-maximum
and CJK content, plus the same as PPTX → PNG through LibreOffice for parity. The fixtures live in
`apps/desktop/src/outputs/deck/fixtures/` and are shared with the backend's grammar tests.
