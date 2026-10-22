"""The Python half of the composed-slide block and scene contract."""

import base64
import json
from pathlib import Path

import pytest

from gunther.outputs import (
    Spec,
    _marked,
    chart_rows_coverage_reason,
    heading_of,
    layout_of,
    unmarked,
)
from gunther.slide_scene import (
    SceneEnvelope,
    SceneGroup,
    SceneLeaf,
    SlideBlock,
    allowed_views,
    decode_scene,
    encode_scene,
    graphics_hash,
    rebind_graphics_hash,
    scene_marker,
    source_hash,
    split_blocks,
    validate_scene,
    with_scene,
)

FIXTURE = (
    Path(__file__).parents[3]
    / "artifacts/validation/presentation-composer/contract_cases.json"
)


def test_blocks_share_the_fixture_types_and_preserve_original_markdown() -> None:
    fixture = json.loads(FIXTURE.read_text())
    heading, blocks = split_blocks(fixture["markdown"])
    assert heading == fixture["heading"]
    assert [block.model_dump() for block in blocks] == fixture["blocks"]
    assert "Presenter-only" not in "\n".join(block.markdown for block in blocks)
    assert split_blocks("## Using C#\n\nA title mentioning a language.")[0] == "Using C#"


def test_source_hash_ignores_citation_renumbering_and_notes_but_keeps_code_examples() -> None:
    fixture = json.loads(FIXTURE.read_text())
    original = fixture["markdown"]
    assert source_hash(original) == source_hash(fixture["renumbered"])
    assert source_hash(original) == source_hash(fixture["noteChanged"])
    assert source_hash(original) != source_hash(original.replace("[9]", "[8]"))
    assert source_hash(original) != source_hash(original.replace("a change", "a revision"))


def valid_tree() -> SceneGroup:
    return SceneGroup(
        kind="row",
        weights=[2, 1],
        children=[
            SceneLeaf(kind="leaf", blockId="b1", view="stat", size="display", role="focus"),
            SceneGroup(
                kind="column",
                weights=[1, 1],
                children=[
                    SceneLeaf(kind="leaf", blockId="b2", view="text"),
                    SceneLeaf(kind="leaf", blockId="b3", view="list", size="small"),
                ],
            ),
        ],
    )


def test_scene_round_trips_and_accepts_valid_json_key_order() -> None:
    blocks = [
        SlideBlock(id="b1", type="stat", markdown="**72%** [1]"),
        SlideBlock(id="b2", type="text", markdown="A short explanation"),
        SlideBlock(id="b3", type="list", markdown="- One\n- Two"),
    ]
    root = valid_tree()
    assert validate_scene(root, blocks) is None
    envelope = SceneEnvelope(version=1, sourceHash=source_hash("## H\n\nWords"), root=root)
    encoded = encode_scene(envelope)
    marker = encoded.removeprefix("<!-- scene: ").removesuffix(" -->")
    line, wrapped = scene_marker(with_scene("<!-- layout: composed -->\n## H\n\nWords", envelope))
    assert line == marker and wrapped == encoded
    decoded = decode_scene(encoded)
    assert decoded is not None and validate_scene(decoded.root, blocks) is None

    raw = base64.urlsafe_b64decode(marker + "=" * (-len(marker) % 4))
    data = json.loads(raw)
    legacy = encode_scene(
        SceneEnvelope(version=1, sourceHash=source_hash("legacy"), root=root)
    )
    legacy_payload = legacy.removeprefix("<!-- scene: ").removesuffix(" -->")
    legacy_data = json.loads(
        base64.urlsafe_b64decode(legacy_payload + "=" * (-len(legacy_payload) % 4))
    )
    assert set(legacy_data) == {"version", "sourceHash", "root"}
    reordered = {"root": data["root"], "sourceHash": data["sourceHash"], "version": data["version"]}
    noncanonical = base64.urlsafe_b64encode(json.dumps(reordered).encode()).decode().rstrip("=")
    assert decode_scene(f"<!-- scene: {noncanonical} -->") is not None


def test_scene_header_after_blank_lines_is_still_metadata() -> None:
    body = "<!-- layout: composed -->\n## Title\n\n**72%** [1]\n\nMeasured [2]."
    root = SceneGroup(
        kind="row",
        weights=[1, 1],
        children=[
            SceneLeaf(kind="leaf", blockId="b1", view="stat"),
            SceneLeaf(kind="leaf", blockId="b2", view="text"),
        ],
    )
    marker = encode_scene(
        SceneEnvelope(version=1, sourceHash=source_hash(body), root=root)
    )
    blank_header = body.replace(
        "<!-- layout: composed -->\n",
        f"<!-- layout: composed -->\n\n{marker}\n\n",
        1,
    )
    assert source_hash(body) == source_hash(blank_header)
    assert split_blocks(body) == split_blocks(blank_header)
    assert scene_marker(blank_header)[1] == marker


@pytest.mark.parametrize(
    ("root", "message"),
    [
        (SceneLeaf(kind="leaf", blockId="b1", view="text"), "root"),
        (
            SceneGroup(
                kind="row",
                weights=[1, 1],
                children=[
                    SceneLeaf(kind="leaf", blockId="b1", view="text"),
                    SceneLeaf(kind="leaf", blockId="b1", view="text"),
                ],
            ),
            "Duplicate",
        ),
        (
            SceneGroup(
                kind="row",
                weights=[1, 1],
                children=[
                    SceneLeaf(kind="leaf", blockId="b1", view="text"),
                    SceneLeaf(kind="leaf", blockId="b3", view="text"),
                ],
            ),
            "Unknown",
        ),
    ],
)
def test_invalid_scene_references_are_rejected(root, message: str) -> None:
    blocks = [
        SlideBlock(id="b1", type="text", markdown="One"),
        SlideBlock(id="b2", type="stat", markdown="Two"),
    ]
    assert message in (validate_scene(root, blocks) or "")


def test_scene_type_mismatch_and_missing_blocks_are_rejected() -> None:
    blocks = [
        SlideBlock(id="b1", type="stat", markdown="**72%** [1]"),
        SlideBlock(id="b2", type="text", markdown="Text"),
    ]
    wrong = SceneGroup(
        kind="row",
        weights=[1, 1],
        children=[
            SceneLeaf(kind="leaf", blockId="b1", view="text"),
            SceneLeaf(kind="leaf", blockId="b2", view="text"),
        ],
    )
    assert "does not match" in (validate_scene(wrong, blocks) or "")
    missing = SceneGroup(
        kind="row",
        weights=[1, 1],
        children=[
            SceneLeaf(kind="leaf", blockId="b1", view="stat"),
            SceneLeaf(kind="leaf", blockId="b1", view="stat"),
        ],
    )
    assert "Duplicate" in (validate_scene(missing, blocks) or "")


def test_scene_decoder_fails_closed_for_unknown_versions_bad_base64_and_oversize() -> None:
    invalid = b'{"version":2,"sourceHash":"x","root":{}}'
    unknown = base64.urlsafe_b64encode(invalid).decode().rstrip("=")
    assert decode_scene(f"<!-- scene: {unknown} -->") is None
    assert decode_scene("<!-- scene: not base64! -->") is None
    assert decode_scene(f"<!-- scene: {'a' * 24_001} -->") is None


def test_malformed_header_scene_is_metadata_but_a_fenced_scene_example_is_body_text() -> None:
    text = (
        "<!-- layout: composed -->\n\n<!-- scene: malformed! -->\n\n"
        "## Full body\n\nFirst point.\n\n~~~text\n<!-- scene: example -->\n~~~"
    )
    marked = _marked(text)
    assert marked.scene is None
    assert marked.scene_line == "<!-- scene: malformed! -->"
    assert heading_of(text) == "Full body"
    assert "malformed!" not in layout_of(text)[1]
    shown = unmarked(text, Spec("slides", "scientist"))
    assert "malformed!" not in shown
    assert "<!-- scene: example -->" in shown


GRAPHICS_FIXTURE = (
    Path(__file__).parents[3]
    / "artifacts/validation/presentation-graphics-composer/contract_cases.json"
)


def test_graphic_candidates_match_shared_contract_fixture() -> None:
    fixture = json.loads(GRAPHICS_FIXTURE.read_text())
    for name, case in fixture["cases"].items():
        block_type = "list" if "list" in case["allowedViews"] else "table"
        block = SlideBlock(id="b1", type=block_type, markdown=case["markdown"])
        assert allowed_views(block) == case["allowedViews"], name


def test_chart_checker_includes_first_data_row_and_preserves_negative_sign() -> None:
    from gunther.agent import Evidence
    from gunther.outputs import Page
    from gunther.skill_checks import Context, chart_numbers_cited

    markdown = (
        "## Graphic block\n\n| Quarter | Change (points) |\n| --- | --- |\n"
        "| Q1 | −3.5 points [1] |\n| Q2 | +2 points [2]"
    )
    page = Page(0, "Graphic block", "bar_chart", markdown)
    evidence = {
        1: Evidence("library", "Q1 source", "Q1 changed by −3.5 points.", ref=1),
        2: Evidence("library", "Q2 source", "Q2 changed by +2 points.", ref=2),
    }
    found = chart_numbers_cited([page], {}, Context("slides", evidence=evidence))
    assert found == []

    missing_first = markdown.replace("−3.5 points [1]", "−35 points [1]")
    page = Page(0, "Graphic block", "bar_chart", missing_first)
    findings = chart_numbers_cited([page], {}, Context("slides", evidence=evidence))
    assert findings and "35" in findings[0].message


def test_chart_source_numbers_allow_cjk_adjacent_values_and_keep_negative_signs() -> None:
    from gunther.agent import Evidence
    from gunther.outputs import VALUE_IN, Page
    from gunther.skill_checks import Context, chart_numbers_cited

    text = (
        "## Graphic block\n| Period | Rate (%) |\n| --- | --- |\n"
        "| 第一组 | −12% [1] |\n| 第二组 | 12% [2] |"
    )
    evidence = {
        1: Evidence("library", "One", "实验完成−12%后继续观察。", ref=1),
        2: Evidence("library", "Two", "该组增长12%并达到目标。", ref=2),
    }
    page = Page(0, "Graphic block", "bar_chart", text)
    assert chart_numbers_cited([page], {}, Context("slides", evidence=evidence)) == []
    assert VALUE_IN.findall("A-12 v2.5 完成−12%") == ["−12%"]


def test_chart_coverage_must_bind_every_row_to_its_label_value_and_citation() -> None:
    text = (
        "<!-- layout: bar_chart -->\n## Graphic block\n\n"
        "| Quarter | Change (points) |\n| --- | --- |\n"
        "| Q1 | −3.5 points [1] |\n| Q2 | +2 points [2] |"
    )
    partial = {
        "status": "complete",
        "claims": [
            {"text": "Q1 changed by −3.5 points.", "refs": [1], "verdict": "supported"}
        ],
    }
    reason = chart_rows_coverage_reason(text, partial)
    assert reason and "rows" in reason

    all_rows = {
        "status": "complete",
        "claims": [
            {"text": "Q1 changed by −3.5 points.", "refs": [1], "verdict": "supported"},
            {"text": "Q2 changed by +2 points.", "refs": [2], "verdict": "supported"},
        ],
    }
    assert chart_rows_coverage_reason(text, all_rows) is None

    same_value_other_label = {
        "status": "complete",
        "claims": [
            {"text": "Q1 changed by −3.5 points.", "refs": [1], "verdict": "supported"},
            {"text": "Q3 changed by +2 points.", "refs": [2], "verdict": "supported"},
        ],
    }
    assert chart_rows_coverage_reason(text, same_value_other_label) is not None


def test_graphic_hash_rebind_requires_the_trusted_reference_mapping() -> None:
    root = SceneGroup(
        kind="row",
        weights=[1, 1],
        children=[
            SceneLeaf(kind="leaf", blockId="b1", view="text"),
            SceneLeaf(kind="leaf", blockId="b2", view="table"),
        ],
    )
    before = (
        "<!-- layout: composed -->\n## Result\n\nMeasured [4][5].\n\n"
        "| A | B |\n| --- | --- |\n| X | 2 [4] |"
    )
    marked = with_scene(
        before,
        SceneEnvelope(
            version=2,
            sourceHash=source_hash(before),
            graphicsHash=graphics_hash(before),
            root=root,
        ),
    )
    after = marked.replace("[4]", "[1]").replace("[5]", "[2]")
    assert rebind_graphics_hash(marked, after) == after
    rebound = rebind_graphics_hash(marked, after, {4: 1, 5: 2})
    rebound_scene = decode_scene(scene_marker(rebound)[1])
    assert rebound_scene is not None
    assert rebound_scene.graphics_hash == graphics_hash(rebound)

    dropped_reference = marked.replace("[4]", "[1]").replace("[5]", "")
    assert rebind_graphics_hash(marked, dropped_reference, {4: 1}) == dropped_reference
