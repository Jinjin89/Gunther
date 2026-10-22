# ruff: noqa: E501
"""The quality evaluation's linter and scoring math (no model, no network)."""

from evals.quality import judge, lint, materials, report


def test_units_count_cjk_double_and_skip_marks() -> None:
    assert lint.units("abc [1][2]") == 3
    assert lint.units("**数据**") == 4


def test_topic_titles_are_told_from_action_titles() -> None:
    assert lint.is_topic_title("Results")
    assert lint.is_topic_title("Methods and data")
    assert not lint.is_topic_title("Savings halve once each summer's heat is counted")
    assert not lint.is_topic_title("The pilot cut review time by 60%")
    assert lint.is_topic_title("研究背景")
    assert not lint.is_topic_title("2024年国内生产总值增长5.0%，经济总量突破130万亿元")


DECK = """<!-- layout: title; design: v2 -->
# Cool roofs cut cooling demand
The saving is 9 percent after adjusting for weather [1]

---

<!-- layout: points -->
## Results
- **Savings**: 18% raw, 9% adjusted
- This is synthetic data and does not represent real buildings [1]

Note: Say it slowly.

---

<!-- layout: stat -->
## The pilot cut review time by 60% across two teams [1]
**40 h → 16 h**
Median review time before and after [1]
"""


def test_lint_finds_each_hard_defect() -> None:
    found = lint.lint("slides", DECK, "talk")
    rules = {(d.where, d.rule) for d in found}
    assert ("slide 2", "topic_title") in rules
    assert ("slide 2", "uncited_number") in rules
    assert ("slide 2", "meta_phrase") in rules
    assert ("slide 3", "missing_notes") in rules
    assert ("slide 3", "uncited_number") not in rules  # the bold value takes the next line's mark
    assert ("slide 2", "missing_notes") not in rules


def test_meta_phrases_are_not_taken_from_speaker_notes() -> None:
    deck = "<!-- layout: statement -->\n## This is a full sentence with a point to make [1]\nIt is solid [1]\nNote: this is synthetic."  # noqa: E501
    assert not [d for d in lint.lint("slides", deck, "talk") if d.rule == "meta_phrase"]


def test_budgets_depend_on_the_use() -> None:
    long = "x" * 100
    deck = f"<!-- layout: stats -->\n## Three numbers that matter for this decision today\n- **{long}** label [1]"  # noqa: E501
    assert any(d.rule == "over_budget" for d in lint.lint("slides", deck, "talk"))
    deck = deck.replace(long, "y" * 14)
    assert any(d.rule == "over_budget" for d in lint.lint("slides", deck, "talk"))
    assert not any(d.rule == "over_budget" for d in lint.lint("slides", deck, "read"))


def test_report_headings_and_numbers() -> None:
    text = "# Title\n\n## Overview\nSales rose 14.5% in 2023.\n\n## Prices rose faster than wages in 2023 [1]\nOK [1]"  # noqa: E501
    found = {(d.where, d.rule) for d in lint.lint("report", text)}
    assert ("section 1", "topic_title") in found
    assert ("section 1", "uncited_number") in found
    assert not [d for d in found if d[0] == "section 2"]


def test_measure_json_is_read_tolerantly() -> None:
    measure = {"slides": [{"index": 2, "overflow": True, "minFontPx": 14, "emptyHalf": False},
                          {"index": 3, "overflow": False, "minFontPx": 20}]}  # fmt: skip
    rules = [(d.where, d.rule) for d in lint.measure_defects(measure)]
    assert rules == [("slide 2", "overflow"), ("slide 2", "small_text")]
    assert lint.measure_defects(None) == []


def test_ship_rate_and_missing_verdicts() -> None:
    assert judge.ship_rate(["ship", "minor edit", "rework", "rework"]) == 0.5
    assert judge.ship_rate([]) is None
    absolute = judge.Absolute(
        insight=5, storyline=5, titles=5, evidence=5, clarity=5, correctness=5,
        sections=[judge.SectionVerdict(index=1, verdict="ship", reason="ok"),
                  judge.SectionVerdict(index=1, verdict="rework", reason="dup"),
                  judge.SectionVerdict(index=9, verdict="ship", reason="out of range")],
        summary="s",
    )  # fmt: skip
    verdicts = judge.section_verdicts(absolute, 3)
    assert [v.verdict for v in verdicts] == ["ship", "rework", "rework"]


def test_pairwise_needs_to_survive_the_order_swap() -> None:
    # Method 1 is A in the first call and B in the second.
    assert judge.combine_pair("A", "B") == "first"
    assert judge.combine_pair("B", "A") == "second"
    assert judge.combine_pair("A", "A") == "tie"  # a position bias, not a preference
    assert judge.combine_pair("tie", "B") == "tie"
    assert judge.tally_pairs(["first", "tie", "first"]) == {"first": 2, "second": 0, "tie": 1}


def test_titles_and_core_message() -> None:
    assert judge.titles_of("slides", DECK)[0] == "Cool roofs cut cooling demand"
    assert judge.core_message_of("slides", DECK).startswith("The saving is 9 percent")
    report_text = (
        "<!-- report: v2 -->\n# T\n\n## Summary\n<!-- block: summary -->\nThe answer.\n\n## Next\nx"  # noqa: E501
    )
    assert judge.titles_of("report", report_text) == ["T", "Summary", "Next"]
    assert "The answer." in judge.core_message_of("report", report_text)


def test_summary_rows_and_materials_file() -> None:
    builds = [
        {"set": "a", "label": "deck", "error": "", "verdicts": [{"verdict": "ship"}, {"verdict": "rework"}],
         "titlesOnly": {"pass": True}, "defectCounts": {"overflow": 2}, "measured": True, "seconds": 10,
         "calls": 4, "tokens": 1000, "absolute": {"insight": 6}},
        {"set": "b", "label": "deck", "error": "boom"},
    ]  # fmt: skip
    summary = report.summarize(builds)
    assert summary["shipRate"] == 0.5 and summary["titlesPass"] == 1.0
    assert summary["defects"] == 2 and summary["ok"] == 1 and len(summary["failed"]) == 1
    sets = materials.load_sets()
    assert [s.id for s in sets][:2] == ["velmora", "rag-papers"] and len(sets) == 7
    assert all(s.deck.use == "talk" and s.report.kind == "report" for s in sets)
