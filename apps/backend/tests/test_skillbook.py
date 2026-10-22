"""Skills as files: the ones Gunther ships load, and a broken one says what is wrong."""

import shutil
from pathlib import Path

import pytest

from gunther.agent import Evidence
from gunther.schemas import ConversationCitationOut
from gunther.skill_checks import Context, Page, diagram_problem, layout_shape, run_checks, units
from gunther.skillbook import (
    SKILLS_DIR,
    SkillError,
    ask_skills,
    builtin_skills,
    load_ask_skills,
    load_skill,
    skill_for,
)


def test_the_report_and_slides_skills_load_with_every_step_described() -> None:
    skills = builtin_skills()
    assert {skill.kind for skill in skills.values()} == {"report", "slides"}
    for skill in skills.values():
        expected = (
            ["understand", "structure", "outline_review", "design", "write", "compose"]
            if skill.kind == "slides"
            else ["understand", "structure", "write"]
        )
        assert [step.does for step in skill.steps][: len(expected)] == expected
        for step in skill.steps:
            assert step.instructions.startswith(f"## {step.id}")
    assert skill_for("slides").step("reader_test").preamble is False


def test_the_slides_skill_knows_the_figure_layout_and_checks_how_each_slide_is_shaped() -> None:
    slides = skill_for("slides")
    assert slides.version == 11
    assert slides.steps[-1].does == "visual_review"
    assert {"figure", "diagram"} <= set(slides.layouts)
    assert slides.step("design") is not None
    assert slides.step("compose") is not None
    assert "scene.md" in slides.step("compose").references
    assert "layout_shape" in slides.step("write").checks
    assert "layout_shape" in slides.step("edit_review").checks
    for step_id in ("design", "write", "edit_review", "revise"):
        instructions = slides.instructions(slides.step(step_id), layout="flow")
        assert "# Visual design method" in instructions
        assert "peers at equal visual" in instructions
    assert {"repeated_heading", "slide_links"} <= set(slides.step("structure").checks)
    assert {"repeated_heading", "slide_links"} <= set(slides.step("edit_review").checks)
    assert slides.check_params["layout_run"] == {"max_run": 2, "max_text": 2}
    # A figure slide reads its own section, and the report skill has no such check.
    text = slides.instructions(slides.step("write"), layout="figure")
    assert "## figure" in text and "gunther-asset:" in text and "## quote" not in text
    assert not any("layout_shape" in step.checks for step in skill_for("report").steps)


def test_slide_links_and_repeated_headings_are_recorded_as_review_findings() -> None:
    context = Context("slides")
    pages = [
        Page(0, "Title", "title", "# Title\n\nSubhead"),
        Page(1, "Same claim", "text", "## Same claim\n- evidence", ""),
        Page(2, "Same claim", "text", "## Same claim\n- more evidence", "contrast"),
    ]
    found = run_checks(["slide_links", "repeated_heading"], pages, {}, context)
    assert [(item.check, item.index) for item in found] == [
        ("slide_link", 1),
        ("repeated_heading", 2),
    ]
    assert "No planned relationship" in found[0].message
    assert "check whether the slides make distinct points" in found[1].message


def test_diagram_layout_requires_a_cited_small_dag_and_a_readable_placement() -> None:
    branch = (
        "## Material status controls the next step\n"
        "| Start | Relationship | End |\n| --- | --- | --- |\n"
        "| Collect materials | complete [1] | Check materials |\n"
        "| Check materials | complete [1] | Analyze |\n"
        "| Check materials | missing [1] | Request more |"
    )
    assert diagram_problem(branch) is None
    cycle = branch + "\n| Analyze | loops back [1] | Collect materials |"
    assert "cycle" in (diagram_problem(cycle) or "")
    uncited = branch.replace("missing [1]", "missing")
    assert "numbered source citation" in (diagram_problem(uncited) or "")
    long = branch.replace("Collect materials", "Review the available evidence with all limitations")
    assert "too long" in (diagram_problem(long) or "")
    page = Page(1, "Claim", "diagram", branch)
    assert not layout_shape([page], {}, Context("slides"))


def test_a_step_reads_the_preamble_its_section_and_its_references() -> None:
    slides = skill_for("slides")
    write = slides.step("write")
    text = slides.instructions(write, layout="compare")
    assert text.startswith("# Slides")
    assert "## write" in text and "## structure" not in text
    # Only this page's layout is read, and an example's "## " inside a fence is not a section.
    assert "## compare" in text and "## flow" not in text
    assert "| 前期投入 |" in text
    reader = slides.instructions(slides.step("reader_test"))
    assert not reader.startswith("# Slides") and reader.startswith("## reader_test")


def test_a_report_style_fixes_a_structure_the_references_describe() -> None:
    report = skill_for("report")
    assert report.styles["decision_brief"] == "which"
    assert "## which" in report.reference("structures.md#which")


def broken(tmp_path: Path, file: str, old: str, new: str) -> Path:
    root = tmp_path / "report"
    shutil.copytree(SKILLS_DIR / "report", root)
    path = root / file
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("file", "old", "new", "says"),
    [
        ("skill.toml", 'does = "edit_review"', 'does = "polish"', "“does” must be one of"),
        (
            "SKILL.md",
            "## reader_test：读者测试",
            "## reader：读者测试",
            "no section “## reader_test”",
        ),
        ("skill.toml", 'checks = ["section_count"]', 'checks = ["spelling"]', "unknown checks"),
        ("skill.toml", 'references = ["writing.md"]', 'references = ["style.md"]', "no references"),
        ("skill.toml", '"what", "why"', '"what", "who"', "no reference describes the structure"),
    ],
)
def test_a_broken_skill_says_what_is_wrong(
    tmp_path: Path, file: str, old: str, new: str, says: str
) -> None:
    with pytest.raises(SkillError, match=says):
        load_skill(broken(tmp_path, file, old, new))


def test_outputs_skills_still_load_beside_ask_ones() -> None:
    # An Ask skill's folder sits beside them; Outputs neither loads it nor trips on it.
    assert (SKILLS_DIR / "compare" / "skill.toml").exists()
    assert set(builtin_skills()) == {"report", "slides"}
    with pytest.raises(SkillError, match="kind must be report or slides"):
        load_skill(SKILLS_DIR / "compare")


def test_the_compare_skill_loads_with_its_method_and_checklist() -> None:
    compare = ask_skills()["compare"]
    assert (compare.title, compare.version, compare.frame) == ("Compare sources", 1, False)
    assert not compare.budgets and set(compare.sections) == {"plan", "write"}
    assert compare.preamble.startswith("# Compare sources")
    assert not compare.sections["plan"].startswith("##")
    assert len(compare.checklist) == 3
    assert compare.checklist[0] == "Each position compared is stated with its own source."


def ask_folder(tmp_path: Path, toml: str, text: str, name: str = "mine") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True)
    (root / "skill.toml").write_text(toml, encoding="utf-8")
    (root / "SKILL.md").write_text(text, encoding="utf-8")
    return root


GOOD = """name = "mine"
kind = "ask"
version = 1
command = "mine"
title = "Mine"
description = "A method."
"""
GOOD_TEXT = "Preamble.\n\n## write\nWrite it.\n\n## check\n- One.\n1. Two.\n"


def test_an_ask_skill_reads_budgets_and_a_checklist(tmp_path: Path) -> None:
    ask_folder(
        tmp_path,
        GOOD
        + "frame = true\n[budgets.standard]\nsearches = 8\nsub_questions = [3, 5]\n"
        + "[budgets.deep]\nsearches = 14\nreads = 6\ncalls = 30\n",
        "Preamble.\n\n## frame\nSplit it.\n\n" + GOOD_TEXT.split("\n\n", 1)[1],
    )
    skill = load_ask_skills(tmp_path)["mine"]
    assert skill.frame and skill.checklist == ("One.", "Two.")
    assert list(skill.budgets) == ["standard", "deep"]
    assert skill.budget().searches == 8 and skill.budget().sub_questions == (3, 5)
    assert skill.budget("deep").reads == 6 and skill.budget("deep").calls == 30
    assert skill.method("write") == "Preamble.\nWrite it."


@pytest.mark.parametrize(
    ("toml", "text", "says"),
    [
        (GOOD.replace('"mine"\ntitle', '"Mine!"\ntitle'), GOOD_TEXT, "mine/skill.toml: command"),
        (GOOD, GOOD_TEXT.replace("## write", "## poem"), "mine/SKILL.md: unknown section “## po"),
        (GOOD, GOOD_TEXT + "\n## write\nTwice.", "mine/SKILL.md: “## write” appears twice"),
        (GOOD + "frame = true\n", GOOD_TEXT, "no section “## frame”"),
        (GOOD + "[budgets.deep]\nspeed = 3\n", GOOD_TEXT, "budget “deep”: unknown settings"),
        (GOOD + "[budgets.deep]\nsearches = -1\n", GOOD_TEXT, "searches must be a whole"),
        (GOOD, GOOD_TEXT + "".join(f"- more {n}\n" for n in range(9)), "more than 10 items"),
        (GOOD.replace('title = "Mine"\n', ""), GOOD_TEXT, "“title” is missing"),
    ],
)
def test_a_broken_ask_skill_names_the_file(tmp_path: Path, toml: str, text: str, says: str) -> None:
    ask_folder(tmp_path, toml, text)
    with pytest.raises(SkillError, match=says):
        load_ask_skills(tmp_path)


def test_two_ask_skills_may_not_share_a_command(tmp_path: Path) -> None:
    ask_folder(tmp_path, GOOD, GOOD_TEXT, "one")
    ask_folder(tmp_path, GOOD.replace('name = "mine"', 'name = "other"'), GOOD_TEXT, "two")
    with pytest.raises(SkillError, match="two/skill.toml: the command /mine is already used"):
        load_ask_skills(tmp_path)


def test_a_skill_zh_beside_skill_md_is_ignored(tmp_path: Path) -> None:
    root = ask_folder(tmp_path, GOOD, GOOD_TEXT)
    (root / "SKILL.zh.md").write_text("## poem\nnot read", encoding="utf-8")
    assert load_ask_skills(tmp_path)["mine"].checklist == ("One.", "Two.")


def test_the_backend_bundle_carries_the_skills() -> None:
    script = (SKILLS_DIR.parents[3] / "scripts" / "build-backend-sidecar.mjs").read_text()
    assert '"gunther", "skills")}:gunther/skills' in script


# Checks ---------------------------------------------------------------------------------------


def test_length_counts_characters_and_words_alike() -> None:
    assert units("人工成本最高 [3]") == 6
    assert units("Costs are **highest** [2][?]") == pytest.approx(3 * 1.7)


def test_a_deck_is_checked_for_its_shape_and_its_density() -> None:
    pages = [
        Page(0, "Markers", "text", "## Markers"),
        Page(1, "T cells", "text", "## T cells\n- a\n- b\n- c\n- d\nNote: say more"),
        Page(2, "B cells", "text", "## B cells\n- one"),
        Page(3, "NK cells", "text", "## NK cells\n- one"),
        Page(4, "Costs", "bar_chart", "## Costs\n| Item | % |\n| --- | --- |\n| Staff | 62 |"),
    ]
    found = run_checks(
        ["first_and_last", "layout_run", "density", "chart_numbers_cited", "heading_length"],
        pages,
        {
            "density": {"talk_items": 3},
            "layout_run": {"max_run": 2},
            "heading_length": {"min_units": 6},
        },
        Context("slides", "talk", {}),
    )
    said = {(item.check, item.index) for item in found}
    assert ("first_and_last", 0) in said and ("first_and_last", 4) in said
    assert ("layout_run", 2) in said  # the third text slide in a row
    assert ("density", 1) in said  # four items where three fit
    assert ("chart_numbers_cited", 4) in said
    assert ("heading_length", 1) in said and ("heading_length", 0) not in said


# The form each layout asks for ------------------------------------------------------------------

TABLE = "| Item | Share (%) |\n| --- | --- |\n"
SHAPES = [
    # (layout, the slide as the check sees it, whether it takes the form)
    ("title", "# Deck\n\nOne line saying it", True),
    # A title without its message line is still the title.
    ("title", "# Deck", True),
    ("title", "## Deck\n\nOne line", False),
    ("big_number", "## Labour\n**62%**\nOf the three-year cost [3]", True),
    ("big_number", "## Labour\n**62%**", False),
    ("big_number", "## Labour\n**62%** of the cost [3]\nMore", False),
    ("big_number", "## Labour\n**" + "word " * 20 + "**\nMore", False),
    ("compare", "## C\n| | A | B |\n| --- | --- | --- |\n| x | 1 [1] | 2 [2] |\nA wins.", True),
    ("two_column", "## C\n| Usual | Better |\n| :--- | ---: |\n| a [1] | b [1] |", True),
    ("compare", "## C\n- a [1]\n- b [1]", False),
    ("compare", "## C\n| a | b |\n| x | y |", False),
    ("compare", "## C\n| a | b |\n| --- | --- |", False),
    ("bar_chart", "## C\n" + TABLE + "| Staff | 62 [3] |\n| Gear | 18 [3] |\nStaff leads.", True),
    (
        "line_chart",
        "## C\n| Year | Users |\n| --- | --- |\n| 2019 | 1,200 [2] |\n| 2020 | 3.5 万 [2] |",
        True,
    ),
    ("bar_chart", "## C\n" + TABLE + "| Staff | 62 [3] |", False),
    ("bar_chart", "## C\n" + TABLE + "".join(f"| r{n} | {n} [1] |\n" for n in range(7)), False),
    ("bar_chart", "## C\n" + TABLE + "| Staff | high [3] |\n| Gear | 18 [3] |", False),
    ("bar_chart", "## C\n" + TABLE + "| Staff | 12–15 [3] |\n| Gear | 18 [3] |", False),
    ("bar_chart", "## C\n- Staff 62 [3]\n- Gear 18 [3]", False),
    ("flow", "## F\n1. Clean [1]\n2. Label [2]", True),
    ("flow", "## F\n1. Clean [1]", False),
    ("flow", "## F\n- Clean [1]\n- Label [2]", False),
    ("timeline", "## T\n- **2016** First [1]\n- **2020** Second [2]", True),
    ("timeline", "## T\n- 2016 First [1]\n- **2020** Second [2]", False),
    ("timeline", "## T\n" + "".join(f"- **{2000 + n}** x [1]\n" for n in range(8)), False),
    ("cards", "## K\n- **A**: one [1]\n- **B**: two [2]", True),
    ("cards", "## K\n- **A**: one [1]", False),
    ("quote", "## Q\n> The words.\n— Someone, 2023 [6]", True),
    ("quote", "## Q\n> The words.", False),
    ("quote", "## Q\n— Someone, 2023 [6]\n> The words.", False),
    ("figure", "## G\n![Figure 2](gunther-asset:ast_3f9a1c)\nLook at week four [4]", True),
    ("figure", "## G\n![Figure 2](gunther-asset:ast_3f9a1c)", False),
    ("figure", "## G\n![Figure 2](gunther-asset:ast_3f9a1c) [1].\nLook at it", False),
    ("figure", "## G\n![Figure 2](https://example.org/a.png)\nLook at week four [4]", False),
    ("figure", "## G\n![One](gunther-asset:ast_1)\nLook ![Two](gunther-asset:ast_2)", False),
    ("text", "## T\nAnything at all", True),
    ("takeaways", "## T", True),
    ("", "## T\nA slide with no layout", True),
]


@pytest.mark.parametrize(("layout", "text", "fits"), SHAPES)
def test_a_slide_fits_its_layout_when_its_body_takes_the_form_the_layout_asks_for(
    layout: str, text: str, fits: bool
) -> None:
    page = Page(0, text.splitlines()[0], layout, text)
    found = layout_shape([page], {}, Context("slides", "talk", {}))
    assert [(item.check, item.index) for item in found] == ([] if fits else [("layout_shape", 0)])


def test_a_figure_must_be_one_the_material_kept() -> None:
    citation = ConversationCitationOut(
        id="cit_1",
        source_id="src_1",
        source_title="Paper",
        quote="Figure 2 shows it.",
        locator="p. 3",
        status="provisional",
        confidence=0.0,
        figures=[{"assetId": "ast_kept", "caption": "Figure 2"}],
    )
    evidence = {1: Evidence("library", "Paper", "Figure 2 shows it.", payload=(citation, None))}
    context = Context("slides", "talk", {}, evidence)

    def verdicts(asset: str) -> list[str]:
        text = f"## G\n![Figure 2](gunther-asset:{asset})\nLook at week four [1]"
        return [item.message for item in layout_shape([Page(0, "G", "figure", text)], {}, context)]

    assert verdicts("ast_kept") == []
    [message] = verdicts("ast_invented")
    assert "one of the figures kept with the material" in message


def test_text_is_the_last_resort_and_a_figure_slide_has_no_items_to_count() -> None:
    def pages(*layouts: str) -> list[Page]:
        return [
            Page(n, f"Slide {n}", layout, f"## Slide {n}\n- a") for n, layout in enumerate(layouts)
        ]

    context = Context("slides", "talk", {})
    deck = pages("title", "text", "flow", "text", "cards", "text", "takeaways")
    found = run_checks(["layout_run"], deck, {"layout_run": {"max_run": 2, "max_text": 2}}, context)
    assert [(item.index, "3 slides use the text layout" in item.message) for item in found] == [
        (None, True)
    ]
    together = pages("title", "text", "text", "flow", "takeaways")
    found = run_checks(["layout_run"], together, {"layout_run": {"max_text": 2}}, context)
    assert [(item.index, "Two text slides in a row" in item.message) for item in found] == [
        (2, True)
    ]
    # With no limit for text, the check is what it was.
    assert not run_checks(["layout_run"], together, {"layout_run": {"max_run": 2}}, context)
    long_caption = "![A long caption for the figure here](gunther-asset:ast_1)"
    figure = Page(1, "G", "figure", f"## G\n{long_caption}\n" + "Look. " * 9)
    dense = {"density": {"talk_items": 3, "talk_item_units": 24}}
    assert not run_checks(["density"], [figure], dense, context)
