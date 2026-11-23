import json

import pytest
from fake_models import FakeProvider, gateway, model
from pydantic import ValidationError
from test_outputs import app_for, body, build, built, library, route, workspace
from test_studio_pipeline import DECK, PASSAGES, pool, request_text, router

from gunther.artifact_manifest import artifact_manifest_hash
from gunther.artifact_workspace import ConversationBrief, load_workspace, spec_of
from gunther.llm import ModelError
from gunther.models import Artifact
from gunther.outputs import Notes, Planned, Spec
from gunther.schemas import BuildOutputInput
from gunther.studio.format import SUMMARY_MARK
from gunther.studio.pipeline import StudioAgents
from gunther.studio.prompts import LANGUAGE_FALLBACK
from gunther.studio.state import ReportStory, Run, _validate_storyline
from gunther.studio.work import StudioWorkAgents


@pytest.mark.parametrize(
    ("style", "shape", "label", "recipe", "limit"),
    [
        ("report", "report", "Report", "Answer first.", 8),
        ("briefing", "report", "Briefing doc", "Set out the options, the evidence", 8),
        ("study_guide", "report", "Study guide", "Go from the basics to the advanced", 12),
        ("faq", "report", "FAQ", "Use one reader question as the heading", 16),
        ("timeline", "report", "Timeline", "Put events in chronological order", 16),
        ("slides", "slides", "Slides", "slides that repeat each other", 14),
    ],
)
def test_catalog_types_build_with_their_recipe_and_save_their_label(
    tmp_path, style, shape, label, recipe, limit,
):
    summary = style in ("report", "briefing")
    story = {
        "title": "Trial results",
        "summary_heading": "Results in brief" if summary else "",
        "sections": [{"heading": "Cooling use fell"}, {"heading": "Rates by district"}],
        "requested_count": 3 if summary else 2,
    }
    report = (
        "# Trial results\n"
        + ("\n## Results in brief\nCooling use fell by 5% [1].\n" if summary else "")
        + "\n## Cooling use fell\nThe trial cut cooling use by 5% [1].\n"
        + "\n## Rates by district\nThe rates were 4%, 5% and 6% [1]."
    )
    misplaced_marker = "<!-- block:summary -->"
    report = report.replace("## Rates by district\n", f"## Rates by district\n{misplaced_marker}\n")

    def reply(request):
        task = request_text(request)
        if "planning a written report" in task:
            return json.dumps(story)
        if "Write the report" in task:
            return report
        return router(request, deck=DECK.replace("[2]", "[1]"))

    fake = FakeProvider(reply)
    with app_for(tmp_path, fake) as client:
        headers = workspace(client)
        catalog = client.get("/api/output-types", headers=headers).raise_for_status().json()
        assert [item["id"] for item in catalog] == [
            "report", "briefing", "study_guide", "faq", "timeline", "slides",
        ]
        [item] = [row for row in catalog if row["id"] == style]
        assert (item["shape"], item["label"]) == (shape, label)
        assert item["description"] and item["icon"]
        assert item["partLimit"] == limit
        aliases = {
            "report": ["auto", "overview", "field_guide"],
            "briefing": ["decision_brief"],
            "study_guide": ["teaching_path"],
        }
        assert item["legacyStyles"] == aliases.get(style, [])
        options = ["focus", "audience", "length", "language"]
        assert item["options"] == options + (["use"] if shape == "slides" else [])

        passage = (
            " ".join(PASSAGES) if shape == "slides"
            else "Cooling use fell by 5%. Rates were 4%, 5% and 6%."
        )
        base, _ = library(client, notes={"Trial": passage})
        brief = f"Write exactly {story['requested_count']} sections" if shape == "report" else ""
        events = build(client, base, body(kind=shape, style=style, brief=brief))
        result = built(events)
        listed = client.get(f"/api/knowledge-bases/{base}/artifacts", headers=headers).json()
        reopened = client.get(route(base, result["id"]), headers=headers).raise_for_status().json()

    assert result == reopened
    assert (result["kind"], result["style"], result["outputType"]) == (shape, style, label)
    assert listed[0]["outputType"] == label
    tasks = [request_text(request) for request in fake.requests]
    critic = next(task for task in tasks if "demanding senior partner" in task)
    assert recipe in critic
    assert any(f"The outline can have up to {limit}" in task for task in tasks)
    assert any(section["checked"] for section in result["sections"])
    if shape == "report":
        planner = next(task for task in tasks if "planning a written report" in task)
        writer = next(task for task in tasks if "Write the report" in task)
        assert recipe in planner
        assert ("Executive summary first:" if style == "report" else recipe) in writer
        assert (SUMMARY_MARK in result["content"]) == summary
        assert misplaced_marker not in result["content"]
        assert any("add at least 1 exhibit" in task for task in tasks) == summary
        [preview] = [event["sections"] for name, event in events if name == "outline"]
        assert len(preview) == len(result["outline"]) == story["requested_count"]


@pytest.mark.parametrize(
    ("options", "memo_language", "expected_use", "expected_length", "expected_language"),
    [
        ({"use": "read", "length": "short", "language": "Italian"}, "English", "read",
         "5–7 slides", "Write in Italian."),
        ({"length": "long"}, "Spanish", "talk", "12–14 slides", "Write in Spanish."),
        ({"length": "long"}, "", "talk", "12–14 slides", LANGUAGE_FALLBACK),
    ],
)
def test_slides_use_language_and_soft_length_are_explicit(
    tmp_path, options, memo_language, expected_use, expected_length, expected_language,
):
    def reply(request):
        response = router(request)
        if "lead analyst" in request_text(request):
            return json.dumps(json.loads(response) | {"language": memo_language})
        return response

    fake = FakeProvider(reply)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        request = body(kind="slides", brief="Send the deck as a handout", **options)
        version = built(build(client, base, request))
        assert len(version["outline"]) == 3
        assert version["provenance"]["use"] == expected_use
        assert version["provenance"]["language"] == options.get("language")
        assert version["provenance"]["length"] == options["length"]
        assert built(build(client, base, request))["id"] == version["id"]
        changed = request | {"language": "French"}
        assert build(client, base, changed)[-1][1]["status"] == 409
    tasks = [request_text(request) for request in fake.requests]
    writer = next(task for task in tasks if "presentation writer" in task)
    planner = next(task for task in tasks if "presentation strategist" in task)
    assert expected_length in planner
    assert expected_language in planner and expected_language in writer
    assert "Write in English." not in writer
    assert ("notes are optional" in writer) == (expected_use == "read")


def test_approved_outlines_use_the_types_ceiling():
    outline = [{"heading": f"Question {n}?"} for n in range(16)]
    assert len(BuildOutputInput.model_validate(body(style="faq", outline=outline)).outline) == 16
    with pytest.raises(ValidationError, match="The limit for Report is 8 sections"):
        BuildOutputInput.model_validate(body(style="report", outline=outline))
    with pytest.raises(ValidationError, match="The limit for Slides is 14 slides"):
        BuildOutputInput.model_validate(body(kind="slides", style="slides", outline=outline))


def test_model_outline_and_report_can_exceed_the_submitted_outline_limit(tmp_path):
    summary = "Results in brief"
    headings = [f"Results in district {number}" for number in range(1, 9)]
    story = {
        "title": "Trial results",
        "summary_heading": summary,
        "sections": [{"heading": heading} for heading in headings],
    }
    report = "# Trial results\n" + "".join(
        f"\n## {heading}\nThe trial establishes the outcome [1].\n"
        for heading in [summary, *headings]
    )

    def reply(request):
        task = request_text(request)
        if "planning a written report" in task:
            return json.dumps(story)
        if "Write the report" in task:
            return report
        return router(request)

    with app_for(tmp_path, FakeProvider(reply)) as client:
        base, _ = library(client)
        first = built(build(client, base, body()))
        rebuilt = built(build(client, base, body(supersedesArtifactId=first["id"])))
    for version in (first, rebuilt):
        assert [part["heading"] for part in version["outline"]] == [summary, *headings]
        assert len(version["sections"]) == 9


def test_replanning_a_faq_keeps_the_first_question():
    heading = "How did cooling use change?"

    def reply(request):
        task = request_text(request)
        if "planning a written report" in task:
            assert "a null summary_id" in task
            assert "Return the report summary's id as summary_id." not in task
            assert '"id": "p1"' in task
            return json.dumps({
                "title": "Cooling trial", "sections": [{"heading": heading, "id": "p1"}],
            })
        return router(request)

    studio = StudioWorkAgents(gateway(FakeProvider(reply)), "report")
    _, outline, _, _ = studio.replan_work(
        model(), "low", Spec("report", "student", output_type="faq"), pool(), Notes(),
        lambda _: None, {}, [], [Planned(heading, id="p1")], "Clarify the first answer.",
    )
    assert [(part.id, part.heading) for part in outline] == [("p1", heading)]


def test_an_empty_faq_outline_is_rejected():
    run = Run(Spec("report", "student", output_type="faq"), "")
    with pytest.raises(ModelError, match="The outline has no sections"):
        _validate_storyline(run, ReportStory(title="Cooling trial", sections=[]))


def test_a_part_writer_uses_the_type_craft_and_saved_memo_language():
    section = "## Trial results\n\nThe trial establishes the outcome [1]."
    fake = FakeProvider(lambda _: section)
    studio = StudioWorkAgents(gateway(fake), "report")
    assert studio.write_part(
        model(), "low", Spec("report", "student", output_type="briefing"),
        [""], ["Trial results"], 0, pool(), approach={"memo": {"language": "Spanish"}},
    ) == section
    [request] = fake.requests
    task = request_text(request)
    assert "Write in Spanish." in task
    assert "Set out the options, the evidence" in task
    assert "Executive summary first:" in task
    assert "Answer first." not in task


def test_a_guessed_legacy_structure_builds_a_default_report():
    [work] = load_workspace(json.dumps({
        "works": [{"key": "draft_legacy", "spec": {
            "kind": "report", "structure": {"text": "change", "mark": "guessed"},
        }}],
    })).works
    assert work.spec.output_type is None
    spec = spec_of(ConversationBrief(), work)

    built = StudioAgents(gateway(FakeProvider(router))).make(model(), "low", spec, pool(), Notes())
    assert SUMMARY_MARK in built.content


def test_rebuild_with_a_new_language_plans_new_headings(tmp_path):
    def replies(request):
        task = request_text(request)
        if "Write in French." in task:
            if "planning a written report" in task:
                return '{"title": "Résultats", "sections": [{"heading": "Bilan"}]}'
            if "Write the report" in task:
                return "# Résultats\n\n## Bilan\nLes résultats sont confirmés [1]."
        return router(request)

    with app_for(tmp_path, FakeProvider(replies)) as client:
        base, _ = library(client)
        first = built(build(client, base, body()))
        request = body(supersedesArtifactId=first["id"], language="French")
        rebuilt = built(build(client, base, request))
    assert first["outline"][0]["heading"] == "Results"
    assert rebuilt["outline"][0]["heading"] == "Bilan"
    assert rebuilt["provenance"]["language"] == "French"


def test_saved_legacy_style_labels_do_not_change_the_seal(tmp_path):
    fake = FakeProvider(router)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        headers = workspace(client)
        first = built(build(client, base, body()))
        with client.app.state.knowledge_service.sessions() as session:
            row = session.get(Artifact, first["id"])
            row.style = "field_guide"
            row.provenance_json = json.dumps(json.loads(row.provenance_json) | {"style": row.style})
            seal = row.manifest_hash = artifact_manifest_hash(row.__getattribute__)
            session.commit()

        opened = client.get(route(base, first["id"]), headers=headers).raise_for_status().json()
        assert (opened["kind"], opened["style"], opened["outputType"], opened["manifestHash"]) == (
            "report", "field_guide", "Field guide", seal,
        )
        rebuilt = built(build(client, base, body(supersedesArtifactId=first["id"])))
        assert rebuilt["style"] == "report"
        assert client.get(route(base, first["id"]), headers=headers).json() == opened
