# ruff: noqa: E501
"""The format as the writer is shown it: a compact copy of docs/STUDIO_FORMAT.md. Length limits
are in units: a Latin character is 1, a CJK character 2; citation marks count nothing."""

DECK_FORMAT = """\
GUNTHER SLIDE FORMAT
Slides are separated by a line "---". Each starts with a marker line <!-- layout: ID -->. \
Titles never carry citation marks. Content slides then have "## Action title": one sentence, ideally 8–14 words, at most 90 units, no semicolons stacking two points. Speaker notes come last, after \
a line starting with "Note:". Citations [n] go right after the claim they support, inside \
any line or table cell.

title (first slide only):
<!-- layout: title; design: v2; theme: paper -->
# Deck title (≤80)
The core message in one line (≤160)
Committee briefing · March 2026
(Line 2 is the subtitle, line 3 an optional meta line with no citations. Never write labels such as "Subtitle:", "Meta:", "Verdict:", "Takeaway:" or "Label:" — every example line below shows content only.)

section:
<!-- layout: section -->
# Part title (≤80)
One line (optional)

summary (2–4 items; lead ≤60, detail ≤130):
## Title
- **Lead finding**: detail with number [n]

stat (value ≤16, label ≤90, context ≤160 optional):
## Title
**40 h → 16 h**
Median review time per case, before vs during the pilot [n]
Two teams over eight weeks; error rates were not tracked [n]

stats (2–4 items; value ≤10, label ≤60; closing line ≤140 optional):
## Title
- **412** buildings in six districts [n]
- **3** summers, 2021–2023 [n]

chart (marker option chart: bar | column | line; a table: first column labels, 1–3 numeric \
columns with the unit in the header; 2–12 rows; every value a plain number with its \
citation; bold label = highlighted row, at most one; takeaway line ≤140 optional):
<!-- layout: chart; chart: column -->
## Title
| Summer | Raw saving (%) | Adjusted saving (%) |
| --- | --- | --- |
| 2021 | 21 [1] | 10 [2] |
| **2023** | 16 [1] | 8 [2] |
The adjusted series is the one to plan with [2]

table (2–5 columns, 2–7 rows, cells ≤50; bold row = highlighted; takeaway ≤140 optional):
## Title
| | Option A | Option B |
| --- | --- | --- |
| Upfront | €1.2 m [2] | €0.8 m [2] |
A costs more upfront but less from year two [2]

compare (exactly two sides: "### name" ≤30, then 1–4 items ≤90 each; verdict line ≤140 \
optional after both):
## Title
### Cool roof
- €15–25 per m² [4]
### Green roof
- €80–150 per m² [4]
Pick green roofs only where stormwater is the binding problem [4]

process (2–6 numbered steps; step ≤24, detail ≤90):
## Title
1. **Survey**: roof type and insulation [5]
2. **Coat**: two layers in spring [5]

timeline (3–6 items; when ≤16, event ≤90):
## Title
- **2019** Heatwave; 31 excess deaths [6]
- **2021** Trial starts [1]

quote (quote ≤240, attribution ≤90):
## Title
> Exact words from the material.
— Who, where [n]

figure (only with a figure listed in the material; copy its reference exactly; takeaway ≤160):
## Title
![caption](gunther-asset:ID)
The gap opens above 30 °C outside [n]

statement (statement ≤180; up to 3 items ≤80 optional):
## Title
Coatings lose a third of their reflectivity in 18 months [n]

points (2–4 items; lead ≤40 optional, detail ≤120) — last resort:
## Title
- **Lead**: detail [n]

actions (2–4 numbered items; lead ≤24, detail ≤100):
## Title
1. **Approve** coatings for 1,200 flats [7]

Every content slide ends with:
Note: What the presenter says…"""

READ_NOTE = (
    "This deck is for reading: body limits are 1.5× those above (titles unchanged), and full "
    "short sentences are fine; notes are optional."
)

REPORT_FORMAT = """\
GUNTHER REPORT FORMAT
<!-- report: v2 -->
# Report title
_One line saying who it is for and what it answers._

## Fund cool roofs, but plan on a 9% saving
<!-- block: summary -->
One short paragraph with the answer [n].
- **Savings halve after weather adjustment**: 18% raw vs 9% adjusted [1][2]
- **The benefit sits on the top floor**: peak indoor temperature 2.1 °C lower [1]
(Each bullet's bold lead is the finding itself, never a label like "Finding".)

## Weather, not the coating, explains half the saving
(Headings: one claim, ideally 6–12 words, at most 90 units.)
Paragraphs. Citations [n] right after each claim. "###" for sub-headings only.

:::exhibit chart=column
Exhibit 1 — What the exhibit shows, as a statement
| Label | Series (unit) | Series 2 (unit) |
| --- | --- | --- |
| A | 21 [1] | 10 [2] |
| B | 17 [1] | 9 [2] |
Source: short source names [1][2]
:::

(chart= is bar, column or line; leave it out for a plain table. A chart's values are plain \
numbers with the unit in the header; 2–12 rows; 1–3 series.)

:::callout Short label
One or two sentences the reader must not miss [n].
:::

## Implications / Recommendations heading as a claim
..."""
