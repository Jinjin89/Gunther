"""Compatibility with saved legacy outlines and slide markers."""

from gunther.outputs import _marked, with_layout
from gunther.schemas import OutlineItem


def test_planning_fields_round_trip_and_legacy_outlines_stay_small():
    planned = OutlineItem(
        heading="A result",
        id="result",
        message="A qualified observation",
        question="What changed?",
        requires=["definition"],
    )
    assert OutlineItem.model_validate(planned.model_dump(by_alias=True)) == planned
    assert OutlineItem(heading="Legacy").model_dump(by_alias=True) == {
        "heading": "Legacy",
        "goal": "",
    }


def test_design_version_is_kept_with_the_title_without_changing_its_body():
    body = "# Pilot\n\nA shorter wait, with limits [1][2]."
    styled = with_layout(body, "title", theme="editorial", design_version=1)
    assert _marked(styled).body == body
    assert _marked(styled).design_version == 1
    assert _marked(with_layout(styled, "title", accent="blue")).design_version == 1
    assert _marked(with_layout(styled, "title", accent="ink")).accent == "ink"
    assert _marked(with_layout(body, "title", theme="editorial")).design_version == 0


def test_empty_planning_fields_preserve_a_legacy_workspaces_freshness_stamp():
    from gunther.artifact_workspace import (
        ConversationBrief,
        ItemRec,
        Line,
        Work,
        WorkSpec,
        digest_part_inputs,
    )

    brief = ConversationBrief(audience=Line(text="student"))
    item = ItemRec(
        id="p1", heading="A measured result", goal="Explain the observation", layout="text"
    )
    work = Work(key="w", spec=WorkSpec(kind="slides"), outline=[item])
    # Recorded with the pre-upgrade digest. Defaulted fields must not invalidate old drafts.
    legacy = (
        '{"audience":"e6d1a79d18df","cards":"4f53cda18c2b",'
        '"item":"18bebedf4081","kind":"0bb98d65b204","use":"12ae32cb1ec0"}'
    )
    assert digest_part_inputs(brief, work, item) == legacy
    item.message = "The measured result has limited scope"
    assert digest_part_inputs(brief, work, item) != legacy
