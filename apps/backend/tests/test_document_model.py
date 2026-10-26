"""The document plan: what check() rejects and what assemble() builds, from hand-made evidence."""

import copy
import io
import random

import pytest

from gunther.document_model import (
    IMAGE_LABELS,
    Block,
    Drop,
    Evidence,
    Figure,
    Note,
    Part,
    Plan,
    Section,
    StructureStatus,
    Table,
    Violation,
    assemble,
    check,
)
from gunther.documents import reference_data


def sup(start: int, stop: int) -> dict:
    return {"start": start, "end": stop, "script": "super"}


def raised(text: str, *marks: str) -> list[dict]:
    """Superscript spans over the first occurrence of each mark."""
    return [sup(text.index(mark), text.index(mark) + len(mark)) for mark in marks]


def small(*labels: str) -> Evidence:
    """Blocks b0, b1, … with these labels; text blocks read 'Text of bN.'."""
    return Evidence(
        [
            Block(f"b{n}", label, "" if label in IMAGE_LABELS else f"Text of b{n}.")
            for n, label in enumerate(labels)
        ]
    )


def paper() -> tuple[Plan, Evidence]:
    """A small paper whose plan places, drops or prints inside a picture every block."""
    authors = "Alice Smith1,2, Bob Jones3 and Carol Lee1,*"
    grid = {
        "table_cells": [
            {"text": "Name", "start_row_offset_idx": 0, "end_row_offset_idx": 1,
             "start_col_offset_idx": 0, "end_col_offset_idx": 1},
            {"text": "Value", "start_row_offset_idx": 0, "end_row_offset_idx": 1,
             "start_col_offset_idx": 1, "end_col_offset_idx": 2},
        ],
        "num_rows": 1,
        "num_cols": 2,
    }
    blocks = [
        Block("b0", "title", "Deep Examples", page=1, box=(100_000, 50_000, 900_000, 100_000),
              font_size=20.0, extra={"documentRef": "#/texts/0"}),
        Block("b1", "text", authors, page=1, box=(100_000, 120_000, 900_000, 160_000),
              inline=raised(authors, "1,2", "3", "1,*")),
        Block("b2", "text", "1 Department of Examples", page=1),
        Block("b3", "text", "We study examples and report what they show.", page=1),
        Block("b4", "section_header", "Introduction", page=2),
        Block("b5", "text", "Water is H2O and we", page=2,
              inline=[{"start": 10, "end": 11, "script": "sub"}]),
        Block("b6", "text", "describe CO2 here.", page=2,
              inline=[{"start": 11, "end": 12, "script": "sub"}]),
        Block("b7", "picture", "", page=2, box=(100_000, 300_000, 700_000, 600_000),
              image_key="#/pictures/0", extra={"documentRef": "#/pictures/0"},
              regions=[{"page": 2, "bboxPpm": [100_000, 300_000, 600_000, 300_000]}]),
        Block("b8", "text", "A", page=2, box=(50_000, 250_000, 120_000, 290_000)),
        Block("b9", "caption", "Figure 1. Overview of the method.", page=2,
              box=(100_000, 610_000, 700_000, 650_000)),
        Block("b10", "text", "axis", page=2, inside="b7"),
        Block("b11", "section_header", "Methods", page=3),
        Block("b12", "text", "We measured things twice.", page=3),
        Block("b13", "table", "", page=3, table=grid),
        Block("b14", "caption", "Table 1. Results.", page=3),
        Block("b15", "footnote", "a. Measured twice.", page=3),
        Block("b16", "section_header", "References", page=4),
        Block("b17", "reference",
              "[1] Smith, A. First work. 2020. [2] Jones, B. Second work. 2021.", page=4),
        Block("b18", "reference", "[3] Lee, C. Third work. 2022.", page=4),
        Block("b19", "footnote", "* Corresponding author", page=1),
        Block("b20", "page_footer", "Page 1", page=1),
    ]
    plan = Plan(
        type="paper",
        title=["b0"],
        authors=["b1"],
        front=[Part(part="abstract", blocks=["b3"])],
        body=[
            Section(heading="b4", level=1, blocks=["b5", "F1", "b6"]),
            Section(heading="b11", level=1, blocks=["b12", "T1"]),
        ],
        back=[Part(part="references", heading="b16", blocks=["b17..b18"])],
        figures=[Figure(id="F1", number="1", images=["b7"], caption=["b9"], labels=["b8"])],
        tables=[Table(id="T1", number="1", table=["b13"], caption=["b14"], footnotes=["b15"])],
        notes=[Note(id="N1", marker="*", blocks=["b19"])],
        joins=[["b5", "b6"]],
        drop=[Drop(blocks=["b2", "b20"], reason="affiliations and page furniture")],
    )
    pages = {n: {"width": 600, "height": 800} for n in range(1, 5)}
    return plan, Evidence(blocks, pages=pages, body_font_size=9.5)


def errors(violations: list[Violation]) -> list[Violation]:
    return [v for v in violations if v.severity == "error"]


def of(violations: list[Violation], code: str) -> list[Violation]:
    return [v for v in violations if v.code == code]


def codes(violations: list[Violation]) -> list[str]:
    return [v.code for v in violations]


def built(plan: Plan, evidence: Evidence):
    return assemble(plan, evidence, StructureStatus("checked"))


# Check: errors ------------------------------------------------------------------


def test_a_complete_paper_plan_has_no_violations():
    plan, evidence = paper()
    assert check(plan, evidence) == []


def test_bad_ranges_are_named_and_the_rest_is_still_checked():
    plan, evidence = paper()
    plan.body[1].blocks = ["b12", "b14..b12", "b12..b999", "b12...b13", "T1"]
    found = of(check(plan, evidence), "bad-range")
    assert [v.blocks for v in found] == [["b14..b12"], ["b12..b999"], ["b12...b13"]]
    assert "body section 2" in found[0].message and "b4..b9" in found[0].message
    # T1 was still read: it is placed once, so it is not reported.
    assert not of(check(plan, evidence), "float-not-placed")


def test_ids_that_are_not_blocks_are_unknown():
    plan, evidence = paper()
    plan.body[0].blocks += ["b999", "F9", "N1", "T9"]
    plan.drop[0].blocks.append("F1")
    plan.body[1].heading = "T1"
    found = of(check(plan, evidence), "unknown-id")
    assert {tuple(v.blocks) for v in found} == {
        ("b999",), ("F9",), ("N1",), ("T9",), ("F1",), ("T1",),
    }
    by_id = {v.blocks[0]: v.message for v in found}
    assert "not a block" in by_id["b999"]
    assert "not defined" in by_id["F9"] and "figures" in by_id["F9"]
    assert "not defined" in by_id["T9"] and "tables" in by_id["T9"]
    assert "note" in by_id["N1"]
    assert "figure" in by_id["F1"] and "drop list" in by_id["F1"]
    assert "table" in by_id["T1"] and "heading" in by_id["T1"]


def test_a_block_placed_twice_is_named_with_both_places():
    plan, evidence = paper()
    plan.body[0].blocks.append("b3")  # also the abstract
    plan.drop[0].blocks.append("b0")  # also the title
    found = of(check(plan, evidence), "duplicate")
    assert {tuple(v.blocks) for v in found} == {("b0",), ("b3",)}
    by_id = {v.blocks[0]: v.message for v in found}
    assert "the title" in by_id["b0"] and "the drop list" in by_id["b0"]
    assert "front part 1 (abstract)" in by_id["b3"]
    assert "body section 1 (heading b4)" in by_id["b3"]


def test_blocks_placed_in_the_same_two_places_are_reported_together():
    plan, evidence = paper()
    plan.back[0].blocks = ["b17..b18", "b17", "b18"]
    found = of(check(plan, evidence), "duplicate")
    assert [v.blocks for v in found] == [["b17", "b18"]]
    assert found[0].message.startswith("b17 and b18 are placed more than once")


def test_the_heading_and_a_range_over_it_count_as_two_placements():
    plan, evidence = paper()
    plan.body[0].blocks = ["b4..b6", "F1"]
    found = of(check(plan, evidence), "duplicate")
    assert [v.blocks for v in found] == [["b4"]]


def test_blocks_placed_nowhere_are_reported_as_ranges_where_they_run_on():
    plan, evidence = paper()
    plan.drop = []
    plan.body[1].blocks = ["T1"]
    found = of(check(plan, evidence), "unplaced")
    assert len(found) == 1
    assert found[0].blocks == ["b2", "b12", "b20"]
    assert found[0].message.startswith("b2, b12 and b20 are not placed anywhere")

    evidence = small("text", "picture", "text", "text", "text")
    evidence.blocks[2].inside = "b1"
    found = check(Plan(body=[Section(blocks=["b4"])]), evidence)
    # b2 is printed inside b1, so b0, b1 and b3 read as one run.
    assert of(found, "unplaced")[0].message.startswith("b0..b3 are not placed anywhere")
    assert of(found, "unplaced")[0].blocks == ["b0", "b1", "b3"]


@pytest.mark.parametrize(
    "plan",
    [
        Plan(title=["b1"]),
        Plan(subtitle=["b1"]),
        Plan(authors=["b1"]),
        Plan(body=[Section(heading="b1")]),
        Plan(front=[Part(part="abstract", heading="b1")]),
        Plan(back=[Part(part="appendix", heading="b1")]),
    ],
)
def test_titles_authors_and_headings_must_be_text(plan):
    found = of(check(plan, small("text", "picture")), "heading-not-text")
    assert [v.blocks for v in found] == [["b1"]]
    assert "picture" in found[0].message


def test_figures_need_an_image_and_text_captions():
    evidence = small("picture", "text", "table", "formula", "picture")
    plan = Plan(
        body=[Section(blocks=["F1", "F2", "F3", "F4"])],
        figures=[
            Figure(id="F1"),
            Figure(id="F2", images=["b1"]),
            Figure(id="F3", images=["b0"], caption=["b2"], labels=["b3"]),
            Figure(id="F4", images=["b4"]),
        ],
    )
    found = errors(check(plan, evidence))
    assert [(v.code, v.blocks) for v in found] == [
        ("figure-without-image", ["F1"]),
        ("image-not-picture", ["b1"]),
        ("caption-not-text", ["b2"]),
        ("caption-not-text", ["b3"]),
    ]
    assert "Figure F1 has no image" in found[0].message
    assert "figure F2" in found[1].message and "text block" in found[1].message
    assert "caption of figure F3" in found[2].message
    assert "label of figure F3" in found[3].message


def test_tables_need_a_table_or_picture_and_text_captions():
    evidence = small("table", "picture", "text", "table", "text")
    plan = Plan(
        body=[Section(blocks=["T1", "T2", "T3", "T4"])],
        tables=[
            Table(id="T1"),
            Table(id="T2", table=["b2"]),
            Table(id="T3", table=["b0"]),
            Table(id="T4", table=["b1"], caption=["b4"], footnotes=["b3"]),
        ],
    )
    found = errors(check(plan, evidence))
    assert [(v.code, v.blocks) for v in found] == [
        ("table-without-content", ["T1"]),
        ("table-without-content", ["b2"]),
        ("caption-not-text", ["b3"]),
    ]
    assert "footnote of table T4" in found[2].message


def test_figures_and_tables_are_placed_once():
    plan, evidence = paper()
    plan.body[0].blocks.remove("F1")
    plan.body[1].blocks.append("T1")
    found = errors(check(plan, evidence))
    assert [(v.code, v.blocks) for v in found] == [
        ("float-not-placed", ["F1"]),
        ("float-placed-twice", ["T1"]),
    ]
    assert "Figure F1 is defined but not placed" in found[0].message
    assert "twice in body section 2" in found[1].message


def test_a_figure_id_cannot_belong_to_two_figures():
    evidence = small("picture", "picture")
    plan = Plan(
        body=[Section(blocks=["F1"])],
        figures=[Figure(id="F1", images=["b0"]), Figure(id="F1", images=["b1"])],
    )
    found = of(check(plan, evidence), "duplicate")
    assert [v.blocks for v in found] == [["F1"]]
    assert "F1" in found[0].message


@pytest.mark.parametrize(
    ("sections", "joins", "expected"),
    [
        ([["b0", "b1"]], [["b0", "b1"]], None),
        ([["b0", "F1", "b1"]], [["b0", "b1"]], None),
        ([["b0", "b4", "b1"]], [["b0", "b1"]], None),
        ([["b0", "b3..b5", "b1"]], [["b0", "b1"]], "lie between"),
        ([["b0", "b1", "b2"]], [["b0", "b2"]], "b1 lies between"),
        ([["b0", "b1"]], [["b1", "b0"]], "does not come after"),
        ([["b0", "b1"]], [["b0", "b0"]], "does not come after"),
        ([["b0"], ["b1"]], [["b0", "b1"]], "different sections"),
        ([["b0", "b3"]], [["b0", "b3"]], "b3 is a picture block"),
        ([["b0"]], [["b0", "b1"]], "b1 is not in the blocks"),
        ([["b0", "b1"]], [["b0", "b1", "b2"]], "exactly two"),
    ],
)
def test_joins_need_text_blocks_side_by_side(sections, joins, expected):
    # b0-b2 text, b3 picture, b4 text printed inside b3, b5 text.
    evidence = small("text", "text", "text", "picture", "text", "text")
    evidence.blocks[4].inside = "b3"
    plan = Plan(
        body=[Section(blocks=blocks) for blocks in sections],
        figures=[Figure(id="F1", images=["b3"])],
        joins=joins,
    )
    found = of(check(plan, evidence), "join-not-adjacent")
    if expected is None:
        assert found == []
    else:
        assert len(found) == 1 and expected in found[0].message


def test_a_join_of_a_missing_block_is_an_unknown_id():
    plan, evidence = paper()
    plan.joins = [["b5", "b999"]]
    found = of(check(plan, evidence), "unknown-id")
    assert [v.blocks for v in found] == [["b999"]]
    assert not of(check(plan, evidence), "join-not-adjacent")


def test_text_printed_inside_a_picture_is_ignored_wherever_it_appears():
    evidence = small("text", "picture", "text", "text")
    evidence.blocks[2].inside = "b1"
    plan = Plan(
        title=["b0"],
        # The range covers b2, and b2 is also named and dropped: none of that counts.
        body=[Section(blocks=["b1..b3", "b2"])],
        drop=[Drop(blocks=["b2"], reason="inside")],
    )
    assert check(plan, evidence) == []
    # Nothing needs to place it either.
    assert check(Plan(title=["b0"], body=[Section(blocks=["b1", "b3"])]), evidence) == []


def test_errors_come_before_warnings():
    plan, evidence = paper()
    plan.title = []
    plan.drop[0].blocks.append("b1")
    found = check(plan, evidence)
    kinds = [v.severity for v in found]
    assert "error" in kinds and "warning" in kinds
    assert kinds == sorted(kinds, key=lambda severity: severity != "error")


# Check: warnings ----------------------------------------------------------------


def test_every_type_is_warned_about_a_missing_title():
    plan, evidence = paper()
    plan.title = []
    plan.drop[0].blocks.append("b0")
    assert codes(check(plan, evidence)) == ["no-title"]
    plan.type = "book"
    assert codes(check(plan, evidence)) == ["no-title"]


def test_only_a_paper_is_expected_to_have_an_abstract_and_references():
    plan, evidence = paper()
    plan.front, plan.back = [], []
    plan.drop[0].blocks += ["b3", "b16..b18"]
    assert codes(check(plan, evidence)) == ["no-abstract", "no-references"]
    plan.type = "report"
    assert check(plan, evidence) == []


def test_a_part_without_blocks_does_not_count():
    plan, evidence = paper()
    plan.front[0].blocks = []
    plan.back[0].blocks = []
    plan.drop[0].blocks += ["b3", "b17", "b18"]
    plan.back[0].heading = "b16"
    assert codes(check(plan, evidence)) == ["no-abstract", "no-references"]


def figure_plan(*numbers: str | None, captions: bool = True) -> tuple[Plan, Evidence]:
    """A paper with one figure per number: a picture b(3+2n) and its caption b(4+2n)."""
    blocks = [Block("b0", "title", "T"), Block("b1", "text", "Abstract."),
              Block("b2", "reference", "[1] A. B. 2020.")]
    for n, _ in enumerate(numbers):
        blocks.append(Block(f"b{3 + 2 * n}", "picture"))
        blocks.append(Block(f"b{4 + 2 * n}", "caption", f"Figure. Caption {n}."))
    plan = Plan(
        type="paper",
        title=["b0"],
        front=[Part(part="abstract", blocks=["b1"])],
        back=[Part(part="references", blocks=["b2"])],
        body=[Section(blocks=[f"F{n + 1}" for n in range(len(numbers))])],
        figures=[
            Figure(
                id=f"F{n + 1}",
                number=number,
                images=[f"b{3 + 2 * n}"],
                caption=[f"b{4 + 2 * n}"] if captions else [],
            )
            for n, number in enumerate(numbers)
        ],
        drop=[] if captions else [Drop(blocks=[f"b{4 + 2 * n}" for n in range(len(numbers))],
                                       reason="x")],
    )
    return plan, Evidence(blocks)


def test_figure_numbers_that_cover_one_to_the_largest_raise_no_warning():
    assert check(*figure_plan("1", "2", "3")) == []
    assert check(*figure_plan("2", "1")) == []
    assert check(*figure_plan("1", None)) == []


def test_figure_numbers_with_gaps_name_what_is_missing():
    found = check(*figure_plan("1", "3", "6"))
    assert codes(found) == ["figure-numbers-gap"]
    assert "go up to 6" in found[0].message and "2, 4 and 5 are missing" in found[0].message
    found = check(*figure_plan("2"))
    assert "1 is missing" in found[0].message


def test_figure_numbers_with_letters_are_left_out_of_the_gap_check():
    assert check(*figure_plan("1", "S2", "2")) == []


def test_two_figures_with_one_number_are_named():
    found = check(*figure_plan("1", "2", " 2"))
    assert codes(found) == ["duplicate-figure-number"]
    assert found[0].blocks == ["F2", "F3"]


def test_a_figure_without_a_caption_is_warned_about():
    found = check(*figure_plan("1", "2", captions=False))
    assert codes(found) == ["figure-without-caption"] * 2
    assert [v.blocks for v in found] == [["F1"], ["F2"]]
    plan, evidence = figure_plan("1", captions=False)
    plan.type = "article"
    assert check(plan, evidence) == []


# Assemble: refusing -------------------------------------------------------------


def test_a_plan_with_errors_is_never_assembled():
    plan, evidence = paper()
    plan.drop = []
    with pytest.raises(ValueError, match="b2 and b20 are not placed anywhere"):
        built(plan, evidence)


def test_a_plan_with_only_warnings_is_assembled():
    plan, evidence = paper()
    plan.title = []
    plan.drop[0].blocks.append("b0")
    assert codes(check(plan, evidence)) == ["no-title"]
    assert built(plan, evidence).elements[0].kind == "metadata"


# Assemble: a whole paper --------------------------------------------------------


def test_elements_follow_the_reading_order_with_kinds_roles_and_parents():
    plan, evidence = paper()
    document = built(plan, evidence)
    elements = document.elements
    assert [(e.kind, e.payload.get("role"), e.text) for e in elements] == [
        ("heading", "title", "Deep Examples"),
        ("metadata", "authors", "Alice Smith, Bob Jones and Carol Lee"),
        ("paragraph", "abstract", "We study examples and report what they show."),
        ("heading", None, "Introduction"),
        ("paragraph", None, "Water is H2O and we describe CO2 here."),
        ("figure", None, "Figure 1. Overview of the method."),
        ("heading", None, "Methods"),
        ("paragraph", None, "We measured things twice."),
        ("table", None, "Table 1. Results.\n\nName | Value"),
        ("note", "note", "* Corresponding author"),
        ("heading", "references", "References"),
        ("reference", None, "[1] Smith, A. First work. 2020."),
        ("reference", None, "[2] Jones, B. Second work. 2021."),
        ("reference", None, "[3] Lee, C. Third work. 2022."),
    ]
    assert [e.payload["part"] for e in elements] == (
        ["front"] * 3 + ["body"] * 6 + ["notes"] + ["back"] * 4
    )
    # The title is closed by the first section; the note belongs to the whole document.
    parents = [None, 0, 0, None, 3, 3, None, 6, 6, None, None, 10, 10, 10]
    assert [e.parent for e in elements] == parents
    assert elements[9].kind == "note" and elements[9].headings == []
    assert elements[4].headings == ["Introduction"]
    assert elements[1].headings == ["Deep Examples"]
    assert elements[0].headings == ["Deep Examples"]
    assert elements[10].headings == ["References"]
    assert [e.payload["level"] for e in elements if e.kind == "heading"] == [1, 1, 1, 1]
    for index, element in enumerate(elements):
        assert element.parent is None or element.parent < index
    document.validate()


def test_noembed_marks_headings_metadata_and_references():
    plan, evidence = paper()
    elements = built(plan, evidence).elements
    quiet = {"heading", "metadata", "reference"}
    assert all(e.payload.get("noEmbed") is True for e in elements if e.kind in quiet)
    assert not any(e.payload.get("noEmbed") for e in elements if e.kind not in quiet)
    assert all(e.payload.get("reference") for e in elements if e.kind == "reference")


def test_provenance_and_positions_come_from_the_blocks():
    plan, evidence = paper()
    elements = built(plan, evidence).elements
    title = elements[0]
    assert title.payload["blockIds"] == ["b0"]
    assert title.payload["documentRef"] == "#/texts/0"
    assert title.payload["fontSize"] == 20.0
    assert title.anchor == {
        "documentRef": "#/texts/0",
        "page": 1,
        "bboxPpm": [100_000, 50_000, 800_000, 50_000],
    }
    assert title.locator == "Page 1"
    assert elements[4].locator == "Page 2"
    assert elements[3].anchor == {"page": 2}


def test_texts_are_the_blocks_texts_word_for_word():
    text = "  Odd   spacing kept, and a trailing space. "
    evidence = Evidence([Block("b0", "text", text)])
    document = built(Plan(body=[Section(blocks=["b0"])]), evidence)
    assert document.elements[0].text == text


def test_dropped_blocks_and_text_inside_pictures_are_not_stored():
    plan, evidence = paper()
    stored = [e.text for e in built(plan, evidence).elements]
    for gone in ("1 Department of Examples", "Page 1", "axis"):
        assert not any(gone in text for text in stored)
    figure = next(e for e in built(plan, evidence).elements if e.kind == "figure")
    assert figure.payload["figureText"] == "A\naxis"  # kept on the figure, for search


def test_the_stored_document_chunks_and_resolves_references():
    plan, evidence = paper()
    document = built(plan, evidence)
    chunks = document.chunks()
    assert [c.kind for c in chunks].count("reference") == 3
    assert all(c.payload["noEmbed"] for c in chunks if c.kind == "reference")
    entries, _ = reference_data(document.elements)
    assert [entry["label"] for entry in entries] == ["1", "2", "3"]


# Assemble: pieces ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "marks", "expected"),
    [
        ("Alice Smith1,2, Bob Jones3", ["1,2", "3"], "Alice Smith, Bob Jones"),
        ("Alice Smith1,*, Bob Jones†", ["1,*"], "Alice Smith, Bob Jones"),
        ("Alice Smith*, Bob Jones†", [], "Alice Smith, Bob Jones"),
        ("Alice Smith†‡ and Bob Jones§", ["†‡"], "Alice Smith and Bob Jones"),
        ("A. Smith1 , B. Jones2 ,", ["1", "2"], "A. Smith, B. Jones"),
        ("张伟1,2，李娜3", ["1,2", "3"], "张伟，李娜"),
        ("Alice Smith", [], "Alice Smith"),
    ],
)
def test_authors_lose_their_affiliation_markers(text, marks, expected):
    evidence = Evidence([Block("b0", "text", text, inline=raised(text, *marks))])
    element = built(Plan(authors=["b0"]), evidence).elements[0]
    assert (element.kind, element.text) == ("metadata", expected)
    assert element.payload["role"] == "authors" and element.payload["noEmbed"]
    assert element.payload["sourceText"] == text
    assert "inlineSpans" not in element.payload or expected == text


def test_each_author_block_is_one_element_and_the_tidy_line_goes_to_the_metadata():
    evidence = Evidence([Block("b0", "text", "Ann Lee1"), Block("b1", "text", "Bo Wu2")])
    evidence.blocks[0].inline = raised("Ann Lee1", "1")
    evidence.blocks[1].inline = raised("Bo Wu2", "2")
    document = built(Plan(authors=["b0", "b1"]), evidence)
    assert [e.text for e in document.elements] == ["Ann Lee", "Bo Wu"]
    values = [c["value"] for c in document.manifest["metadata"]["authors"]]
    assert values == ["Ann Lee", "Bo Wu"]


def test_title_and_subtitle_blocks_are_joined_with_their_spans_moved():
    title = [
        Block("b0", "title", "Water in H2O"),
        Block("b1", "title", "and CO2", inline=[{"start": 6, "end": 7, "script": "sub"}]),
        Block("b2", "title", "A subtitle"),
    ]
    title[0].inline = [{"start": 10, "end": 11, "script": "sub"}]
    document = built(Plan(title=["b0..b1"], subtitle=["b2"]), Evidence(title))
    first, second = document.elements
    assert (first.text, first.payload["level"], first.payload["role"]) == (
        "Water in H2O and CO2", 1, "title",
    )
    assert first.payload["blockIds"] == ["b0", "b1"]
    assert first.payload["inlineSpans"] == [
        {"start": 10, "end": 11, "script": "sub"},
        {"start": 19, "end": 20, "script": "sub"},
    ]
    assert (second.text, second.payload["level"], second.payload["role"]) == (
        "A subtitle", 2, "subtitle",
    )
    assert second.parent == 0


def test_a_join_makes_one_paragraph_over_a_figure_in_between():
    plan, evidence = paper()
    paragraph = built(plan, evidence).elements[4]
    first, second = evidence.get("b5"), evidence.get("b6")
    assert paragraph.text == first.text + " " + second.text
    assert paragraph.payload["blockIds"] == ["b5", "b6"]
    assert paragraph.payload["inlineSpans"] == [
        {"start": 10, "end": 11, "script": "sub"},
        {"start": len(first.text) + 1 + 11, "end": len(first.text) + 1 + 12, "script": "sub"},
    ]


def test_joined_blocks_keep_the_regions_of_both_and_chains_become_one_element():
    region = {"page": 1, "bboxPpm": [0, 0, 10, 10]}
    other = {"page": 2, "bboxPpm": [5, 5, 10, 10]}
    evidence = Evidence(
        [
            Block("b0", "text", "One", regions=[region], extra={"sourceText": "One!"}),
            Block("b1", "text", "two", regions=[other]),
            Block("b2", "text", "three"),
        ]
    )
    plan = Plan(body=[Section(blocks=["b0..b2"])], joins=[["b0", "b1"], ["b1", "b2"]])
    (element,) = built(plan, evidence).elements
    assert element.text == "One two three"
    assert element.payload["blockIds"] == ["b0", "b1", "b2"]
    # Each region says which part of the joined text it holds.
    assert element.anchor["spans"] == [
        {**region, "textStart": 0, "textEnd": 3}, {**other, "textStart": 4, "textEnd": 7},
    ]
    assert "sourceText" not in element.payload


def test_a_join_can_merge_two_parts_of_one_reference():
    evidence = Evidence(
        [Block("b0", "reference", "[1] Smith, A. A long"), Block("b1", "reference", "title. 2020.")]
    )
    plan = Plan(back=[Part(part="references", blocks=["b0", "b1"])], joins=[["b0", "b1"]])
    (element,) = built(plan, evidence).elements
    assert (element.kind, element.text) == ("reference", "[1] Smith, A. A long title. 2020.")


def test_a_figure_is_its_caption_with_the_union_box_of_its_images_and_labels():
    plan, evidence = paper()
    figure = built(plan, evidence).elements[5]
    assert figure.text == "Figure 1. Overview of the method."
    assert figure.anchor["page"] == 2
    # The picture and the label printed beside it; the caption is not part of the crop.
    assert figure.anchor["bboxPpm"] == [50_000, 250_000, 650_000, 350_000]
    assert figure.anchor["cropSource"] == "plan-figure"
    assert figure.anchor["documentRef"] == "#/pictures/0"
    assert figure.anchor["spans"] == [{
        "page": 2, "bboxPpm": [100_000, 300_000, 600_000, 300_000], "documentRef": "#/pictures/0",
    }]
    assert figure.payload["number"] == "1"
    assert figure.payload["caption"] == "Figure 1. Overview of the method."
    assert figure.payload["imageKey"] == "#/pictures/0"
    assert figure.payload["imageBlocks"] == ["b7"]
    assert figure.payload["captionBlocks"] == ["b9"]
    assert figure.payload["blockIds"] == ["b7", "b9", "b8"]
    assert figure.payload["figureText"] == "A\naxis"


def test_a_label_on_another_page_is_left_out_of_the_crop():
    plan, evidence = paper()
    evidence.get("b8").page = 3
    figure = built(plan, evidence).elements[5]
    assert figure.anchor["bboxPpm"] == [100_000, 300_000, 600_000, 300_000]
    assert figure.payload["figureText"] == "A\naxis"


def test_several_images_of_one_figure_share_one_crop():
    evidence = Evidence(
        [
            Block("b0", "picture", page=1, box=(0, 100, 400, 300), image_key="#/pictures/1"),
            Block("b1", "picture", page=1, box=(500, 120, 900, 350), image_key="#/pictures/2"),
            Block("b2", "picture", page=2, box=(0, 0, 999, 999), image_key="#/pictures/3"),
            Block("b3", "caption", "Figure 2. Two panels."),
        ]
    )
    plan = Plan(
        body=[Section(blocks=["F1"])],
        figures=[Figure(id="F1", images=["b0", "b1", "b2"], caption=["b3"])],
    )
    figure = built(plan, evidence).elements[0]
    assert figure.anchor["bboxPpm"] == [0, 100, 900, 250]
    assert figure.payload["imageKey"] == "#/pictures/1"
    assert figure.payload["imageBlocks"] == ["b0", "b1", "b2"]


def test_a_figure_whose_picture_has_no_position_is_not_cropped():
    evidence = Evidence([Block("b0", "picture", image_key="#/pictures/1")])
    plan = Plan(body=[Section(blocks=["F1"])], figures=[Figure(id="F1", images=["b0"])])
    figure = built(plan, evidence).elements[0]
    assert "cropSource" not in figure.anchor and "bboxPpm" not in figure.anchor
    assert figure.text == "" and figure.payload["caption"] == ""


def test_caption_parts_join_with_a_newline_after_a_sentence_and_a_space_otherwise():
    evidence = Evidence(
        [
            Block("b0", "picture"),
            Block("b1", "caption", "Figure 1. First sentence."),
            Block("b2", "caption", "Second sentence is split", inline=[sup(0, 6)]),
            Block("b3", "caption", "across a column."),
        ]
    )
    plan = Plan(
        body=[Section(blocks=["F1"])],
        figures=[Figure(id="F1", images=["b0"], caption=["b1..b3"])],
    )
    figure = built(plan, evidence).elements[0]
    assert figure.text == "Figure 1. First sentence.\nSecond sentence is split across a column."
    assert figure.payload["inlineSpans"] == [
        {"start": 26, "end": 32, "script": "super"},
    ]


@pytest.mark.parametrize(
    ("first", "separator"),
    [
        ("Figure 1. A sentence.", "\n"),
        ("Is it a cell?", "\n"),
        ("It works!", "\n"),
        ("Panels:", "\n"),
        ("这是一个图。", "\n"),
        ('He wrote "stop."', "\n"),
        ("Figure 1. A sentence that", " "),
        ("Two cells, one nucleus,", " "),
    ],
)
def test_caption_parts_break_the_line_only_after_a_finished_sentence(first, separator):
    evidence = Evidence(
        [Block("b0", "picture"), Block("b1", "caption", first), Block("b2", "caption", "Next part")]
    )
    plan = Plan(
        body=[Section(blocks=["F1"])],
        figures=[Figure(id="F1", images=["b0"], caption=["b1", "b2"])],
    )
    assert built(plan, evidence).elements[0].text == f"{first}{separator}Next part"


def test_a_table_has_its_cells_caption_and_footnotes():
    plan, evidence = paper()
    table = built(plan, evidence).elements[8]
    # The caption leads the searchable text; the grid stays in the payload.
    assert table.text == "Table 1. Results.\n\nName | Value"
    assert table.payload["table"] == evidence.get("b13").table
    assert table.payload["number"] == "1"
    assert table.payload["caption"] == "Table 1. Results."
    assert table.payload["footnotes"] == "a. Measured twice."
    assert table.payload["blockIds"] == ["b13", "b14", "b15"]
    assert "imageKey" not in table.payload


def test_a_table_that_is_a_picture_keeps_its_image_key():
    evidence = Evidence(
        [Block("b0", "picture", page=3, box=(0, 0, 10, 10), image_key="#/pictures/7"),
         Block("b1", "caption", "Table 2. As an image.")]
    )
    plan = Plan(
        body=[Section(blocks=["T1"])],
        tables=[Table(id="T1", number="2", table=["b0"], caption=["b1"])],
    )
    table = built(plan, evidence).elements[0]
    assert table.kind == "table" and table.text == "Table 2. As an image."
    assert table.payload["imageKey"] == "#/pictures/7" and "table" not in table.payload
    assert table.payload["caption"] == "Table 2. As an image."
    assert table.anchor["bboxPpm"] == [0, 0, 10, 10]


def test_a_table_cut_across_pages_is_stacked_into_one_grid():
    def grid(rows: list[list[str]]) -> dict:
        return {
            "num_rows": len(rows),
            "num_cols": 2,
            "table_cells": [
                {"text": text, "start_row_offset_idx": r, "end_row_offset_idx": r + 1,
                 "start_col_offset_idx": c, "end_col_offset_idx": c + 1}
                for r, row in enumerate(rows)
                for c, text in enumerate(row)
            ],
        }

    evidence = Evidence(
        [Block("b0", "table", table=grid([["a", "b"], ["c", "d"]])),
         Block("b1", "table", table=grid([["e", "f"]]))]
    )
    plan = Plan(body=[Section(blocks=["T1"])], tables=[Table(id="T1", table=["b0", "b1"])])
    table = built(plan, evidence).elements[0]
    assert table.text == "a | b\nc | d\ne | f"
    data = table.payload["table"]
    assert (data["num_rows"], data["num_cols"]) == (3, 2)
    assert [c["start_row_offset_idx"] for c in data["table_cells"]] == [0, 0, 1, 1, 2, 2]
    assert [c["end_row_offset_idx"] for c in data["table_cells"]][-1] == 3


def test_references_are_split_and_the_outline_counts_the_split_entries():
    evidence = Evidence(
        [
            Block("b0", "reference", "[1] Smith, A. One. 2020. [2] Jones, B. Two. 2021."),
            Block("b1", "section_header", "Appendix A"),
            Block("b2", "text", "Details."),
        ]
    )
    plan = Plan(
        back=[
            Part(part="references", blocks=["b0"]),
            Part(part="appendix", heading="b1", blocks=["b2"]),
        ]
    )
    document = built(plan, evidence)
    assert [e.kind for e in document.elements] == ["reference", "reference", "heading", "paragraph"]
    assert document.manifest["outline"][0]["ordinal"] == 2
    assert document.elements[3].parent == 2


def test_notes_come_after_the_body_and_before_the_back_matter():
    evidence = Evidence(
        [
            Block("b0", "text", "Body."),
            Block("b1", "footnote", "1 First note", marker="1"),
            Block("b2", "footnote", "continued note."),
            Block("b3", "reference", "[1] A. B. 2020."),
        ]
    )
    plan = Plan(
        body=[Section(blocks=["b0"])],
        back=[Part(part="references", blocks=["b3"])],
        notes=[Note(id="N1", blocks=["b1..b2"])],
    )
    elements = built(plan, evidence).elements
    assert [(e.kind, e.payload["part"]) for e in elements] == [
        ("paragraph", "body"), ("note", "notes"), ("reference", "back"),
    ]
    assert elements[1].text == "1 First note continued note."
    assert elements[1].payload["marker"] == "1" and elements[1].payload["role"] == "note"


def test_blocks_become_the_element_their_label_calls_for():
    evidence = Evidence(
        [
            Block("b0", "list_item", "First point", marker="•"),
            Block("b1", "code", "x = 1"),
            Block("b2", "formula", "E = mc2", image_key="#/texts/9",
                  extra={"formulaLayout": "linear", "glyphCheck": "verified"}),
            Block("b3", "formula", "", image_key="#/texts/10"),
            Block("b4", "table", "", table={"table_cells": [{"text": "A"}, {"text": "B"}]}),
            Block("b5", "picture", "", image_key="#/pictures/3"),
            Block("b6", "text", "axis label", inside="b5"),
            Block("b7", "reference", "A reference-labelled paragraph."),
            Block("b8", "section_header", "A header in the middle"),
            Block("b9", "caption", "A stray caption"),
            Block("b10", "text", "   "),
        ]
    )
    elements = built(Plan(body=[Section(blocks=["b0..b10"])]), evidence).elements
    assert [e.kind for e in elements] == [
        "list_item", "code", "equation", "equation", "table", "figure",
        "paragraph", "paragraph", "paragraph",
    ]
    assert elements[0].payload["marker"] == "•"
    assert elements[2].payload["imageKey"] == "#/texts/9"
    assert elements[2].payload["transcription"] == "available"
    assert not elements[2].payload.get("noEmbed")
    assert elements[3].text == "" and elements[3].payload["transcription"] == "unavailable"
    assert elements[3].payload["noEmbed"] is True
    assert elements[4].text == "A\nB"
    assert elements[4].payload["table"]["table_cells"][1]["text"] == "B"
    assert elements[5].payload["imageKey"] == "#/pictures/3"
    assert elements[5].payload["figureText"] == "axis label"
    assert "number" not in elements[5].payload and "caption" not in elements[5].payload
    assert [e.text for e in elements[6:]] == [
        "A reference-labelled paragraph.", "A header in the middle", "A stray caption",
    ]
    assert not any(e.payload.get("role") for e in elements)


def test_headings_nest_by_level_and_close_deeper_ones():
    labels = ["section_header", "text"] * 5
    evidence = small(*labels)
    levels = [1, 2, 3, 2, 1]
    plan = Plan(
        body=[
            Section(heading=f"b{2 * n}", level=level, blocks=[f"b{2 * n + 1}"])
            for n, level in enumerate(levels)
        ]
    )
    elements = built(plan, evidence).elements
    assert [e.parent for e in elements] == [None, 0, 0, 2, 2, 4, 0, 6, None, 8]
    assert elements[5].headings == ["Text of b0.", "Text of b2.", "Text of b4."]
    assert elements[7].headings == ["Text of b0.", "Text of b6."]
    assert elements[9].headings == ["Text of b8."]
    assert [o["level"] for o in built(plan, evidence).manifest["outline"]] == levels


def test_a_section_without_a_heading_continues_the_open_one():
    evidence = small("section_header", "text", "text")
    plan = Plan(
        body=[Section(heading="b0", level=2, blocks=["b1"]), Section(blocks=["b2"])]
    )
    elements = built(plan, evidence).elements
    assert [e.parent for e in elements] == [None, 0, 0]


def test_front_parts_are_labelled_by_their_names():
    evidence = small("section_header", "text", "text", "section_header", "text", "text", "text")
    plan = Plan(
        front=[
            Part(part="keywords", heading="b0", blocks=["b1"]),
            Part(part="summary", blocks=["b2"]),
            Part(part="preface", heading="b3", level=4, blocks=["b4"]),
            Part(part="other", blocks=["b5", "b6"]),
        ]
    )
    elements = built(plan, evidence).elements
    assert [(e.kind, e.payload.get("role")) for e in elements] == [
        ("heading", "keywords"),
        ("metadata", "keywords"),
        ("paragraph", "summary"),
        ("heading", "preface"),
        ("paragraph", "preface"),
        ("paragraph", "other"),
        ("paragraph", "other"),
    ]
    assert [e.payload.get("level") for e in elements if e.kind == "heading"] == [1, 4]
    assert elements[1].payload["noEmbed"] is True
    assert {e.payload["part"] for e in elements} == {"front"}


def test_back_parts_are_labelled_by_their_names():
    blocks = [
        Block("b0", "section_header", "Appendix"),
        Block("b1", "text", "Appendix text."),
        Block("b2", "picture", image_key="#/pictures/1", page=9, box=(1, 2, 3, 4)),
        Block("b3", "caption", "Figure A1. Extra."),
        Block("b4", "section_header", "Availability"),
        Block("b5", "text", "Data are public."),
        Block("b6", "text", "We thank everyone."),
        Block("b7", "text", "Term: meaning."),
        Block("b8", "section_header", "Notes"),
        Block("b9", "footnote", "1. A note.", marker="1."),
        Block("b10", "text", "Anything else."),
    ]
    plan = Plan(
        body=[Section(blocks=["F1"])],
        back=[
            Part(part="appendix", heading="b0", level=1, blocks=["b1"]),
            Part(part="availability", heading="b4", level=2, blocks=["b5"]),
            Part(part="acknowledgements", blocks=["b6"]),
            Part(part="glossary", blocks=["b7"]),
            Part(part="notes", heading="b8", level=2, blocks=["b9"]),
            Part(part="other", blocks=["b10"]),
        ],
        figures=[Figure(id="F1", images=["b2"], caption=["b3"])],
    )
    elements = built(plan, Evidence(blocks)).elements
    assert [(e.kind, e.payload.get("role")) for e in elements] == [
        ("figure", None),
        ("heading", "appendix"),
        ("paragraph", None),
        ("heading", "availability"),
        ("paragraph", "availability"),
        ("paragraph", "acknowledgements"),
        ("paragraph", "glossary"),
        ("heading", "notes"),
        ("note", "note"),
        ("paragraph", "other"),
    ]
    assert [e.payload.get("level") for e in elements if e.kind == "heading"] == [1, 2, 2]
    assert {e.payload["part"] for e in elements} == {"body", "back"}
    assert elements[8].payload["marker"] == "1."


def test_a_figure_can_sit_in_a_back_part():
    evidence = Evidence(
        [Block("b0", "picture", image_key="#/pictures/1"), Block("b1", "text", "x")]
    )
    plan = Plan(
        back=[Part(part="appendix", blocks=["b1", "F1"])],
        figures=[Figure(id="F1", images=["b0"])],
    )
    elements = built(plan, evidence).elements
    assert [(e.kind, e.payload["part"]) for e in elements] == [
        ("paragraph", "back"), ("figure", "back"),
    ]


def test_a_reference_keeps_its_printed_marker_for_citation_matching():
    evidence = Evidence(
        [
            Block("b0", "text", "As shown [7]."),
            Block("b1", "list_item", "Example, A. An observed result. 2024.", marker="7."),
        ]
    )
    plan = Plan(body=[Section(blocks=["b0"])], back=[Part(part="references", blocks=["b1"])])
    document = built(plan, evidence)
    entries, mentions = reference_data(document.elements)
    assert entries[0]["label"] == "7"
    assert mentions[0]["status"] == "resolved"


# Assemble: manifest -------------------------------------------------------------


def test_the_manifest_describes_the_structure_and_the_evidence():
    plan, evidence = paper()
    evidence.manifest = {"schema": 1, "conversionStatus": "success", "pageCount": 99}
    status = StructureStatus(
        "checked", by="analysis", violations=[Violation("warning", "no-title", "No title.", [])]
    )
    manifest = assemble(plan, evidence, status).manifest
    assert manifest["schema"] == 2 and manifest["method"] == "docling"
    assert manifest["documentType"] == "paper"
    assert manifest["structure"] == status.to_json()
    assert manifest["structure"]["violations"][0]["code"] == "no-title"
    # The file's physical page count, not the pages the layout kept blocks on.
    assert manifest["bodyFontSize"] == 9.5 and manifest["pageCount"] == 99
    assert manifest["images"] == "pending"
    # The evidence's own entries fill gaps only.
    assert manifest["conversionStatus"] == "success" and manifest["schema"] == 2


def test_the_outline_lists_every_heading_but_the_title():
    plan, evidence = paper()
    outline = built(plan, evidence).manifest["outline"]
    assert [(o["ordinal"], o["title"], o["level"], o["status"]) for o in outline] == [
        (3, "Introduction", 1, "observed"),
        (6, "Methods", 1, "observed"),
        (10, "References", 1, "observed"),
    ]
    assert outline[0]["anchor"] == {"page": 2}


def test_the_outline_keeps_subtitles_and_part_headings():
    evidence = small("title", "title", "section_header", "text")
    plan = Plan(
        title=["b0"], subtitle=["b1"], front=[Part(part="abstract", heading="b2", blocks=["b3"])]
    )
    outline = built(plan, evidence).manifest["outline"]
    assert [(o["title"], o["level"]) for o in outline] == [("Text of b1.", 2), ("Text of b2.", 1)]


def test_the_metadata_gains_the_title_and_authors_without_repeats():
    plan, evidence = paper()
    declared = {
        "title": [
            {"value": "Declared title", "origin": "pdf-info:/Title", "status": "observed"},
            {"value": "Deep Examples", "origin": "document", "status": "observed"},
        ],
        "doi": [{"value": "10.1/x", "origin": "pdf-info:/doi", "status": "observed"}],
    }
    evidence.metadata = copy.deepcopy(declared)
    metadata = built(plan, evidence).manifest["metadata"]
    assert [c["value"] for c in metadata["title"]] == ["Declared title", "Deep Examples"]
    assert metadata["doi"] == declared["doi"]
    assert metadata["authors"] == [
        {
            "value": "Alice Smith, Bob Jones and Carol Lee",
            "origin": "document",
            "status": "observed",
        }
    ]
    assert evidence.metadata == declared  # the evidence is read-only


def test_a_title_the_pdf_never_declared_becomes_the_documents_own():
    plan, evidence = paper()
    metadata = built(plan, evidence).manifest["metadata"]
    assert metadata["title"] == [
        {"value": "Deep Examples", "origin": "document", "status": "observed"}
    ]


def test_blocks_without_text_make_no_text_element():
    evidence = Evidence(
        [Block("b0", "title", ""), Block("b1", "text", ""), Block("b2", "text", "x")]
    )
    document = built(Plan(title=["b0"], body=[Section(blocks=["b1", "b2"])]), evidence)
    assert [e.text for e in document.elements] == ["x"]
    assert document.manifest["metadata"] == {}


def test_a_document_over_the_size_budget_is_refused():
    evidence = Evidence([Block(f"b{n}", "text", "x") for n in range(10_001)])
    plan = Plan(body=[Section(blocks=["b0..b10000"])])
    assert errors(check(plan, evidence)) == []
    with pytest.raises(ValueError, match="budget"):
        built(plan, evidence)


# Assemble: the figure crop, with the real cropper -------------------------------


def test_a_plan_figure_is_cropped_from_the_page_render(tmp_path):
    from PIL import Image

    from gunther.document_parser import attach_document_images

    plan, evidence = paper()
    document = built(plan, evidence)
    Image.new("RGB", (1000, 2000), "white").save(tmp_path / "page-2.png")
    parsed = {
        "pages": {"2": {"size": {"width": 600, "height": 800}}},
        "gunther_page_images": [{"page": 2, "file": "page-2.png"}],
        # The parser's own crop of the picture is never read: the plan's crop wins.
        "gunther_images": [{"ref": "#/pictures/0", "file": "figure-0.png"}],
    }
    attach_document_images(document, parsed, tmp_path)
    (asset,) = document.assets
    assert asset.key == "#/pictures/0"
    width, height = Image.open(io.BytesIO(asset.data)).size
    assert abs(width - 650) <= 2 and abs(height - 700) <= 2
    figure = next(e for e in document.elements if e.kind == "figure")
    assert figure.payload["widthPt"] == 390.0
    assert document.manifest["images"] == "preserved"


# Broken plans never crash the check ----------------------------------------------


def plan_lists(plan: Plan) -> list[list[str]]:
    lists = [plan.title, plan.subtitle, plan.authors]
    lists += [part.blocks for part in [*plan.front, *plan.back]]
    lists += [section.blocks for section in plan.body]
    for figure in plan.figures:
        lists += [figure.images, figure.caption, figure.labels]
    for table in plan.tables:
        lists += [table.table, table.caption, table.footnotes]
    lists += [note.blocks for note in plan.notes] + [drop.blocks for drop in plan.drop]
    return lists


def test_a_plan_a_model_got_wrong_is_judged_and_assemble_agrees_with_check():
    pool = [f"b{n}" for n in range(21)] + ["F1", "T1", "N1", "b99", "b3..b9", "b9..b3", "x", ""]
    clean = broken = 0
    for seed in range(300):
        rng = random.Random(seed)
        plan, evidence = paper()
        for _ in range(rng.randrange(1, 4)):
            target = rng.choice(plan_lists(plan))
            action = rng.choice(["add", "clear", "shuffle", "repeat", "join", "heading"])
            if action == "add":
                target.insert(rng.randrange(len(target) + 1), rng.choice(pool))
            elif action == "clear":
                target.clear()
            elif action == "shuffle":
                rng.shuffle(target)
            elif action == "repeat" and target:
                target.append(target[0])
            elif action == "join":
                plan.joins.append([rng.choice(pool), rng.choice(pool)])
            else:
                plan.body[0].heading = rng.choice(pool)
        before = (plan.model_dump(), evidence.to_json())
        found = check(plan, evidence)
        assert all(isinstance(v, Violation) and v.message for v in found)
        if errors(found):
            broken += 1
            with pytest.raises(ValueError):
                built(plan, evidence)
        else:
            clean += 1
            built(plan, evidence).validate()
        assert (plan.model_dump(), evidence.to_json()) == before  # nothing is edited
    assert clean > 10 and broken > 100


def test_parts_are_named_from_the_shape():
    evidence = Evidence([Block("b0", "text", "We summarise the work."), Block("b1", "text", "x")])
    plan = Plan(
        front=[Part(part="Abstract", blocks=["b0"])],
        back=[Part(part="refs", blocks=["b1"])],
    )
    codes = [v.code for v in check(plan, evidence) if v.severity == "error"]
    assert codes == ["unknown-part", "unknown-part"]


def test_correspondence_marks_leave_the_author_line():
    evidence = Evidence([Block("b0", "text", "Ada Lovelace & Caleb Webber ✉")])
    document = assemble(Plan(authors=["b0"]), evidence, StructureStatus("draft"))
    assert document.elements[0].text == "Ada Lovelace & Caleb Webber"


def test_author_cells_keep_the_name_line_without_badges_or_addresses():
    cell = Block(
        "b0", "text", "Ashish Vaswani∗ Google Brain avaswani@google.com",
        extra={"lineStarts": [16, 29]},
    )
    # "ID" is an ORCID badge: glyphs covered by a small link.
    badged = Block(
        "b1", "text", "Giovanni ColavizzaID, Isla Staden¤",
        extra={"icons": [{"start": 18, "end": 20}]},
    )
    evidence = Evidence([cell, badged])
    document = assemble(Plan(authors=["b0", "b1"]), evidence, StructureStatus("draft"))
    assert [e.text for e in document.elements] == [
        "Ashish Vaswani", "Giovanni Colavizza, Isla Staden",
    ]
    assert document.elements[0].payload["sourceText"] == cell.text


def test_a_caption_hyphenated_across_regions_reads_as_one_word():
    first = Block("b1", "text", "Fig. 1 Cell responses after pertur", extra={"joinsNext": True})
    evidence = Evidence([Block("b0", "picture"), first, Block("b2", "text", "bation.")])
    plan = Plan(
        body=[Section(blocks=["F1"])],
        figures=[Figure(id="F1", number="1", images=["b0"], caption=["b1", "b2"])],
    )
    figure = assemble(plan, evidence, StructureStatus("draft")).elements[0]
    assert figure.text == "Fig. 1 Cell responses after perturbation."
