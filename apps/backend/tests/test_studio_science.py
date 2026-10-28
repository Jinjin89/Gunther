"""Invented studies and pixels, exercised only through fake model gateways."""

import json
import re
from copy import deepcopy
from dataclasses import replace

import pytest
from fake_models import FakeProvider, gateway, model
from PIL import Image, ImageDraw
from test_studio_pipeline import request_text, router

from gunther.agent import Evidence
from gunther.llm import ModelError
from gunther.outputs import Notes, Pool, Spec
from gunther.schemas import ConversationCitationOut
from gunther.studio import format as fmt
from gunther.studio import prompts as P
from gunther.studio import science as S
from gunther.studio.pipeline import StudioAgents

STUDY = {
    "question": "Does adaptive filtering reduce sensor drift?",
    "prior_gap": "A fixed filter was evaluated only under steady noise.",
    "contribution": "A feedback filter handles changing noise in two controlled experiments.",
    "method": {
        "inputs": "Sensor stream and fixed reference",
        "mechanism": "Estimate drift against the reference, then update the filter gain.",
        "outputs": "Corrected signal",
        "assumptions": ["Reference is stable"],
    },
    "study_type": "experimental",
    "claims": [
        {
            "claim": "Feedback reduces drift under changing noise",
            "refs": [1],
            "experiment": "Noise challenge",
            "comparison_or_control": "Fixed vs adaptive filter",
            "measurement": "Absolute drift in mV",
            "result": {
                "quantity": "2",
                "unit": "mV",
                "baseline": "6 mV",
                "condition": "Changing noise",
                "uncertainty": "Interval not reported",
            },
            "figures": [
                {"asset_id": "ast_signal", "panels": ["A"], "why": "Controlled drift test"}
            ],
            "limits": ["Only simulated noise"],
        },
        {
            "claim": "The feedback component explains the improvement",
            "refs": [2],
            "experiment": "Ablation",
            "comparison_or_control": "Feedback on vs off",
            "measurement": "Absolute drift in mV",
            "figures": [{"asset_id": "ast_signal", "panels": ["B"], "why": "Mechanism ablation"}],
            "limits": ["No field validation"],
        },
    ],
    "limitations": ["Stable reference assumed", "No field validation"],
    "open_questions": ["Does the filter work with reference drift?"],
}

READING = {
    "asset_id": "ast_signal",
    "shows": "Two controlled sensor experiments",
    "panels": [
        {
            "label": "A",
            "shows": "Noise test",
            "axes": "Noise vs drift (mV)",
            "legend": "Blue adaptive; gray fixed",
            "box": [0.0, 0.0, 0.5, 1.0],
        },
        {
            "label": "B",
            "shows": "Ablation",
            "axes": "Feedback vs drift (mV)",
            "legend": "On vs off",
            "box": [0.5, 0.0, 0.5, 1.0],
        },
    ],
    "comparison": "Adaptive vs fixed; feedback on vs off",
    "key_observation": "Adaptive and feedback-on conditions have less drift",
    "supports_claim": "Supports both controlled results; no field conclusion",
    "legible": True,
    "uncertain": [],
    "confirmed_values": ["2 mV adaptive vs 6 mV fixed under changing noise"],
}

ANSWERS = {
    "question_and_contribution": "Adaptive filter against drift",
    "method_or_experiment": "Reference error adjusts filter gain",
    "figures_and_controls": "Panels A/B; adaptive/fixed and feedback on/off",
    "evidence_for_conclusions": "Noise challenge and controlled ablation",
    "assumptions_limits_and_questions": "Stable reference; simulated noise; no field validation",
}

DECK = """<!-- layout: title; design: v2; theme: paper -->
# Learning the adaptive filter study
Feedback reduces drift in controlled synthetic experiments [1]

---
<!-- layout: process -->
## How the filter works
1. **Measure error**: Compare the sensor stream with a stable reference [1]
2. **Update gain**: Feed reference error back into the filter [1]

---
<!-- layout: figure -->
## Noise challenge and feedback ablation
![Controlled experiments](gunther-asset:ast_signal)
Adaptive filtering reduces drift; disabling feedback weakens it [1][2]
- A: fixed vs adaptive; x is noise, y is drift in mV; blue means adaptive [1]
- B: feedback on vs off tests the proposed mechanism [2]
- These are simulated conditions; no field validation [2]

---
<!-- layout: points -->
## What remains unresolved
- **Assumption**: A stable reference is required [1]
- **Open question**: Performance with reference drift is unknown [2]
"""
NO_IMAGE = (
    DECK.replace("<!-- layout: figure -->", "<!-- layout: points -->")
    .replace("![Controlled experiments](gunther-asset:ast_signal)\n", "")
    .replace("Adaptive filtering reduces drift; disabling feedback weakens it [1][2]\n", "")
)


def science_pool(*, figures=True, warning=False):
    pool = Pool(50)
    for n, text in enumerate(
        [
            "The filter feeds error against a stable reference back into its gain. Under changing "
            "noise adaptive drift was 2 mV vs 6 mV for the fixed filter; "
            "simulated conditions only.",
            "A second experiment disabled feedback. Drift increased relative to feedback on. "
            "No field validation was performed; reference drift remains an open question.",
        ],
        1,
    ):
        if warning and n == 1:
            text += " The unreliable peak was 12\ufffd5 mV."
        warnings = (
            [{"kind": "uncertain_glyph", "reason": "Damaged peak value"}]
            if warning and n == 1
            else []
        )
        cite = ConversationCitationOut(
            id=f"cit{n}",
            source_id="src_study",
            source_title="Synthetic filter study",
            quote=text,
            locator=f"p. {n}",
            status="provisional",
            confidence=1,
            source_revision_id="rev_study",
            block_id=f"block{n}",
            extraction_warnings=warnings,
            figures=[
                {
                    "assetId": "ast_signal",
                    "elementId": "el_signal",
                    "caption": "Controlled experiments",
                    "panels": ["A", "B"],
                    "revisionId": "rev_study",
                    "page": 2,
                    "bbox": [0, 0, 100, 100],
                    "quality": {"cropCheck": {"state": "complete"}},
                }
            ]
            if figures
            else [],
            source_metadata={
                "title": "Learning the adaptive filter study",
                "authors": "A. Researcher, B. Collaborator",
                "year": 2026,
            },
        )
        pool.adopt(
            Evidence(
                "library",
                cite.source_title,
                text,
                payload=(cite, None),
                extraction_warnings=tuple(warnings),
            )
        )
    return pool


def pixels(tmp_path):
    """Draw two independent experiments with visible axes and labels, entirely in code."""
    path = tmp_path / "signal.png"
    image = Image.new("RGB", (1000, 480), "white")
    draw = ImageDraw.Draw(image)
    for x, label in [(40, "A - Noise challenge"), (540, "B - Feedback ablation")]:
        draw.text((x, 20), label, fill="black")
        draw.line([(x + 40, 60), (x + 40, 380), (x + 380, 380)], fill="black", width=3)
        draw.text((x, 42), "Drift (mV)", fill="black")
        draw.rectangle((x + 100, 180, x + 180, 380), fill="gray")
        draw.rectangle((x + 230, 310, x + 310, 380), fill="blue")
        draw.text((x + 90, 400), "fixed/off", fill="black")
        draw.text((x + 220, 400), "adaptive/on", fill="blue")
    image.save(path)
    return lambda _asset: (path, "image/png", "signal.png")


def science_router(study=None, *, first=DECK, revised=DECK, reading=READING, read_error=False):
    study = deepcopy(study or STUDY)

    def reply(request):
        task = request_text(request).split(P.TASK_BOUNDARY)[-1]
        if "You own the presentation purpose decision" in task:
            return json.dumps(
                {"purpose": "explain_study", "reason": "User asks to learn the study"}
            )
        if task.startswith("Build the study map"):
            return json.dumps(study)
        if task.startswith("Make the journal-club coverage plan"):
            return json.dumps(
                {
                    "method_teaching": [
                        "Reference error updates filter gain",
                        "Changing-noise challenge precedes feedback ablation",
                    ],
                    "result_dependencies": [
                        "The noise challenge motivates the feedback-on/off ablation"
                    ],
                    "figures": [
                        {
                            "asset_id": "ast_signal",
                            "central": True,
                            "disposition": "teach",
                            "panels": ["A", "B"],
                            "teaching_role": "Both controlled experiments",
                            "reason": "",
                        }
                    ]
                    if any(claim.get("figures") for claim in study["claims"])
                    else [],
                }
            )
        if task.startswith("Read the attached original source figure pixels"):
            if read_error:
                raise ModelError("Synthetic image read failed")
            assert any(
                part["type"] == "image_url"
                for message in request["messages"]
                if isinstance(message["content"], list)
                for part in message["content"]
            )
            return json.dumps(reading)
        if task.startswith("Plan a scientific explanation deck"):
            assert "ast_signal" in task or not any(c.get("figures") for c in study["claims"])
            return json.dumps(
                {
                    "title": "Learning the adaptive filter study",
                    "slides": [
                        {"title": "Learning the study", "layout": "title"},
                        {
                            "title": "How the filter works",
                            "layout": "process",
                            "purpose": "Teach mechanism",
                            "role": "Method enabling the result",
                            "refs": [1],
                            "takeaway": "Error adjusts gain",
                        },
                        {
                            "title": "Noise challenge and ablation",
                            "layout": "figure",
                            "refs": [1, 2],
                            "purpose": "Explain both controlled experiments",
                            "role": "Evidence for contribution",
                            "figures": study["claims"][0].get("figures", []),
                            "takeaway": "Feedback matters",
                        },
                        {"title": "What remains unresolved", "layout": "points", "refs": [1, 2]},
                    ],
                }
            )
        if task.startswith("Scientific review of the complete argument"):
            return json.dumps(
                {
                    "verdict": "ready",
                    "understood": "The controlled study",
                    "answers": ANSWERS,
                    "problems": [],
                }
            )
        if task.startswith("You are an independent learner"):
            return json.dumps(
                {
                    "question_and_contribution": {
                        "answer": "Adaptive filtering addresses changing-noise drift",
                        "evidence": [1, 3],
                    },
                    "method_or_experiment": {
                        "answer": "Reference error updates filter gain",
                        "evidence": [2],
                    },
                    "figures_and_controls": {
                        "answer": "Panels A/B compare adaptive/fixed and feedback on/off",
                        "evidence": [3],
                    },
                    "evidence_for_conclusions": {
                        "answer": "Noise challenge and ablation support the contribution",
                        "evidence": [3],
                    },
                    "assumptions_limits_and_questions": {
                        "answer": "Stable reference assumed; no field validation",
                        "evidence": [3, 4],
                    },
                    "unexplained": [],
                }
            )
        if task.startswith("Compare an independent learner"):
            return json.dumps(
                {
                    "understood": "The learner reconstructed the controlled study",
                    "verdict": "ready",
                    "coverage_problems": [],
                    "learner_problems": [],
                }
            )
        if task.startswith("Write the scientific explanation deck"):
            return first
        if task.startswith("Revise only the scientific slides"):
            return revised
        if task.startswith("You are working on one slide of an existing scientific"):
            return fmt.split_slides(DECK)[1]
        if written := re.search(r"Slide \d+ as written:\n(.*?)\n\nRewrite it now\.", task, re.S):
            # A talk deck's slide fixed for the format: the presenter's notes are added.
            return written.group(1).strip() + "\nNote: The presenter walks through it."
        return router(request)

    return reply


def make(
    tmp_path,
    *,
    study=None,
    figures=True,
    warning=False,
    first=DECK,
    revised=DECK,
    writer=None,
    design=False,
    read_error=False,
):
    writer = writer or model()
    fake = FakeProvider(science_router(study, first=first, revised=revised, read_error=read_error))
    agents = StudioAgents(
        gateway(fake, writer), use="read", design=design, asset_reader=pixels(tmp_path)
    )
    saved = {}
    built = agents.make(
        writer,
        "low",
        Spec("slides", "Researchers", brief="Explain this study"),
        science_pool(figures=figures, warning=warning),
        Notes(),
        checkpoint=saved.update,
    )
    return built, agents, fake, saved


def test_multi_experiment_map_pixels_story_writer_revision_and_design(tmp_path):
    built, agents, fake, saved = make(tmp_path, first=NO_IMAGE, design=True)
    assert built.approach["purpose"] == "explain_study"
    assert len(built.approach["study_map"]["claims"]) == 2
    assert built.approach["figure_readings"][0]["panels"][1]["label"] == "B"
    tasks = [request_text(r).split(P.TASK_BOUNDARY)[-1] for r in fake.requests]
    assert sum(t.startswith("Revise only the scientific slides") for t in tasks) == 1
    for marker in ("Plan a scientific", "Write the scientific"):
        prompt = next(t for t in tasks if t.startswith(marker))
        assert "ast_signal" in prompt and "Actual FigureReadings" in prompt
        assert "Ablation" in prompt or "ablation" in prompt
    assert "gunther-asset:ast_signal" in built.content
    figure = next(s for s in fmt.split_slides(built.content) if "gunther-asset:" in s)
    assert not fmt.design_of(figure)  # source image stays in the image-capable template
    assert "x is noise" in figure and "no field validation" in figure
    assert saved["purpose"]["purpose"] == "explain_study" and saved["study_map"]
    assert built.approach["review_status"] == "ready"
    assert built.approach["allowed_assets"] == ["ast_signal"]
    assert not any("lead analyst" in t or "presentation strategist" in t for t in tasks)
    assert not fmt.check_deck(built.content, read=True, notes_required=False, scientific=True)


def test_methods_benchmark_uses_mechanism_and_evaluation_instead_of_consulting_memo(tmp_path):
    study = deepcopy(STUDY)
    study["study_type"] = "methods"
    study["contribution"] = "A reference feedback algorithm evaluated against fixed filtering"
    study["claims"][1]["experiment"] = "Benchmark across noise conditions"
    built, _, fake, _ = make(tmp_path, study=study)
    assert built.approach["study_map"]["study_type"] == "methods"
    story = next(
        request_text(r).split(P.TASK_BOUNDARY)[-1]
        for r in fake.requests
        if "Plan a scientific explanation deck" in request_text(r)
    )
    assert "mechanism and evaluation" in story and "Benchmark across noise conditions" in story


def test_no_appropriate_figure_has_no_image_demand(tmp_path):
    study = deepcopy(STUDY)
    for claim in study["claims"]:
        claim["figures"] = []
    built, _, fake, _ = make(tmp_path, study=study, figures=False, first=NO_IMAGE)
    assert not built.approach["figure_readings"]
    assert not any("Revise the complete scientific deck" in request_text(r) for r in fake.requests)
    assert not S.content_problems(S.StudyMap.model_validate(study), [], NO_IMAGE)


def test_uncertain_value_is_omitted_after_review_and_warning_is_kept_internally(tmp_path):
    damaged = DECK.replace("Adaptive filtering reduces drift;", "Peak drift is 12.5 mV [1];")
    built, _, fake, _ = make(tmp_path, warning=True, first=damaged)
    assert "12.5" not in built.content and "\ufffd" not in built.content
    assert any("extraction warnings" in s for s in built.approach["limitations"])
    assert any("Extraction warnings:" in request_text(r) for r in fake.requests)
    assert not any("proofread" in line.lower() for line in built.content.splitlines())
    assert any("Unconfirmed value" in request_text(r) for r in fake.requests)


@pytest.mark.parametrize("failure", ["blind", "failed_read", "no_pixels", "missing_file"])
def test_unavailable_vision_never_invents_a_reading(tmp_path, failure):
    from gunther.asset_service import AssetNotFoundError

    def missing(_asset_id):
        raise AssetNotFoundError("The original file is unavailable")

    fake = FakeProvider(science_router(first=NO_IMAGE, read_error=failure == "failed_read"))
    writer = model("deepseek-v4-pro") if failure == "blind" else model()
    reader = {"no_pixels": None, "missing_file": missing}.get(failure, pixels(tmp_path))
    agents = StudioAgents(gateway(fake, writer), use="read", design=False, asset_reader=reader)
    built = agents.make(
        writer, "low", Spec("slides", "Readers", brief="Explain the study"), science_pool(), Notes()
    )
    assert built.approach["figure_readings"] == []
    assert any("not read" in reason for reason in built.approach["limitations"])
    if failure not in ("failed_read",):
        assert not any(
            "Read the attached original source figure pixels" in request_text(r)
            for r in fake.requests
        )


def test_repeated_content_failure_stops_before_design(tmp_path):
    fake = FakeProvider(science_router(first=NO_IMAGE, revised=NO_IMAGE))
    agents = StudioAgents(gateway(fake), use="read", asset_reader=pixels(tmp_path))
    with pytest.raises(ModelError, match="after revision"):
        agents.make(
            model(),
            "low",
            Spec("slides", "Readers", brief="Explain the study"),
            science_pool(),
            Notes(),
        )
    assert not any("Choose the visual style" in request_text(r) for r in fake.requests)


def test_failed_purpose_or_review_is_not_replaced_by_a_general_or_ready_answer(tmp_path):
    with pytest.raises(ModelError, match="purpose unavailable"):
        StudioAgents(gateway(FakeProvider(ModelError("purpose unavailable")))).make(
            model(), "low", Spec("slides", "Readers"), science_pool(), Notes()
        )
    route = science_router()

    def failing(request):
        if request_text(request).split(P.TASK_BOUNDARY)[-1].startswith("Scientific review"):
            raise ModelError("review unavailable")
        return route(request)

    # An unavailable review is recorded as such with the deck, never as a ready verdict.
    agents = StudioAgents(gateway(FakeProvider(failing)), use="read", asset_reader=pixels(tmp_path))
    built = agents.make(model(), "low", Spec("slides", "Readers"), science_pool(), Notes())
    assert any("review-ready" in reason for reason in built.approach["limitations"])
    assert built.approach["review_status"] == "unavailable"
    assert any(s["label"] == "Scientific review unavailable" for s in agents.skipped)
    assert not any("answers" in review for review in built.approach["reviews"])


def test_unselected_figure_link_is_rejected_before_pixels_are_read(tmp_path):
    study = deepcopy(STUDY)
    study["claims"][0]["figures"][0]["asset_id"] = "ast_unselected"
    fake = FakeProvider(science_router(study))
    with pytest.raises(ModelError, match="not retained with the passages"):
        StudioAgents(gateway(fake), asset_reader=pixels(tmp_path)).make(
            model(), "low", Spec("slides", "Readers"), science_pool(), Notes()
        )
    # The wrong link was sent back once with what is wrong, then the step failed.
    maps = [r for r in fake.requests if "Build the study map" in request_text(r)]
    assert len(maps) == 2 and "ast_unselected" in request_text(maps[1])
    assert not any("Read the attached original" in request_text(r) for r in fake.requests)


def test_clean_and_pixel_confirmation_are_scoped_and_damaged_glyphs_never_pass():
    pool = science_pool(warning=True)
    bad = "<!-- layout: points -->\n## Result\n- Peak is 12.5 mV [1]"
    assert S.value_problems(bad, pool.items, [])
    same = replace(
        pool.items[0],
        ref=3,
        text="Peak is 12.5 mV",
        extraction_warnings=(),
        payload=(pool.items[0].payload[0].model_copy(update={"extraction_warnings": []}), None),
    )
    assert not S.value_problems(bad, [*pool.items, same], [])
    reading = S.FigureReading.model_validate({**READING, "confirmed_values": ["12.5 mV peak"]})
    assert not S.value_problems(bad, pool.items, [reading])
    assert S.value_problems(bad, pool.items, [reading.model_copy(update={"asset_id": "other"})])
    assert S.value_problems("Peak 12\ufffd5 [1]", [*pool.items, same], [reading])
    # Numbers the damaged passage states cleanly stay usable.
    assert not S.value_problems("- Drift was 2 mV vs 6 mV [1]", pool.items, [])


def test_review_problems_left_after_revision_are_recorded_with_the_deck(tmp_path):
    route = science_router()

    def picky(request):
        task = request_text(request).split(P.TASK_BOUNDARY)[-1]
        if task.startswith("Scientific review of the complete argument"):
            return json.dumps(
                {
                    "verdict": "revise",
                    "understood": "The controlled study",
                    "answers": ANSWERS,
                    "problems": [
                        {"where": "slide 3", "problem": "The control is unnamed", "fix": "Name it"}
                    ],
                }
            )
        return route(request)

    fake = FakeProvider(picky)
    agents = StudioAgents(gateway(fake), use="read", asset_reader=pixels(tmp_path))
    built = agents.make(
        model(),
        "low",
        Spec("slides", "Readers", brief="Explain the study"),
        science_pool(),
        Notes(),
    )
    # One targeted revision; what the reviewer still finds goes with the deck.
    revisions = [
        r for r in fake.requests if "Revise only the scientific slides" in request_text(r)
    ]
    assert len(revisions) == 1 and "The control is unnamed" in request_text(revisions[0])
    assert "Review: slide 3: The control is unnamed" in built.approach["limitations"]
    assert built.approach["review_status"] == "open_problems"
    assert "gunther-asset:ast_signal" in built.content


def test_an_edit_that_states_a_damaged_value_is_asked_again_then_fails(tmp_path):
    from gunther.artifact_workspace import ConversationBrief, Work, WorkSpec
    from gunther.studio.work import StudioWorkAgents, load_built

    built, _, _, _ = make(tmp_path)
    conversation = Pool(100)
    work = Work(key="deck", spec=WorkSpec(kind="slides"))
    load_built(work, built, science_pool(warning=True), conversation, ConversationBrief())
    parts = [work.parts[i.id].text for i in work.outline]
    damaged = parts[1] + "\n- The peak was 12.8 mV [1]"
    replies = [damaged, parts[1], damaged, damaged]
    asked = []

    def edit(request):
        task = request_text(request).split(P.TASK_BOUNDARY)[-1]
        if task.startswith("You are working on one slide"):
            asked.append(task)
            return replies.pop(0)
        raise AssertionError(task[:200])

    agents = StudioWorkAgents(gateway(FakeProvider(edit)), "slides")
    agents.use = "read"
    agents.scientific_context = work.approach

    def write():
        return agents.write_part(
            model(), "low", Spec("slides", "Readers", brief="Explain the study"), parts,
            [i.heading for i in work.outline], 1, conversation, instruction="Add the peak",
        )  # fmt: skip

    assert write() == parts[1]
    assert "12.8" in asked[1] and "Fix this" in asked[1]
    with pytest.raises(ModelError, match="breaks a scientific rule"):
        write()


def test_edit_keeps_scientific_purpose_map_readings_and_reference_numbers(tmp_path):
    from gunther.artifact_workspace import ConversationBrief, Work, WorkSpec
    from gunther.studio.work import StudioWorkAgents, load_built

    built, _, _, _ = make(tmp_path)
    made = science_pool()
    conversation = Pool(100)
    conversation.adopt(Evidence("web", "Earlier context", "Unrelated context"))
    work = Work(key="deck", spec=WorkSpec(kind="slides"))
    load_built(work, built, made, conversation, ConversationBrief())
    assert work.approach["purpose"] == "explain_study"
    assert work.approach["study_map"]["claims"][0]["refs"] == [2]
    parts = [work.parts[i.id].text for i in work.outline]
    rewritten = parts[1].replace("Feed reference error back", "Use reference error to update gain")
    route = science_router()

    def edit(request):
        task = request_text(request).split(P.TASK_BOUNDARY)[-1]
        if task.startswith("You are working on one slide"):
            assert "Scientific evidence rules" in task and "Actual FigureReadings" in task
            assert '"study_type":"experimental"' in task
            return rewritten
        return route(request)

    agents = StudioWorkAgents(gateway(FakeProvider(edit)), "slides")
    agents.use = "read"
    agents.scientific_context = work.approach
    result = agents.write_part(
        model(),
        "low",
        Spec("slides", "Readers", brief="Explain method more"),
        parts,
        [i.heading for i in work.outline],
        1,
        conversation,
        instruction="Explain method more",
    )
    assert "Use reference error" in result
    assert work.approach["purpose"] == "explain_study"


def test_scientific_eval_uses_completeness_not_business_conventions(tmp_path):
    from evals.quality.judge import judge_absolute
    from evals.quality.lint import lint

    built, _, _, _ = make(tmp_path)
    defects = lint(
        "slides",
        built.content,
        "read",
        purpose="explain_study",
        study_map=STUDY,
        coverage_plan=built.approach["coverage_plan"],
        source_figures=["ast_signal"],
        figure_readings=[READING],
    )
    assert not any(d.rule == "topic_title" for d in defects)
    missing = lint(
        "slides",
        NO_IMAGE,
        "read",
        purpose="explain_study",
        study_map=STUDY,
        coverage_plan=built.approach["coverage_plan"],
        source_figures=["ast_signal"],
        figure_readings=[READING],
    )
    assert any(d.rule == "missing_source_figure" for d in missing)
    fake = FakeProvider(
        json.dumps(
            {
                "insight": 8,
                "storyline": 8,
                "titles": 8,
                "evidence": 8,
                "clarity": 8,
                "correctness": 8,
                "sections": [],
                "summary": "Study explained",
            }
        )
    )
    result, error = judge_absolute(
        gateway(fake),
        model(),
        kind="slides",
        brief="Explain study",
        audience="Readers",
        use="read",
        content=DECK,
        citations=[],
        purpose="explain_study",
        study_map=STUDY,
    )
    assert result and not error
    prompt = request_text(fake.requests[0])
    assert "five reader questions" in prompt and "topic titles are acceptable" in prompt


def test_selected_revision_figures_quality_and_withheld_formulas_reach_evidence():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from gunther.artifact_workspace import evidence_from_citation
    from gunther.database import Base
    from gunther.models import (
        Asset,
        BlockElementMap,
        ContentBlock,
        DocumentElement,
        Source,
        SourceIndexHead,
        SourceRevision,
    )
    from gunther.service import KnowledgeService
    from gunther.skill_checks import figures_of

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for name in ("selected", "excluded"):
            source_id, revision = f"src_{name}", f"rev_{name}"
            session.add(
                Source(id=source_id, title=name, kind="paper", content=name, content_hash=name)
            )
            session.add(
                SourceRevision(
                    id=revision, source_id=source_id, fingerprint=name, parser="synthetic"
                )
            )
            session.add(SourceIndexHead(source_id=source_id, revision_id=revision))
            session.add(
                Asset(
                    id=f"ast_{name}",
                    content_hash=name,
                    original_name="figure.png",
                    media_type="image/png",
                    size_bytes=1,
                    relative_path=f"{name}.png",
                )
            )
            for ordinal, text, extra in [
                (
                    1,
                    "Noise experiment; uncertain value 7\ufffd2.",
                    {"inlineSpans": [{"start": 33, "end": 34, "mark": "uncertain"}]},
                ),
                (
                    4,
                    "The ablation compares feedback on and off at 12 Hz.",
                    {"glyphCheck": "unverified"},
                ),
            ]:
                block_id, element_id = f"b_{name}_{ordinal}", f"e_{name}_{ordinal}"
                anchor = json.dumps({"page": 2, "bboxPpm": [10, 20, 300, 200]})
                payload = json.dumps(extra)
                session.add(
                    ContentBlock(
                        id=block_id,
                        revision_id=revision,
                        ordinal=ordinal,
                        kind="paragraph",
                        content=text,
                        locator="p. 2",
                        anchor_json=anchor,
                        payload_json=payload,
                    )
                )
                session.add(
                    DocumentElement(
                        id=element_id,
                        revision_id=revision,
                        ordinal=ordinal,
                        kind="paragraph",
                        content=text,
                        locator="p. 2",
                        anchor_json=anchor,
                        payload_json=payload,
                    )
                )
                session.add(
                    BlockElementMap(
                        block_id=block_id, element_id=element_id, char_start=0, char_end=len(text)
                    )
                )
            session.add(
                DocumentElement(
                    id=f"equation_{name}",
                    revision_id=revision,
                    ordinal=2,
                    kind="equation",
                    content="",
                    locator="p. 2",
                    anchor_json='{"page":2}',
                    payload_json=json.dumps(
                        {
                            "transcription": "unreliable",
                            "transcriptionIssue": "two-dimensional layout",
                            "textLayer": "unchecked formula",
                            "noEmbed": True,
                        }
                    ),
                )
            )
            session.add(
                DocumentElement(
                    id=f"figure_{name}",
                    revision_id=revision,
                    ordinal=3,
                    kind="figure",
                    content="Panels A and B",
                    locator="p. 2",
                    anchor_json='{"page":2,"bboxPpm":[10,20,300,200]}',
                    payload_json=json.dumps(
                        {
                            "imageAssetId": f"ast_{name}",
                            "cropCheck": {"state": "clipped", "reason": "Legend may be incomplete"},
                        }
                    ),
                )
            )
        session.flush()
        pool = Pool(50)
        service = KnowledgeService.__new__(KnowledgeService)
        service._studio_reading(session, ["src_excluded"], pool)
        assert figures_of(pool.items[0])[0]["assetId"] == "ast_excluded"
        # Earlier conversation evidence cannot leak into a narrowed build scope.
        service._studio_reading(session, ["src_selected"], pool)
        assert pool.items and {e.title for e in pool.items} == {"selected"}
        evidence = pool.items[0]
        figure = figures_of(evidence)[0]
        assert figure["assetId"] == "ast_selected" and figure["caption"] == "Panels A and B"
        assert figure["revisionId"] == "rev_selected" and figure["region"] == [10, 20, 300, 200]
        assert figure["quality"]["cropCheck"]["state"] == "clipped"
        kinds = {w["kind"]: w for w in evidence.extraction_warnings}
        assert set(kinds) == {"uncertain_glyph", "withheld_formula", "unverified_glyphs"}
        assert kinds["unverified_glyphs"]["numbers"] == ["12"]
        # The unverified number cannot be stated; the clean one can.
        assert S.trusted_numbers(evidence) == set()
        assert S.value_problems(f"- Drift fell at 12 Hz [{evidence.ref}]", [evidence], [])
        assert "unchecked formula" not in evidence.text
        restored = evidence_from_citation(evidence.payload[0])
        assert restored.extraction_warnings == evidence.extraction_warnings
        serialized = StudioAgents._passage(restored)
        assert "gunther-asset:ast_selected" in serialized and "ast_excluded" not in serialized
        assert "Extraction warnings:" in serialized and "two-dimensional layout" in serialized
    engine.dispose()


def test_equal_length_pool_passage_keeps_new_figure_metadata():
    pool = science_pool(figures=False)
    richer = science_pool(figures=True)
    held = pool.adopt(richer.items[0])
    assert held.ref == 1 and held.payload[0].figures


def test_figure_in_notes_does_not_satisfy_the_visible_content_gate():
    note_only = NO_IMAGE + "\nNote: ![Controlled experiments](gunther-asset:ast_signal)"
    assert S.content_problems(
        S.StudyMap.model_validate(STUDY), [S.FigureReading.model_validate(READING)], note_only
    )


def test_scientific_design_cannot_drop_a_method_explanation_with_no_numbers():
    from gunther.studio import design as D

    slide = (
        "<!-- layout: points -->\n## Feedback mechanism\n"
        "- Compare sensor error with a stable reference [1]\n"
        "- Update the gain from that error [1]"
    )
    shortened = '<section class="slide"><h2>Feedback mechanism</h2><p>Update gain [1]</p></section>'
    fake = FakeProvider(shortened)
    agents = StudioAgents(gateway(fake))
    agents.scientific_context = {"purpose": "explain_study"}
    result, count, limitations = D.design_slides(agents, model(), [slide], "light", workers=1)
    assert result == [slide] and count == 0
    assert "full scientific explanation" in limitations[0]["reason"]


def test_explicit_business_purpose_uses_the_existing_general_prompts(tmp_path):
    fake = FakeProvider(router)
    agents = StudioAgents(gateway(fake), use="talk", design=False)
    # A different requested purpose wins even with study-like material in scope.
    from test_studio_pipeline import pool as general_pool

    built = agents.make(
        model(),
        "low",
        Spec("slides", "Board", brief="Make a business decision deck"),
        general_pool(),
        Notes(),
    )
    assert built.approach["purpose"] == "general"
    tasks = [request_text(r).split(P.TASK_BOUNDARY)[-1] for r in fake.requests]
    assert any("lead analyst" in t for t in tasks)
    assert any("presentation strategist" in t for t in tasks)
    assert not any(t.startswith("Build the study map") for t in tasks)


def test_artifact_conversation_edit_keeps_the_study_workflow(tmp_path):
    from fake_models import orchestrator_replies
    from test_artifact_agent import done, kept, turn
    from test_outputs import app_for, library, workspace

    study = deepcopy(STUDY)
    for claim in study["claims"]:
        claim["figures"] = []
    route = science_router(study, first=NO_IMAGE)

    def inner(request):
        task = request_text(request).split(P.TASK_BOUNDARY)[-1]
        if "knowledge assertions" in task:
            return '{"assertions": []}'
        if task.startswith("You are working on one slide"):
            assert "Scientific evidence rules" in task
            return fmt.split_slides(NO_IMAGE)[1].replace(
                "Feed reference error back", "Use reference error"
            )
        return route(request)

    fake = FakeProvider(
        orchestrator_replies(
            [
                {
                    "actions": [
                        {"do": "select", "artifact": "new:slides"},
                        {"do": "make"},
                        {"do": "save", "text": "Explained the study."},
                    ]
                }
            ],
            inner,
        )
    )
    with app_for(tmp_path, fake) as client:
        client.app.state.knowledge_service.output_method = "studio"
        base_id, _ = library(
            client, notes={f"Experiment {i}": e.text for i, e in enumerate(science_pool().items, 1)}
        )
        client.post(
            f"/api/knowledge-bases/{base_id}/artifact-conversations",
            headers=workspace(client),
            json={"id": "study", "title": "Study explanation"},
        )
        result = done(turn(client, base_id, "study", "Explain the study as a reading deck"))
        assert result["outcome"] == "saved", json.dumps(result, ensure_ascii=False)
        held, _ = kept(client, "study")
        assert held.works[0].approach["purpose"] == "explain_study"
        fake.replies[:] = [
            orchestrator_replies(
                [
                    {
                        "actions": [
                            {"do": "write", "parts": [2], "instruction": "Explain the method more"},
                            {"do": "save", "text": "Explained the method."},
                        ]
                    }
                ],
                inner,
            )
        ]
        result = done(turn(client, base_id, "study", "Explain the method more"))
        assert result["outcome"] == "saved", json.dumps(result, ensure_ascii=False)
        assert "Use reference error" in result["artifact"]["content"]
        held, _ = kept(client, "study")
        assert held.works[0].approach["purpose"] == "explain_study"


@pytest.mark.parametrize("purpose", ["general", "explain_study"])
def test_only_a_study_deck_is_held_to_the_scientific_rules_when_edited(purpose):
    from gunther.artifact_steps import WorkSteps
    from gunther.artifact_workspace import ConversationBrief, Work, WorkSpec
    from gunther.studio.work import StudioWorkAgents

    agents = StudioWorkAgents(gateway(FakeProvider("unused")), "slides")
    work = Work(key="deck", spec=WorkSpec(kind="slides"))
    work.approach = {"purpose": purpose, "study_map": STUDY if purpose != "general" else None}
    steps = WorkSteps(
        agents, work, ConversationBrief(), Pool(10), Notes(), agents.gateway.models[0], None, None
    )
    steps._load()
    assert bool(agents.scientific_context) == (purpose == "explain_study")


def test_the_app_receives_the_scientific_record_and_other_decks_do_not(tmp_path):
    from gunther.schemas import OutputApproachOut

    built, *_ = make(tmp_path)
    shown = OutputApproachOut.model_validate(built.approach).model_dump(by_alias=True)
    assert shown["studyMap"]["claims"] and shown["figureReadings"][0]["asset_id"] == "ast_signal"
    assert shown["reviewStatus"] == "ready" and isinstance(shown["limitations"], list)
    assert shown["purposeReason"]
    general = OutputApproachOut.model_validate({"method": "studio", "purpose": "general"})
    assert not {"studyMap", "figureReadings", "limitations", "reviewStatus"} & set(
        general.model_dump(by_alias=True)
    )


def test_blank_model_answers_are_not_success(tmp_path):
    with pytest.raises(ValueError):
        S.StudyMap.model_validate({**STUDY, "claims": []})
    with pytest.raises(ValueError):
        S.FigureReading.model_validate({**READING, "key_observation": " "})
    assert not S.FigureReading.model_validate(
        {**READING, "legible": False, "key_observation": ""}
    ).legible
    route = science_router()

    def blank(request):
        task = request_text(request).split(P.TASK_BOUNDARY)[-1]
        if task.startswith("Scientific review of the complete argument"):
            answers = dict.fromkeys(ANSWERS, "")
            return json.dumps({"verdict": "ready", "answers": answers, "problems": []})
        return route(request)

    agents = StudioAgents(gateway(FakeProvider(blank)), use="read", asset_reader=pixels(tmp_path))
    built = agents.make(
        model(),
        "low",
        Spec("slides", "Readers", brief="Explain the study"),
        science_pool(),
        Notes(),
    )
    # Empty answers are asked for again; still empty, the deck is marked not reviewed.
    assert built.approach["review_status"] == "unavailable"


def test_a_saved_study_map_is_reused_only_for_the_same_passages(tmp_path):
    built, agents, fake, saved = make(tmp_path)
    other = science_pool()
    first = other.items[0]
    other.items[0] = replace(
        first,
        payload=(first.payload[0].model_copy(update={"block_id": "other_block"}), None),
    )
    assert S.pool_print(other.items) != saved["pool"]
    fake.requests.clear()
    agents.make(
        model(),
        "low",
        Spec("slides", "Researchers", brief="Explain this study"),
        science_pool(),
        Notes(),
        resume=saved,
    )
    assert not any("Build the study map" in request_text(r) for r in fake.requests)
    fake.requests.clear()
    other_pool = Pool(50)
    for item in other.items:
        other_pool.adopt(item)
    agents.make(
        model(),
        "low",
        Spec("slides", "Researchers", brief="Explain this study"),
        other_pool,
        Notes(),
        resume=saved,
    )
    assert any("Build the study map" in request_text(r) for r in fake.requests)


def test_an_edit_may_only_show_the_builds_own_images(tmp_path):
    from gunther.artifact_workspace import ConversationBrief, Work, WorkSpec
    from gunther.studio.work import StudioWorkAgents, load_built

    built, _, _, _ = make(tmp_path)
    conversation = Pool(100)
    earlier = science_pool().items[0]
    stray = earlier.payload[0].model_copy(
        update={
            "source_id": "src_other",
            "block_id": "other",
            "figures": [{**earlier.payload[0].figures[0], "assetId": "ast_other"}],
        }
    )
    conversation.adopt(replace(earlier, payload=(stray, None)))
    work = Work(key="deck", spec=WorkSpec(kind="slides"))
    load_built(work, built, science_pool(), conversation, ConversationBrief())
    parts = [work.parts[i.id].text for i in work.outline]
    elsewhere = parts[1] + "\n![Other study](gunther-asset:ast_other)"
    replies = [elsewhere, elsewhere]

    def edit(request):
        return replies.pop(0)

    agents = StudioWorkAgents(gateway(FakeProvider(edit)), "slides")
    agents.use = "read"
    agents.scientific_context = work.approach
    with pytest.raises(ModelError, match="outside the chosen material"):
        agents.write_part(
            model(), "low", Spec("slides", "Readers", brief="Explain the study"), parts,
            [i.heading for i in work.outline], 1, conversation, instruction="Add a figure",
        )  # fmt: skip


def test_finished_edits_are_checked_and_fitting_keeps_the_checks(tmp_path):
    from gunther.artifact_workspace import ConversationBrief, Work, WorkSpec
    from gunther.outputs import Draft
    from gunther.studio.work import StudioWorkAgents, load_built

    built, _, _, _ = make(tmp_path)
    conversation = Pool(100)
    work = Work(key="deck", spec=WorkSpec(kind="slides"))
    load_built(work, built, science_pool(warning=True), conversation, ConversationBrief())
    parts = [work.parts[i.id].text for i in work.outline]
    agents = StudioWorkAgents(gateway(FakeProvider("unused")), "slides")
    agents.use = "read"
    agents.scientific_context = work.approach
    spec = Spec("slides", "Readers", brief="Explain the study")
    # The central figure removed by an edit is said with the version.
    without = [p for p in parts if "gunther-asset:" not in p]
    notes = agents.final_notes(spec, [], [Draft(p, [], {}) for p in without], conversation)
    assert any(n["step"] == "science" and "ast_signal" in n["reason"] for n in notes)
    # Fitting a slide may not bring in a damaged value.
    broken = "<!-- layout: points -->\n## Too short"
    damaged = "<!-- layout: points -->\n## What remains open\n- The peak was 12.8 mV [1]\n- Two"
    agents.gateway = gateway(FakeProvider(damaged))
    kept = agents.fit(model(), "low", spec, [], 2, conversation, [], None, "", None, broken)
    assert kept == broken
    clean = "<!-- layout: points -->\n## What remains open\n- Drift was 2 mV vs 6 mV [1]\n- Two"
    agents.gateway = gateway(FakeProvider(clean))
    assert (
        agents.fit(model(), "low", spec, [], 2, conversation, [], None, "", None, broken) == clean
    )


def test_another_reading_of_a_passage_keeps_its_figures():
    longer = science_pool(figures=False).items[0]
    longer = replace(longer, text=longer.text + " More context read around it.")
    pool = Pool(10)
    pool.adopt(longer)
    shorter = science_pool(figures=True, warning=True).items[0]
    kept = pool.adopt(shorter)
    assert kept.text == longer.text  # the longer reading stays
    assert kept.payload[0].figures and kept.extraction_warnings
    assert pool.items == [kept]


def test_text_beside_a_figure_fits_its_column():
    slide = fmt.parse_slide(
        "<!-- layout: figure -->\n## Training converges on held-out data\n"
        "![c](gunther-asset:ast_x)\n" + "a " * 60 + "\n- " + "b" * 85 + "\n- " + "c" * 95
    )
    problems = fmt.check_slide(slide, first=False, read=True)
    assert any("explanation is 95 units" in p for p in problems)
    assert any("beside the figure is 299 units" in p for p in problems)
