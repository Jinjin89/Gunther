"""Skills as files: the ones Gunther ships load, and a broken one says what is wrong."""

from pathlib import Path

import pytest

from gunther.skill_checks import diagram_problem, units
from gunther.skillbook import SKILLS_DIR, SkillError, ask_skills, load_ask_skills


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


def test_the_compare_skill_loads_with_its_method_and_checklist() -> None:
    assert (SKILLS_DIR / "compare" / "skill.toml").is_file()
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


def test_ask_instruction_sections_ignore_headings_inside_fenced_examples(tmp_path: Path) -> None:
    text = GOOD_TEXT.replace("Write it.", "Write it.\n```md\n## example\nExample heading.\n```")
    ask_folder(tmp_path, GOOD, text)
    skill = load_ask_skills(tmp_path)["mine"]
    assert skill.method("write") == "Preamble.\nWrite it.\n```md\n## example\nExample heading.\n```"
    assert skill.checklist == ("One.", "Two.")


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
