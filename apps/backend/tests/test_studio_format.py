import pytest

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


def test_deck_checks_structure_density_and_evidence_without_counting_a_figure_as_points():
    cover, _, chart, _ = fmt.split_slides(GOOD)
    title = fmt.parse_slide(cover).title
    items = "\n- One finding [1]\n- Another finding [2]\nNote: Explain the findings."
    repeated = fmt.with_marker(f"## {title}{items}", "points")
    normal = fmt.with_marker(f"## The observed change persists across summers{items}", "points")
    crowded = normal.replace("Note:", "- A third finding\n- A fourth finding\n- A fifth\nNote:")
    uncited = chart.replace("[1]", "").replace("[2]", "")
    figure = fmt.with_marker(
        "## The measured trajectory confirms the comparison\n"
        "![Source plot](gunther-asset:ast_plot)\nThe direction is consistent [1].\n"
        "Note: Point out the axes.",
        "figure",
    )
    slides = [cover, repeated, normal, crowded, uncited, figure]
    found = dict(fmt.check_deck("\n\n---\n\n".join(slides)))
    assert any("repeats the deck title" in problem for problem in found[2])
    assert any("there are 5" in problem for problem in found[4])
    assert any("Three points slides in a row" in problem for problem in found[4])
    assert any("two points slides" in problem for problem in found[4])
    assert any("without a citation" in problem for problem in found[5])
    assert 1 not in found and 3 not in found and 6 not in found
    assert "The first slide must use the title layout." in dict(fmt.check_deck(normal))[1]


def test_over_budget_is_measured():
    long = "x" * 130
    deck = GOOD.replace("## The pilot cut review time by 60%", f"## {long}")
    ((slide, problems),) = fmt.check_deck(deck)
    assert slide == 2 and "Shorten it by 40" in problems[0]


def test_chart_cells_must_be_numbers():
    deck = GOOD.replace("| 2021 | 21 [1] |", "| 2021 | about twenty [1] |")
    found = dict(fmt.check_deck(deck))
    assert "not a number" in found[3][0]


def test_talk_slides_need_notes():
    deck = GOOD.replace("Note: Lead with the drop.", "")
    assert any("speaker notes" in p for p in dict(fmt.check_deck(deck))[2])
    assert fmt.check_deck(deck, notes_required=False) == []


def test_chart_number_normalization():
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


@pytest.mark.parametrize(("body", "visible"), [
    ("[material]: /appendix", False),
    ("<!-- editor note -->\n\n[material]:\n  /appendix 'Supporting material'", False),
    ("[^material]: Supporting notes for the appendix.", False),
    ("[^material]: Supporting notes.\n\n    More appendix details.", False),
    ("[^a]: See the other note [^b].\n\n[^b]: Supporting notes.", False),
    (r"[^appendix\]details]: Supporting notes.", False),
    (":::callout\n[material]: /appendix\n:::", False),
    (":::callout\n[^material]: Supporting notes.\n:::", False),
    ("[Read the comparison][material]\n\n[material]: /appendix", True),
    ("[^material]\n\n[^material]:", True),
    ("[^undefined]", True),
    ("[^two\twords]: This is literal text.", True),
    (":::callout Key point\nRead the comparison.\n:::", True),
    ("[^material]: Supporting notes.\n:::callout Key point\n:::", True),
    ("```text\n[material]: /appendix\n```", True),
    ("```text\n[^material]: Supporting notes.\n```", True),
    ("![Comparison](gunther-asset:ast_comparison)", True),
    ("$$\nx + y = z\n$$", True),
])
def test_report_section_visibility_follows_decoded_markdown(body, visible):
    assert fmt.report_section_has_content(f"## Summary\n{fmt.SUMMARY_MARK}\n{body}") is visible


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


def test_phrase_checks_apply_to_body_text():
    deck = GOOD.replace(
        "## The pilot cut review time by 60%", "## What the evidence shows about review time"
    )
    assert fmt.check_deck(deck) == []
    deck = deck.replace("Median review time per case", "The evidence shows that review time fell")
    assert any("about the world" in p for p in dict(fmt.check_deck(deck))[2])


@pytest.mark.parametrize("label", ["Source: ", "参照資料：", "来源："])
def test_uncited_exhibit_rows_cite_the_source_line(label):
    text = (
        ":::exhibit\nExhibit 1 — Growth\n| 指标 | 增速 |\n| --- | --- |\n| GDP | +5.0% |\n"
        f"| CPI | +0.2% [3] |\n{label}统计公报 [2]\n:::"
    )
    assert fmt.check_report(f"## Growth\n{text}", exhibit_counts=(1,)) == []
    out = fmt.cite_exhibit_rows(text)
    assert "| GDP | +5.0% [2] |" in out and "| CPI | +0.2% [3] |" in out
    assert "| 指标 | 增速 |" in out


def test_exhibit_row_citations_preserve_escaped_pipes_and_empty_trailing_cells():
    text = "\r\n".join([
        ":::exhibit", "Enrollment", "| Group | Count | Alternate count |", "| --- | --- | --- |",
        r"| Control |  | 12\|", "| Arm | 12 ||", "Source: Trial [2]", ":::",
    ])

    cited = fmt.cite_exhibit_rows(text)

    assert cited == text.replace(r"12\|", r"12\| [2]").replace("| 12 ||", "| 12 [2] ||")
    [exhibit] = fmt.report_exhibits(cited)
    lines = cited.splitlines(keepends=True)
    assert [fmt.table_cells(lines[index]) for index in exhibit.rows[1:]] == [
        ["Control", "", "12| [2]"], ["Arm", "12 [2]", ""],
    ]
    assert fmt.cite_exhibit_rows(cited) == cited


def test_attribution_must_follow_the_table():
    text = (
        ":::exhibit\nSource: Trial [2]\n| Group | Result |\n| --- | --- |\n"
        "| A | 10 |\n:::\n"
    )
    [problem] = fmt.check_report(f"## Results\n{text}")
    assert "attribution line after the table" in problem
    assert fmt.cite_exhibit_rows(text) == text


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_only_exhibits_in_the_planned_section_outside_code_fences_count(fence):
    exhibit = (
        ":::exhibit\nComparison\n| Group | Rate |\n| --- | --- |\n| A | 10% |\n"
        "| B | 20% |\n| C | 30% |\n参照資料：Trial [1]\n:::"
    )
    report = "## Findings\nThe rates were 10%, 20% and 30% [1]."
    assert fmt.check_report(report, exhibit_counts=(0,)) == []
    fenced = f"{report}\n\n{fence}markdown\n{exhibit}\n{fence}"
    elsewhere = f"{fenced}\n\n## Appendix\n{exhibit}"
    [problem] = fmt.check_report(elsewhere, exhibit_counts=(1, 0))
    assert "Section 1 plans 1 exhibit(s) and has 0" in problem
    assert fmt.cite_exhibit_rows(fenced) == fenced
    assert fmt.check_report(f"{report}\n\n{exhibit}", exhibit_counts=(1,)) == []


def test_single_dash_separator_without_data_rows_is_not_a_complete_exhibit():
    empty_grid = (
        "## Findings\nThe pilot measured cooling [1].\n\n"
        ":::exhibit\nComparison\n| Group | Rate |\n| - | - |\nSource: Trial [1]\n:::"
    )
    problems = fmt.check_report(empty_grid, exhibit_counts=(1,))
    assert any("data rows" in problem for problem in problems)
    assert any("Section 1 plans 1 exhibit(s) and has 0" in problem for problem in problems)

    with_dash_data = (
        "## Findings\nThe pilot measured cooling [1].\n\n"
        ":::exhibit\nComparison\n| Group | Rate |\n| - | - |\n"
        "| Control | - [1] |\nSource: Trial [1]\n:::"
    )
    assert fmt.check_report(with_dash_data, exhibit_counts=(1,)) == []


def test_short_japanese_slide_title_keeps_the_structural_citation_check():
    deck = GOOD.replace(
        "## The pilot cut review time by 60%", "## 熱損失が止まる"
    )
    assert fmt.check_deck(deck) == []
    cited = deck.replace("## 熱損失が止まる", "## 熱損失が止まる [1]")
    assert any("citation marks" in p for p in dict(fmt.check_deck(cited))[2])


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


def test_short_japanese_report_heading_keeps_the_structural_length_check():
    report = (
        "<!-- report: v2 -->\n# 断熱実験\n\n## 結論\n<!-- block: summary -->\n"
        "断熱材を追加した。\n\n## 熱損失が止まる\n測定結果が安定した [1]。\n"
    )
    assert fmt.check_report(report) == []
    long = report.replace("熱損失が止まる", "熱" * 46)
    assert any("Section 2 heading" in p for p in fmt.check_report(long))


def test_a_table_header_without_a_separator_gets_one():
    from gunther.studio import format as fmt

    text = ":::exhibit\nExhibit 3 — x [1]\n| Task | Self-RAG 13B |\n| PopQA | 55.8 [7] |\n:::"
    fixed = fmt.table_separators(text)
    assert "| Task | Self-RAG 13B |\n| --- | --- |\n| PopQA" in fixed
    assert fmt.table_separators(fixed) == fixed
    assert not fmt.uncited_lines(fixed.split("\n"))
