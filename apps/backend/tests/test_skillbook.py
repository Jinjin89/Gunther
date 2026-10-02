"""Skills as files: the ones Gunther ships load, and a broken one says what is wrong."""

import shutil
from pathlib import Path

import pytest

from gunther.skill_checks import Context, Page, run_checks, units
from gunther.skillbook import SKILLS_DIR, SkillError, builtin_skills, load_skill, skill_for


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
