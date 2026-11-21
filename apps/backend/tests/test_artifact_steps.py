"""WorkSteps regressions against Studio and saved workspaces."""

import json
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest
from fake_models import (
    FakeProvider,
    gateway,
    is_outlining,
    is_replacing,
    is_writing,
    output_replies,
    studio_task,
)
from test_output_skills import OUTLINE, REPORT, SLIDES, SLIDES_OUTLINE

from gunther.agent import Evidence, Tool
from gunther.artifact_steps import PassageNotFound, WorkSteps
from gunther.artifact_workspace import (
    ConversationBrief,
    ItemRec,
    Kind,
    Line,
    PartRec,
    Work,
    WorkSpec,
    approach_state,
    digest_outline_inputs,
    digest_part_inputs,
    locate,
    outline_state,
    ready,
    status,
)
from gunther.checks import finding, verdicts_of
from gunther.llm import ModelError
from gunther.outputs import Notes, Pool, layout_of, section_hash, split_document, with_layout
from gunther.schemas import ConversationCitationOut
from gunther.studio import format as fmt
from gunther.studio.work import StudioWorkAgents


def steps_for(fake: FakeProvider, kind: Kind = "report") -> WorkSteps:
    agents = StudioWorkAgents(gateway(fake), kind, tools=[library_tool()])
    pool = Pool(120)
    for item in LIBRARY:
        pool.adopt(item)
    return WorkSteps(
        agents, Work(key="draft_1", spec=WorkSpec(kind=kind)), ConversationBrief(),
        pool, Notes(), agents.gateway.models[0], None,
    )


def drafted(fake: FakeProvider, kind: Kind = "report") -> WorkSteps:
    """Load existing parts with their original checks and stamps."""
    steps = steps_for(fake, kind)
    work = steps.work
    outline, texts = (SLIDES_OUTLINE, SLIDES) if kind == "slides" else (OUTLINE, REPORT)
    work.title = outline["title"]
    for index, (row, text) in enumerate(zip(outline["sections"], texts, strict=True)):
        layout = row.get("layout", "") if kind == "slides" else ""
        if kind == "slides":
            layout = "title" if index == 0 else "takeaways" if index == 2 else layout
            text = with_layout(text, layout, design_version=1)
        item = ItemRec(id=f"p{index + 1}", heading=row["heading"],
                       goal=row.get("goal", ""), layout=layout)
        work.outline.append(item)
        work.parts[item.id] = PartRec(
            text=text, inputs=digest_part_inputs(steps.brief, work, item),
            checked=section_hash(text), issues=[],
        )
    work.next_item = len(work.outline) + 1
    work.outline_inputs = digest_outline_inputs(work)
    return steps


def report_replies(**options):
    return output_replies(OUTLINE, REPORT, **options)


def passage(title: str, quote: str, kind: str = "library", url: str | None = None) -> Evidence:
    citation = ConversationCitationOut(
        id=f"cit_{title}",
        source_id=f"src_{title}",
        source_title=title,
        quote=quote,
        locator="note",
        status="provisional",
        confidence=0.0,
        source_revision_id=f"rev_{title}",
        block_id=f"blk_{title}",
        url=url,
    )
    return Evidence(
        kind=kind,
        title=title,
        text=quote,
        locator="note",
        status="provisional",
        url=url,
        payload=(citation, None),
    )


LIBRARY = [
    passage("Markers", "CD3D is a marker of T cells."),
    passage("Receptors", "CD19 is a marker of B cells."),
]


def library_tool(passages: list[Evidence] | None = None) -> Tool:
    found = LIBRARY if passages is None else passages
    return Tool("search_library", "the library", lambda q: list(found), "your library", grade=True)


SHOWN = re.compile(
    r"The passage to change \(it is in the \w+ above\):\n(.*?)\n\nInstruction:", re.S
)


def replacing(answers: dict[str, str]) -> Callable[[dict[str, Any]], str]:
    """What the writer answers when asked to change a passage: the new text for the passage it
    was shown (a passage it was not expected to be shown fails the test)."""

    def reply(request: dict[str, Any]) -> str:
        shown = SHOWN.search(str(request["messages"][-1]["content"]))
        assert shown is not None
        return json.dumps({"text": answers[shown.group(1)]})

    return reply


def snapshot(work: Work) -> dict[str, Any]:
    return {key: part.model_copy(deep=True) for key, part in work.parts.items()}


def states(steps: WorkSteps) -> list[str]:
    return [part.state for part in status(steps.work, steps.brief)]


def test_understand_keeps_claim_ids_and_stales_the_previous_outline() -> None:
    memos = iter([
        {
            "answer": "These cells use different markers.",
            "insights": [
                {"claim": "CD3D marks T cells", "refs": [1]},
                {"claim": "CD19 marks B cells", "refs": [2]},
            ],
        },
        {
            "answer": "A second reading compares the markers' roles.",
            "insights": [
                {"claim": "cd3d MARKS  T cells", "refs": [1, 3], "so_what": "New comparison"},
                {"claim": "The markers distinguish the two populations", "refs": [1, 2]},
            ],
        },
    ])
    replies = report_replies()

    def reply(request):
        task = studio_task(request)
        if "lead analyst" in task:
            assert "Choose a marker panel" in task
            return json.dumps(next(memos))
        return replies(request)

    fake = FakeProvider(reply)
    steps = steps_for(fake)
    goal = Line(text="Choose a marker panel", mark="you")
    steps.brief.goal = goal
    steps.understand()
    work = steps.work
    assert work.approach["answer"] == "These cells use different markers."
    assert [(card.id, card.refs) for card in work.cards] == [("C1", [1]), ("C2", [2])]
    assert work.approach_top == steps.pool.top == 2
    assert approach_state(work, steps.brief).state == "ok"
    assert outline_state(work).state == "not_written" and work.outline_inputs == ""

    steps.plan_whole()
    original_stamp = work.outline_inputs
    assert outline_state(work).state == "ok"
    news = passage("Trial update", "A later trial confirms the marker.", "web", "https://news.test/a")
    assert steps.pool.adopt(news).ref == 3
    steps.understand("Compare the markers' roles.")

    assert [(card.id, card.refs, card.note) for card in work.cards] == [
        ("C1", [1, 3], "New comparison"),
        ("C3", [1, 2], ""),
    ]
    readings = [r for r in fake.requests if "lead analyst" in studio_task(r)]
    first, second = [studio_task(r) for r in readings]
    assert "Focus for this reading" not in first
    assert "Focus for this reading: Compare the markers' roles." in second
    assert "Trial update" in readings[1]["messages"][-1]["content"]
    assert steps.pool.by_ref()[3].url == news.url and steps.brief.goal == goal
    assert work.next_card == 4 and approach_state(work, steps.brief).state == "ok"
    assert work.outline_inputs == original_stamp and outline_state(work).state == "out_of_date"


@pytest.mark.parametrize("goal", [None, "Choose markers for a panel"])
def test_studio_work_steps_make_and_assemble_a_report(goal) -> None:
    summary, *sections = OUTLINE["sections"]
    story = {
        "title": OUTLINE["title"],
        "summary_heading": summary["heading"],
        "sections": sections,
    }
    replies = report_replies()

    def reply(request):
        if is_outlining(request):
            return json.dumps(story)
        if "lead analyst" in studio_task(request) and goal:
            task = studio_task(request)
            assert goal in task and "Report style (decision_brief)" in task
        return replies(request)

    steps = steps_for(FakeProvider(reply))
    if goal:
        steps.brief.goal = Line(text=goal, mark="you")
        steps.work.spec.structure = Line(text="which", mark="you")
    steps.understand()
    steps.plan_whole()
    assert steps.write(["p1", "p2", "p3"]) == ["p1", "p2", "p3"]
    built, mapping = steps.assemble()
    preamble, parts = split_document(built.content, "report")
    expected = f"<!-- report: v2 -->\n# {OUTLINE['title']}"
    assert preamble.strip() == (f"{expected}\n\n_{goal}_" if goal else expected)
    assert parts == REPORT and mapping == list(built.pool_refs) == [1, 2]
    assert ready(steps.work, steps.brief) == []


@pytest.mark.parametrize("use", [None, Line(text="read", mark="you")], ids=["guessed", "explicit"])
def test_studio_makes_a_deck_and_writes_the_cover_without_research(use) -> None:
    closing = SLIDES[2].replace(
        "- CD3D for T cells [1]", "- CD3D for T cells [1]\n- CD19 for B cells [2]",
    )
    slides = [
        fmt.with_marker(f"{SLIDES[0]} [1][2]", "title", design="v2", theme="paper"),
        fmt.with_marker(SLIDES[1], "table"),
        fmt.with_marker(closing, "points"),
    ]
    rows, layouts = SLIDES_OUTLINE["sections"], ("title", "table", "points")
    outline = {
        **SLIDES_OUTLINE,
        "sections": [{**row, "layout": layout} for row, layout in zip(rows, layouts, strict=True)],
    }
    fake = FakeProvider(output_replies(outline, slides))
    steps = steps_for(fake, "slides")
    steps.work.spec.use = use
    steps.understand()
    steps.plan_whole()
    events = []
    steps.emit = events.append
    assert steps.write(["p1"]) == ["p1"]
    assert [event["state"] for event in events if event["type"] == "section"] == [
        "writing", "checking", "done",
    ]
    assert not any(event.get("tool") == "search_library" for event in events)
    expected_use = use or Line(text="talk", mark="guessed")
    cover_call = next(r for r in fake.requests if "Write slide 1." in r["messages"][0]["content"])
    assert f"Use: {expected_use.text}." in cover_call["messages"][0]["content"]
    assert all(item.text in cover_call["messages"][-1]["content"] for item in LIBRARY)
    assert "[1][2]" in steps.work.parts["p1"].text
    assert steps.write(["p2", "p3"]) == ["p2", "p3"]
    built, mapping = steps.assemble()
    assert [layout_of(slide)[0] for slide in split_document(built.content, "slides")[1]] == [
        "title", "table", "points",
    ]
    assert built.title == outline["title"] and mapping == [1, 2]
    assert steps.work.spec.use == expected_use
    assert approach_state(steps.work, steps.brief).state == "ok"
    assert outline_state(steps.work).state == "ok" and ready(steps.work, steps.brief) == []


@pytest.mark.parametrize("change", ["none", "rename-and-replace", "two-new-parts"])
def test_replan_preserves_drafts_only_by_stable_id(change) -> None:
    replies = report_replies()
    summary, *sections = [
        {"id": f"p{index}", "heading": row["heading"], "job": row["goal"]}
        for index, row in enumerate(OUTLINE["sections"], 1)
    ]
    story = dict(
        title=OUTLINE["title"], summary_heading=summary["heading"],
        summary_job=summary["job"], summary_id=summary["id"], sections=sections,
    )

    def reply(request):
        if is_outlining(request):
            return json.dumps(story)
        return replies(request)

    steps = drafted(FakeProvider(reply))
    steps.understand()
    steps.plan_whole()
    before = steps.work.model_copy(deep=True)
    events = []
    steps.emit = events.append
    if change == "rename-and-replace":
        story.update(summary_heading="How the markers compare",
                     sections=[sections[0], {**sections[1], "id": None}])
    if change == "two-new-parts":
        story["sections"] = [
            {"heading": "One result", "refs": [1]}, {"heading": "A second result", "refs": [1]},
        ]
    steps.understand()
    steps.plan_whole()
    work = steps.work
    ids, surviving, expected_states = {
        "none": (["p1", "p2", "p3"], {"p1", "p2", "p3"}, ["ok"] * 3),
        "rename-and-replace": (
            ["p1", "p2", "p4"], {"p1", "p2"}, ["out_of_date", "ok", "not_written"],
        ),
        "two-new-parts": (["p1", "p4", "p5"], {"p1"}, ["ok", "not_written", "not_written"]),
    }[change]
    assert [part.id for part in work.outline] == ids and work.next_item == int(ids[-1][1:]) + 1
    assert work.parts == {identity: before.parts[identity] for identity in surviving}
    assert states(steps) == expected_states
    assert work.title == before.title and outline_state(work).state == "ok"
    assert approach_state(work, steps.brief).state == "ok"
    if change == "none":
        assert work.approach == before.approach and work.cards == before.cards
        assert work.next_card == before.next_card
    steps.plan_insert(1, "Costs matter", "What it costs")
    first, edit = [event for event in events if event["type"] == "outline"]
    assert first["title"] == work.title
    assert edit["sections"][1] == {"heading": "Costs matter", "goal": "What it costs"}


def test_replanned_cover_keeps_its_id_and_legacy_theme_through_a_rewrite() -> None:
    new_heading = "Two markers distinguish these cell populations"
    cover = f"# {new_heading}\n\nThe measured differences are useful for a panel."
    slides = [
        {"id": "p1", "title": new_heading, "layout": "title"},
        *[{"id": f"p{index}", "title": row["heading"], "layout": "table"}
          for index, row in enumerate(SLIDES_OUTLINE["sections"][1:], 2)],
    ]
    replies = output_replies(SLIDES_OUTLINE, [cover, *SLIDES[1:]])

    def reply(request):
        if is_outlining(request):
            return json.dumps({"title": new_heading, "slides": slides})
        return replies(request)

    steps = drafted(FakeProvider(reply), "slides")
    work = steps.work
    work.spec.theme = "editorial"
    part = work.parts["p1"]
    part.text = with_layout(part.text, "title", theme=work.spec.theme)
    steps.understand()
    steps.plan_whole()
    assert work.outline[0].id == "p1" and work.outline[0].heading == new_heading
    assert work.parts["p1"] is part
    assert steps.write(["p1"]) == ["p1"]
    saved_cover = fmt.parse_slide(work.parts["p1"].text)
    assert (saved_cover.title, saved_cover.attrs["theme"]) == (new_heading, "editorial")
    assert steps.assemble()[0].title == new_heading


def test_work_steps_refuse_a_different_kind_or_a_missing_approach_or_part() -> None:
    fake = FakeProvider()
    steps = steps_for(fake)
    with pytest.raises(ValueError, match="report cannot write a slides work"):
        WorkSteps(
            steps.agents, Work(key="deck", spec=WorkSpec(kind="slides")), steps.brief,
            steps.pool, steps.notes, steps.model, steps.effort,
        )
    with pytest.raises(ValueError, match="approach is not written"):
        steps.plan_whole()
    steps.work.outline = [ItemRec(id="p1", heading="A report section")]
    with pytest.raises(ValueError, match="no part p9"):
        steps.write(["p1", "p9"])
    assert steps.write([]) == [] and steps.work.parts == {} and fake.requests == []


def test_a_change_of_one_part_is_code_only() -> None:
    fake = FakeProvider()
    steps = drafted(fake, "slides")
    work = steps.work
    calls = len(fake.requests)
    kept = snapshot(work)

    added = steps.plan_insert(1, "Costs matter", "What it costs", "stat")
    assert (added.id, added.layout) == ("p4", "stat")
    assert [item.id for item in work.outline] == ["p1", "p4", "p2", "p3"]
    steps.plan_move("p3", 1)
    assert [item.id for item in work.outline] == ["p1", "p3", "p4", "p2"]
    steps.plan_item("p2", goal="Say it with the table")
    changed = steps.plan_item("p4", heading="Costs fall", layout="compare")
    assert (changed.heading, changed.layout) == ("Costs fall", "compare")
    steps.plan_delete("p3")
    assert [item.id for item in work.outline] == ["p1", "p4", "p2"]

    assert len(fake.requests) == calls  # no model was asked
    # The deleted part's text went with it; the others have theirs, byte for byte.
    assert set(work.parts) == {"p1", "p2"}
    assert work.parts["p1"] == kept["p1"] and work.parts["p2"] == kept["p2"]
    # A part is out of date when its item changed; a moved or untouched one is not.
    assert states(steps) == ["ok", "not_written", "out_of_date"]
    assert status(work, steps.brief)[2].why == "its outline item changed"


def test_a_decks_title_slide_stays_first_and_its_closing_slide_stays_last() -> None:
    steps = drafted(FakeProvider(), "slides")
    work = steps.work

    def order() -> list[str]:
        return [item.id for item in work.outline]

    # Added at the start it goes after the title; at the end, before the takeaways.
    steps.plan_insert(0, "At the start", "", "points")
    steps.plan_insert(len(work.outline), "At the end", "", "points")
    assert [item.heading for item in work.outline] == [
        "Markers",
        "At the start",
        "CD3D and CD19 set T and B cells apart",
        "At the end",
        "Use both in every panel",
    ]
    assert order() == ["p1", "p4", "p2", "p5", "p3"]
    # The title cannot be deleted or moved; nothing goes before it, and no other slide is one.
    with pytest.raises(ValueError, match="title slide"):
        steps.plan_delete("p1")
    with pytest.raises(ValueError, match="title slide"):
        steps.plan_move("p1", 3)
    steps.plan_move("p5", 0)
    assert order() == ["p1", "p5", "p4", "p2", "p3"]
    steps.plan_move("p5", len(work.outline))  # to the end: before the takeaways
    assert order() == ["p1", "p4", "p2", "p5", "p3"]
    with pytest.raises(ValueError, match="one title slide"):
        steps.plan_item("p2", layout="title")
    with pytest.raises(ValueError, match="one title slide"):
        steps.plan_item("p1", layout="points")
    with pytest.raises(ValueError, match="one title slide"):
        steps.plan_insert(1, "Another title", "", "title")
    # The person may change the closing slide: it is then no longer kept last.
    steps.plan_item("p3", layout="points")
    steps.plan_insert(len(work.outline), "After the last", "", "points")
    assert work.outline[-1].heading == "After the last"


def test_outline_changes_require_valid_parts_and_layouts() -> None:
    steps = drafted(FakeProvider(), "slides")
    with pytest.raises(ValueError, match="“pie” is not one of the deck's layouts"):
        steps.plan_insert(1, "Pie", "", "pie")
    with pytest.raises(ValueError, match="needs a heading"):
        steps.plan_insert(1, "  ", "", "text")
    with pytest.raises(ValueError, match="no position 9"):
        steps.plan_insert(9, "Too far", "", "points")
    with pytest.raises(ValueError, match="no position 9"):
        steps.plan_move("p2", 9)
    with pytest.raises(ValueError, match="no part p9"):
        steps.plan_delete("p9")
    with pytest.raises(ValueError, match="Say what to change"):
        steps.plan_item("p2")
    with pytest.raises(ValueError, match="cannot be empty"):
        steps.plan_item("p2", heading=" ")
    assert [item.id for item in steps.work.outline] == ["p1", "p2", "p3"]


def test_a_reports_sections_have_no_layout_and_no_title_slide() -> None:
    steps = drafted(FakeProvider())
    work = steps.work
    with pytest.raises(ValueError, match="no layout"):
        steps.plan_insert(1, "A section", "", "text")
    with pytest.raises(ValueError, match="no layout"):
        steps.plan_item("p2", layout="text")
    added = steps.plan_insert(0, "A new first section", "Why it matters")
    assert [item.id for item in work.outline] == ["p4", "p1", "p2", "p3"]
    assert added.layout == ""
    steps.plan_move("p1", 4)
    assert [item.id for item in work.outline] == ["p4", "p2", "p3", "p1"]
    steps.plan_delete("p4")
    assert [item.id for item in work.outline] == ["p2", "p3", "p1"]
    # A report is named by its own title, not by a first section.
    steps.plan_item("p2", heading="CD3D marks T cells")
    assert work.title == "Telling immune cells apart"


def test_the_title_slides_heading_is_the_decks_title() -> None:
    steps = drafted(FakeProvider(), "slides")
    steps.plan_item("p1", heading="Markers for a panel")
    assert steps.work.title == "Markers for a panel"


def test_one_passage_is_replaced_and_the_rest_of_the_part_is_as_it_was() -> None:
    new = "CD3D is on every T cell [1]"
    fake = FakeProvider(report_replies(replacer=replacing({"CD3D marks T cells [1]": new})))
    steps = drafted(fake)
    work = steps.work
    kept = snapshot(work)
    old = kept["p1"].text
    before = len(fake.requests)

    # Quoted as the person saw it: another case, another number for the same source.
    changed = steps.edit("p1", "cd3d marks T cells [7]", "Say where it is found")

    assert changed is True
    sent = fake.requests[before:]
    [asked] = [r for r in sent if is_replacing(r)]
    assert len(sent) <= 5
    system = str(asked["messages"][0]["content"])
    assert system.startswith(
        "You are Gunther's writer, changing one passage of a section in a report, from the "
        "numbered sources (the pool)."
    )
    assert "Reply with the JSON object" in system and "bullet stays a bullet" in system
    assert "Keep the source numbers [n]" in system
    assert "[?] when it is your own knowledge" in system
    prompt = str(asked["messages"][-1]["content"])
    assert "Instruction: Say where it is found" in prompt
    assert "[1] (library" in prompt  # the sources
    assert "CD19 is the B-cell marker" not in prompt  # no other part is sent
    start = old.index("CD3D marks T cells [1]")
    end = start + len("CD3D marks T cells [1]")
    part = work.parts["p1"]
    assert part.text == old[:start] + new + old[end:]
    assert part.text.endswith("; CD19 marks B cells [2].")  # the citations it kept
    assert part.checked == section_hash(part.text) and part.issues == []
    assert part.inputs == kept["p1"].inputs
    assert work.parts["p2"] == kept["p2"] and work.parts["p3"] == kept["p3"]
    assert part.coverage["status"] == "partial"
    assert states(steps) == ["not_checked", "ok", "ok"]


def test_a_passage_that_is_not_there_is_an_error_that_costs_nothing() -> None:
    fake = FakeProvider(report_replies(replacer=replacing({})))
    steps = drafted(fake)
    kept, calls = steps.work.model_dump(), len(fake.requests)
    with pytest.raises(PassageNotFound, match=r"Couldn't find that passage in part 2\.") as error:
        steps.edit("p2", "A sentence that was never written", "Change it")
    assert isinstance(error.value, ValueError)
    assert len(fake.requests) == calls and steps.work.model_dump() == kept


def test_a_passage_edit_cannot_split_a_slide() -> None:
    row = "| Marker | CD3D [1] | CD19 [2] |"
    fake = FakeProvider(replacing({row: row + "\n\n---\n\n## A second slide\n\nCD3D [1]"}))
    steps = drafted(fake, "slides")
    outline = [item.model_copy(deep=True) for item in steps.work.outline]
    parts = snapshot(steps.work)

    with pytest.raises(ModelError, match="one complete slide at position 2"):
        steps.edit("p2", row, "Explain this comparison")

    assert steps.work.outline == outline and steps.work.parts == parts
    assert layout_of(steps.work.parts["p2"].text)[0] == "compare"
    assert len(fake.requests) == 1 and is_replacing(fake.requests[0])


@pytest.mark.parametrize("index", [0, 1], ids=["cover-accent", "comparison-layout"])
def test_legacy_slide_edits_keep_the_layout_and_other_parts(index) -> None:
    span, replacement = [
        ("Two markers tell the cells apart", "The two markers distinguish cell types"),
        ("Note: Start with the table [1].", "Note: Read down each column [1].\n### Then [2]"),
    ][index]
    steps = drafted(FakeProvider(output_replies(
        SLIDES_OUTLINE, SLIDES, replacer=replacing({span: replacement}),
    )), "slides")
    steps.set_accent("blue")
    identity = f"p{index + 1}"
    before = snapshot(steps.work)

    assert steps.edit(identity, span, "Clarify the explanation")

    part = steps.work.parts[identity]
    assert part.text == before[identity].text.replace(span, replacement)
    assert part.checked == section_hash(part.text)
    assert {key: value for key, value in steps.work.parts.items() if key != identity} == {
        key: value for key, value in before.items() if key != identity
    }
    assert len(split_document(steps.assemble()[0].content, "slides")[1]) == 3


def test_an_edit_that_changes_nothing_changes_nothing() -> None:
    answers = {"CD3D marks T cells [1]": "CD3D marks T cells [1]"}
    fake = FakeProvider(report_replies(replacer=replacing(answers)))
    steps = drafted(fake)
    kept, before = snapshot(steps.work), len(fake.requests)
    assert steps.edit("p1", "CD3D marks T cells [1]", "Leave it") is False
    assert len(fake.requests) - before == 1  # the writer was asked; nothing was checked
    assert steps.work.parts == kept


def test_an_edit_needs_a_part_that_is_written_and_something_to_do() -> None:
    steps = drafted(FakeProvider())
    saved = steps.work.parts.pop("p1")
    with pytest.raises(ValueError, match="Section 1 is not written yet"):
        steps.edit("p1", "CD3D", "Change it")
    steps.work.parts["p1"] = saved
    with pytest.raises(ValueError, match="Say what to change"):
        steps.edit("p1", "CD3D", "  ")
    with pytest.raises(ValueError, match="no part p9"):
        steps.edit("p9", "CD3D", "Change it")


def test_a_changed_heading_becomes_the_items_and_a_part_out_of_date_stays_so() -> None:
    old, new = "## Surface markers tell T and B cells apart", "## Markers tell T and B cells apart"
    answers = {old: new, "CD3D is a marker of T cells [1]": "CD3D marks T cells [1]"}
    fake = FakeProvider(report_replies(replacer=replacing(answers)))
    steps = drafted(fake)
    steps.edit("p1", old, "Shorten the heading")
    assert steps.work.outline[0].heading == "Markers tell T and B cells apart"
    assert steps.work.parts["p1"].text.startswith(f"{new}\n\n")
    # The part's inputs are current. The new heading still needs verification.
    part = steps.work.parts["p1"]
    assert part.inputs == digest_part_inputs(steps.brief, steps.work, steps.work.outline[0])
    assert states(steps) == ["not_checked", "ok", "ok"]
    steps.brief.audience = Line(text="first-year students", mark="you")
    steps.edit("p2", "CD3D is a marker of T cells [1]", "Say it for students")
    assert states(steps) == ["out_of_date"] * 3


def test_deleting_a_passage_takes_its_line_with_it() -> None:
    fake = FakeProvider(report_replies(replacer=replacing({"- second bullet [2]": ""})))
    steps = drafted(fake)
    heading = "## Surface markers tell T and B cells apart"
    steps.work.parts[
        "p1"
    ].text = f"{heading}\n\n- first bullet [1]\n- second bullet [2]\n- third [1]"
    assert steps.edit("p1", "- second bullet [2]", "Remove it") is True
    assert steps.work.parts["p1"].text == f"{heading}\n\n- first bullet [1]\n- third [1]"


def test_the_sources_are_numbered_in_reading_order_and_the_pool_numbers_are_told() -> None:
    steps = drafted(FakeProvider())
    work = steps.work
    kept = snapshot(work)
    # The last section cites only the pool's second source; first of all, it is source 1.
    steps.plan_move("p3", 0)
    built, version_map = steps.assemble()
    assert version_map == [2, 1] == list(built.pool_refs)
    assert [(c.ref, c.source_title) for c in built.citations] == [(1, "Receptors"), (2, "Markers")]
    first = split_document(built.content, "report")[1][0]
    assert first == "## CD19 is the B-cell marker\n\nCD19 is a marker of B cells [1]."
    # In the workspace, the parts still cite the pool's numbers, byte for byte.
    assert work.parts == kept
    assert ready(work, steps.brief) == []


@pytest.mark.parametrize("no_change", [False, True])
def test_writing_one_part_updates_only_that_part_and_its_stamp(no_change) -> None:
    changed = REPORT[0].replace("CD3D marks T cells", "CD3D identifies T cells")
    reply = "UNCHANGED" if no_change else changed
    fake = FakeProvider(report_replies(rewrite=lambda *_: reply))
    steps = drafted(fake)
    steps.understand()
    steps.work.outline_inputs = digest_outline_inputs(steps.work)
    steps.brief.audience = Line(text="Beginning students", mark="you")
    before = snapshot(steps.work)
    start = len(fake.requests)

    assert steps.write(["p1"], "Clarify the first sentence") == ([] if no_change else ["p1"])

    calls = fake.requests[start:]
    (writer,) = [request for request in calls if is_writing(request)]
    assert "Rewrite section 1" in writer["messages"][0]["content"]
    assert "Clarify the first sentence" in writer["messages"][0]["content"]
    assert "[section 2]\n" + REPORT[1] in writer["messages"][-1]["content"]
    if no_change:
        assert calls == [writer]
        assert steps.work.parts == before and states(steps) == ["out_of_date"] * 3
    else:
        written = steps.work.parts["p1"]
        assert written.text == changed and written.checked == section_hash(changed)
        assert written.issues == [] and states(steps) == ["ok", "out_of_date", "out_of_date"]
        assert all(steps.work.parts[key] == before[key] for key in ("p2", "p3"))
    assert approach_state(steps.work, steps.brief).state == "out_of_date"
    assert outline_state(steps.work).state == "ok" and ready(steps.work, steps.brief)


def test_writing_an_inserted_part_researches_its_goal_and_keeps_its_place() -> None:
    heading, goal = "Use both markers in the panel", "Explain the comparison"
    text = f"## {heading}\n\nCD3D marks T cells [1]. CD19 marks B cells [2]."
    replies = output_replies(OUTLINE, [REPORT[0], text, *REPORT[1:]],
                             rewrite=lambda *_: text.replace("marks", "identifies"))
    fake = FakeProvider(replies)
    steps = drafted(fake)
    events, queries = [], []
    steps.emit = events.append

    def search(query):
        queries.append(query)
        return LIBRARY

    steps.agents.tools["search_library"] = Tool("search_library", "the library", search, "library")
    before = snapshot(steps.work)
    item = steps.plan_insert(1, heading, goal)
    assert states(steps) == ["ok", "not_written", "ok", "ok"]
    assert steps.write([item.id], "For existing text only") == [item.id]
    (writer,) = [r for r in fake.requests if "Write section 2." in r["messages"][0]["content"]]
    system, prompt = (writer["messages"][at]["content"] for at in (0, -1))
    assert heading in system and goal in system and "For existing text only" not in system
    assert "[section 1]\n" + REPORT[0] in prompt
    assert len(queries) == 1 and queries[0].startswith(f"{heading} {goal}")
    assert [(e["index"], e["state"]) for e in events if e["type"] == "section"] == [
        (1, "writing"), (1, "checking"), (1, "done"),
    ]
    assert item.refs == [1, 2] and steps.work.parts[item.id].text == text
    assert steps.write([item.id], "Use a more precise verb") == [item.id]
    assert len(queries) == 1
    assert {key: value for key, value in steps.work.parts.items() if key != item.id} == before
    assert states(steps) == ["ok"] * 4


def test_rewriting_a_legacy_slide_fits_its_table_without_requesting_a_design() -> None:
    incomplete = fmt.with_marker(
        "## CD3D and CD19 set T and B cells apart\n\n- A table is needed.", "table",
    )
    fitted = fmt.with_marker(SLIDES[1].replace("Start with", "Read across"), "table")
    replies = output_replies(SLIDES_OUTLINE, SLIDES, rewrite=lambda *_: incomplete)

    def reply(request):
        system = request["messages"][0]["content"]
        if "Slide 2 as written:" in system:
            return fitted
        assert "world-class presentation designer" not in system
        return replies(request)

    fake = FakeProvider(reply)
    steps = drafted(fake, "slides")
    before = snapshot(steps.work)
    assert steps.write(["p2"], "Make the comparison clearer") == ["p2"]
    assert steps.work.parts["p2"].text == fmt.with_marker(fitted, "compare")
    assert steps.work.parts["p1"] == before["p1"] and steps.work.parts["p3"] == before["p3"]
    assert sum("Slide 2 as written:" in r["messages"][0]["content"] for r in fake.requests) == 1


@pytest.mark.parametrize("kind", ["report", "slides"])
def test_studio_keeps_fenced_writer_words_and_requires_the_part_heading(kind) -> None:
    if kind == "report":
        outline, parts, text = OUTLINE, REPORT, REPORT[0].replace("CD3D marks", "CD3D identifies")
    else:
        outline, parts = SLIDES_OUTLINE, SLIDES
        text = SLIDES[0].replace("Two markers", "Both markers")
    written = f"```markdown\n{text}\n```"
    fake = FakeProvider(output_replies(outline, parts, rewrite=lambda *_: written))
    steps = drafted(fake, kind)
    before = snapshot(steps.work)
    assert steps.write(["p1"], "Improve the opening") == ["p1"]
    result = steps.work.parts["p1"].text
    assert (layout_of(result)[1] if kind == "slides" else result) == text
    assert all(steps.work.parts[key] == before[key] for key in ("p2", "p3"))

    before = snapshot(steps.work)
    written = text.partition("\n")[2].strip()
    with pytest.raises(ModelError, match="(valid heading|complete section)"):
        steps.write(["p1"], "Improve the opening again")
    assert steps.work.parts == before


@pytest.mark.parametrize("kept", [True, False], ids=["selected-figure", "unknown-figure"])
def test_a_figure_written_into_a_slide_must_come_from_its_material(kept) -> None:
    asset = "ast_kept" if kept else "ast_invented"
    text = fmt.with_marker(
        "## The measured response changes over time\n\n"
        f"![Response curve](gunther-asset:{asset})\nThe response rises [3].\n"
        "Note: Point out the axes.",
        "figure",
    )
    fake = FakeProvider(output_replies(SLIDES_OUTLINE, SLIDES, rewrite=lambda *_: text))
    steps = drafted(fake, "slides")
    source = passage("Responses", "The response rises over time.")
    evidence = source.payload[0].model_copy(
        update={"figures": [{"assetId": "ast_kept", "caption": "Response curve"}]},
    )
    assert steps.pool.adopt(replace(source, payload=(evidence, None))).ref == 3
    before = snapshot(steps.work)
    if kept:
        assert steps.write(["p2"], "Show the response curve") == ["p2"]
        assert steps.work.parts["p2"].text == text
        assert steps.work.parts["p2"].checked == section_hash(text)
    else:
        with pytest.raises(ModelError, match="source images from outside the chosen material"):
            steps.write(["p2"], "Show the response curve")
        assert steps.work.parts == before


def test_completed_work_notes_and_final_assembly_findings_have_separate_lifetimes() -> None:
    steps = drafted(FakeProvider(ModelError("The reader is unavailable.")), "slides")
    work = steps.work
    work.spec.design_version = 1
    work.parts["p2"].text = fmt.with_marker(
        "## Two markers tell the cells apart\n\nThe figures are explained in the table.", "stat",
    )
    with pytest.raises(ModelError, match="reader is unavailable"):
        steps.review(["p2"])
    (review_note,) = work.skipped
    assert review_note["step"] == "review" and "reader is unavailable" in review_note["reason"]
    before = work.model_dump()
    first, _ = steps.assemble()
    second, _ = steps.assemble()
    assert first.skill["skipped"] == second.skill["skipped"]
    assert first.skill["skipped"].count(review_note) == 1
    assert any(note["step"] == "format" and note["label"] == "Slide 2"
               for note in first.skill["skipped"])
    assert work.model_dump() == before


def test_a_writer_failure_keeps_the_parts_it_finished() -> None:
    def writer(index, _prompt):
        if index == 2:
            raise ModelError("The next part could not be written.")
        return REPORT[index]

    steps = drafted(FakeProvider(output_replies(OUTLINE, writer)))
    steps.work.parts.clear()
    with pytest.raises(ModelError, match="next part could not be written"):
        steps.write(["p1", "p2", "p3"], "Write these sections")
    assert [part.text for part in steps.work.parts.values()] == REPORT[:2]
    assert states(steps) == ["ok", "ok", "not_written"]


def test_a_work_with_nothing_planned_cannot_be_assembled_or_reviewed() -> None:
    steps = steps_for(FakeProvider())
    for step in (steps.assemble, steps.review):
        with pytest.raises(ValueError, match="Nothing is planned yet"):
            step()


@pytest.mark.parametrize("operation", ["assemble", "review"])
def test_assemble_and_review_require_every_part_to_be_written(operation) -> None:
    fake = FakeProvider()
    steps = drafted(fake)
    del steps.work.parts["p2"]
    with pytest.raises(ValueError, match="Section 2 is not written yet"):
        getattr(steps, operation)()
    assert fake.requests == []


@pytest.mark.parametrize(
    ("scope", "suggestions", "expected"),
    [
        (["p3"], [2, 3], [3]),
        (["p1", "p3"], [1, 2, 3], [1, 3]),
        (None, [1, 2, 3], [1, 2, 3]),
    ],
    ids=["one-part", "two-parts", "whole"],
)
def test_review_only_rewrites_requested_parts_and_keeps_stale_stamps(
    scope, suggestions, expected,
) -> None:
    changes = {
        1: REPORT[0].replace("CD3D marks T cells", "CD3D identifies T cells"),
        2: "## CD3D is the T-cell marker\n\nT cells are marked by CD3D [1].",
        3: "## CD19 is the B-cell marker\n\nB cells are marked by CD19 [2].",
    }
    replies = report_replies(rewrite=lambda index, _prompt: changes[index + 1])

    def reply(request):
        if "You are a demanding senior partner" in request["messages"][0]["content"]:
            assert all(part in request["messages"][-1]["content"] for part in REPORT)
            return json.dumps({"changes": [
                {"part": index, "instruction": "Make the explanation shorter."}
                for index in suggestions
            ]})
        return replies(request)

    fake = FakeProvider(reply)
    steps = drafted(fake)
    steps.work.outline[2].goal = "Explain the scope of the B-cell result."
    before = snapshot(steps.work)
    assert states(steps) == ["ok", "ok", "out_of_date"]

    revised = steps.review(scope)

    assert revised == [f"p{index}" for index in expected]
    writes = [r["messages"][0]["content"] for r in fake.requests if is_writing(r)]
    assert all(f"Rewrite section {index} as" in prompt
               for index, prompt in zip(expected, writes, strict=True))
    assert all(part == steps.work.parts[identity] for identity, part in before.items()
               if identity not in revised)
    for identity in revised:
        part = steps.work.parts[identity]
        assert part.text == changes[int(identity[1:])] and part.checked == section_hash(part.text)
    assert steps.work.parts["p3"].inputs == before["p3"].inputs
    assert states(steps) == ["ok", "ok", "out_of_date"]


# Finding the passage -----------------------------------------------------------------------------

TEXT = (
    "## Markers\n\n"
    "CD3D marks T cells [1]. CD19 marks B cells [2]. Both sit at the cell surface [1][2].\n\n"
    "- CD3D is on every T cell [1]\n"
    "- CD19 is on every B cell [2]\n"
)


def cut(passage: str, text: str = TEXT) -> str | None:
    """What ``locate`` finds of the text for this passage."""

    found = locate(passage, text)
    return None if found is None else text[found[0] : found[1]]


def test_a_passage_is_found_exactly_first_of_all() -> None:
    start = TEXT.index("CD19 marks B cells [2].")
    assert locate("CD19 marks B cells [2].", TEXT) == (
        start,
        start + len("CD19 marks B cells [2]."),
    )
    # The first of several; nothing for nothing.
    assert locate("cell", TEXT) == (TEXT.index("cell"), TEXT.index("cell") + 4)
    assert locate("", TEXT) is None and locate("  \n", TEXT) is None


def test_a_passage_is_found_when_spacing_capitals_and_source_numbers_differ() -> None:
    assert cut("cd3d   MARKS\nT cells") == "CD3D marks T cells"
    # A number at its end is the person's number for the source: the work's numbers that stand
    # for it go with it. Without one, the match stops where the words do.
    assert cut("CD3D marks T cells [9]") == "CD3D marks T cells [1]"
    assert cut("Both sit at the cell surface [4][5]") == "Both sit at the cell surface [1][2]"
    assert cut("Both sit at the cell surface [4, 5].") == "Both sit at the cell surface [1][2]."
    assert cut("cd3d marks t cells") == "CD3D marks T cells"
    assert cut("CD3D marks T cells.") == "CD3D marks T cells [1]."
    # Lines of a list, with other numbers, with or without their bullets.
    assert cut("- CD3D is on every T cell [7]\n- CD19 is on every B cell [8]") == (
        "- CD3D is on every T cell [1]\n- CD19 is on every B cell [2]"
    )
    assert cut("CD19 is on every B cell [8]") == "CD19 is on every B cell [2]"


def test_a_models_own_marker_and_the_space_before_a_stop_are_ignored_too() -> None:
    text = "## A\n\nThe cell has a surface [?]. It is thin ."
    assert cut("The cell has a surface.", text) == "The cell has a surface [?]."
    assert cut("It is thin.", text) == "It is thin ."
    # A number between the words is not one of them.
    assert cut("CD3D marks T cells", "CD3D [1] marks T cells") == "CD3D [1] marks T cells"


def test_a_passage_is_found_by_its_likeness_to_whole_sentences() -> None:
    # A slipped letter or word, and a different number: still that sentence, whole.
    assert cut("CD19 marks B cels [9].") == "CD19 marks B cells [2]."
    assert cut("Both sit at the cell surfaces [1][2].") == "Both sit at the cell surface [1][2]."
    # Two list lines, one of them misquoted.
    assert cut("- CD3D is on each T cell [1]\n- CD19 is on every B cell [2]") == (
        "- CD3D is on every T cell [1]\n- CD19 is on every B cell [2]"
    )
    # The best window wins: here the two sentences together, not either alone.
    assert cut("CD3D marks T cells [1]. CD19 marks B cels [2].") == (
        "CD3D marks T cells [1]. CD19 marks B cells [2]."
    )


def test_a_passage_that_is_not_like_anything_in_the_text_is_not_found() -> None:
    assert locate("Peak heat fell by three degrees [1].", TEXT) is None
    # Too different: three words in ten are not the sentence's.
    assert locate("CD19 marks cells of the blood [2].", TEXT) is None
    # A quotation of a part of a sentence is not a sentence.
    assert locate("CD3D marks T cellz", "CD3D marks T cells and more words go on and on.") is None


def test_only_the_changed_paragraph_of_an_edited_part_goes_to_the_checker() -> None:
    first = "Untouched words that the Checker once doubted [1]."
    fake = FakeProvider(
        report_replies(replacer=replacing({"CD3D marks T cells [1]": "CD3D is on T cells [1]"}))
    )
    steps = drafted(fake)
    part = steps.work.parts["p1"]
    heading, rest = part.text.split("\n", 1)
    part.text = f"{heading}\n\n{first}\n\n{rest.strip()}"
    part.checked = section_hash(part.text)
    part.verdicts = verdicts_of(finding({
        "claim": first, "verdict": "unsupported", "note": "Doubted.",
    }))
    before = len(fake.requests)

    assert steps.edit("p1", "CD3D marks T cells [1]", "Say where it is") is True

    text = steps.work.parts["p1"].text
    assert text.startswith(f"{heading}\n\n{first}\n\n") and "CD3D is on T cells [1]" in text
    checked = [r for r in fake.requests[before:] if not is_replacing(r)]
    assert checked and all("Untouched words" not in str(r["messages"]) for r in checked)
    assert [i["claim"] for i in steps.work.parts["p1"].issues] == [first]


def test_a_shorter_heading_carries_no_source_numbers_and_needs_no_check() -> None:
    heading = "## Surface markers tell T and B cells apart"
    fake = FakeProvider(
        report_replies(replacer=replacing({heading: "## Markers tell cells apart [1]"}))
    )
    steps = drafted(fake)
    part = steps.work.parts["p1"]
    body = part.text.split("\n", 1)[1]
    issues = list(part.issues or [])
    before = len(fake.requests)

    assert steps.edit("p1", heading, "Shorten the heading") is True

    assert steps.work.parts["p1"].text == "## Markers tell cells apart\n" + body
    assert steps.work.outline[0].heading == "Markers tell cells apart"
    assert [r for r in fake.requests[before:] if not is_replacing(r)] == []
    assert steps.work.parts["p1"].issues == issues
