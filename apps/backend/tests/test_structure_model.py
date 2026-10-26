"""The model that corrects a document's structure: fake gateways only, no network."""

from types import SimpleNamespace

import pytest
from fake_models import FakeProvider, gateway, model

from gunther import document_model, structure_model, usage
from gunther.document_model import (
    DOCUMENT_TYPES,
    Block,
    Drop,
    Evidence,
    Figure,
    Part,
    Plan,
    Section,
    StructureStatus,
    Table,
    Violation,
)
from gunther.document_profiles import PROFILES, guides
from gunther.llm import Completion, ModelError, ModelGateway, Turn
from gunther.structure_model import (
    MAX_BLOCKS,
    MAX_CHARS,
    MAX_LISTED,
    SYSTEM_PROMPT,
    StructureError,
    StructureReviser,
    StructureTooLong,
    create_structure_reviser,
    preview,
)

CHOSEN = model(label="Flash")


def block(block_id: str, label: str = "text", text: str = "", **fields) -> Block:
    return Block(id=block_id, label=label, text=text, **fields)


def paper() -> Evidence:
    """A small paper: furniture, title, authors, a logo, abstract, one figure with text printed
    inside it, one table, one formula and a reference. b9 is printed inside the picture b8."""

    return Evidence(
        [
            block(
                "b0",
                "page_header",
                "Nature Communications (2021) 12:1234",
                page=1,
                box=(50_000, 20_000, 480_000, 40_000),
                font_size=8.0,
                clues=["edge:top", "repeats:top"],
            ),
            block("b1", "title", "A fast method for counting cells", page=1, font_size=20.0),
            block("b2", "text", "Ana Ruiz, Bo Chen", page=1),
            block("b3", "picture", page=1, clues=["no-caption"]),
            block("b4", "section_header", "Abstract", page=1),
            block("b5", "text", "Counting cells by hand is slow, so we automate it.", page=1),
            block("b6", "section_header", "Introduction", page=1),
            block("b7", "text", "Many methods exist.", page=1),
            block("b8", "picture", page=2),
            block("b9", "text", "cells per well", page=2, inside="b8"),
            block("b10", "caption", "Fig. 1 | Cell counts.", page=2, clues=["caption:figure:1"]),
            block("b11", "table", page=2, table={"num_rows": 5, "num_cols": 3}),
            block("b12", "caption", "Table 1 | Results.", page=2, clues=["caption:table:1"]),
            block("b13", "formula", "E = mc2", page=3),
            block("b14", "section_header", "References", page=3),
            block("b15", "reference", "Smith, J. A study. Nature 1, 1 (2000).", page=3),
        ],
        pages={1: {"width": 595, "height": 842}},
    )


GOOD = Plan(
    type="paper",
    title=["b1"],
    authors=["b2"],
    front=[Part(part="abstract", heading="b4", blocks=["b5"])],
    body=[Section(heading="b6", level=1, blocks=["b7", "F1", "T1", "b13"])],
    back=[Part(part="references", heading="b14", blocks=["b15"])],
    figures=[Figure(id="F1", number="1", images=["b8"], caption=["b10"])],
    tables=[Table(id="T1", number="1", table=["b11"], caption=["b12"])],
    drop=[Drop(blocks=["b0", "b3"], reason="page furniture and a logo")],
)
# The logo b3 is placed nowhere.
BAD = GOOD.model_copy(update={"drop": [Drop(blocks=["b0"], reason="page furniture")]})
# What the layout rules drafted, before the model corrected it.
DRAFT = GOOD.model_copy(update={"type": "unknown", "title": ["b0"]})
UNPLACED = Violation("error", "unplaced", "b3 is not placed anywhere.", ["b3"])


class FakeGateway:
    """Answers ``complete_json`` from a queue (plans or errors) and remembers every call."""

    def __init__(self, *answers: Plan | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[SimpleNamespace] = []

    def for_role(self, role: str):
        return (CHOSEN, "low") if role == "analysis" else None

    def complete_json(
        self,
        chosen,
        schema,
        *,
        system,
        prompt,
        images=(),
        history=(),
        schema_at="system",
        effort=None,
    ):
        self.calls.append(
            SimpleNamespace(
                model=chosen,
                schema=schema,
                system=system,
                prompt=prompt,
                history=list(history),
                effort=effort,
                usage=usage.current(),
            )
        )
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        text = answer.model_dump_json()
        # A fresh object each time, as parsing an answer gives.
        return schema.model_validate_json(text), Completion(text, chosen, reasoning="thought")


def reviser(fake: FakeGateway) -> StructureReviser:
    return StructureReviser(fake, CHOSEN, "low")


@pytest.fixture
def placed_b3(monkeypatch):
    """A check() stand-in: an error while b3 is placed nowhere. Lists the plans it saw."""

    seen: list[Plan] = []

    def fake(plan: Plan, evidence: Evidence) -> list[Violation]:
        seen.append(plan)
        return [] if '"b3"' in plan.model_dump_json() else [UNPLACED]

    monkeypatch.setattr(structure_model, "check", fake)
    return seen


# A plan that passes -------------------------------------------------------------


def test_a_plan_that_passes_is_returned_as_the_models_and_marked_checked(placed_b3):
    fake = FakeGateway(GOOD)

    plan, status = reviser(fake).revise(paper(), DRAFT)

    assert plan.model_dump(exclude={"by"}) == GOOD.model_dump(exclude={"by"})
    assert plan.by == CHOSEN.ref == "deepseek/deepseek-flash"
    assert status == StructureStatus("checked", by=CHOSEN.ref, violations=[])
    assert DRAFT.by == "rules" and DRAFT.title == ["b0"]
    [call] = fake.calls
    assert call.schema is Plan and call.model is CHOSEN and call.effort == "low"
    assert call.system == SYSTEM_PROMPT and call.history == []
    assert placed_b3 == [plan]


def test_warnings_do_not_stop_a_plan_and_stay_in_the_status(monkeypatch):
    warning = Violation("warning", "no-abstract", "The paper has no abstract.", [])
    monkeypatch.setattr(structure_model, "check", lambda plan, evidence: [warning])
    fake = FakeGateway(GOOD)

    _, status = reviser(fake).revise(paper(), DRAFT)

    assert status.state == "checked" and status.violations == [warning]
    assert len(fake.calls) == 1


# Problems go back once ------------------------------------------------------------


def test_errors_go_back_once_and_the_second_answer_is_used(placed_b3):
    fake = FakeGateway(BAD, GOOD)

    plan, status = reviser(fake).revise(paper(), DRAFT)

    first, second = fake.calls
    assert plan.by == CHOSEN.ref and plan.drop == GOOD.drop
    assert status == StructureStatus("checked", by=CHOSEN.ref, violations=[])
    # The model sees the whole conversation: its question, its answer, then the problems.
    assert second.history == [
        Turn("user", first.prompt),
        Turn("assistant", BAD.model_dump_json(), CHOSEN.ref, "thought"),
    ]
    assert "b3 is not placed anywhere." in second.prompt
    assert "whole corrected plan" in second.prompt
    assert second.system == first.system and second.effort == first.effort
    assert [seen.drop for seen in placed_b3] == [BAD.drop, GOOD.drop]


def test_the_warnings_of_the_second_answer_are_the_ones_kept(monkeypatch):
    warning = Violation("warning", "no-references", "The paper has no references.", [])
    answers = iter([[UNPLACED, Violation("warning", "no-title", "No title.")], [warning]])
    monkeypatch.setattr(structure_model, "check", lambda plan, evidence: next(answers))

    _, status = reviser(FakeGateway(BAD, GOOD)).revise(paper(), DRAFT)

    assert status.violations == [warning]


def test_only_the_errors_are_sent_back(monkeypatch):
    nothing_to_say = Violation("warning", "no-abstract", "The paper has no abstract.", [])
    answers = iter([[UNPLACED, nothing_to_say], []])
    monkeypatch.setattr(structure_model, "check", lambda plan, evidence: next(answers))
    fake = FakeGateway(BAD, GOOD)

    reviser(fake).revise(paper(), DRAFT)

    assert "b3 is not placed anywhere." in fake.calls[1].prompt
    assert "no abstract" not in fake.calls[1].prompt


def test_many_problems_are_listed_up_to_a_limit_and_the_rest_counted(monkeypatch):
    many = [
        Violation("error", "unplaced", f"b{number} is not placed anywhere.", [f"b{number}"])
        for number in range(MAX_LISTED + 5)
    ]
    answers = iter([many, []])
    monkeypatch.setattr(structure_model, "check", lambda plan, evidence: next(answers))
    fake = FakeGateway(BAD, GOOD)

    reviser(fake).revise(paper(), DRAFT)

    prompt = fake.calls[1].prompt
    assert prompt.count("is not placed anywhere.") == MAX_LISTED
    assert "and 5 more problems" in prompt


def test_a_plan_that_still_has_errors_after_the_second_try_is_an_error(placed_b3):
    fake = FakeGateway(BAD, BAD)

    with pytest.raises(StructureError) as raised:
        reviser(fake).revise(paper(), DRAFT)

    assert len(fake.calls) == 2
    assert not isinstance(raised.value, StructureTooLong)
    assert raised.value.violations == [UNPLACED]
    assert str(raised.value) == (
        "DeepSeek · Flash could not correct the structure; it still breaks the rules after "
        "a second try: b3 is not placed anywhere."
    )


def test_the_error_counts_the_other_problems(monkeypatch):
    other = Violation("error", "duplicate", "b5 is placed twice.", ["b5"])
    monkeypatch.setattr(structure_model, "check", lambda plan, evidence: [UNPLACED, other])

    with pytest.raises(StructureError, match=r"b3 is not placed anywhere \(and 1 more\)\.$"):
        reviser(FakeGateway(BAD, BAD)).revise(paper(), DRAFT)


@pytest.mark.parametrize("answered_before", [0, 1])
def test_a_model_error_is_a_structure_error_with_its_own_words(placed_b3, answered_before):
    refusal = ModelError("DeepSeek did not answer in time.")
    fake = FakeGateway(*([BAD] * answered_before), refusal)

    with pytest.raises(StructureError) as raised:
        reviser(fake).revise(paper(), DRAFT)

    assert str(raised.value) == "DeepSeek did not answer in time."
    assert raised.value.violations == []
    assert len(fake.calls) == answered_before + 1


def test_a_document_with_nothing_to_place_is_not_sent_to_the_model():
    fake = FakeGateway()
    only_text_inside_a_picture = Evidence([block("b0", inside="b1")])

    with pytest.raises(StructureError, match="no text"):
        reviser(fake).revise(only_text_inside_a_picture, DRAFT)

    assert fake.calls == []


# Too long ------------------------------------------------------------------------


def test_a_document_with_too_many_blocks_is_too_long_and_nothing_is_sent():
    evidence = Evidence([block(f"b{n}", text="x") for n in range(MAX_BLOCKS + 1)])
    fake = FakeGateway()

    with pytest.raises(StructureTooLong, match="3,001 parts") as raised:
        reviser(fake).revise(evidence, DRAFT)

    assert isinstance(raised.value, StructureError) and fake.calls == []
    assert MAX_BLOCKS == 3_000


def test_a_document_with_too_much_text_is_too_long():
    # 2,000 blocks are under the block limit; their lines are over the character limit.
    evidence = Evidence(
        [
            block(f"b{n}", text="word " * 40, page=3, font_size=9.5, box=(0, 0, 1, 1))
            for n in range(2_000)
        ]
    )
    assert len(evidence.blocks) < MAX_BLOCKS and MAX_CHARS == 240_000

    with pytest.raises(StructureTooLong, match="too much text"):
        preview(evidence)


def test_a_document_at_the_block_limit_is_previewed_and_inside_text_is_not_counted():
    evidence = Evidence(
        [block(f"b{n}", text="x") for n in range(MAX_BLOCKS)]
        + [block(f"b{MAX_BLOCKS + n}", text="in a picture", inside="b0") for n in range(500)]
    )

    assert len(preview(evidence).splitlines()) == MAX_BLOCKS


def test_the_character_limit_is_for_more_than_it_not_for_exactly_it(monkeypatch):
    size = len(preview(paper()))

    monkeypatch.setattr(structure_model, "MAX_CHARS", size)
    assert len(preview(paper())) == size
    monkeypatch.setattr(structure_model, "MAX_CHARS", size - 1)
    with pytest.raises(StructureTooLong):
        preview(paper())


# What the model reads ----------------------------------------------------------------


def test_each_block_is_one_line_with_the_fields_that_are_known():
    evidence = Evidence(
        [
            block(
                "b21",
                "text",
                "Many computational approaches",
                page=2,
                box=(50_000, 100_000, 480_000, 350_000),
                font_size=9.5,
                clues=["first-page", "small-type"],
            ),
            block("b30", "text", "Hello"),
            block("b31", "text", '  A  line\nbreak with "quotes"  ', font_size=10.0),
            block("b32", "text"),
            block(
                "b8",
                "picture",
                page=2,
                box=(100_000, 400_000, 900_000, 700_000),
                clues=["no-caption"],
            ),
            block("b11", "table", page=2, table={"num_rows": 5, "num_cols": 3}),
            block("b12", "table", table={"rows": [["a", "b"], ["c"]]}),
            block("b13", "table"),
            block("b14", "formula", "E = mc2", page=3),
            block("b15", "formula"),
            block("b16", "text", "At the edge", box=(0, 0, 1_000_000, 1_000_000)),
        ]
    )

    assert preview(evidence).splitlines() == [
        'b21 | p2 | text | 9.5pt | x.05-.48 y.10-.35 | "Many computational approaches"'
        " | first-page, small-type",
        'b30 | text | "Hello"',
        "b31 | text | 10pt | \"A line break with 'quotes'\"",
        'b32 | text | ""',
        "b8 | p2 | picture | x.10-.90 y.40-.70 | [picture] | no-caption",
        "b11 | p2 | table | [table 5×3]",
        "b12 | table | [table 2×2]",
        "b13 | table | [table]",
        'b14 | p3 | formula | [formula] "E = mc2"',
        "b15 | formula | [formula]",
        'b16 | text | x.00-1.00 y.00-1.00 | "At the edge"',
    ]


def test_long_text_is_cut_at_a_word_with_its_full_length_in_brackets():
    text = "Many computational approaches " * 40
    evidence = Evidence([block("b21", text=text, clues=["first-page"])])

    [line] = preview(evidence).splitlines()

    kept = ("Many computational approaches " * 3).rstrip()
    assert line == f'b21 | text | "{kept}…" (1199) | first-page'


def test_text_without_spaces_is_cut_at_ninety_characters():
    [line] = preview(Evidence([block("b1", text="x" * 500)])).splitlines()

    assert line == f'b1 | text | "{"x" * 90}…" (500)'


def test_text_printed_inside_a_picture_is_not_in_the_preview():
    lines = preview(paper()).splitlines()

    assert [line.split(" | ")[0] for line in lines] == [
        f"b{number}" for number in range(16) if number != 9
    ]
    assert "cells per well" not in "\n".join(lines)


# What the model is asked -------------------------------------------------------------


def test_the_question_has_the_guides_the_blocks_and_the_draft(placed_b3):
    fake = FakeGateway(GOOD)

    reviser(fake).revise(paper(), DRAFT)

    [call] = fake.calls
    assert guides() in call.prompt
    assert preview(paper()) in call.prompt
    marker = "Draft plan from the layout rules (it may be wrong):\n"
    draft = call.prompt.split(marker)[1].split("\n")[0]
    assert draft.startswith('{"type":"unknown","title":["b0"]')
    assert '"by"' not in draft
    assert call.prompt.endswith("Reply with the corrected plan.")
    # The same for every document comes first, then the blocks, then the draft.
    assert call.prompt.index("paper:") < call.prompt.index("b0 | p1") < call.prompt.index(marker)


def test_the_instructions_cover_every_part_of_the_plan_and_forbid_writing_text():
    for field in Plan.model_fields:
        if field != "by":
            assert field in SYSTEM_PROMPT
    assert "You never write, rewrite, shorten or correct text" in SYSTEM_PROMPT
    assert "bA..bB" in SYSTEM_PROMPT and "reading order" in SYSTEM_PROMPT


def test_every_document_type_has_a_profile_and_its_guide_is_in_the_guides_in_order():
    assert set(PROFILES) == set(DOCUMENT_TYPES)
    for name, profile in PROFILES.items():
        assert profile.type == name
        assert all([profile.what, profile.front, profile.body, profile.back, profile.junk])
        assert profile.guide().startswith(f"{name}: ")
    assert guides() == "\n\n".join(PROFILES[name].guide() for name in DOCUMENT_TYPES)


def test_the_guides_hold_what_each_type_expects_and_drops():
    paper_guide = PROFILES["paper"].guide()
    for word in ("affiliations", "e-mail", "received/accepted", "competing interests"):
        assert word in paper_guide
    for word in ("author contributions", "funding", "acknowledgements", "publisher notes"):
        assert word in paper_guide
    for word in ("licence", "reprint", "peer-review", "no caption", "availability"):
        assert word in paper_guide
    assert "Footnotes" in PROFILES["book"].guide() and "index" in PROFILES["book"].junk
    assert "Boxes" in PROFILES["report"].guide()
    assert "without a caption" in PROFILES["slides"].guide()
    assert "navigation" in PROFILES["article"].junk
    assert "3.2.1" in PROFILES["manual"].body


# Who is charged -------------------------------------------------------------------


def test_every_call_is_metered_as_the_analysis_structure_job_of_the_source(placed_b3):
    fake = FakeGateway(BAD, GOOD)

    reviser(fake).revise(paper(), DRAFT, source_id="src_9")

    assert [call.usage for call in fake.calls] == [
        {"job": "analysis", "detail": "structure", "ref": "src_9"}
    ] * 2
    # Nothing leaks out of the job.
    assert usage.current() == {"job": "other", "detail": None, "ref": None}


def test_without_a_source_id_the_callers_reference_is_kept(placed_b3):
    fake = FakeGateway(GOOD)

    with usage.job(ref="outer"):
        reviser(fake).revise(paper(), DRAFT)

    assert fake.calls[0].usage == {"job": "analysis", "detail": "structure", "ref": "outer"}


# Through the real gateway, with a fake provider -------------------------------------------


def test_the_follow_up_reaches_the_model_as_a_conversation_and_is_metered(placed_b3):
    fake = FakeProvider(BAD.model_dump_json(), GOOD.model_dump_json())
    records: list[dict] = []
    chosen = model("deepseek-flash")
    models = ModelGateway(
        [chosen],
        {"analysis": (chosen.ref, "low")},
        client_factory=fake.factory,
        recorder=records.append,
    )

    plan, status = create_structure_reviser(models).revise(paper(), DRAFT, source_id="src_9")

    assert status.state == "checked" and plan.by == "deepseek/deepseek-flash"
    first, second = (request["messages"] for request in fake.requests)
    assert [message["role"] for message in first] == ["system", "user"]
    assert [message["role"] for message in second] == ["system", "user", "assistant", "user"]
    assert first[0]["content"].startswith(SYSTEM_PROMPT)
    assert "JSON schema" in first[0]["content"]
    assert second[1] == first[1]
    assert second[2]["content"] == BAD.model_dump_json()
    assert "b3 is not placed anywhere." in second[3]["content"]
    assert [(r["job"], r["detail"], r["ref"]) for r in records] == [
        ("analysis", "structure", "src_9")
    ] * 2


def test_an_answer_in_the_wrong_shape_is_a_structure_error(placed_b3):
    models = gateway(FakeProvider("this is not a plan"), model("deepseek-flash"))

    with pytest.raises(StructureError, match="shape"):
        create_structure_reviser(models).revise(paper(), DRAFT)


def test_a_reviser_exists_only_when_the_analysis_job_has_a_model():
    fake = FakeProvider("{}")
    ready = create_structure_reviser(gateway(fake, model("deepseek-flash")))

    assert ready is not None
    assert ready.model.ref == "deepseek/deepseek-flash" and ready.effort == "low"
    assert create_structure_reviser(None) is None
    assert create_structure_reviser(gateway(fake, model("deepseek-flash"), roles={})) is None


# With the real check -----------------------------------------------------------------


@pytest.fixture
def real_check():
    """The real check(), or a skip while Package A has not written it."""

    try:
        document_model.check(Plan(), Evidence([]))
    except NotImplementedError:
        pytest.skip("check() is not written yet (Package A)")


def test_the_real_check_passes_a_good_plan_and_drives_the_correction(real_check):
    assert [v for v in document_model.check(GOOD, paper()) if v.severity == "error"] == []
    problems = [v for v in document_model.check(BAD, paper()) if v.severity == "error"]
    assert problems, "a plan that leaves b3 out should break a rule"
    fake = FakeGateway(BAD, GOOD)

    plan, status = reviser(fake).revise(paper(), DRAFT)

    assert status.state == "checked" and plan.drop == GOOD.drop
    for problem in problems:
        assert problem.message in fake.calls[1].prompt


def test_the_real_check_names_what_stays_wrong_in_the_error(real_check):
    with pytest.raises(StructureError) as raised:
        reviser(FakeGateway(BAD, BAD)).revise(paper(), DRAFT)

    [problem] = raised.value.violations
    assert problem.code == "unplaced" and problem.blocks == ["b3"]
    assert problem.message.rstrip(".") in str(raised.value)
