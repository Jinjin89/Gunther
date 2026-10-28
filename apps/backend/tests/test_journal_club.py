"""Journal-club handoffs, using invented source material and fake model calls only."""

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fake_models import FakeProvider, gateway, model
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_studio_pipeline import request_text
from test_studio_science import (
    DECK,
    NO_IMAGE,
    READING,
    STUDY,
    make,
    pixels,
    science_pool,
    science_router,
)

from gunther.llm import ModelError
from gunther.models import Artifact
from gunther.outputs import Notes, Spec
from gunther.studio import format as fmt
from gunther.studio import science as S
from gunther.studio.pipeline import Run, StudioAgents
from gunther.studio.reference import DECK_FORMAT, STUDY_DECK_FORMAT


def run():
    return Run(
        Spec("slides", "Researchers", brief="Explain this study"),
        "read",
        "English",
        study=S.StudyMap.model_validate(STUDY),
        figure_readings=[S.FigureReading.model_validate(READING)],
        reviews=[],
    )


@pytest.mark.parametrize(
    "fragment,valid",
    [
        ("", True),
        ("#x=0.5,y=0,w=0.5,h=1", True),
        ("#x=0.8,y=0,w=0.5,h=1", False),
        ("#x=nan,y=0,w=0.5,h=1", False),
        ("#x=0,y=0,w=0,h=1", False),
        ("#garbage", False),
    ],
)
def test_crop_parsing_scope_and_malformed_checks(fragment, valid):
    reference = "gunther-asset:ast_signal" + fragment
    slide = f"<!-- layout: figure -->\n## Evidence\n![Panel B]({reference})"
    readings = run().figure_readings
    assert bool(S.image_parts(reference)) == valid
    assert bool(S.crop_problems(slide, readings)) == (not valid)
    assert bool(S.scope_problems(slide, science_pool().items)) == (not valid)
    assert S.scope_problems(slide.replace("ast_signal", "outside"), science_pool().items)


def test_only_reader_boxes_authorize_crops_and_panel_labels_are_visible_tokens():
    slide = "<!-- layout: figure -->\n## Evidence\n![Data](gunther-asset:ast_signal)\nA baseline"
    study = S.StudyMap.model_validate(STUDY)
    readings = run().figure_readings
    assert len(S.content_problems(study, readings, slide + "\nNote: Panel A and Panel B")) == 2
    cropped = slide.replace("ast_signal)", "ast_signal#x=0,y=0,w=0.5,h=1)")
    problems = S.content_problems(study, readings, cropped)
    assert len(problems) == 1 and "panel ast_signal:b" in problems[0]
    both = cropped + "\n\n---\n\n" + cropped.replace("#x=0,", "#x=0.5,")
    assert S.content_problems(study, readings, both) == []
    assert S.crop_problems(both, readings) == []
    assert S.crop_problems(cropped.replace("w=0.5", "w=0.4"), readings)


@pytest.mark.parametrize(
    "metadata,expected",
    [
        ({}, []),
        (
            {
                "title": "Source title",
                "authors": ["First Author", "Second", "Third", "Fourth"],
                "venue": "Source Journal",
                "year": 2024,
            },
            ["First Author et al. · Source Journal · 2024"],
        ),
        ({"title": "Source title", "authors": "First Author, Second"}, ["First Author, Second"]),
        ({"authors": '["First Author", "Second"]'}, ["First Author, Second"]),
    ],
)
def test_cover_replaces_model_metadata_and_omits_missing_fields(metadata, expected):
    pool = science_pool()
    for item in pool.items:
        item.payload[0].source_metadata = metadata
    cover = (
        "<!-- layout: title; design: v2; theme: ink -->\n# Source title\n"
        "Why does it work? Reliable sensing matters.\nInvented event 2099\nNote: Extra context."
    )
    result = S.apply_source_cover(cover, pool.items)
    parsed = fmt.parse_slide(result)
    assert parsed.body == ["Why does it work? Reliable sensing matters.", *expected]
    assert result.count("Source title") == 1
    assert "Invented event" not in result and parsed.attrs["theme"] == "ink"
    assert parsed.notes == "Extra context."
    assert S.cover_problems(result, pool.items) == []


def test_generic_format_is_unchanged_and_study_format_has_no_event_example():
    assert hashlib.sha256(DECK_FORMAT.encode()).hexdigest() == (
        "6545b7d909ad3dff610b875910c6d3c2f95f835483796ac9cd4ea329084c83df"
    )
    assert STUDY_DECK_FORMAT.count("figure_detail (") == 1
    assert "Committee briefing" not in STUDY_DECK_FORMAT
    assert "March 2026" not in STUDY_DECK_FORMAT
    assert "Line 3 is an optional meta" not in STUDY_DECK_FORMAT


@pytest.mark.parametrize("rendered", [False, True])
def test_learner_has_only_visible_slides_and_uses_photos_model_for_images(tmp_path, rendered):
    eye, writer = model(), model("deepseek-v4-pro", vision=False)
    fake = FakeProvider(science_router())
    reader = pixels(tmp_path)
    renderer = (
        SimpleNamespace(
            capture=lambda *_: {"id": "capture", "slides": [{"image": "one.png"}]},
            file=lambda *_: reader("unused")[0],
        )
        if rendered
        else None
    )
    agents = StudioAgents(
        gateway(fake, writer, eye, roles={"photos": (eye.ref, "low")}), renderer=renderer
    )
    agents._stage(lambda _: None, "science", "Science", "running")
    held = run()
    held.study.question = "SECRET planning rationale"
    deck = DECK.replace("ast_signal)", "ast_signal#x=0,y=0,w=0.5,h=1)")
    deck += "\nNote: SECRET speaker notes\n:::design\n<div>SECRET design</div>\n:::"
    result, images = agents._learner_review(
        writer, "low", held, deck, science_pool(), lambda _: None
    )
    assert result is not None and bool(images) == rendered
    request = fake.requests[-1]
    text = request_text(request)
    assert "SECRET" not in text and "ast_signal" not in text and "gunther-asset" not in text
    assert science_pool().items[0].text not in text and "#x=" not in text
    assert "[Image: Controlled experiments; enlarged panel region]" in text
    assert request["model"] == (eye.model_id if rendered else writer.model_id)
    assert ("image_url" in str(request["messages"])) == rendered


def test_gate_runs_code_and_learner_even_when_source_review_is_unavailable(tmp_path, monkeypatch):
    route = science_router()

    def reply(request):
        if "Scientific review of the complete argument" in request_text(request):
            raise ModelError("Source review offline")
        return route(request)

    fake = FakeProvider(reply)
    agents = StudioAgents(gateway(fake), use="read", design=False, asset_reader=pixels(tmp_path))
    checked = []
    original = S.code_problems

    def check(*args, **kwargs):
        checked.append(args[2])
        return original(*args, **kwargs)

    monkeypatch.setattr(S, "code_problems", check)
    held = run()
    result = agents.scientific_gate(
        model(), "low", held, NO_IMAGE, science_pool(), "source", lambda _: None
    )
    assert held.review_status == "unavailable"
    assert "gunther-asset:ast_signal" in result and checked[0] == S.apply_source_cover(
        NO_IMAGE, science_pool().items
    )
    assert any("independent learner" in request_text(r) for r in fake.requests)
    assert any("Compare an independent learner" in request_text(r) for r in fake.requests)


def revise(reply, problems, deck=DECK):
    fake = FakeProvider(reply)
    agents = StudioAgents(gateway(fake), use="read", design=False)
    result = agents._revise_science(model(), "low", run(), deck, science_pool(), "source", problems)
    return result, agents, fake


def test_targeted_revision_can_split_a_figure_and_keeps_real_slide_numbers():
    old = fmt.split_slides(DECK)
    reply = "<!-- replaces slide 3 -->\n" + old[1] + "\n\n---\n\n" + old[2]
    result, _, fake = revise(
        reply,
        [S.ScientificProblem(where="Slide 3", problem="Too shallow", fix="Teach setup first")],
    )
    parts = fmt.split_slides(result)
    assert len(parts) == 5 and parts[:2] == old[:2] and parts[-1] == old[-1]
    assert parts[2:4] == [old[1], old[2]]
    assert "replaces slide" not in result
    assert "Slide 3:" in request_text(fake.requests[0])
    assert "Slide 1:" not in request_text(fake.requests[0])


def test_structural_omission_after_targeted_problem_can_insert_new_figure():
    old = fmt.split_slides(NO_IMAGE)
    added = fmt.split_slides(DECK)[2]
    reply = "\n\n---\n\n".join([*old[:3], added, old[3]])
    result, _, fake = revise(
        reply,
        [
            S.ScientificProblem(where="Slide 2", problem="Shallow method", fix="Explain mechanism"),
            S.ScientificProblem(
                where="Figure 3", problem="Never taught", fix="Insert figure walkthrough"
            ),
        ],
        NO_IMAGE,
    )
    assert len(fmt.split_slides(result)) == 5 and "gunther-asset:ast_signal" in result
    assert "deck slide numbers all" in request_text(fake.requests[0])


def test_revision_accepts_slide_ranges_and_records_rejections():
    old = fmt.split_slides(DECK)
    result, _, fake = revise(
        "\n\n---\n\n".join(old[1:3]),
        [S.ScientificProblem(where="Slides 2–3", problem="Too shallow", fix="Explain")],
    )
    assert fmt.split_slides(result) == old
    assert "deck slide numbers 2, 3" in request_text(fake.requests[0])
    result, agents, _ = revise(
        old[1], [S.ScientificProblem(where="Slide 3", problem="Too shallow", fix="Explain")]
    )
    assert result == DECK and any("rejected" in row["label"] for row in agents.skipped)


@pytest.mark.parametrize("change", [None, "asset_id", "source_revision_id", "vision_model"])
def test_cross_build_readings_need_same_asset_revision_and_model(tmp_path, change):
    saved = {
        **READING,
        "source_revision_id": "rev_study",
        "vision_model": model().ref,
        "legible": False,
        "uncertain": ["Legend unreadable"],
    }
    if change:
        saved[change] = "different"
    fake = FakeProvider(science_router())
    agents = StudioAgents(gateway(fake), asset_reader=pixels(tmp_path), reusable_figures=[saved])
    held = run()
    readings = agents.read_figures(model(), "low", held, science_pool())
    assert len(readings) == 1
    assert len(fake.requests) == (0 if change is None else 1)
    if change is None:
        assert any("Legend unreadable" in reason for reason in held.limitations)
    assert agents.trail[-1]["modelCalls"] == len(fake.requests)


def test_resume_restores_review_types_and_changed_plan_invalidates_draft(tmp_path):
    _, agents, fake, saved = make(tmp_path)
    assert {r["kind"] for r in saved["reviews"]} == {"scientific", "learner", "coverage"}
    fake.requests.clear()
    built = agents.make(model(), "low", run().spec, science_pool(), Notes(), resume=saved)
    assert any("answers" in r for r in built.approach["reviews"])
    assert any("unexplained" in r for r in built.approach["reviews"])
    assert any("coverage_problems" in r for r in built.approach["reviews"])
    assert not any("Write the scientific explanation" in request_text(r) for r in fake.requests)
    changed = deepcopy(saved)
    changed["coverage_plan"]["figures"] = []  # Missing inventory forces a regenerated plan.
    changed["draft"] = DECK.replace("What remains unresolved", "STALE DRAFT")
    fake.requests.clear()
    built = agents.make(model(), "low", run().spec, science_pool(), Notes(), resume=changed)
    assert "STALE DRAFT" not in built.content
    assert any("Write the scientific explanation" in request_text(r) for r in fake.requests)


def test_scientific_length_and_stage_counts_follow_the_actual_build(tmp_path):
    _, agents, fake, _ = make(tmp_path)
    counts = {row["step"]: row["modelCalls"] for row in agents.trail}
    assert counts["verify"] == 4
    assert sum(counts.values()) == len(fake.requests)
    assert all(row["seconds"] >= 0 for row in agents.trail)
    prompt = next(request_text(r) for r in fake.requests if "Plan a scientific" in request_text(r))
    assert "8–12" not in prompt and "split complex figures" in prompt
    general = run()
    general.study = None
    assert "8–12" in agents._length(general)


def test_saved_readings_are_loaded_only_from_decks_in_the_same_library():
    from gunther.service import KnowledgeService

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Artifact.__table__.create(engine)
    with Session(engine) as session:
        for identity, library, kind in [
            ("kept", "selected", "slides"),
            ("other", "other_library", "slides"),
            ("report", "selected", "report"),
            ("broken", "selected", "slides"),
        ]:
            session.add(
                Artifact(
                    id=identity,
                    workspace_id="fixture",
                    knowledge_base_id=library,
                    client_request_id=identity,
                    request_fingerprint=identity,
                    lineage_id=identity,
                    version_number=1,
                    format="slides",
                    kind=kind,
                    audience="Researchers",
                    title="Invented study",
                    content="fixture",
                    content_hash=identity,
                    manifest_hash=identity,
                    accepted_unit_ids_json="[]",
                    revision_snapshot_json="{}",
                    provenance_json=(
                        "broken"
                        if identity == "broken"
                        else json.dumps(
                            {
                                "approach": {
                                    "figure_readings": [{**READING, "asset_id": identity}]
                                },
                            }
                        )
                    ),
                )
            )
        session.commit()
        saved = KnowledgeService._reusable_figure_readings(session, "selected")
    assert [row["asset_id"] for row in saved] == ["kept"]


@pytest.mark.parametrize(
    "subtitle",
    [
        "Does the 2019 result still hold when the noise changes?",
        "Why a 1998 assumption still shapes every reading today.",
    ],
)
def test_a_question_that_mentions_a_year_is_not_mistaken_for_cover_metadata(subtitle):
    pool = science_pool()
    for item in pool.items:
        item.payload[0].source_metadata = {"title": "Source title", "year": 2024}
    cover = f"<!-- layout: title; design: v2 -->\n# Source title\n{subtitle}\nSpring 2026"
    parsed = fmt.parse_slide(S.apply_source_cover(cover, pool.items))
    assert parsed.body == [subtitle, "2024"]


def test_a_cover_question_wrapped_over_two_lines_keeps_all_of_it():
    pool = science_pool()
    for item in pool.items:
        item.payload[0].source_metadata = {"title": "Source title", "authors": "Only Author"}
    cover = (
        "<!-- layout: title; design: v2 -->\n# Source title\n"
        "Can one short guide choose the cut site, so that retargeting\n"
        "needs a new guide rather than a new protein? [1]"
    )
    parsed = fmt.parse_slide(S.apply_source_cover(cover, pool.items))
    assert parsed.body == [
        "Can one short guide choose the cut site, so that retargeting needs a new guide "
        "rather than a new protein? [1]",
        "Only Author",
    ]
    assert fmt.check_slide(parsed, first=True) == []
