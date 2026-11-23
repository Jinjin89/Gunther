# Studio format: how a new deck and a new report are written down

Studio stores Markdown with a defined grammar for each layout. The backend writer,
in-app renderer and exporters use that content for citations, checking and editing.
Saved markers select the format and renderer for each version. [Outputs](OUTPUTS.md)

## 1. Decks (design v2)

A deck is slides separated by a line `---`. Each slide starts with a marker line:

```
<!-- layout: <id>[; key: value]... -->
```

The **title slide's** marker carries `design: v2` to select the Studio renderer. It can also
carry `theme: paper|ink|editorial|brand` (default `paper`) and `accent: <name>` (default
`blue`). Other slide markers name the layout and its options.

Every slide except `title` and `section` starts with `## <title>` (the action title: a full
sentence carrying the point). Speaker notes come last, after a line starting with `Note:`
(everything after it belongs to the notes). Citations are `[n]` (a source number) or `[?]`
(unverified model knowledge) and may appear in any text.

The renderer draws citation marks as small superscripts and adds a **source line** at the foot of
the slide listing the sources cited on that slide ("Sources: Harlow 2024, p. 3; Okafor 2025").

### Units and budgets

Wide and fullwidth characters count as two units. Other characters count as one, and
citation markers and emphasis do not count. Most body budgets allow 1.5× in `read` decks.
The format checker asks for a shorter slide when needed and records unresolved format problems.
Decks render at 1280×720. [Format checks](../apps/backend/gunther/studio/format.py)

| Field | Units |
| --- | --- |
| Action title (`## …`) | 12–90 |
| Title slide | Title 8–80, subtitle up to 160, at most one metadata line |

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

**`section`** — a divider between parts with a title of 4–80 units and up to one line beneath it.
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
Image line, then a takeaway. A figure with explanation bullets uses the fixed side-column
budget. `figure_detail` uses a larger column and three to six explanation bullets. The
figure can carry a source-backed panel crop.

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

### Designed slides and saved scenes

The cover can declare a `style` of `light`, `dark` or `bold`. A slide can end with a
`:::design` block containing its HTML. The fact guard checks the HTML against the verified
Markdown. It rejects unsafe content and checks that the title, visible text, numbers and
citations are kept. The renderer sanitizes the HTML and supplies a managed chart for the
`<gunther-chart>` placeholder. Slides without an accepted design use the template.
[Design rules](../apps/backend/gunther/studio/design.py) · [Renderer](../apps/desktop/src/outputs/deck/DesignedSlide.tsx)

The reader also accepts saved Markdown layout and scene markers. A scene's tree positions
blocks by ID. Its source hash and, for version 2 scenes, graphics hash bind it to the
slide content. An invalid or stale scene falls back to its Markdown layout. Edits keep
the marker format of the version being edited. [Scene reader](../apps/backend/gunther/slide_scene.py)

## 2. Reports (design v2)

A report is Markdown: `# Title`, an optional italic line, then `## ` sections. A report in this
design starts with a line `<!-- report: v2 -->` before the title. It supports a summary,
exhibits and callouts.

**Executive summary for Report and Briefing doc.** The first section is `## <heading>` immediately followed by the marker
line `<!-- block: summary -->`. The renderer draws it as a summary box. It holds 1 short
paragraph (the answer) and 3–5 bullets `**finding**: detail [n]`.

Study guide, FAQ and Timeline start with their first topic, question or dated event. The
summary marker and numeric exhibit requirement apply to Report and Briefing doc.

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

Report and Briefing doc headings state claims. Sub-headings use `###`. Source citations use `[n]`.

Each number identifies a passage from the evidence pool. Studio can use checked key results to locate those passages and frame the draft. The saved citation identifies the original source revision and block. [Build in Studio](OUTPUTS.md#build-in-studio)

## 3. Exports (desktop)

- **PPTX** uses editable text and tables, native charts for template slides, and speaker
  notes. A designed slide exports a picture of the visual layer with editable text boxes
  placed over it. [Deck export](../apps/desktop/src/outputs/deck/exportPptx.ts)
- **DOCX** keeps headings, summary and callout boxes, exhibit tables and a Sources list.
  Citation marks are superscript numbers. [Report export](../apps/desktop/src/outputs/deck/exportDocx.ts)
- **PDF** uses the print renderer. The same content can be copied or downloaded as Markdown.

## 4. Gallery

Developer render and export checks use the short, maximum, CJK and designed fixtures.
The backend grammar tests share the deck inputs. [Fixtures](../apps/desktop/src/outputs/deck/fixtures/) · [Slide evals](../apps/backend/evals/slides/README.md)
