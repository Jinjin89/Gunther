from gunther.studio import format as fmt

GOOD = """<!-- layout: title; design: v2; theme: paper -->
# Cool roofs cut cooling demand, but less than the headline says
The 18% saving falls to 9% after weather adjustment [1][2]

---

<!-- layout: stat -->
## The pilot cut review time by 60%
**40 h → 16 h**
Median review time per case, before vs during [1]
Note: Lead with the drop.

---

<!-- layout: chart; chart: column -->
## Savings shrink once each summer's heat is accounted for
| Summer | Raw saving (%) | Adjusted saving (%) |
| --- | --- | --- |
| 2021 | 21 [1] | 10 [2] |
| **2023** | 16 [1] | 8 [2] |
The adjusted series is the one to plan with [2]
Note: Point at 2023.

---

<!-- layout: compare -->
## Green roofs win on stormwater, cool roofs on cost
### Cool roof
- €15–25 per m² [4]
### Green roof
- €80–150 per m² [4]
Pick green roofs only where stormwater binds [4]
Note: Two sides.
"""


def test_units_count_cjk_twice_and_ignore_citations():
    assert fmt.units("abc [1]") == 3
    assert fmt.units("屋顶") == 4
    assert fmt.units("**bold**") == 4


def test_a_good_deck_has_no_problems():
    assert fmt.check_deck(GOOD) == []


def test_problems_name_the_slide_and_the_fix():
    deck = GOOD.replace("**40 h → 16 h**", "40 h → 16 h")
    found = dict(fmt.check_deck(deck))
    assert list(found) == [2]
    assert "bold value" in found[2][0]


def test_over_budget_is_measured():
    long = "x" * 130
    deck = GOOD.replace("## The pilot cut review time by 60%", f"## {long}")
    (slide, problems), = fmt.check_deck(deck)
    assert slide == 2 and "Shorten it by 40" in problems[0]


def test_chart_cells_must_be_numbers():
    deck = GOOD.replace("| 2021 | 21 [1] |", "| 2021 | about twenty [1] |")
    found = dict(fmt.check_deck(deck))
    assert "not a number" in found[3][0]


def test_talk_slides_need_notes():
    deck = GOOD.replace("Note: Lead with the drop.", "")
    assert any("speaker notes" in p for p in dict(fmt.check_deck(deck))[2])
    assert fmt.check_deck(deck, notes_required=False) == []


def test_chart_numbers_carry_their_citations():
    slide = fmt.split_slides(GOOD)[2]
    assert fmt.chart_numbers(slide)[:2] == [("21", [1]), ("10", [2])]
    assert fmt.normal_number("1,200") == "1200"


def test_marker_is_replaced():
    slide = fmt.split_slides(GOOD)[2]
    assert fmt.with_marker(slide, "table").startswith("<!-- layout: table -->\n## Savings")


def test_report_form_problems():
    text = "<!-- report: v2 -->\n# T\n\n## Short claim\n- **Finding**: 9% [1]\n\n## " + "x" * 120
    problems = fmt.check_report(text)
    assert any("Section 2 heading" in p for p in problems)
    assert any("label" in p for p in problems)
    marked = fmt.ensure_summary_mark(text)
    assert "## Short claim\n<!-- block: summary -->" in marked


def test_copied_labels_are_stripped_but_not_from_notes():
    slide = "<!-- layout: title -->\n# T\nSubtitle: the point\nNote: Verdict: keep"
    assert fmt.strip_labels(slide) == "<!-- layout: title -->\n# T\nthe point\nNote: Verdict: keep"
