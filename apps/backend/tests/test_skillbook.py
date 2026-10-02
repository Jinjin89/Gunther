"""Skills as files: the ones Gunther ships load, and a broken one says what is wrong."""

import shutil
from pathlib import Path

import pytest

from gunther.skill_checks import Context, Page, run_checks, units
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
        assert [step.does for step in skill.steps][:3] == ["understand", "structure", "write"]
        for step in skill.steps:
            assert step.instructions.startswith(f"## {step.id}")
    assert skill_for("slides").step("reader_test").preamble is False


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
        + 'frame = true\n[budgets.standard]\nsearches = 8\nsub_questions = [3, 5]\n'
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
