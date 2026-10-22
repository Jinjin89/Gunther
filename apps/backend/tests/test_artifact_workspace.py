"""The Artifacts agent's workspace: what is out of date (computed), the brief's rules, and
bringing versions in (import_work) and back in line (sync)."""

import itertools
from contextlib import contextmanager
from pathlib import Path

import pytest
from fake_models import FakeProvider, skill_replies
from test_output_skills import (
    APPROACH,
    OUTLINE,
    REPORT,
    SEARCH,
    SLIDES,
    SLIDES_APPROACH,
    SLIDES_OUTLINE,
)
from test_outputs import app_for, body, build, built, library, route, workspace

from gunther.agent import Evidence
from gunther.artifact_workspace import (
    BriefUpdate,
    CardRec,
    ConversationBrief,
    ItemRec,
    Line,
    PartRec,
    Pending,
    Work,
    Workspace,
    WorkSpec,
    _take_check,
    apply_brief_updates,
    approach_state,
    digest_approach_inputs,
    digest_outline_inputs,
    digest_part_inputs,
    dump_workspace,
    edit_brief,
    import_work,
    load_workspace,
    outline_state,
    pool_from,
    pool_numbered,
    ready,
    render_brief,
    spec_of,
    status,
    store_pool,
    sync,
)
from gunther.outputs import _marked, layout_of, section_hash, split_document
from gunther.schemas import ConversationCitationOut
from gunther.slide_scene import (
    SceneEnvelope,
    SceneGroup,
    SceneLeaf,
    graphics_hash,
    source_hash,
    with_scene,
)

_edits = itertools.count(1)


# A work to ask about ----------------------------------------------------------------------------


def made(kind: str = "slides") -> tuple[ConversationBrief, Work]:
    """A brief and a work that is complete: everything written, checked and up to date."""

    brief = ConversationBrief(
        goal=Line(text="Explain the Velmora trial", mark="you"),
        audience=Line(text="lab members", mark="you"),
        constraints=[Line(text="at most 3 bullets", mark="you")],
    )
    work = Work(
        key="arl_1",
        spec=WorkSpec(
            kind=kind,
            use=Line(text="talk") if kind == "slides" else None,
            length=Line(text="10 minutes"),
            structure=Line(text="what"),
        ),
        title="The trial",
        approach={"question": "What did the trial find?", "answer": "A 3 degree drop"},
        cards=[
            CardRec(id="C1", claim="Peak heat fell by 3 degrees", refs=[1]),
            CardRec(id="C2", claim="Labour is most of the cost", refs=[2]),
        ],
        outline=[
            ItemRec(id="p1", heading="The trial", layout="title"),
            ItemRec(
                id="p2",
                heading="Heat fell",
                goal="The result",
                layout="big_number",
                cards=["C1"],
                refs=[1],
            ),
            ItemRec(
                id="p3",
                heading="Labour costs most",
                goal="The cost",
                layout="bar_chart",
                cards=["C2"],
                refs=[2],
                link="because",
            ),
        ],
        version_id="art_1",
        version_map=[1, 2],
        next_card=3,
        next_item=4,
    )
    work.approach_inputs = digest_approach_inputs(brief, work)
    work.outline_inputs = digest_outline_inputs(work)
    for item in work.outline:
        text = f"## {item.heading}\n\nIt is so [{item.refs[0] if item.refs else 1}]."
        work.parts[item.id] = PartRec(
            text=text,
            inputs=digest_part_inputs(brief, work, item),
            checked=section_hash(text),
            issues=[],
        )
    return brief, work


def states(work: Work, brief: ConversationBrief) -> list[str]:
    return [part.state for part in status(work, brief)]


def test_a_complete_work_is_up_to_date_and_ready_to_save() -> None:
    brief, work = made()
    assert approach_state(work, brief).state == "ok"
    assert outline_state(work).state == "ok"
    assert [
        (part.position, part.item_id, part.heading, part.layout, part.state)
        for part in status(work, brief)
    ] == [
        (1, "p1", "The trial", "title", "ok"),
        (2, "p2", "Heat fell", "big_number", "ok"),
        (3, "p3", "Labour costs most", "bar_chart", "ok"),
    ]
    assert ready(work, brief) == []


# §2.5: the approach is out of date when the brief fields it was written from differ -------------


def set_goal(brief: ConversationBrief, work: Work) -> None:
    brief.goal = Line(text="Focus on cost instead", mark="you")


def set_audience(brief: ConversationBrief, work: Work) -> None:
    brief.audience = Line(text="first-year students", mark="you")


def set_kind(brief: ConversationBrief, work: Work) -> None:
    work.spec.kind = "report"


def set_use(brief: ConversationBrief, work: Work) -> None:
    work.spec.use = Line(text="read", mark="you")


def set_length(brief: ConversationBrief, work: Work) -> None:
    work.spec.length = Line(text="20 minutes", mark="you")


def set_structure(brief: ConversationBrief, work: Work) -> None:
    work.spec.structure = Line(text="which", mark="you")


@pytest.mark.parametrize(
    ("change", "named"),
    [
        (set_goal, "the goal"),
        (set_audience, "the audience"),
        (set_kind, "the kind"),
        (set_use, "the use"),
        (set_length, "the length"),
        (set_structure, "the structure"),
    ],
)
def test_the_approach_is_out_of_date_when_a_field_it_was_written_from_changes(
    change, named: str
) -> None:
    brief, work = made()
    change(brief, work)
    state = approach_state(work, brief)
    assert state.state == "out_of_date"
    assert state.why == f"{named} changed"
    assert "approach" in " ".join(ready(work, brief))


def test_the_approach_ignores_constraints_marks_spacing_and_capitals() -> None:
    brief, work = made()
    brief.constraints.append(Line(text="no jargon", mark="you"))
    brief.goal = Line(text="  explain   the VELMORA trial ", mark="edited")
    brief.audience = Line(text="lab members", mark="guessed")
    assert approach_state(work, brief).state == "ok"
    # Constraints do not make a part out of date either; the agent decides which break them.
    assert states(work, brief) == ["ok", "ok", "ok"]


def test_an_audience_nobody_set_is_a_scientist_and_saying_so_changes_nothing() -> None:
    brief, work = made()
    brief.audience = None
    work.approach_inputs = digest_approach_inputs(brief, work)
    for item in work.outline:
        work.parts[item.id].inputs = digest_part_inputs(brief, work, item)
    brief.audience = Line(text="Scientist ", mark="you")
    assert approach_state(work, brief).state == "ok"
    assert states(work, brief) == ["ok", "ok", "ok"]
    brief.audience = Line(text="students", mark="you")
    assert approach_state(work, brief).why == "the audience changed"
    assert states(work, brief) == ["out_of_date"] * 3


def test_there_is_no_approach_until_one_is_written() -> None:
    brief, work = made()
    work.approach = None
    assert approach_state(work, brief).state == "not_written"
    # A work brought in without an approach takes small changes; a new one needs it first.
    assert "The approach is not written yet." not in ready(work, brief)
    work.parts = {}
    assert "The approach is not written yet." in ready(work, brief)


def test_several_changes_are_named_together() -> None:
    brief, work = made()
    set_goal(brief, work)
    set_audience(brief, work)
    set_length(brief, work)
    assert approach_state(work, brief).why == "the goal, the audience and the length changed"


# The outline is out of date when the approach it was planned from differs, or it is empty ---------


def test_the_outline_is_out_of_date_when_the_approach_changes() -> None:
    brief, work = made()
    work.approach = {**(work.approach or {}), "answer": "A 5 degree drop"}
    state = outline_state(work)
    assert (state.state, state.why) == ("out_of_date", "the approach changed")
    assert "The outline is out of date (the approach changed)." in ready(work, brief)


def test_the_outline_is_out_of_date_when_the_material_cards_change() -> None:
    brief, work = made()
    work.cards[0].claim = "Peak heat fell by 4 degrees"
    assert outline_state(work).why == "the material cards changed"


def test_an_empty_outline_is_not_written() -> None:
    brief, work = made()
    work.outline, work.parts = [], {}
    assert outline_state(work).state == "not_written"
    assert ready(work, brief) == ["Nothing is planned yet."]


# A part is out of date when it is missing or what it was written from differs --------------------


def reword_heading(brief: ConversationBrief, work: Work) -> None:
    work.outline[1].heading = "Peak heat fell by 3 degrees"


def new_goal(brief: ConversationBrief, work: Work) -> None:
    work.outline[1].goal = "The headline number"


def new_layout(brief: ConversationBrief, work: Work) -> None:
    work.outline[1].layout = "compare"


def new_link(brief: ConversationBrief, work: Work) -> None:
    work.outline[1].link = "contrast"


@pytest.mark.parametrize("change", [reword_heading, new_goal, new_layout, new_link])
def test_a_part_is_out_of_date_when_its_outline_item_changes(change) -> None:
    brief, work = made()
    change(brief, work)
    assert states(work, brief) == ["ok", "out_of_date", "ok"]
    assert status(work, brief)[1].why == "its outline item changed"


def test_a_part_is_out_of_date_when_its_cards_change() -> None:
    brief, work = made()
    work.cards[0].evidence = "Table 2"
    # Only the part that uses card C1 is affected.
    assert states(work, brief) == ["ok", "out_of_date", "ok"]
    assert status(work, brief)[1].why == "its cards changed"
    work.cards[0].evidence = ""
    work.outline[1].cards = ["C1", "C2"]
    assert states(work, brief) == ["ok", "out_of_date", "ok"]


def test_a_part_is_out_of_date_when_its_card_is_gone() -> None:
    brief, work = made()
    work.cards = [card for card in work.cards if card.id != "C2"]
    assert states(work, brief) == ["ok", "ok", "out_of_date"]


@pytest.mark.parametrize(
    ("change", "named"),
    [(set_audience, "the audience"), (set_kind, "the kind"), (set_use, "the use")],
)
def test_every_part_is_out_of_date_when_the_audience_kind_or_use_changes(
    change, named: str
) -> None:
    brief, work = made()
    change(brief, work)
    assert states(work, brief) == ["out_of_date"] * 3
    assert all(named in part.why for part in status(work, brief))


def test_the_goal_length_and_structure_do_not_touch_the_parts() -> None:
    brief, work = made()
    set_goal(brief, work)
    set_length(brief, work)
    set_structure(brief, work)
    assert states(work, brief) == ["ok", "ok", "ok"]


def test_new_material_makes_nothing_out_of_date() -> None:
    brief, work = made()
    workspace_state = Workspace(works=[work])
    for number in (3, 4, 5):
        workspace_state.pool.append(
            {
                "id": f"cit_{number}",
                "source_id": f"src_{number}",
                "source_title": f"Source {number}",
                "quote": f"Passage {number}",
                "locator": "",
                "status": "provisional",
                "confidence": 0.5,
                "ref": number,
            }
        )
    assert approach_state(work, brief).state == "ok"
    assert states(work, brief) == ["ok", "ok", "ok"]
    # The map can say how many were found since: the pool's top less the approach's.
    work.approach_top = 2
    assert max(item["ref"] for item in workspace_state.pool) - work.approach_top == 3


def test_a_part_that_was_never_written_is_not_written() -> None:
    brief, work = made()
    del work.parts["p3"]
    assert states(work, brief) == ["ok", "ok", "not_written"]
    assert "Slide 3 is not written yet." in ready(work, brief)
    work.outline.append(ItemRec(id="p4", heading="One more"))
    assert states(work, brief)[-1] == "not_written"


# A check is out of date when the text it checked differs from the part's -----------------------


def test_a_check_is_out_of_date_when_the_text_changed_after_it() -> None:
    brief, work = made()
    work.parts["p2"].text += "\n\nA line typed after the check."
    assert states(work, brief) == ["ok", "not_checked", "ok"]
    # Typed text is saved as it is, shown as not re-checked, as an edit always was.
    assert ready(work, brief) == []


def test_a_part_with_no_check_is_not_checked() -> None:
    brief, work = made()
    work.parts["p1"].issues = None
    assert states(work, brief)[0] == "not_checked"


def test_a_check_that_found_something_is_still_a_check() -> None:
    brief, work = made()
    work.parts["p2"].issues = [{"claim": "It is so", "verdict": "unsupported", "note": ""}]
    assert states(work, brief) == ["ok", "ok", "ok"]
    assert ready(work, brief) == []


def test_ready_lists_every_problem_in_plain_words() -> None:
    brief, work = made("report")
    set_goal(brief, work)
    del work.parts["p2"]
    work.parts["p3"].text += " More."
    assert ready(work, brief) == [
        "The approach is out of date (the goal changed).",
        "Section 2 is not written yet.",
    ]


# The brief's rules (§7.5) ---------------------------------------------------------------------


def update(field: str, text: str, quote: str = "") -> BriefUpdate:
    return BriefUpdate(field=field, text=text, quote=quote)


def test_a_quote_found_in_the_message_makes_the_line_yours() -> None:
    brief, work = ConversationBrief(), made()[1]
    work.approach = None  # still being worked out: the agent may guess
    said = "Slides for the lab  Meeting, about TEN minutes please"
    result = apply_brief_updates(
        brief,
        work,
        [
            update("audience", "lab members", "the LAB meeting"),
            update("length", "10 minutes", "ten minutes"),
            update("goal", "The trial results", "the trial"),
        ],
        said,
    )
    # Spaces and case are folded; the last quote is not in the message, so it is a guess.
    assert result.brief.audience == Line(text="lab members", mark="you")
    assert result.spec is not None
    assert result.spec.length == Line(text="10 minutes", mark="you")
    assert result.brief.goal == Line(text="The trial results", mark="guessed")
    assert result.changed and result.notes == []


def test_an_update_with_no_quote_is_a_guess() -> None:
    result = apply_brief_updates(
        ConversationBrief(), None, [update("goal", "Explain the trial")], "anything"
    )
    assert result.brief.goal == Line(text="Explain the trial", mark="guessed")


def test_the_option_the_user_clicked_counts_as_a_quote() -> None:
    brief = ConversationBrief()
    # The clicked option need not appear in what they wrote.
    result = apply_brief_updates(
        brief,
        None,
        [update("goal", "The whole debate", "Whole debate")],
        "",
        clicked="whole debate",
    )
    assert result.brief.goal == Line(text="The whole debate", mark="you")
    other = apply_brief_updates(
        brief, None, [update("goal", "The trial", "Trial")], "", clicked="whole debate"
    )
    assert other.brief.goal == Line(text="The trial", mark="guessed")


def test_a_line_you_said_or_edited_is_not_replaced_by_a_guess() -> None:
    brief = ConversationBrief(
        goal=Line(text="Explain the trial", mark="you"),
        audience=Line(text="lab members", mark="edited"),
    )
    result = apply_brief_updates(
        brief,
        None,
        [update("goal", "Compare roofs"), update("audience", "the public", "not said")],
        "Make it shorter",
    )
    assert result.brief.goal == brief.goal and result.brief.audience == brief.audience
    assert len(result.notes) == 2 and not result.changed
    assert "you said it" in result.notes[0] and "you wrote it" in result.notes[1]


def test_a_line_you_said_is_replaced_when_you_say_something_else() -> None:
    brief = ConversationBrief(
        goal=Line(text="Explain the trial", mark="you"),
        audience=Line(text="lab members", mark="edited"),
    )
    result = apply_brief_updates(
        brief,
        None,
        [
            update("goal", "Focus on cost", "focus on cost"),
            update("audience", "students", "for students"),
        ],
        "Focus on cost, for students",
    )
    assert result.brief.goal == Line(text="Focus on cost", mark="you")
    assert result.brief.audience == Line(text="students", mark="you")


def test_a_guess_is_replaced_by_a_guess_and_by_your_words() -> None:
    brief = ConversationBrief(goal=Line(text="Explain the trial"))
    again = apply_brief_updates(brief, None, [update("goal", "Explain the debate")], "hi")
    assert again.brief.goal == Line(text="Explain the debate", mark="guessed")
    yours = apply_brief_updates(
        brief, None, [update("goal", "Explain the debate", "the debate")], "the debate"
    )
    assert yours.brief.goal == Line(text="Explain the debate", mark="you")
    # The same words said by the user turn the guess into theirs.
    same = apply_brief_updates(
        brief,
        None,
        [update("goal", "explain  the trial", "explain the trial")],
        "Explain the trial",
    )
    assert same.brief.goal == Line(text="Explain the trial", mark="you")


def test_dropping_a_constraint_needs_your_words() -> None:
    brief = ConversationBrief(
        constraints=[Line(text="at most 3 bullets", mark="guessed"), Line(text="no jargon")]
    )
    refused = apply_brief_updates(
        brief, None, [update("drop_constraint", "at most 3 bullets")], "make it shorter"
    )
    assert len(refused.brief.constraints) == 2
    assert "dropping one needs your own words" in refused.notes[0]
    dropped = apply_brief_updates(
        brief,
        None,
        [update("drop_constraint", "3 bullets", "forget the 3 bullets limit")],
        "Forget the 3 bullets limit",
    )
    assert [line.text for line in dropped.brief.constraints] == ["no jargon"]
    missing = apply_brief_updates(
        brief, None, [update("drop_constraint", "in French", "in french")], "in French"
    )
    assert len(missing.brief.constraints) == 2 and "no constraint" in missing.notes[0]


def test_closing_an_open_question_needs_your_words_unless_it_was_a_guess() -> None:
    brief = ConversationBrief(
        open=[
            Line(text="Include costs?", mark="guessed"),
            Line(text="For how long?", mark="you"),
        ]
    )
    guessed = apply_brief_updates(brief, None, [update("close_open", "Include costs?")], "ok")
    assert [line.text for line in guessed.brief.open] == ["For how long?"]
    yours = apply_brief_updates(brief, None, [update("close_open", "For how long?")], "ok")
    assert len(yours.brief.open) == 2 and "needs your own words" in yours.notes[0]
    said = apply_brief_updates(
        brief, None, [update("close_open", "For how long?", "ten minutes")], "ten minutes"
    )
    assert [line.text for line in said.brief.open] == ["Include costs?"]


def test_constraints_and_questions_are_added_once_and_the_limit_keeps_your_lines() -> None:
    brief = ConversationBrief()
    first = apply_brief_updates(
        brief,
        None,
        [update("constraint", "no jargon", "no jargon"), update("constraint", "No  Jargon")],
        "no jargon",
    )
    assert first.brief.constraints == [Line(text="no jargon", mark="you")]
    full = ConversationBrief(constraints=[Line(text=f"rule {n}", mark="you") for n in range(8)])
    refused = apply_brief_updates(full, None, [update("constraint", "one more")], "x")
    assert len(refused.brief.constraints) == 8 and "all yours" in refused.notes[0]
    guesses = ConversationBrief(constraints=[Line(text=f"guess {n}") for n in range(8)])
    made_room = apply_brief_updates(guesses, None, [update("constraint", "new one")], "x")
    texts = [line.text for line in made_room.brief.constraints]
    assert len(texts) == 8 and "guess 0" not in texts and texts[-1] == "new one"


def test_use_length_and_structure_belong_to_the_active_artifact() -> None:
    brief, work = made()
    work.spec.use = work.spec.length = work.spec.structure = None
    work.approach = None  # still being worked out: the agent may guess
    result = apply_brief_updates(
        brief,
        work,
        [
            update("use", "Talk", "a talk"),
            update("length", "about 12 slides"),
            update("structure", "Which", "a decision"),
        ],
        "A talk, for a decision",
    )
    assert result.spec is not None
    assert result.spec.use == Line(text="talk", mark="you")
    assert result.spec.length == Line(text="about 12 slides", mark="guessed")
    assert result.spec.structure == Line(text="which", mark="you")
    # The work itself is not changed: the caller decides what to keep.
    assert work.spec.use is None
    nothing = apply_brief_updates(brief, None, [update("length", "3 pages")], "x")
    assert nothing.spec is None and "no artifact yet" in nothing.notes[0]
    # A report's style names the structure it fixes.
    report = made("report")[1]
    styled = apply_brief_updates(
        brief, report, [update("structure", "decision brief", "decision brief")], "a decision brief"
    )
    assert styled.spec is not None
    assert styled.spec.structure == Line(text="which", mark="you")


def test_a_use_or_structure_the_skill_does_not_have_is_refused() -> None:
    brief, deck = made()
    deck.approach = None
    refused = apply_brief_updates(
        brief,
        deck,
        [update("use", "pecha kucha"), update("structure", "a poem")],
        "x",
    )
    assert refused.notes[0].startswith("“pecha kucha” is not a use")
    assert refused.notes[1].startswith("“a poem” is not a structure")
    report = made("report")[1]
    report.approach = None
    assert (
        "A report has no use"
        in apply_brief_updates(brief, report, [update("use", "talk")], "").notes[0]
    )


def test_updates_with_no_text_are_left_out_and_nothing_is_changed_in_place() -> None:
    brief = ConversationBrief(goal=Line(text="Explain the trial"))
    result = apply_brief_updates(brief, None, [update("goal", "  ")], "x")
    assert not result.changed and "no text" in result.notes[0]
    apply_brief_updates(brief, None, [update("goal", "Something else", "something")], "something")
    assert brief.goal == Line(text="Explain the trial", mark="guessed")


def test_the_panel_marks_what_the_user_types_as_edited() -> None:
    brief = ConversationBrief(
        goal=Line(text="Explain the trial", mark="you"),
        audience=Line(text="lab members", mark="guessed"),
        constraints=[Line(text="no jargon", mark="you"), Line(text="short", mark="guessed")],
    )
    work = made()[1]
    edited, spec = edit_brief(
        brief,
        work,
        goal="Explain the trial",  # unchanged: keeps its mark
        audience="first-year students",
        constraints=["no jargon", "short", "cite everything"],
        open=["Include costs?"],
        use="read",
        length="",
    )
    assert edited.goal == Line(text="Explain the trial", mark="you")
    assert edited.audience == Line(text="first-year students", mark="edited")
    assert edited.constraints == [
        Line(text="no jargon", mark="you"),
        Line(text="short", mark="guessed"),
        Line(text="cite everything", mark="edited"),
    ]
    assert edited.open == [Line(text="Include costs?", mark="edited")]
    assert spec is not None
    assert spec.use == Line(text="read", mark="edited") and spec.length is None
    # A list is the whole new list: leaving one out removes it. None leaves a field alone.
    shorter, _ = edit_brief(edited, work, constraints=["short"])
    assert [line.text for line in shorter.constraints] == ["short"]
    assert shorter.goal == edited.goal and shorter.open == edited.open
    cleared, _ = edit_brief(edited, None, goal="")
    assert cleared.goal is None


def test_the_panel_refuses_what_it_cannot_set() -> None:
    brief, work = made()
    with pytest.raises(ValueError, match="no artifact yet"):
        edit_brief(brief, None, use="talk")
    with pytest.raises(ValueError, match="not a use"):
        edit_brief(brief, work, use="forever")
    with pytest.raises(ValueError, match="A report has no use"):
        edit_brief(brief, made("report")[1], use="talk")
    with pytest.raises(ValueError, match="at most 8"):
        edit_brief(brief, work, open=[f"question {n}" for n in range(9)])


def test_the_brief_becomes_the_skills_request() -> None:
    brief, work = made()
    brief.open.append(Line(text="Include costs?"))
    assert render_brief(brief, work) == (
        "Goal: Explain the Velmora trial\n"
        "Audience: lab members\n"
        "Use: talk\n"
        "Length: 10 minutes\n"
        "Structure: what\n"
        "Constraints:\n"
        "- at most 3 bullets"
    )
    spec = spec_of(brief, work)
    assert (spec.kind, spec.audience) == ("slides", "lab members")
    assert (spec.style, spec.title) == (None, None)
    assert spec.brief == render_brief(brief, work)
    assert spec_of(ConversationBrief(), work).audience == "scientist"
    assert render_brief(ConversationBrief()) == ""


def test_a_structure_the_user_chose_fixes_a_reports_style_but_a_guess_does_not() -> None:
    brief, report = made("report")
    report.spec.structure = Line(text="which", mark="you")
    assert spec_of(brief, report).style == "decision_brief"
    report.spec.structure = Line(text="which", mark="guessed")
    assert spec_of(brief, report).style is None
    report.spec.structure = Line(text="how", mark="you")  # no style names it
    assert spec_of(brief, report).style is None


# The workspace as stored -------------------------------------------------------------------------


def test_a_workspace_survives_a_trip_through_json() -> None:
    brief, work = made()
    state = Workspace(
        pool=[{"id": "cit_1", "source_id": "src_1", "ref": 1}],
        works=[work],
        active=work.key,
        pending=Pending(question="Which focus?", options=["Trial", "Debate"], message_id="acm_1"),
        log=["Version 1 saved."],
        asked_in_a_row=1,
    )
    work.parts["p2"].coverage = {"status": "complete", "claims": [{"text": "It is so [1]"}]}
    work.skipped.append({"step": "reader_test", "reason": "down"})
    raw = dump_workspace(state)
    assert load_workspace(raw) == state
    assert approach_state(load_workspace(raw).works[0], brief).state == "ok"
    assert load_workspace(None) == Workspace() == load_workspace("{}")


def test_an_unreadable_workspace_starts_again_empty() -> None:
    assert load_workspace("not json at all") == Workspace()
    assert load_workspace('{"works": [{"key": 3}]}') == Workspace()


def test_the_pool_keeps_its_numbers_and_grows_from_the_top() -> None:
    state = Workspace()
    pool = pool_from(state)
    assert len(pool) == 0 and pool.top == 0

    def passage(name: str) -> Evidence:
        citation = ConversationCitationOut(
            id=f"cit_{name}",
            source_id=f"src_{name}",
            source_title=name,
            quote=f"Quote of {name}",
            locator="p. 1",
            status="provisional",
            confidence=0.5,
            source_revision_id=f"rev_{name}",
            block_id=f"blk_{name}",
        )
        return Evidence(
            kind="library",
            title=name,
            text=citation.quote,
            locator="p. 1",
            payload=(citation, None),
        )

    numbers = [pool.adopt(passage(name)).ref for name in ("a", "b", "c")]
    assert numbers == [1, 2, 3]
    store_pool(state, pool)
    assert [item["ref"] for item in state.pool] == [1, 2, 3]
    # Back from storage: the same numbers, the same passages, and the next number is 4.
    again = pool_from(state)
    assert [(item.ref, item.title) for item in again.items] == [(1, "a"), (2, "b"), (3, "c")]
    assert again.adopt(passage("b")).ref == 2
    assert again.adopt(passage("d")).ref == 4


def test_a_versions_numbers_become_the_pools_and_a_dangling_one_is_left_out() -> None:
    text = "A [1] and B [2][p:3] and a group [1, 3], then [9]."
    assert pool_numbered(text, [7, 5, 6]) == "A [7] and B [5][p:6] and a group [7][6], then."
    # Nothing but the numbers is touched.
    assert pool_numbered("Plain  text\n| a | b |\n", [1]) == "Plain  text\n| a | b |\n"


# Bringing a saved version in -------------------------------------------------------------------


def test_what_the_checker_found_is_given_to_the_part_in_the_pools_numbers() -> None:
    section = "## A\n\nIt is so [1][2]."
    part = PartRec(text="## A\n\nIt is so [7][5].", inputs="")
    record = {
        "hash": section_hash(section),
        "issues": [{"claim": "It is so", "verdict": "unverified", "note": "From memory."}],
        "coverage": {
            "status": "complete",
            "considered": 1,
            "checked": 1,
            "unchecked": 0,
            "answerHash": section_hash(section),
            "claims": [{"text": "It is so [1][2]", "refs": [1, 2, 9], "verdict": "supported"}],
        },
    }
    assert _take_check(part, section, record, [7, 5]) is True
    assert part.checked == section_hash(part.text) != section_hash(section)
    assert part.issues == record["issues"] and part.issues is not record["issues"]
    assert part.coverage is not None
    assert part.coverage["answerHash"] == section_hash(part.text)
    assert part.coverage["claims"] == [
        {"text": "It is so [7][5]", "refs": [7, 5], "verdict": "supported"}
    ]
    # The version's own record is not touched.
    assert record["coverage"]["claims"][0]["refs"] == [1, 2, 9]
    # A record for other text is not taken.
    other = PartRec(text="## B", inputs="")
    assert _take_check(other, "## B", record, [7, 5]) is False and other.issues is None


def test_a_part_keeps_its_own_check_unless_the_versions_is_more_complete() -> None:
    section = "## A\n\nIt is so [1]."
    whole = {"hash": section_hash(section), "issues": [], "coverage": {"status": "complete"}}
    partial = {"hash": section_hash(section), "issues": [], "coverage": {"status": "partial"}}
    own = PartRec(
        text=section, inputs="", checked=section_hash(section), issues=[{"claim": "mine"}]
    )
    # Its own check, with no coverage record, stands against a version's.
    assert _take_check(own, section, whole, [1]) is False and own.issues == [{"claim": "mine"}]
    own.coverage = {"status": "partial"}
    assert _take_check(own, section, partial, [1]) is False
    assert _take_check(own, section, whole, [1]) is True
    assert own.issues == [] and own.coverage is not None and own.coverage["status"] == "complete"


def a_deck(tmp_path: Path, **overrides):
    fake = FakeProvider(skill_replies(SLIDES_APPROACH, SLIDES_OUTLINE, SLIDES, gather=SEARCH))
    return app_for(tmp_path, fake, skills=True, **overrides)


def version_of(client, base_id: str, artifact_id: str):
    """A saved version as the workspace reads it, and the Checker's records for it."""

    service = client.app.state.knowledge_service
    found = service.get_artifact(base_id, artifact_id, workspace(client)["X-Gunther-Workspace-Id"])
    with service.sessions() as session:
        return found, service._latest_check_records(session, artifact_id)


def hand_edit(client, base_id: str, artifact_id: str, content: str) -> str:
    response = client.post(
        route(base_id, artifact_id, "/edits"),
        headers=workspace(client),
        json={"clientRequestId": f"workspace_edit_{next(_edits):04d}", "content": content},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_import_work_restores_a_composed_scene_after_citation_mapping(tmp_path: Path) -> None:
    with a_deck(tmp_path) as client:
        base_id, _ = library(client)
        saved = built(build(client, base_id, body(kind="slides")))
        version, checks = version_of(client, base_id, saved["id"])

    sections = split_document(version.content, "slides")[1]
    page_body = (
        "<!-- layout: composed -->\n## A mixed result\n\n"
        "| Group | Rate (%) |\n| --- | --- |\n| A | 72% [1] |\n| B | 68% [2] |\n\n"
        "Measured within the study group [2]."
    )
    tree = SceneGroup(
        kind="row",
        weights=[2, 1],
        children=[
            SceneLeaf(kind="leaf", blockId="b1", view="bar_chart"),
            SceneLeaf(kind="leaf", blockId="b2", view="text"),
        ],
    )
    composed = with_scene(
        page_body,
        SceneEnvelope(
            version=2,
            sourceHash=source_hash(page_body),
            graphicsHash=graphics_hash(page_body),
            graphics={"b1": "bar_chart"},
            root=tree,
        ),
    )
    sections[1] = composed
    outline = list(version.outline)
    outline[1] = outline[1].model_copy(update={"heading": "A mixed result", "layout": "composed"})
    restored_version = version.model_copy(
        update={"content": "\n\n---\n\n".join(sections), "outline": outline}
    )
    state, brief = Workspace(), ConversationBrief()

    work = import_work(restored_version, checks, state, brief)

    assert _marked(work.parts["p2"].text).scene is not None
    assert source_hash(work.parts["p2"].text) == source_hash(composed)
    imported_scene = _marked(work.parts["p2"].text).scene
    assert imported_scene is not None and imported_scene.graphics == {"b1": "bar_chart"}
    assert imported_scene.graphics_hash == graphics_hash(work.parts["p2"].text)
    assert work.outline[1].layout == "composed"


def test_a_saved_deck_becomes_a_work_that_is_up_to_date(tmp_path: Path) -> None:
    with a_deck(tmp_path) as client:
        base_id, _ = library(client)
        saved = built(build(client, base_id, body(kind="slides", theme="editorial")))
        version, checks = version_of(client, base_id, saved["id"])

    state, brief = Workspace(), ConversationBrief()
    work = import_work(version, checks, state, brief)

    assert (work.key, work.version_id, work.title) == (
        version.lineage_id,
        version.id,
        version.title,
    )
    assert work.spec.kind == "slides" and work.spec.use == Line(text="talk", mark="guessed")
    assert work.spec.theme == "editorial"
    assert work.spec.structure is not None and work.spec.structure.text == "what"
    assert work.approach is not None and work.approach["question"] == APPROACH["question"]
    assert work.cards == [] and work.next_card == 1
    # The outline is the version's, zipped with its sections; ids run p1, p2, p3.
    assert [(item.id, item.heading, item.layout) for item in work.outline] == [
        ("p1", "Markers", "title"),
        ("p2", "CD3D and CD19 set T and B cells apart", "compare"),
        ("p3", "Use both in every panel", "takeaways"),
    ]
    assert work.next_item == 4
    assert [item.goal for item in work.outline] == [outline.goal for outline in version.outline]
    assert work.outline[1].link == version.outline[1].link
    # The parts are the sections, each with the Checker's findings for its text.
    sections = split_document(version.content, "slides")[1]
    assert [work.parts[item.id].text for item in work.outline] == sections
    assert all(part.checked == section_hash(part.text) for part in work.parts.values())
    assert all(part.issues is not None for part in work.parts.values())
    # The pool is the version's citations, numbered 1..N; its version map is the identity.
    assert work.version_map == list(range(1, len(version.citations) + 1))
    assert [item["ref"] for item in state.pool] == work.version_map
    assert [item["source_title"] for item in state.pool] == [
        citation.source_title for citation in version.citations
    ]
    # Each item lists the pool numbers its section cites; the slide cites [1] and [2].
    assert work.outline[1].refs == [1, 2]
    # Everything is up to date, so a small edit could be saved without redoing anything.
    assert approach_state(work, brief).state == "ok"
    assert outline_state(work).state == "ok"
    assert states(work, brief) == ["ok", "ok", "ok"]
    assert ready(work, brief) == []
    # The version's audience is taken as a guess, so the brief does not start out of date.
    assert brief.audience == Line(text="scientist", mark="guessed")
    assert state.active == work.key and state.works == [work]
    assert state.log == [
        f"“{version.title}” (version 1) was brought in: "
        "it was made before the agent kept its notes."
    ]


def test_a_saved_report_becomes_a_work_that_is_up_to_date(tmp_path: Path) -> None:
    fake = FakeProvider(skill_replies(APPROACH, OUTLINE, REPORT, gather=SEARCH))
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        saved = built(build(client, base_id, body(style="decision_brief")))
        version, checks = version_of(client, base_id, saved["id"])

    state, brief = Workspace(), ConversationBrief()
    work = import_work(version, checks, state, brief)

    assert work.spec.kind == "report" and work.spec.use is None
    # A style the person picked fixes the structure, and it is theirs.
    assert work.spec.structure == Line(text="which", mark="edited")
    assert [item.heading for item in work.outline] == [item.heading for item in version.outline]
    assert all(item.layout == "" for item in work.outline)
    assert [part.text.splitlines()[0] for part in work.parts.values()] == [
        f"## {item.heading}" for item in work.outline
    ]
    assert states(work, brief) == ["ok", "ok", "ok"]
    assert ready(work, brief) == []
    # The preamble (the title and the italic brief line) is not part of any section.
    assert split_document(version.content, "report")[0].startswith("# ")
    assert all(not part.text.startswith("# ") for part in work.parts.values())


def test_a_version_with_no_matching_outline_gets_items_from_its_headings(tmp_path: Path) -> None:
    with a_deck(tmp_path) as client:
        base_id, _ = library(client)
        saved = built(build(client, base_id, body(kind="slides")))
        content = saved["content"] + "\n\n---\n\n## An extra slide\n\n- Added by hand\n"
        head_id = hand_edit(client, base_id, saved["id"], content)
        version, checks = version_of(client, base_id, head_id)

    assert len(version.outline) == 3 and len(split_document(version.content, "slides")[1]) == 4
    work = import_work(version, checks, Workspace(), ConversationBrief())
    assert [(item.id, item.heading, item.goal) for item in work.outline] == [
        ("p1", "Markers", ""),
        ("p2", "CD3D and CD19 set T and B cells apart", ""),
        ("p3", "Use both in every panel", ""),
        ("p4", "An extra slide", ""),
    ]
    # The slides say how they are drawn (a marker, when they have one); the first, headed
    # with "# ", is the title; a slide typed by hand says nothing.
    slides = split_document(version.content, "slides")[1]
    marked = [layout_of(section)[0] or "" for section in slides]
    assert marked[3] == "" and [item.layout for item in work.outline] == [
        marked[0] or "title",
        *marked[1:],
    ]
    # The typed slide has no check; the others kept theirs by hash.
    scientists = ConversationBrief(audience=Line(text="scientist"))
    assert states(work, scientists) == ["ok", "ok", "ok", "not_checked"]


def test_a_second_version_joins_the_pool_without_changing_any_number(tmp_path: Path) -> None:
    fake = FakeProvider(skill_replies(APPROACH, OUTLINE, REPORT, gather=SEARCH))
    # The second report cites the second passage first, so its numbers are the other way round.
    swapped = [
        "## Surface markers tell T and B cells apart\n\nCD19 marks B cells [2].",
        "## CD3D is the T-cell marker\n\nCD3D marks T cells [1].",
        "## CD19 is the B-cell marker\n\nCD19 again [2] and CD3D [1].",
    ]
    other = FakeProvider(skill_replies(APPROACH, OUTLINE, swapped, gather=SEARCH))
    with app_for(tmp_path / "one", fake, skills=True) as one:
        base_id, _ = library(one)
        first = built(build(one, base_id, body()))
        v1, c1 = version_of(one, base_id, first["id"])
    with app_for(tmp_path / "two", other, skills=True) as two:
        base_id, _ = library(two)
        second = built(build(two, base_id, body()))
        v2, c2 = version_of(two, base_id, second["id"])

    state, brief = Workspace(), ConversationBrief()
    one_work = import_work(v1, c1, state, brief)
    # The two libraries are different databases, so nothing is shared: the new sources join.
    two_work = import_work(v2, c2, state, brief)
    assert one_work.version_map == [1, 2]
    assert two_work.version_map == [3, 4]
    assert [item["ref"] for item in state.pool] == [1, 2, 3, 4]
    # The second version's [1] is the pool's 3, and its [2] the pool's 4.
    assert two_work.parts[two_work.outline[0].id].text.endswith("CD19 marks B cells [3].")
    assert two_work.parts[two_work.outline[1].id].text.endswith("CD3D marks T cells [4].")
    assert [work.key for work in state.works] == [v1.lineage_id, v2.lineage_id]
    # The first work is still in the pool's numbers it had.
    assert one_work.version_map == [1, 2]
    with pytest.raises(ValueError, match="already has a work"):
        import_work(v1, c1, state, brief)


def test_the_same_passages_keep_their_pool_numbers_in_a_second_work(tmp_path: Path) -> None:
    fake = FakeProvider(skill_replies(APPROACH, OUTLINE, REPORT, gather=SEARCH))
    swapped = [
        "## Surface markers tell T and B cells apart\n\nCD19 marks B cells [2].",
        "## CD3D is the T-cell marker\n\nCD3D marks T cells [1].",
        "## CD19 is the B-cell marker\n\nCD19 again [2] and CD3D [1].",
    ]
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        first = built(build(client, base_id, body()))
        v1, c1 = version_of(client, base_id, first["id"])
        # Another output in the same library, citing the same passages in another order.
        fake.replies[:] = [skill_replies(APPROACH, OUTLINE, swapped, gather=SEARCH)]
        second = built(build(client, base_id, body()))
        v2, c2 = version_of(client, base_id, second["id"])

    assert [c.quote for c in v1.citations] != [c.quote for c in v2.citations]
    state, brief = Workspace(), ConversationBrief()
    one = import_work(v1, c1, state, brief)
    two = import_work(v2, c2, state, brief)
    # The same two passages: no new sources, but the second version's [1] is the pool's 2.
    assert len(state.pool) == 2
    assert one.version_map == [1, 2] and two.version_map == [2, 1]
    # In the pool's numbers the text reads as the writer wrote it.
    assert two.parts[two.outline[0].id].text.endswith("CD19 marks B cells [2].")
    assert two.parts[two.outline[1].id].text.endswith("CD3D marks T cells [1].")


# Bringing the work back in line after the version changed outside the agent --------------------


@contextmanager
def a_report_work(tmp_path: Path):
    """An app with a saved three-section report imported as a work: the client, the library,
    the saved version, the work, the workspace and the brief."""

    fake = FakeProvider(skill_replies(APPROACH, OUTLINE, REPORT, gather=SEARCH))
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        saved = built(build(client, base_id, body()))
        version, checks = version_of(client, base_id, saved["id"])
        state, brief = Workspace(), ConversationBrief()
        work = import_work(version, checks, state, brief)
        yield client, base_id, saved, work, state, brief


def test_a_work_that_matches_its_head_is_left_alone(tmp_path: Path) -> None:
    with a_report_work(tmp_path) as (client, base_id, saved, work, state, brief):
        version, checks = version_of(client, base_id, saved["id"])
        before = work.model_dump()
        assert sync(work, version, checks, state, brief) is False
        assert work.model_dump() == before


def test_a_hand_edited_section_is_taken_in_and_unchanged_ones_are_kept(tmp_path: Path) -> None:
    with a_report_work(tmp_path) as (client, base_id, saved, work, state, brief):
        sections = split_document(saved["content"], "report")[1]
        content = saved["content"].replace(
            "CD3D is a marker of T cells [1].", "CD3D is a marker of T cells [1]. I typed this."
        )
        head_id = hand_edit(client, base_id, saved["id"], content)
        head, checks = version_of(client, base_id, head_id)
        was = {item_id: part.model_copy(deep=True) for item_id, part in work.parts.items()}

        assert sync(work, head, checks, state, brief) is True

    assert work.version_id == head_id and work.title == head.title
    assert [item.id for item in work.outline] == ["p1", "p2", "p3"]
    # The two sections that were not touched are exactly as they were, checks included.
    for item_id in ("p1", "p3"):
        assert work.parts[item_id] == was[item_id]
    # The typed one has its new text, counts as up to date, and is not checked.
    typed = work.parts["p2"]
    assert typed.text.endswith("I typed this.") and typed.text != was["p2"].text
    assert typed.checked is None and typed.issues is None
    assert [part.state for part in status(work, brief)] == ["ok", "not_checked", "ok"]
    assert work.version_map == [1, 2] and sections[1] != typed.text
    # A second sync finds nothing to do.
    assert sync(work, head, checks, state, brief) is False


def test_pressing_check_again_brings_the_checks_in(tmp_path: Path) -> None:
    with a_report_work(tmp_path) as (client, base_id, saved, work, state, brief):
        content = saved["content"].replace(
            "CD19 is a marker of B cells [2].", "CD19 marks B cells [2]."
        )
        head_id = hand_edit(client, base_id, saved["id"], content)
        head, checks = version_of(client, base_id, head_id)
        sync(work, head, checks, state, brief)
        assert [part.state for part in status(work, brief)] == ["ok", "ok", "not_checked"]

        checked = client.post(route(base_id, head_id, "/check"), headers=workspace(client))
        assert checked.status_code == 200, checked.text
        head, checks = version_of(client, base_id, head_id)
        # The version did not change, so only the new findings come in.
        assert sync(work, head, checks, state, brief) is True
        assert [part.state for part in status(work, brief)] == ["ok", "ok", "ok"]
        assert work.parts["p3"].checked == section_hash(work.parts["p3"].text)


def test_a_slide_added_by_hand_is_a_new_item_and_the_others_keep_their_ids(
    tmp_path: Path,
) -> None:
    with a_deck(tmp_path) as client:
        base_id, _ = library(client)
        saved = built(build(client, base_id, body(kind="slides")))
        version, checks = version_of(client, base_id, saved["id"])
        state, brief = Workspace(), ConversationBrief()
        work = import_work(version, checks, state, brief)
        was = {item_id: part.model_copy(deep=True) for item_id, part in work.parts.items()}

        sections = split_document(saved["content"], "slides")[1]
        added = "## A slide I added\n\n- By hand [1]"
        content = "\n\n---\n\n".join([sections[0], added, sections[1], sections[2]])
        head_id = hand_edit(client, base_id, saved["id"], content)
        head, head_checks = version_of(client, base_id, head_id)
        assert sync(work, head, head_checks, state, brief) is True

    assert [(item.id, item.heading) for item in work.outline] == [
        ("p1", "Markers"),
        ("p4", "A slide I added"),
        ("p2", "CD3D and CD19 set T and B cells apart"),
        ("p3", "Use both in every panel"),
    ]
    assert work.next_item == 5
    for item_id in ("p1", "p2", "p3"):
        assert work.parts[item_id] == was[item_id]
    assert work.parts["p4"].text == added and work.parts["p4"].issues is None
    # The new item cites what its slide cites, in the pool's numbers.
    assert work.outline[1].refs == [1] and work.outline[1].layout == ""
    assert [part.state for part in status(work, brief)] == ["ok", "not_checked", "ok", "ok"]
    assert ready(work, brief) == []


def test_a_slide_deleted_by_hand_drops_its_item_and_its_id_is_never_used_again(
    tmp_path: Path,
) -> None:
    with a_deck(tmp_path) as client:
        base_id, _ = library(client)
        saved = built(build(client, base_id, body(kind="slides")))
        version, checks = version_of(client, base_id, saved["id"])
        state, brief = Workspace(), ConversationBrief()
        work = import_work(version, checks, state, brief)

        sections = split_document(saved["content"], "slides")[1]
        without_second = "\n\n---\n\n".join([sections[0], sections[2]])
        head_id = hand_edit(client, base_id, saved["id"], without_second)
        head, head_checks = version_of(client, base_id, head_id)
        sync(work, head, head_checks, state, brief)
        assert [item.id for item in work.outline] == ["p1", "p3"]
        assert set(work.parts) == {"p1", "p3"}

        later = "\n\n---\n\n".join([sections[0], "## Brand new\n\n- text", sections[2]])
        newest = hand_edit(client, base_id, head_id, later)
        head, head_checks = version_of(client, base_id, newest)
        sync(work, head, head_checks, state, brief)
    assert [item.id for item in work.outline] == ["p1", "p4", "p3"]


def test_a_retitled_slide_keeps_its_item_and_moved_slides_keep_theirs(tmp_path: Path) -> None:
    with a_deck(tmp_path) as client:
        base_id, _ = library(client)
        saved = built(build(client, base_id, body(kind="slides")))
        version, checks = version_of(client, base_id, saved["id"])
        state, brief = Workspace(), ConversationBrief()
        work = import_work(version, checks, state, brief)
        sections = split_document(saved["content"], "slides")[1]

        retitled = sections[2].replace("## Use both in every panel", "## Use both, always")
        with_retitled = "\n\n---\n\n".join([*sections[:2], retitled])
        head_id = hand_edit(client, base_id, saved["id"], with_retitled)
        head, head_checks = version_of(client, base_id, head_id)
        sync(work, head, head_checks, state, brief)
        # Same number of slides: the retitled one is matched by its place, and its heading follows.
        assert [(item.id, item.heading) for item in work.outline][2] == ("p3", "Use both, always")

        swapped = "\n\n---\n\n".join([sections[0], retitled, sections[1]])
        newest = hand_edit(client, base_id, head_id, swapped)
        head, head_checks = version_of(client, base_id, newest)
        sync(work, head, head_checks, state, brief)
    # The slides moved; each keeps its item, so its goal, layout and cards go with it.
    assert [item.id for item in work.outline] == ["p1", "p3", "p2"]
    assert work.parts["p3"].text == retitled


def test_a_head_numbered_differently_is_read_by_what_it_cites(tmp_path: Path) -> None:
    with a_report_work(tmp_path) as (client, base_id, saved, work, state, brief):
        version, checks = version_of(client, base_id, saved["id"])
    assert work.version_map == [1, 2]
    # A version made outside the agent can number the same passages the other way round.
    content = version.content.replace("[1]", "[x]").replace("[2]", "[1]").replace("[x]", "[2]")
    flipped = version.model_copy(
        update={
            "id": "art_flipped",
            "citations": list(reversed(version.citations)),
            "content": content,
        }
    )
    assert sync(work, flipped, {}, state, brief) is True
    assert work.version_map == [2, 1] and len(state.pool) == 2
    # Its text now cites the same pool numbers as before, so the parts did not change.
    assert [part.state for part in status(work, brief)] == ["ok", "ok", "ok"]
    # A passage the pool has not met joins it under the next number.
    extra = version.citations[0].model_copy(
        update={"source_id": "src_new", "block_id": "blk_new", "quote": "A new passage"}
    )
    newer = flipped.model_copy(update={"id": "art_newer", "citations": [*flipped.citations, extra]})
    sync(work, newer, {}, state, brief)
    assert work.version_map == [2, 1, 3] and [item["ref"] for item in state.pool] == [1, 2, 3]


def test_a_number_the_head_has_no_source_for_is_left_out_and_noted(tmp_path: Path) -> None:
    with a_report_work(tmp_path) as (client, base_id, saved, work, state, brief):
        content = saved["content"].replace("CD19 marks B cells [2]", "CD19 marks B cells [2][9]")
        head_id = hand_edit(client, base_id, saved["id"], content)
        head, checks = version_of(client, base_id, head_id)
        sync(work, head, checks, state, brief)
    # [9] could be read as some other source of the pool, so it is not carried over.
    assert all("[9]" not in part.text for part in work.parts.values())
    assert work.skipped and work.skipped[0]["step"] == "sync"
    assert "[9]" in work.skipped[0]["reason"]


def test_once_the_approach_is_written_only_the_users_words_change_its_inputs() -> None:
    brief, work = made()
    guessed = apply_brief_updates(
        brief, work, [update("goal", "A new reading"), update("length", "20 minutes")], "Shorter"
    )
    assert guessed.brief.goal == brief.goal and guessed.spec == work.spec
    assert len(guessed.notes) == 2 and "only your own words" in guessed.notes[0]
    said = apply_brief_updates(
        brief, work, [update("goal", "Focus on cost", "focus on cost")], "Now focus on cost"
    )
    assert said.brief.goal == Line(text="Focus on cost", mark="you")
