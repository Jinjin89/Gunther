"""Planning must preserve evidence through compression and respect reading prerequisites."""

import json

import pytest
from fake_models import FakeProvider, gateway, model, skill_step

from gunther.agent import Evidence
from gunther.llm import ModelError
from gunther.outputs import Planned, Pool, Spec, _marked, with_layout
from gunther.schemas import OutlineItem
from gunther.skill_runner import Approach, Card, SkillAgents
from gunther.skillbook import skill_for
from gunther.slide_planning import dependency_problems, requested_pages


@pytest.mark.parametrize(
    ("brief", "expected"),
    [
        ("Make exactly 6 slides for a 10-minute talk", 6),
        ("请做6页，讲十分钟", 6),
        ("Use about 6 slides", None),
        ("Use 6 to 8 slides", None),
        ("Use 6–8 slides", None),
        ("A 10-minute talk", None),
    ],
)
def test_explicit_counts_are_separate_from_duration_and_ranges(brief, expected):
    assert requested_pages(brief) == expected


def planner(initial, repaired=None, review=None):
    calls = []

    def reply(request):
        step = skill_step(request)
        if step == "outline_review":
            return json.dumps(review or {"issues": [], "omitted": []})
        calls.append(request)
        return json.dumps(initial if len(calls) == 1 else repaired or initial)

    agents = SkillAgents(gateway(FakeProvider(reply)), skill_for("slides"), use="talk")
    agents.cards = [
        Card(claim="The pilot recorded a shorter wait", refs=[1], evidence="The wait was shorter."),
        Card(
            claim="Quality was not measured",
            refs=[2],
            evidence="Quality was not measured.",
            scope="No quality conclusion",
        ),
    ]
    agents.approach = Approach(
        "What does the pilot establish?", "Wait only", "Review the evidence", "what"
    )
    pool = Pool(10)
    for card in agents.cards:
        pool.adopt(
            Evidence(
                kind="library",
                title=card.claim,
                text=card.evidence,
                locator="Synthetic test source",
            )
        )
    return agents, pool, calls


def page(heading, cards=(), **extra):
    return {"heading": heading, "cards": list(cards), **extra}


def test_count_repair_retains_the_middle_limitation_and_its_source():
    initial = {
        "title": "Pilot",
        "sections": [
            page("Pilot"),
            page("Result", [1]),
            page("Unmeasured quality", [2]),
            page("Detail", [1]),
            page("Summary", [1, 2]),
        ],
    }
    repaired = {
        "title": "Pilot",
        "sections": [
            page("Pilot"),
            page("A shorter wait does not establish quality", [1, 2]),
            page("What remains unknown", [2]),
        ],
    }
    agents, pool, calls = planner(
        initial,
        repaired,
        review={
            "requirements": [
                {"requirement": "Preserve the quality limitation", "basis": "brief", "cards": [2]}
            ],
        },
    )
    _, planned = agents.structure(
        model(),
        None,
        Spec("slides", "scientist", brief="Exactly 3 slides"),
        lambda _: None,
        pool=pool,
    )
    assert len(planned) == 3
    assert planned[1].refs == (1, 2)
    record = agents.record()["outlineReview"]
    assert record["coverage"][1]["slides"] == [2, 3]
    assert record["requirements"][0]["basis"] == "brief"
    assert record["requirements"][0]["slides"] == [2, 3]
    assert record["coverage"][1]["evidence"] == "Quality was not measured."
    assert record["coverage"][1]["sourceIdentities"]
    assert len(calls) == 2


def test_a_repair_that_drops_evidence_is_refused_and_retains_its_candidate():
    initial = {
        "sections": [
            page("Topic"),
            page("Result", [1]),
            page("Limitation", [2]),
            page("Close", [1, 2]),
        ]
    }
    repaired = {"sections": [page("Topic"), page("Result", [1]), page("Close", [1])]}
    agents, pool, _ = planner(initial, repaired)
    with pytest.raises(ModelError, match="lost previously assigned evidence"):
        agents.structure(
            model(),
            None,
            Spec("slides", "scientist", brief="Exactly 3 slides"),
            lambda _: None,
            pool=pool,
        )
    assert agents.outline_review["status"] == "incomplete"
    assert len(agents.outline_review["candidate"]["sections"]) == 3


def test_unknown_cards_are_not_silently_filtered():
    invalid = {"sections": [page("Topic"), page("Unsupported card", [88]), page("Close")]}
    agents, pool, _ = planner(invalid)
    with pytest.raises(ModelError, match="unknown material cards"):
        agents.structure(model(), None, Spec("slides", "scientist"), lambda _: None, pool=pool)


def test_a_factual_cover_gets_source_cards_before_writing():
    initial = {"sections": [page("Pilot", id="cover"), page("Result", [1, 2], id="result")]}
    repaired = {
        "sections": [page("Pilot", [1, 2], id="cover"), page("Result", [1, 2], id="result")]
    }
    agents, pool, _ = planner(initial, repaired, {"factual_pages": ["cover", "result"]})
    _, planned = agents.structure(
        model(),
        None,
        Spec("slides", "scientist", brief="Exactly 2 slides"),
        lambda _: None,
        pool=pool,
    )
    assert planned[0].refs == (1, 2)
    assert agents.record()["outlineReview"]["repaired"] is True


def test_missing_or_later_prerequisites_are_rejected():
    pages = [
        Planned("A result", id="result", requires=("definition",)),
        Planned("Definition", id="definition"),
    ]
    assert dependency_problems(pages)
    assert not dependency_problems(list(reversed(pages)))


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
