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
- Applied in a day or two [4]
### Green roof
- €80–150 per m² [4]
- Holds 40–60% of rainfall [4]
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


def test_the_desktop_fixtures_keep_the_backend_grammar():
    from pathlib import Path

    folder = Path(__file__).parents[2] / "desktop/src/outputs/deck/fixtures"
    for name in ("deck.short.md", "deck.max.md", "deck.zh.md"):
        slides = fmt.split_slides((folder / name).read_text(encoding="utf-8"))
        for index, text in enumerate(slides):
            problems = fmt.check_slide(fmt.parse_slide(text), first=index == 0)
            # The renderer is tested at 100 units of title; the writer is held to 90.
            problems = [p for p in problems if not p.startswith("title is")]
            assert problems == [], (name, index + 1, problems)


def test_an_exhibit_caption_with_numbers_cites_its_table():
    text = (
        ":::exhibit chart=bar\nExhibit 2 — Savings fall from 18% to 9%\n| A | B (%) |\n"
        "| --- | --- |\n| raw | 18 [1] |\n| adj | 9 [2] |\nSource: X [1][2]\n:::"
    )
    assert "Exhibit 2 — Savings fall from 18% to 9% [1][2]" in fmt.cite_captions(text)


def test_writing_about_the_material_is_a_format_problem():
    deck = GOOD.replace(
        "## The pilot cut review time by 60%", "## What the evidence shows about review time"
    )
    assert any("about the world" in p for p in dict(fmt.check_deck(deck))[2])


def test_uncited_exhibit_rows_cite_the_source_line():
    text = (
        ":::exhibit\nExhibit 1 — Growth\n| 指标 | 增速 |\n| --- | --- |\n| GDP | +5.0% |\n"
        "| CPI | +0.2% [3] |\nSource: 统计公报 [2]\n:::"
    )
    out = fmt.cite_exhibit_rows(text)
    assert "| GDP | +5.0% [2] |" in out and "| CPI | +0.2% [3] |" in out
    assert "| 指标 | 增速 |" in out


def test_a_title_that_announces_content_is_sent_back():
    deck = GOOD.replace(
        "## The pilot cut review time by 60%", "## Three findings change the decision"
    )
    assert any("announces" in p for p in dict(fmt.check_deck(deck))[2])


def test_a_slide_still_over_budget_moves_its_tail_clause_into_the_notes():
    long = "Raw cooling electricity fell 18% across the trial buildings, " * 2
    slide = (
        "<!-- layout: statement -->\n## Weather explains half of the headline saving\n"
        f"{long.strip()}; most of the gain sat in a few buildings with large systems [1]\n"
        "Note: Say it plainly."
    )
    trimmed = fmt.trim_to_notes(slide, first=False)
    body = fmt.parse_slide(trimmed)
    assert fmt.units(body.body[0]) <= 180 and "[1]" in body.body[0]
    assert "Most of the gain sat in a few buildings" in body.notes


def test_a_repeated_number_takes_the_citation_it_has_elsewhere():
    text = (
        "Savings fell to 9% after adjustment [2].\n\nSo the plan should assume 9%, not 18%."
        "\n\nRaw 18% [1]."
    )
    out = fmt.cite_repeated_numbers(text)
    assert "So the plan should assume 9%, not 18%. [2][1]" in out
    assert fmt.cite_repeated_numbers("A new 37% figure.") == "A new 37% figure."
