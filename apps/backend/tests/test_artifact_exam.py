"""The Artifacts exam (evals/artifacts): its cases, its scoring, the baseline mapping and one
run with stand-in models. It needs no keys."""

import json
from pathlib import Path

import pytest
from fake_models import FakeProvider, gateway, model, orchestrator_replies, skill_replies

from evals.artifacts import report, score
from evals.artifacts.run import (
    RUNNERS,
    Configuration,
    Counter,
    Options,
    baseline_request,
    edited_outline,
    open_session,
    parse,
    route_of,
    run_exam,
)
from evals.ask.run import ExamError
from gunther.outputs import join_document
from gunther.skillbook import skill_for

CASES = {case.id: case for case in score.load_cases()}
FIXTURES = score.load_fixtures()


# The case file ---------------------------------------------------------------------------------


def test_the_case_file_is_well_formed() -> None:
    cases = list(CASES.values())
    assert 28 <= len(cases) <= 36
    assert {case.category for case in cases} == score.CATEGORIES
    # Every part a case names is in its fixture, every placeholder is set up.
    assert score.problems(cases, FIXTURES) == []
    deck, written = FIXTURES["deck"], FIXTURES["report"]
    slides = skill_for("slides")
    reports = skill_for("report")
    assert (deck.kind, deck.use, deck.audience) == ("slides", "talk", "student")
    assert 10 <= len(deck.outline) <= slides.limits["pages_max"]
    assert [slot.layout for slot in deck.outline][0] == "title"
    assert [slot.layout for slot in deck.outline][-1] == "takeaways"
    assert {slot.layout for slot in deck.outline} <= set(slides.layouts)
    # Mixed layouts, a chart among them, and no layout run longer than the skill allows.
    assert len({slot.layout for slot in deck.outline}) >= 8
    assert sum(slot.layout in ("bar_chart", "line_chart") for slot in deck.outline) <= 1
    assert (written.kind, written.style) == ("report", "overview")
    assert len(written.outline) == 5
    assert reports.limits["sections_min"] <= len(written.outline) <= reports.limits["sections_max"]
    # The layouts a case asks for exist.
    wanted = {name for case in cases for name in case.expect.layout.values()}
    wanted |= {edit.layout for case in cases for edit in case.today.edit if edit.layout}
    assert wanted <= set(slides.layouts)
    # A fixture's definition names its hash; the outline can be sent as it is.
    assert len({fixture.hash for fixture in FIXTURES.values()}) == 2
    request = deck.request()
    assert request["outline"][0] == {
        "heading": deck.outline[0].heading,
        "goal": "",
        "layout": "title",
    }


def test_the_cases_cover_every_kind_of_message_the_plan_names() -> None:
    cases = list(CASES.values())

    def some(test) -> bool:
        return any(test(case) for case in cases)

    # small edits
    assert some(lambda c: c.setup.typo and c.expect.must_not_contain == ("{typo}",))
    assert some(lambda c: c.expect.shorter and c.category == "small edit")
    assert some(lambda c: c.expect.must_contain and c.category == "small edit" and c.expect.layout)
    assert some(lambda c: c.setup.quote and c.category == "small edit")
    # finding by content, focus and conversation
    assert len([c for c in cases if c.category == "find by content"]) >= 3
    assert some(lambda c: c.focus and c.focus.selected and not c.focus.quote)
    assert some(lambda c: c.focus and c.focus.selected and c.focus.quote)
    assert some(lambda c: c.focus and not c.focus.selected)
    assert len([c for c in cases if c.history]) >= 2
    # structure
    assert some(lambda c: (c.expect.count_delta or 0) > 0 and c.expect.new_at)
    assert some(lambda c: (c.expect.count_delta or 0) < 0 and c.category == "structure")
    assert some(lambda c: c.expect.order)
    assert some(lambda c: "split" in c.message.lower())
    assert some(lambda c: c.expect.layout and c.today.route == "outline")
    assert some(lambda c: c.today.route == "outline" and any(e.heading for e in c.today.edit))
    # upstream, content, whole document, questions
    assert some(lambda c: c.category == "upstream" and "cost" in c.message)
    assert some(lambda c: c.category == "upstream" and "first-year" in c.message)
    assert some(lambda c: c.category == "content" and c.expect.cites)
    assert some(lambda c: c.category == "content" and "answer" in c.expect.intent)
    assert some(lambda c: "Translate" in c.message)
    assert some(lambda c: "shorter" in c.message and c.expect.may_all)
    assert len([c for c in cases if c.category == "question"]) >= 3
    assert all(c.expect.intent == ("answer",) for c in cases if c.category == "question")
    # asking: vague must ask, clear must not, ambiguous must ask
    asking = [c for c in cases if c.category == "asking"]
    assert [c.expect.asks_rule for c in asking] == ["must", "must_not", "must"]
    assert asking[0].artifact == "none" and asking[1].artifact == "none"
    assert asking[2].artifact == "deck"


def test_loading_refuses_a_mistyped_case(tmp_path: Path) -> None:
    def load(text: str) -> None:
        path = tmp_path / "cases.toml"
        path.write_text(text, encoding="utf-8")
        score.load_cases(path)

    base = '[[case]]\nid = "X"\ncategory = "small edit"\nartifact = "deck"\nmessage = "m"\n'
    load(base)
    with pytest.raises(ValueError, match="unknown fields"):
        load(base + 'mesage = "typo"\n')
    with pytest.raises(ValueError, match="unknown category"):
        load(base.replace("small edit", "tiny edit"))
    with pytest.raises(ValueError, match="unknown intent"):
        load(base + '[case.expect]\nintent = "wish"\n')
    with pytest.raises(ValueError, match="unknown fields"):
        load(base + "[case.expect]\nchange = [1]\n")
    with pytest.raises(ValueError, match="unknown route"):
        load(base + 'today = { route = "magic" }\n')
    with pytest.raises(ValueError, match="not unique"):
        load(base + "\n" + base)
    # A part that is not in the fixture, and a placeholder nothing sets up, are caught.
    bad = score.Case("X", "small edit", "deck", "Fix {typo}", score.Expect(changed=(40,)))
    found = score.problems([bad], FIXTURES)
    assert any("names part 40" in line for line in found)
    assert any("{typo} is not set up" in line for line in found)


# Hand-made documents -----------------------------------------------------------------------------

SOURCES = {
    "a": {"sourceId": "src_a", "sourceTitle": "Note A", "quote": "Quote A, said once."},
    "b": {"sourceId": "src_b", "sourceTitle": "Note B", "quote": "Quote B, said once."},
    "c": {"sourceId": "src_c", "sourceTitle": "Note C", "quote": "Quote C, said once."},
    # A note of the real demo library, which says "the thirty-first of August".
    "h": {
        "sourceId": "src_h",
        "sourceTitle": "Velmora Cool Roof Trial (Harlow 2024)",
        "quote": "The buildings were followed over the summers 2021 to 2023.",
    },
}
LIBRARY = {
    "Note A": "Note A says the trial covered 412 buildings and 18% of cooling energy.",
    "Note B": "Note B says fifteen minutes and 2.1 degrees.",
    "Note C": "Note C says nothing about numbers.",
}


def document(
    parts: list[str], cites: list[str], *, kind: str = "slides", layouts=None, checked=True
):
    """A version made by hand: its parts, the sources behind [1], [2]… in order, its layouts."""

    citations = [{**SOURCES[key], "ref": n} for n, key in enumerate(cites, start=1)]
    preamble = "# Title" if kind == "report" else ""
    return score.Version(
        content=join_document(preamble, parts, kind),
        kind=kind,
        citations=citations,
        outline=[
            {"heading": f"H{n}", "goal": "", "layout": layout}
            for n, layout in enumerate(layouts or [], 1)
        ]
        if layouts
        else [],
        sections=[
            {"index": n, "heading": "h", "checked": checked, "issues": []}
            for n in range(len(parts))
        ],
    )


def case(**fields) -> score.Case:
    expect = fields.pop("expect", score.Expect())
    return score.Case("X1", "small edit", "deck", fields.pop("message", "Do it."), expect, **fields)


def outcome(before, after, intent="change", **fields) -> score.Outcome:
    return score.Outcome(intent=intent, before=before, after=after, **fields)


def passed(c: score.Case, o: score.Outcome) -> tuple[dict, dict]:
    measures = score.measure(c, o, LIBRARY)
    return measures, score.checks(c, o, measures)


PARTS = [
    "# Deck\n\nA title line",
    "## Slide two\n\n- The trial covered 412 buildings [1]\n\nNote: about 412 [1]",
    "## Slide three\n\n- Plain words about the roofs [2]",
    "## Slide four\n\n- Heat fell in top-floor flats [1][2]",
]


def test_a_part_that_only_changed_its_source_numbers_is_untouched() -> None:
    # A new first slide cites a third note, so every number after it moves up by one.
    before = document(PARTS, ["a", "b"])
    new = ["## Slide new\n\n- Something new [1]"]
    renumbered = [
        PARTS[0],
        *new,
        "## Slide two\n\n- The trial covered 412 buildings [2]\n\nNote: about 412 [2]",
        "## Slide three\n\n- Plain words about the roofs [3]",
        "## Slide four\n\n- Heat fell in top-floor flats [2][3]",
    ]
    after = document(renumbered, ["c", "a", "b"])
    c = case(expect=score.Expect(count_delta=1, new_at=(2,)))
    measures, found = passed(c, outcome(before, after))
    assert measures["damaged"] == [] and measures["changed"] == []
    # The raw text moved on, and that is reported on its own.
    assert measures["raw_damaged"] == [2, 3, 4]
    assert measures["untouched"] == 4
    assert all(found.values()), found
    # The same documents compared as raw text would have called three parts damaged.
    assert score.identified("see [1][2]", after.citations) != "see [1][2]"
    assert score.identified(PARTS[1], before.citations) == score.identified(
        renumbered[2].replace("[2]", "[1]"), document(renumbered, ["a", "b", "c"]).citations
    )


def test_the_same_number_for_another_passage_is_a_change() -> None:
    before = document(PARTS, ["a", "b"])
    after = document(PARTS, ["a", "c"])  # part three now cites Note C, with the same [2]
    measures, found = passed(case(), outcome(before, after))
    assert measures["damaged"] == [3, 4]
    assert found["untouched"] is False and found["precision"] is False


def test_typo_fixed_and_nothing_else_passes() -> None:
    before = document(PARTS, ["a", "b"])
    fixed = [*PARTS]
    fixed[1] = fixed[1].replace("trial", "trail")
    after = document(fixed, ["a", "b"])
    c = case(expect=score.Expect(changed=(2,), must_contain=("trail",), must_not_contain=("zzz",)))
    measures, found = passed(c, outcome(before, after))
    assert measures["changed"] == [2] and measures["unexpected"] == [] and measures["missed"] == []
    assert all(found.values()), found
    assert score.failures(c, outcome(before, after), found, None) == []


def test_rewriting_everything_fails_precision_and_untouched() -> None:
    before = document(PARTS, ["a", "b"])
    after = document([p + " and more words" for p in PARTS], ["a", "b"])
    c = case(expect=score.Expect(changed=(2,)))
    o = outcome(before, after)
    measures, found = passed(c, o)
    assert measures["changed"] == [1, 2, 3, 4]
    assert measures["unexpected"] == [1, 3, 4] and measures["damaged"] == [1, 3, 4]
    assert measures["changed_ok"] == 1 and measures["untouched"] == 3
    assert (found["precision"], found["recall"], found["untouched"]) == (False, True, False)
    assert score.failures(c, o, found, None) == ["precision", "untouched"]
    why = score.explain(c, o, measures, found, None, "")
    assert why[0] == "changed part(s) 1, 3, 4, which should stay"
    # Anything may change when the case says so.
    everything = case(expect=score.Expect(may_all=True, count_delta=None))
    _, found = passed(everything, o)
    assert all(found.values())


def test_missing_the_part_fails_recall() -> None:
    before = document(PARTS, ["a", "b"])
    c = case(expect=score.Expect(changed=(3,)))
    measures, found = passed(c, outcome(before, document(PARTS, ["a", "b"])))
    assert measures["missed"] == [3] and found["recall"] is False
    assert "left part(s) 3 as they were" in score.explain(
        c, outcome(before, before), measures, found, None, ""
    )


def test_an_inserted_part_moves_nothing_else() -> None:
    before = document(PARTS, ["a", "b"])
    inserted = [*PARTS[:2], "## Slide new\n\n- A new thing [1]", *PARTS[2:]]
    c = case(expect=score.Expect(count_delta=1, new_at=(3,)))
    measures, found = passed(c, outcome(before, document(inserted, ["a", "b"])))
    assert measures["added"] == [3] and measures["changed"] == [] and measures["damaged"] == []
    assert all(found.values()), found
    wrong = case(expect=score.Expect(count_delta=1, new_at=(2,)))
    assert passed(wrong, outcome(before, document(inserted, ["a", "b"])))[1]["new_at"] is False
    unchanged = case(expect=score.Expect(count_delta=0))
    assert passed(unchanged, outcome(before, document(inserted, ["a", "b"])))[1]["count"] is False


def test_a_deleted_part_is_a_change_and_the_rest_stay_the_same() -> None:
    before = document(PARTS, ["a", "b"])
    after = document([PARTS[0], PARTS[1], PARTS[3]], ["a", "b"])
    c = case(expect=score.Expect(changed=(3,), count_delta=-1))
    measures, found = passed(c, outcome(before, after))
    assert measures["deleted"] == [3] and measures["changed"] == [3] and measures["damaged"] == []
    assert all(found.values()), found


def test_a_moved_part_is_not_a_change_but_changes_the_order() -> None:
    before = document(PARTS, ["a", "b"])
    after = document([PARTS[0], PARTS[2], PARTS[1], PARTS[3]], ["a", "b"])
    c = case(expect=score.Expect(order=(1, 3, 2, 4)))
    measures, found = passed(c, outcome(before, after))
    assert measures["changed"] == [] and measures["order"] == [1, 3, 2, 4]
    assert all(found.values()), found
    still = case(expect=score.Expect(order=(1, 2, 4, 3)))
    assert passed(still, outcome(before, after))[1]["order"] is False


def test_a_split_part_keeps_its_place_and_adds_one() -> None:
    before = document(PARTS, ["a", "b"])
    halves = [
        PARTS[0],
        PARTS[1],
        "## Slide three, first half\n\n- Plain words [2]",
        "## Slide three, second half\n\n- about the roofs [2]",
        PARTS[3],
    ]
    c = case(expect=score.Expect(changed=(3,), count_delta=1))
    measures, found = passed(c, outcome(before, document(halves, ["a", "b"])))
    assert measures["changed"] == [3] and len(measures["added"]) == 1
    assert all(found.values()), found


def test_a_translation_is_the_same_parts_rewritten_in_place() -> None:
    before = document(PARTS, ["a", "b"])
    chinese = [
        "# 幻灯片\n\n标题",
        "## 第二张\n\n- 试验覆盖 412 栋楼 [1]",
        "## 第三张\n\n- 屋顶 [2]",
        "## 第四张\n\n- 热量下降 [1][2]",
    ]
    c = case(expect=score.Expect(changed=(1, 2, 3, 4)))
    measures, found = passed(c, outcome(before, document(chinese, ["a", "b"])))
    assert (
        measures["changed"] == [1, 2, 3, 4]
        and measures["added"] == []
        and measures["deleted"] == []
    )
    assert all(found.values()), found


def test_layout_is_read_from_the_part_it_belongs_to_after_the_change() -> None:
    before = document(PARTS, ["a", "b"], layouts=["title", "text", "text", "text"])
    after = document(
        [PARTS[0], PARTS[1], "## Slide three\n\n- **Plain** words [2]", PARTS[3]],
        ["a", "b"],
        layouts=["title", "text", "cards", "text"],
    )
    c = case(expect=score.Expect(changed=(3,), layout={3: "cards"}))
    assert passed(c, outcome(before, after))[1]["layout"] is True
    c = case(expect=score.Expect(changed=(3,), layout={3: "flow"}))
    measures, found = passed(c, outcome(before, after))
    assert found["layout"] is False
    assert "layout of part 3 is cards, not flow" in score.explain(
        c, outcome(before, after), measures, found, None, ""
    )
    # An outline that does not line up with the parts says nothing about layouts.
    odd = document([PARTS[0], PARTS[1]], ["a", "b"], layouts=["title"])
    assert (
        passed(
            case(expect=score.Expect(layout={2: "text"}, may_all=True, count_delta=None)),
            outcome(before, odd),
        )[1]["layout"]
        is False
    )


def test_strings_are_looked_up_without_regard_to_case_quotes_or_source_numbers() -> None:
    before = document(PARTS, ["a", "b"])
    after = document(
        [PARTS[0], "## Slide two\n\n- What Velmora’s roofs taught us [1]", *PARTS[2:]], ["a", "b"]
    )
    c = case(
        expect=score.Expect(
            changed=(2,),
            must_contain=("what velmora's roofs taught us",),
            must_not_contain=("Quote A",),
        )
    )
    assert all(passed(c, outcome(before, after))[1].values())
    absent = case(expect=score.Expect(changed=(2,), must_contain=("nowhere",)))
    found = passed(absent, outcome(before, after))[1]
    assert found["contains 'nowhere'"] is False
    assert "lacks 'nowhere'" in score.explain(
        absent, outcome(before, after), *passed(absent, outcome(before, after)), None, ""
    )


def test_a_sentence_that_must_be_gone_is_looked_for_as_a_sentence() -> None:
    quote = "Plain words about the roofs"
    before = document(PARTS, ["a", "b"])
    c = case(expect=score.Expect(changed=(3,), gone=(quote,)))
    # The words come back inside a longer sentence of the notes: not the same sentence.
    inside = "## Slide three\n\n- Simple talk\n\nNote: Plain words about the roofs matter most [2]."
    reworded = document([*PARTS[:2], inside, PARTS[3]], ["a", "b"])
    assert passed(c, outcome(before, reworded))[1][f"gone {quote!r}"] is True
    # The same sentence kept as a line, or as a sentence of the notes, is not gone.
    kept = "## Slide three\n\n- Plain words about the roofs [2]\n\nNote: Other. More."
    found = passed(c, outcome(before, document([*PARTS[:2], kept, PARTS[3]], ["a", "b"])))[1]
    assert found[f"gone {quote!r}"] is False
    noted = "## Slide three\n\n- Simple talk\n\nNote: Plain words about the roofs. More."
    found = passed(c, outcome(before, document([*PARTS[:2], noted, PARTS[3]], ["a", "b"])))[1]
    assert found[f"gone {quote!r}"] is False
    o = outcome(before, document([*PARTS[:2], kept, PARTS[3]], ["a", "b"]))
    measures, found = passed(c, o)
    assert f"still says {quote!r}" in score.explain(c, o, measures, found, None, "")


def test_shorter_and_cites_are_checked_on_the_changed_parts() -> None:
    before = document(PARTS, ["a", "b"])
    shorter = document([PARTS[0], "## Slide two\n\n- 412 [1]", *PARTS[2:]], ["a", "b"])
    c = case(expect=score.Expect(changed=(2,), shorter=(2,), cites=("Note A",)))
    assert all(passed(c, outcome(before, shorter))[1].values())
    longer = document(
        [PARTS[0], PARTS[1] + " and a good deal more words [1]", *PARTS[2:]], ["a", "b"]
    )
    found = passed(c, outcome(before, longer))[1]
    assert found["shorter"] is False and found["cites"] is True
    elsewhere = case(expect=score.Expect(changed=(2,), cites=("Note C",)))
    assert passed(elsewhere, outcome(before, longer))[1]["cites"] is False


def test_numbers_from_nowhere_are_found_but_numbers_from_the_sources_are_not() -> None:
    before = document(PARTS, ["a", "b"])
    c = case(expect=score.Expect(changed=(2,)), message="Make it about 2024 please.")
    # 412 and 18% are in Note A, "fifteen" in Note B, 2024 in the person's message, 3 is small.
    fine = document(
        [
            PARTS[0],
            "## Slide two\n\n- 412 buildings, 18%, 15 minutes, 2.1 degrees, 2024, 3 bullets [1]",
            *PARTS[2:],
        ],
        ["a", "b"],
    )
    measures, found = passed(c, outcome(before, fine))
    assert measures["invented"] == [] and found["numbers"] is True
    made_up = document([PARTS[0], "## Slide two\n\n- 73% and 412 [1]", *PARTS[2:]], ["a", "b"])
    measures, found = passed(c, outcome(before, made_up))
    assert measures["invented"] == ["73"] and found["numbers"] is False
    assert "numbers from nowhere: 73" in score.explain(
        c, outcome(before, made_up), measures, found, None, ""
    )
    # A reply is held to the same rule.
    answer = case(expect=score.Expect(intent=("answer",)))
    said = score.Outcome(intent="answer", before=before, reply="It was 99% in Note A [1].")
    measures, found = passed(answer, said)
    assert measures["invented"] == ["99"] and found["numbers"] is False


def test_changed_parts_must_have_been_checked_and_unsupported_ones_are_counted() -> None:
    before = document(PARTS, ["a", "b"])
    changed = [PARTS[0], "## Slide two\n\n- Another thing [1]", *PARTS[2:]]
    c = case(expect=score.Expect(changed=(2,)))
    after = document(changed, ["a", "b"])
    after.sections[1] = {"index": 1, "checked": False, "issues": []}
    measures, found = passed(c, outcome(before, after))
    assert measures["support"] == {"ok": 0, "flagged": 0, "unchecked": 1}
    assert found["checked"] is False
    after.sections[1] = {
        "index": 1,
        "checked": True,
        "issues": [{"claim": "x", "verdict": "unsupported", "note": ""}],
    }
    measures, found = passed(c, outcome(before, after))
    assert measures["support"] == {"ok": 0, "flagged": 1, "unchecked": 0}
    # Flagged text was still checked: that is a finding, not a failure of the case.
    assert found["checked"] is True


def test_asking_rules() -> None:
    before = document(PARTS, ["a", "b"])
    asked = score.Outcome(
        intent="ask", before=before, asked=True, question="Which one?", options=["A", "B"]
    )
    must = case(expect=score.Expect(intent=("ask",), asks="must"))
    assert all(passed(must, asked)[1].values())
    silent = score.Outcome(intent="change", before=before, after=before)
    measures, found = passed(must, silent)
    assert found["asked"] is False and found["intent"] is False
    # A clear request must not be asked about.
    clear = case(expect=score.Expect(intent=("make",), asks="must_not"))
    assert passed(clear, asked)[1]["asked"] is False
    # "May ask": a question is a fair answer, and then nothing else is asked of the case.
    may = case(expect=score.Expect(intent=("change", "answer"), asks="may", must_contain=("nope",)))
    found = passed(may, asked)[1]
    assert all(found.values()) and "contains 'nope'" not in found
    assert score.judged(case(expect=score.Expect(asks="may", judge="Fine?")), asked, None) == []
    # Asking and saving at once is wrong.
    both = score.Outcome(intent="ask", before=before, after=before, asked=True)
    assert passed(must, both)[1]["saved"] is False


def test_questions_save_nothing_and_are_read_from_the_reply() -> None:
    before = document(PARTS, ["a", "b"])
    c = case(expect=score.Expect(intent=("answer",), must_contain=("note a",)))
    answered = score.Outcome(intent="answer", before=before, reply="It is Note A [1].")
    assert all(passed(c, answered)[1].values())
    saved = score.Outcome(intent="answer", before=before, after=before, reply="Note A.")
    assert passed(c, saved)[1]["saved"] is False
    wrong = score.Outcome(intent="change", before=before, after=before)
    found = passed(c, wrong)[1]
    assert found["intent"] is False and found["contains 'note a'"] is False


def test_the_judge_decides_the_rest() -> None:
    before = document(PARTS, ["a", "b"])
    after = document([PARTS[0], PARTS[1] + " more", *PARTS[2:]], ["a", "b"])
    c = case(expect=score.Expect(changed=(2,), judge="Is it fine?"))
    o = outcome(before, after)
    measures, found = passed(c, o)
    prompt = score.judge_prompt(c, o, measures)
    assert "Person (the request): Do it." in prompt and "Question: Is it fine?" in prompt
    saved = prompt.split("What the assistant did:")[1]
    assert "[Part 1]\n(unchanged)" in saved and "[Part 2]\n## Slide two" in saved
    assert score.failures(c, o, found, score.Verdict(answer=True)) == []
    assert score.failures(c, o, found, score.Verdict(answer=False, why="no")) == ["judged no"]
    assert score.failures(c, o, found, None) == ["judge"]
    assert (
        score.explain(
            c, o, measures, found, score.Verdict(answer=False, why="It added facts."), ""
        )[-1]
        == "judge: no. It added facts."
    )
    # No question, no judge.
    assert score.failures(case(expect=score.Expect(changed=(2,))), o, found, None) == []
    # What could not be run is said plainly.
    impossible = score.Outcome(impossible="it cannot do this.")
    assert score.failures(c, impossible, {"answered": False}, None) == ["not possible today"]
    assert score.explain(c, impossible, {}, {"answered": False}, None, "") == [
        "Not possible today: it cannot do this."
    ]
    broken = score.Outcome(failed="HTTP 502: boom")
    assert score.failures(c, broken, {"answered": False}, None) == ["answered"]


# Aligning parts --------------------------------------------------------------------------------


def test_align_matches_equal_parts_then_moved_ones_then_rewrites_in_order() -> None:
    old = ["alpha", "bravo", "charlie", "delta"]
    assert score.align(old, old).pairs == {0: 0, 1: 1, 2: 2, 3: 3}
    # One removed, one added at the end: the others keep their partners.
    placing = score.align(old, ["alpha", "charlie", "delta", "echo"])
    assert placing.pairs == {0: 0, 2: 1, 3: 2} and placing.deleted == [1] and placing.added == [3]
    # A part moved down.
    assert score.align(old, ["alpha", "charlie", "bravo", "delta"]).pairs == {
        0: 0,
        1: 2,
        2: 1,
        3: 3,
    }
    # A rewritten part stays that part, even when nothing is left of its words.
    placing = score.align(old, ["alpha", "bravo", "完全不同", "delta"])
    assert placing.pairs == {0: 0, 1: 1, 2: 2, 3: 3} and placing.added == []
    # Two runs of equal length are paired in order, whatever they say.
    placing = score.align(["one", "two"], ["xx", "yy"])
    assert placing.pairs == {0: 0, 1: 1}
    # A part that became two goes with the likelier half.
    placing = score.align(
        ["intro", "the roofs were coated in white paint"],
        ["intro", "paint", "the roofs were coated"],
    )
    assert placing.pairs[1] == 2 and placing.added == [1]


# Fixtures, setup and placeholders --------------------------------------------------------------


def test_a_fixture_is_checked_against_what_the_cases_rely_on() -> None:
    fixture = FIXTURES["report"]
    parts = ["## " + slot.heading + "\n\n" + " ".join(slot.has) for slot in fixture.outline]
    # Section three needs two paragraphs.
    assert fixture.problems(parts) == ["part 3 has fewer than 2 paragraphs"]
    parts[2] += "\n\nA second paragraph."
    assert fixture.problems(parts) == []
    parts[0] = "## Nothing"
    assert fixture.problems(parts) == ["part 1 lacks “2.1”"]
    assert fixture.problems(parts[:3]) == ["it has 3 parts; the outline has 5"]


def test_a_case_breaks_one_word_and_picks_a_sentence_from_its_own_copy() -> None:
    content = join_document("", PARTS, "slides")
    c = case(setup=score.Setup(typo=2, quote=(3, 1)), message="Fix {typo} to {fixed}: {quote}")
    text, subs = score.prepare(c, content, "slides")
    assert subs["fixed"] == "covered" and subs["typo"] == "coevred"
    assert subs["quote"] == "Plain words about the roofs"
    # A sentence is picked from the part as the case starts, so it shows the broken word.
    same = case(setup=score.Setup(typo=2, quote=(2, 1)))
    assert score.prepare(same, content, "slides")[1]["quote"] == "The trial coevred 412 buildings"
    broken = score.split_document(text, "slides")[1][1].splitlines()
    assert broken[2] == "- The trial coevred 412 buildings [1]" and broken[0] == "## Slide two"
    # The other parts are exactly as they were.
    assert score.split_document(text, "slides")[1][2:] == PARTS[2:]
    assert c.resolved(subs).message == "Fix coevred to covered: Plain words about the roofs"
    # A part with nothing to break is an error, not a silent pass.
    with pytest.raises(ValueError, match="no word long enough"):
        score.prepare(case(setup=score.Setup(typo=1)), "# T\n\nA b c", "slides")
    assert score.sentences_of(
        "## H\n\n- First line here today.\nNote: skipped\n| a | b |\n\n"
        "Second one is here. And a third one."
    ) == [
        "First line here today.",
        "Second one is here.",
        "And a third one.",
    ]


def test_a_slide_that_says_its_layout_is_read_by_its_marker_before_the_outline() -> None:
    assert score.marked_layout("<!-- layout: compare -->\n## H\n\nbody") == "compare"
    assert score.marked_layout("<!-- layout: title; accent: blue -->\n# T") == "title"
    assert score.marked_layout("## H\n\n<!-- layout: compare -->") is None
    assert score.plain("<!-- layout: text -->\n## H [1] and [?] more") == "## H and more"
    # Taking a source number off the end of a sentence leaves the full stop where it belongs.
    assert score.plain("It is 9% [6][7] [6][7]. Then, [?] more [1] .") == "It is 9%. Then, more."
    marked = [
        f"<!-- layout: {layout} -->\n{part}"
        for layout, part in zip(["title", "text", "text", "text"], PARTS, strict=True)
    ]
    before = document(marked, ["a", "b"])
    swapped = [*marked]
    swapped[2] = swapped[2].replace("layout: text", "layout: cards")
    # The outline of the version still says "text", the slide itself says "cards": the slide wins.
    after = document(swapped, ["a", "b"], layouts=["title", "text", "text", "text"])
    c = case(expect=score.Expect(changed=(3,), layout={3: "cards"}))
    measures, found = passed(c, outcome(before, after))
    assert measures["changed"] == [3] and found["layout"] is True
    # A slide with no marker (an older deck) is read by the outline.
    old = document(PARTS, ["a", "b"], layouts=["title", "text", "cards", "text"])
    assert passed(c, outcome(document(PARTS, ["a", "b"]), old))[1]["layout"] is True
    # A marker is not something a person can select, break or count as words.
    content = join_document("", marked, "slides")
    text, subs = score.prepare(case(setup=score.Setup(typo=2, quote=(2, 1))), content, "slides")
    assert subs["fixed"] == "covered" and subs["quote"] == "The trial coevred 412 buildings"
    assert score.split_document(text, "slides")[1][1].splitlines()[0] == "<!-- layout: text -->"


def test_figures_read_digits_words_and_percents() -> None:
    assert score.figures("In 2024, 18% of the 412 buildings; slide 3 [1]; 1,200 and 2.10") == [
        ("2024", True),
        ("18", True),
        ("412", True),
        ("3", False),
        ("1200", True),
        ("2.1", True),
    ]
    assert score.figures("1. First\n2. Second") == []
    # A source mark with a slip in it is still a source mark, not a number.
    assert score.figures("No source measures that [?, 24]. See [p:7][1, 2].") == []
    assert "15" in score.known_figures(["every fifteen minutes"])
    assert "25" in score.known_figures(["twenty-five of them"])
    # An ordinal is a number too: "the thirty-first of August" says 31, "the fourth" says 4.
    assert {"31", "4", "20"} <= score.known_figures(
        ["the thirty-first of August, the fourth, twentieth"]
    )
    assert "7" not in score.known_figures(["a well-known second-hand sevenfold"])
    assert score.figures("CD3D and CO2") == []


# The baseline ----------------------------------------------------------------------------------


def request_for(
    case_id: str, head: score.Version, turn: score.Turn | None = None, route: str | None = None
):
    c = CASES[case_id]
    config = Configuration("baseline", "deepseek/deepseek-flash", "high")
    chosen, _ = route_of(c)
    return baseline_request(
        c,
        turn or score.Turn(c.message, c.focus),
        head,
        config,
        route or chosen,
        "exam_request_0001",
    )


DECK_HEAD = score.Version(
    content="x",
    kind="slides",
    id="art_1",
    audience="student",
    use="talk",
    brief="A talk.",
    outline=[{"heading": f"H{n}", "goal": f"G{n}", "layout": "text"} for n in range(1, 13)],
)
REPORT_HEAD = score.Version(
    content="x",
    kind="report",
    id="art_2",
    audience="scientist",
    style="overview",
    brief="A report.",
    outline=[{"heading": f"H{n}", "goal": f"G{n}"} for n in range(1, 6)],
)


def test_a_change_is_a_revision_of_the_selected_part_or_else_of_everything() -> None:
    plain = request_for("E2", DECK_HEAD)
    assert plain.route == "revise" and plain.tail == "artifacts/art_1/revise/stream"
    assert plain.body == {
        "clientRequestId": "exam_request_0001",
        "instruction": "Slide 2 is too long. Shorten it.",
        "model": "deepseek/deepseek-flash",
        "effort": "high",
    }
    # A part that is selected, or has selected text in it, is sent as a section (counted from 0)…
    assert request_for("F1", DECK_HEAD).body["sectionIndex"] == 4
    assert request_for("F2", DECK_HEAD).body["sectionIndex"] == 1
    assert "quote" not in json.dumps(request_for("F2", DECK_HEAD).body)
    # …but the part that is only on screen is not: the old screen does not know it.
    assert "sectionIndex" not in request_for("F3", DECK_HEAD).body
    assert request_for("V1", DECK_HEAD).route == "revise"


def test_an_upstream_change_is_a_rebuild_with_the_message_as_the_brief() -> None:
    deck = request_for("U1", DECK_HEAD)
    assert (deck.route, deck.tail) == ("rebuild", "outputs/build/stream")
    assert deck.body == {
        "clientRequestId": "exam_request_0001",
        "scope": {"mode": "library"},
        "model": "deepseek/deepseek-flash",
        "effort": "high",
        "kind": "slides",
        "audience": "student",
        "supersedesArtifactId": "art_1",
        "use": "talk",
        "brief": "Focus on cost instead.",
    }
    written = request_for("U2", REPORT_HEAD)
    assert written.body["brief"] == "Make it for first-year students."
    assert written.body["style"] == "overview" and "use" not in written.body
    assert written.body["supersedesArtifactId"] == "art_2" and "outline" not in written.body
    assert route_of(CASES["U3"]) == ("rebuild", "")


def test_a_changed_outline_line_is_a_rebuild_with_the_edited_outline() -> None:
    layout = request_for("S5", DECK_HEAD)
    assert layout.route == "outline" and layout.body["brief"] == "A talk."
    assert layout.body["outline"][6] == {"heading": "H7", "goal": "G7", "layout": "cards"}
    assert layout.body["outline"][5] == {"heading": "H6", "goal": "G6", "layout": "text"}
    line = request_for("S6", REPORT_HEAD)
    assert line.body["outline"][2] == {
        "heading": "Keep the roof clean and mind the neighbours",
        "goal": "Cleaning every two years, and glare complaints from the neighbours.",
    }
    assert line.body["outline"][1] == {"heading": "H2", "goal": "G2"}
    edited = edited_outline(DECK_HEAD.outline, CASES["S5"].today.edit)
    assert DECK_HEAD.outline[6]["layout"] == "text" and edited[6]["layout"] == "cards"


def test_a_new_document_is_a_build_with_what_the_person_would_have_picked() -> None:
    new = request_for("A2", DECK_HEAD)
    assert (new.route, new.tail) == ("new", "outputs/build/stream")
    assert new.body["kind"] == "slides" and new.body["audience"] == "collaborator"
    assert new.body["brief"] == "A 10-minute talk for the lab on the Velmora trial results."
    assert "supersedesArtifactId" not in new.body and "outline" not in new.body


def test_what_the_old_path_cannot_say_is_not_possible_today() -> None:
    reasons = {}
    for case_id, c in CASES.items():
        route, why = route_of(c)
        if route == "none":
            reasons[case_id] = why
    assert set(reasons) == {"S1", "S2", "S3", "S4", "N1", "N2", "Q1", "Q2", "Q3", "A1", "A3"}
    assert "never asks" in reasons["A1"] and "never asks" in reasons["A3"]
    assert "cannot answer" in reasons["Q1"] and "cannot answer" in reasons["Q3"]
    for case_id in ("S1", "S2", "S3", "S4", "N1", "N2"):
        assert "cannot add, remove or move a part" in reasons[case_id]
    # Everything else can be sent.
    routes = {case_id: route_of(c)[0] for case_id, c in CASES.items() if case_id not in reasons}
    assert set(routes.values()) == {"revise", "rebuild", "outline", "new"}
    assert routes["E1"] == routes["C3"] == routes["W1"] == "revise"
    assert routes["U1"] == routes["U2"] == routes["U3"] == "rebuild"
    assert routes["S5"] == routes["S6"] == "outline" and routes["A2"] == "new"
    # A case can say why itself.
    owned = case(
        expect=score.Expect(count_delta=1), today=score.Today(route="none", why="Because.")
    )
    assert route_of(owned) == ("none", "Because.")


# The scorecard ---------------------------------------------------------------------------------


def recorded(case_id: str, *, saved=True, **fields) -> dict:
    """What run_case records for a case, with the numbers a test cares about."""

    m = {
        "saved": saved,
        "changed": [2],
        "changed_ok": 1,
        "required_done": 1,
        "missed": [],
        "untouched": 3,
        "damaged": [],
        "raw_damaged": [4],
        "support": {"ok": 1, "flagged": 0, "unchecked": 0},
    }
    outcome = {"failed": "", "impossible": "", "seconds": 10.0, "tokens": 1000, "calls": 4}
    return {
        "case": case_id,
        "category": CASES[case_id].category,
        "outcome": outcome | fields.pop("outcome", {}),
        "measures": m | fields.pop("measures", {}),
        "checks": fields.pop("checks", {"answered": True, "intent": True}),
        "judgement": fields.pop("judgement", None),
        "failures": fields.pop("failures", []),
        "why": fields.pop("why", []),
    }


def test_the_scorecard_counts_parts_and_lists_why_each_case_failed() -> None:
    results = {
        "E1": recorded("E1", outcome={"calls": 4}),
        "E2": recorded(
            "E2",
            measures={"changed": [1, 2, 3], "changed_ok": 1, "damaged": [1, 3]},
            checks={"answered": True, "intent": True, "numbers": False},
            failures=["precision", "untouched"],
            why=["changed part(s) 1, 3, which should stay"],
            outcome={"calls": 12},
            judgement={"answer": False, "why": "no"},
        ),
        "S1": recorded(
            "S1",
            measures={},
            outcome={"impossible": "it cannot add, remove or move a part.", "calls": 0},
            checks={"answered": False},
            failures=["not possible today"],
            why=["Not possible today: it cannot add, remove or move a part."],
        ),
        "Q1": recorded(
            "Q1",
            measures={},
            outcome={"failed": "HTTP 502: down"},
            checks={"answered": False},
            failures=["answered"],
            why=["HTTP 502: down"],
        ),
        "A1": recorded(
            "A1",
            checks={"answered": True, "asked": True, "intent": True},
            judgement={"answer": True, "why": ""},
        ),
    }
    card = report.scorecard(CASES, results)
    assert (card["cases"], card["passed"], card["impossible"], card["failed_requests"]) == (
        5,
        2,
        1,
        1,
    )
    # Parts are counted over the cases that saved a version: 1 + 1 + 1 of 1 + 3 + 1 parts changed.
    assert (
        card["precision"] == [3, 5]
        and card["untouched"] == [7, 9]
        and card["untouched_raw"] == [6, 9]
    )
    # A case that could not be sent has the wrong intent, and an asking case it cannot ask is wrong.
    assert card["intent"] == [3, 5] and card["asked"] == [1, 1] and card["numbers"] == [0, 1]
    assert card["support"] == {"ok": 3, "flagged": 0, "unchecked": 0}
    # Where anything may change, nothing is wrongly changed: those cases are not in precision.
    free = {**results, "U1": recorded("U1", measures={"changed": [1, 2, 3], "changed_ok": 3})}
    assert report.scorecard(CASES, free)["precision"] == [3, 5]
    assert card["judge"] == [1, 2]
    assert card["small_edit_calls"] == 8
    assert card["by_category"]["small edit"] == [1, 2] and card["by_category"]["structure"] == [
        0,
        1,
    ]
    text = report.render(
        "unit",
        [{"name": "baseline: m (write high)", "card": card, "results": results}],
        ["A note."],
    )
    assert "# Artifacts exam: unit" in text and "- A note." in text
    assert "| Not possible today | 1 |" in text
    assert "- S1 (structure): Not possible today: it cannot add, remove or move a part." in text
    assert "- E2 (small edit): changed part(s) 1, 3, which should stay" in text
    assert "Passed by kind of request: small edit 1/2" in text
    assert "E1 (" not in text


def test_a_run_can_be_scored_again_from_what_it_stored(tmp_path: Path) -> None:
    from dataclasses import asdict

    # The note says "the thirty-first of August" and the new text says "31": a fair conversion.
    before = document(PARTS, ["h", "b"])
    after = document(
        [PARTS[0], "## Slide two\n\n- It ran to 31 August [1]", *PARTS[2:]], ["h", "b"]
    )
    stored = {
        "case": "E2",
        "category": "small edit",
        "outcome": asdict(outcome(before, after)),
        "measures": {"saved": True, "invented": ["31"]},
        "checks": {"answered": True, "numbers": False},
        "judgement": {"answer": True, "why": "Fine."},
        "judgeError": "",
        "failures": ["numbers"],
        "why": ["numbers from nowhere: 31"],
        "subs": {},
    }
    again = report.rescore(stored, CASES["E2"], score.library_texts())
    assert again["measures"]["invented"] == [] and again["checks"]["numbers"] is True
    assert again["failures"] == [] and again["why"] == []
    # The judge is not asked again: its answer stays as it was.
    assert again["judgement"] == {"answer": True, "why": "Fine."}
    # The command writes the new scorecard, and keeps the record as it was first scored.
    folder = tmp_path / "run"
    folder.mkdir()
    config = {"name": "x", "results": {"E2": stored}}
    raw = {"notes": ["N."], "complete": True, "configurations": [config]}
    (folder / "raw.json").write_text(json.dumps(raw), encoding="utf-8")
    assert report.main([str(folder), "--rescore"]) == 0
    first = json.loads((folder / "raw.as-scored.json").read_text(encoding="utf-8"))
    assert first["configurations"][0]["results"]["E2"]["failures"] == ["numbers"]
    kept = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    assert kept["configurations"][0]["results"]["E2"]["failures"] == []
    assert report.RESCORED in kept["notes"]
    assert "- E2 (" not in (folder / "report.md").read_text(encoding="utf-8")


# The counter -----------------------------------------------------------------------------------


def test_the_counter_counts_the_calls_made_inside_its_window_only() -> None:
    fake = FakeProvider("Hello.")
    chosen = gateway(fake)
    counter = Counter(chosen)
    info = model()
    chosen.complete(info, system="s", messages="before")
    with counter.window() as used:
        chosen.complete(info, system="s", messages="one")
        chosen.complete(info, system="s", messages="two")
    chosen.complete(info, system="s", messages="after")
    assert used.calls == 2 and used.seconds > 0
    assert len(fake.requests) == 4


# A whole run with stand-in models ----------------------------------------------------------------

TINY_CASES = """
[fixture.deck]
kind = "slides"
audience = "student"
use = "talk"
brief = "A short talk on the trial."

[[fixture.deck.outline]]
heading = "The trial"
layout = "title"

[[fixture.deck.outline]]
heading = "The trial covered 412 buildings over three summers"
goal = "How many buildings the trial covered"
layout = "text"
has = ["412"]

[[fixture.deck.outline]]
heading = "The plan wants thirty percent of roofs coated"
goal = "The plan's target for coated roofs"
layout = "takeaways"
has = ["30%"]

[fixture.report]
kind = "report"
audience = "scientist"
style = "overview"
title = "The trial"
brief = "A short report on the trial."

[[fixture.report.outline]]
heading = "The trial covered 412 buildings"
goal = "How many buildings the trial covered"
has = ["412"]

[[fixture.report.outline]]
heading = "The plan wants 30% of roofs coated"
goal = "The plan's target"
has = ["30%"]

[[case]]
id = "E1"
category = "small edit"
artifact = "deck"
message = "Fix the typo on slide 2."
setup = { typo = 2 }

[case.expect]
intent = "change"
changed = [2]
must_contain = ["{fixed}"]
must_not_contain = ["{typo}"]
judge = "Is the only difference the corrected word?"

[[case]]
id = "F1"
category = "find by focus"
artifact = "deck"
message = "Make this one plainer."

[case.focus]
section = 3
selected = true

[case.expect]
intent = "change"
changed = [3]

[[case]]
id = "R1"
category = "find by content"
artifact = "report"
message = "Shorten the plan section."

[case.expect]
intent = "change"
changed = [2]
shorter = [2]

[[case]]
id = "S1"
category = "structure"
artifact = "report"
message = "Add a section after the first."

[case.expect]
intent = "change"
count_delta = 1
new_at = [2]

[[case]]
id = "U1"
category = "upstream"
artifact = "deck"
message = "Focus on cost instead."

[case.expect]
intent = "change"
may_change = "all"
count_delta = "any"

[[case]]
id = "A2"
category = "asking"
artifact = "none"
message = "A talk on the trial."
today = { route = "new", kind = "slides" }

[case.expect]
intent = "make"
asks = "must_not"
must_contain = ["412"]
"""

SLIDES = [
    "# The trial\n\nA look at the trial",
    "## The trial covered 412 buildings over three summers\n\n- 412 buildings were followed for"
    " three summers [1]\n- The roofs were coated white [1]\n\n"
    "Note: The trial followed 412 buildings [1].",
    "## The plan wants thirty percent of roofs coated\n\n- The plan wants 30% of roofs coated"
    " by 2030 [1]\n\nNote: Roofs coated by 2030 [1].",
]
SECTIONS = [
    "## The trial covered 412 buildings\n\nThe trial covered 412 buildings [1]."
    " It ran for three summers [1].",
    "## The plan wants 30% of roofs coated\n\nThe plan wants 30% of roofs coated by 2030 [1]."
    " It also"
    " wants the owners to help pay for it [1]. This second sentence makes the section long [1].",
]
APPROACH = {
    "question": "What did the trial find?",
    "answer": "It covered 412 buildings.",
    "purpose": "Students.",
    "structure": "what",
    "structure_reason": "It describes.",
    "use": "talk",
    "pages": 6,
    "cards": [{"claim": "The trial covered 412 buildings", "refs": [1], "type": "data"}],
}
OUTLINE = {
    "title": "The trial",
    "sections": [
        {"heading": "The trial", "layout": "title", "cards": []},
        {"heading": "The trial covered 412 buildings", "layout": "text", "cards": [1]},
        {"heading": "The plan wants 30% of roofs coated", "layout": "text", "cards": [1]},
    ],
}
JUDGE_REPLY = json.dumps({"answer": True, "why": "It did."})


def rewrite(index: int, prompt: str) -> str:
    """What the stand-in writer does with a section it is asked to revise."""

    instruction = prompt.split("Instruction:", 1)[1].strip()
    if instruction.startswith("Fix the typo") and index == 1:
        return SLIDES[1]
    if instruction.startswith("Make this one plainer") and index == 2:
        return (
            "## The plan wants thirty percent of roofs coated\n\n- 30% of roofs [1]\n\n"
            "Note: Short [1]."
        )
    if instruction.startswith("Shorten") and index == 1:
        return "## The plan wants 30% of roofs coated\n\nThe plan wants 30% of roofs coated [1]."
    return "UNCHANGED"


def written(index: int, prompt: str) -> str:
    """What the stand-in writer writes for a section of a deck or of a report."""

    if "The whole slides:" in prompt:
        return SLIDES[index]
    return SECTIONS[min(index, len(SECTIONS) - 1)]


def stand_in_models() -> FakeProvider:
    skill = skill_replies(
        APPROACH,
        OUTLINE,
        written,
        gather=[{"action": "search_library", "query": "trial"}] * 200,
        rewrite=rewrite,
    )

    def reply(request):
        system = str(request["messages"][0]["content"])
        if system.startswith("You grade one reply"):
            return JUDGE_REPLY
        return skill(request)

    return FakeProvider(reply)


def run_with_fakes(tmp_path: Path, options: Options, fake: FakeProvider | None = None):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    facts = (
        "The trial covered 412 buildings over three summers.\n"
        "The plan wants 30% of roofs coated by 2030.\n"
    )
    (library / "trial.md").write_text(f"# The trial\n\n{facts}", encoding="utf-8")
    (library / "plan.md").write_text(f"# The plan\n\n{facts}", encoding="utf-8")
    cases = tmp_path / "cases.toml"
    cases.write_text(TINY_CASES, encoding="utf-8")
    fake = fake or stand_in_models()
    said: list[str] = []
    folder = run_exam(
        options,
        model_client_factory=fake.factory,
        overrides={"deepseek_api_key": "sk-test-0000000000001234", "stt_provider": "compatible"},
        results_root=tmp_path / "results",
        library_dir=library,
        cases_file=cases,
        say=said.append,
    )
    return folder, fake, said


def agent_models(*decisions: dict) -> FakeProvider:
    """Stand-in models for the agent path: the orchestrator decides as scripted, and the rest
    (the skill's steps, the judge) are the baseline's stand-ins."""

    inner = stand_in_models().replies[0]
    return FakeProvider(orchestrator_replies(list(decisions), inner))


def test_the_agent_path_is_registered_and_unknown_paths_are_refused(tmp_path: Path) -> None:
    assert callable(RUNNERS["agent"]) and callable(RUNNERS["baseline"])
    with pytest.raises(ExamError, match="Unknown path"):
        run_with_fakes(tmp_path, Options(paths=["magic"]), FakeProvider("{}"))


def test_the_agent_path_sends_each_case_through_the_conversation_endpoint(tmp_path: Path) -> None:
    reply = {"actions": [{"do": "reply", "text": "Nothing needed to change."}]}
    fake = agent_models(reply)
    folder, fake, said = run_with_fakes(
        tmp_path, Options(label="agent", paths=["agent"], only=["F1", "A2"]), fake
    )
    raw = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    configuration = raw["configurations"][0]
    assert configuration["name"] == "agent: deepseek/deepseek-flash (write high)"
    records = configuration["results"]
    focus = records["F1"]["outcome"]
    # The case's document is the conversation's artifact; the message and its focus went
    # through the agent, which replied; nothing was saved.
    assert focus["route"] == "agent" and focus["intent"] == "answer" and focus["after"] is None
    assert focus["before"]["number"] == 1 and focus["reply"] == "Nothing needed to change."
    assert focus["calls"] == 1 and focus["asked"] is False
    assert records["F1"]["failures"]  # it changed nothing; the case wants slide 3 changed
    sent = [
        r
        for r in fake.requests
        if str(r["messages"][0]["content"]).startswith("You are the orchestrator")
    ]
    views = [str(r["messages"][-1]["content"]) for r in sent]
    assert any(
        "Make this one plainer." in v and "Pointed at (selected): part 3" in v for v in views
    )
    assert any("Artifacts: none yet." in v and "A talk on the trial." in v for v in views)
    assert records["A2"]["outcome"]["before"] is None


def test_the_agent_paths_question_and_saved_version_are_read_back(tmp_path: Path) -> None:
    ask = {"actions": [{"do": "ask", "question": "For whom?", "options": ["Lab", "Class"]}]}
    folder, _, _ = run_with_fakes(
        tmp_path,
        Options(label="agent-ask", paths=["agent"], only=["A2"]),
        agent_models(ask),
    )
    raw = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    outcome = raw["configurations"][0]["results"]["A2"]["outcome"]
    assert outcome["intent"] == "ask" and outcome["asked"] is True
    assert outcome["question"] == "For whom?" and outcome["options"] == ["Lab", "Class"]
    assert raw["configurations"][0]["results"]["A2"]["failures"]  # it asked; the case says not to

    edit = [
        {"actions": [{"do": "write", "parts": [3], "instruction": "Make this one plainer."}]},
        {"actions": [{"do": "save", "text": "Plainer."}]},
    ]
    folder, _, _ = run_with_fakes(
        tmp_path,
        Options(label="agent-save", paths=["agent"], only=["F1"]),
        agent_models(*edit),
    )
    raw = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    outcome = raw["configurations"][0]["results"]["F1"]["outcome"]
    assert outcome["intent"] == "change", outcome["failed"]
    assert outcome["after"]["number"] == 2
    assert outcome["before"]["number"] == 1 and outcome["calls"] > 2


def test_runner_end_to_end_with_fake_models(tmp_path: Path) -> None:
    folder, fake, said = run_with_fakes(tmp_path, Options(label="fake"))
    results = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    assert results["complete"] is True
    configuration = results["configurations"][0]
    assert configuration["name"] == "baseline: deepseek/deepseek-flash (write high)"
    records = configuration["results"]
    assert list(records) == ["E1", "F1", "R1", "S1", "U1", "A2"]
    # A typo fixed by the whole-document revision: only slide 2 changed, nothing else moved.
    typo = records["E1"]
    assert typo["failures"] == [] and typo["why"] == []
    assert typo["measures"]["changed"] == [2] and typo["measures"]["damaged"] == []
    assert typo["subs"] == {"typo": "bulidings", "fixed": "buildings"}
    assert typo["outcome"]["intent"] == "change" and typo["outcome"]["route"] == "revise"
    assert typo["outcome"]["calls"] > 0 and typo["outcome"]["seconds"] > 0
    assert typo["judgement"] == {"answer": True, "why": "It did."}
    # A selected slide goes through as one section: the other slides are not even asked about.
    assert records["F1"]["failures"] == [], records["F1"]["why"]
    assert records["F1"]["measures"]["changed"] == [3]
    assert records["F1"]["outcome"]["calls"] < typo["outcome"]["calls"]
    # A report section is shortened.
    assert records["R1"]["failures"] == [], records["R1"]["why"]
    # An insert cannot be asked of the old path; nothing is sent for it.
    assert records["S1"]["outcome"]["impossible"].startswith("it cannot add, remove or move")
    assert (
        records["S1"]["failures"] == ["not possible today"]
        and records["S1"]["outcome"]["calls"] == 0
    )
    # A rebuild replaces the lot; a new build starts from nothing.
    assert records["U1"]["outcome"]["route"] == "rebuild" and records["U1"]["failures"] == []
    new = records["A2"]
    assert new["outcome"]["intent"] == "make" and new["outcome"]["before"] is None
    assert new["failures"] == [], new["why"]
    # The scorecard and the raw record say the same thing and hold no key.
    text = (folder / "report.md").read_text(encoding="utf-8")
    assert "# Artifacts exam: fake" in text and "| Not possible today | 1 |" in text
    assert "- S1 (structure): Not possible today:" in text
    assert "Fixture deck (deepseek/deepseek-flash): built in" in text
    assert "sk-test" not in (folder / "raw.json").read_text(encoding="utf-8")
    assert configuration["card"]["impossible"] == 1 and configuration["card"]["cases"] == 6
    # Template and fixtures are kept for the next run…
    kept = sorted(p.name for p in (tmp_path / "results").iterdir())
    assert any(name.startswith("template-") for name in kept)
    assert sum(name.startswith("fixture-") for name in kept) == 2
    # …and reused, so a second run builds nothing again.
    writes = sum("Write section" in str(r["messages"][-1]["content"]) for r in fake.requests)
    again, fake_again, said_again = run_with_fakes(
        tmp_path, Options(label="again", only=["E1"]), None
    )
    assert any("Reusing the deck fixture" in line for line in said_again)
    assert not any("Building the" in line for line in said_again)
    assert writes > 0 and again != folder


def test_a_session_clones_a_fixture_and_finds_the_newest_version(tmp_path: Path) -> None:
    run_with_fakes(tmp_path, Options(label="seed", only=["E1"]))
    results = tmp_path / "results"
    template = next(p for p in results.iterdir() if p.name.startswith("template-"))
    info = json.loads((template / "template.json").read_text(encoding="utf-8"))
    saved = json.loads(next(results.glob("fixture-deck-*.json")).read_text(encoding="utf-8"))
    assert saved["built"]["attempts"] == 1 and saved["built"]["calls"] > 0
    config = Configuration("baseline", "deepseek/deepseek-flash", "high")
    keys = {"deepseek_api_key": "sk-test-0000000000001234", "stt_provider": "compatible"}
    with open_session(template, info, None, config, stand_in_models().factory, keys) as session:
        first = session.clone(saved)
        # A copy: the same text, sources and findings, as version 1 of a lineage of its own.
        assert first.number == 1 and first.content == saved["content"]
        assert [c["ref"] for c in first.citations] == [c["ref"] for c in saved["citations"]]
        assert [s["checked"] for s in first.sections] == [True] * len(first.parts)
        second = session.clone(saved)
        assert second.id != first.id and second.number == 1
        body = {"clientRequestId": "exam_test_0001", "instruction": "Make this one plainer."}
        name, data = session.stream(
            f"artifacts/{first.id}/revise/stream", {**body, "sectionIndex": 2}
        )
        assert name == "done" and data["versionNumber"] == 2
        assert session.head(first).id == data["id"] and session.head(first).number == 2
        assert session.head(second).number == 1
        # A request that fails says why instead of raising.
        other = {**body, "clientRequestId": "exam_test_0002"}
        name, data = session.stream("artifacts/art_missing/revise/stream", other)
        assert name == "error" and data["status"] == 404
        name, data = session.stream(f"artifacts/{first.id}/revise/stream", {})
        assert name == "error" and data["status"] == 422


def test_cases_can_run_side_by_side_and_more_than_once(tmp_path: Path) -> None:
    folder, _, _ = run_with_fakes(
        tmp_path, Options(label="many", only=["E1", "F1", "S1"], jobs=2, repeat=2)
    )
    raw = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    results = raw["configurations"][0]["results"]
    assert list(results) == ["E1#1", "E1#2", "F1#1", "F1#2", "S1#1", "S1#2"]
    card = raw["configurations"][0]["card"]
    assert card["cases"] == 3 and card["runs"] == 6 and card["passed"] == 2.0
    # The report can be written again from the raw record.
    (folder / "report.md").unlink()
    assert report.main([str(folder)]) == 0
    assert "Artifacts exam" in (folder / "report.md").read_text(encoding="utf-8")


def test_the_command_line() -> None:
    options = parse([])
    assert options.paths == ["baseline"] and options.jobs == 1 and options.repeat == 1
    options = parse(
        ["--path", "baseline,agent", "--only", "E1, F1", "--jobs", "3", "--writer-effort", "low",
         "--rebuild-fixtures", "--fixtures-only", "--label", "x"]
    )  # fmt: skip
    assert options.paths == ["baseline", "agent"] and options.only == ["E1", "F1"]
    assert options.jobs == 3 and options.writer_effort == "low" and options.rebuild_fixtures
    assert options.fixtures_only and options.label == "x"
