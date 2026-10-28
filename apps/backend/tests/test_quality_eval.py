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


def test_the_visual_column_prefers_the_vision_judge_and_falls_back_to_the_text_judge() -> None:
    builds = [
        {"set": "a", "label": "deck", "absolute": {"visual": 4, "insight": 5}, "visualJudge": {"score": 8}},
        {"set": "b", "label": "deck", "absolute": {"visual": 6, "insight": 5}, "visualJudge": {"error": "x"}},
        {"set": "c", "label": "deck", "absolute": {"insight": 5}},
    ]
    assert report.summarize(builds)["scores"]["visual"] == 7.0


def test_judge_pair_returns_the_structured_result():
    import json

    from fake_models import FakeProvider, gateway, model

    answer = {name: "A" for name in judge.PAIR_CRITERIA}
    answer["reason"] = "A explains the control."
    fake = FakeProvider(json.dumps(answer))
    result, error = judge.judge_pair(
        gateway(fake), model(), kind="slides", brief="Teach the study",
        first={"content": DECK, "citations": []},
        second={"content": DECK, "citations": []},
    )
    assert result.overall == "A" and not error and len(fake.requests) == 1


def test_eval_reads_uncited_experiment_and_inventory_from_selected_revision(tmp_path):
    import json
    from types import SimpleNamespace

    from fake_models import FakeProvider, gateway, model
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from test_studio_pipeline import request_text

    from evals.quality.run import judge_build, source_material
    from gunther.database import Base
    from gunther.models import (
        ContentBlock,
        DocumentElement,
        Source,
        SourceIndexHead,
        SourceRevision,
    )

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions.begin() as session:
        for identity in ("selected", "outside"):
            session.add(Source(id=identity, title=identity, kind="paper", content="fixture", content_hash=identity))
            for version in ("old", "current"):
                revision = identity + version
                session.add(SourceRevision(id=revision, source_id=identity, fingerprint=revision, parser="fixture"))
                for n, text in enumerate(["Mechanism setup", "Read every character. " * 400 + "UNCITED CENTRAL ABLATION"]):
                    session.add(ContentBlock(id=revision + str(n), revision_id=revision, ordinal=n, kind="paragraph", content=text, locator=str(n)))
                for n, kind in enumerate(("figure", "equation")):
                    session.add(DocumentElement(id=revision + kind, revision_id=revision, ordinal=n, kind=kind, content="Controlled ablation caption", locator="p. 2", payload_json=json.dumps({"imageAssetId": revision + kind})))
            session.add(SourceIndexHead(source_id=identity, revision_id=identity + "current"))
    service = SimpleNamespace(sessions=sessions, list_knowledge_base_sources=lambda _: [SimpleNamespace(id="selected", title="Selected study")])
    passages, inventory = source_material(service, "fixture")
    assert inventory == ["selectedcurrentfigure"]
    assert [p["locator"] for p in passages] == ["0", "1", "p. 2"]
    assert "UNCITED CENTRAL ABLATION" in judge.whole_source_view(passages)
    assert "outside" not in str(passages) and "selectedold" not in str(passages)
    (tmp_path / "content.md").write_text(DECK)
    (tmp_path / "passages.json").write_text('[{"quote":"Only the cited mechanism"}]')
    (tmp_path / "source-material.json").write_text(json.dumps(passages))
    (tmp_path / "source-figures.json").write_text(json.dumps(inventory))
    learner = {key: {"answer": "Only mechanism explained", "evidence": [1]} for key in judge.LearningReconstruction.model_fields if key != "unexplained"}
    prompts = []

    def reply(request):
        text = request_text(request)
        prompts.append(text)
        if "independent learner's slide-only reconstruction" in text:
            assert "UNCITED CENTRAL ABLATION" in text and "selectedcurrentfigure" in text
            return json.dumps({"source_coverage": 2, "explanation_depth": 3, "slide_only_comprehension": 3, "problems": ["Central ablation is omitted"], "summary": "Missing central evidence"})
        if "You are an independent learner" in text:
            assert "UNCITED" not in text and "selectedcurrentfigure" not in text
            return json.dumps(learner)
        assert "Only the cited mechanism" in text
        return json.dumps({**dict.fromkeys(judge.CRITERIA, 5), "sections": [], "summary": "Partial"})

    fake = FakeProvider(reply)
    gw = gateway(fake)
    brief = materials.Brief("slides", "scientist", "Teach the study", "read")
    material = SimpleNamespace(brief=lambda _: brief)
    record = {"dir": str(tmp_path), "label": "deck", "purpose": "explain_study", "parts": 3,
              "study_map": {}, "coverage_plan": {}, "source_figures": ["WRITER_INVENTORY"]}
    judge_build(record, material, gw, model(), judge.Meter(gw))
    assert record["learningComparison"]["problems"] == ["Central ablation is omitted"]
    assert len(prompts) == 3


def test_whole_source_budget_reports_any_unread_content(monkeypatch):
    monkeypatch.setattr(judge, "SOURCE_BUDGET_CHARS", 100)
    result = judge.whole_source_view([{"supportText": "x" * 200}, {"supportText": "unseen"}])
    assert "block 1 incomplete" in result and "1 further blocks unavailable" in result
    assert "unseen" not in result
