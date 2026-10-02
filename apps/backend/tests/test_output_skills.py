"""Outputs made by following a skill: approach, outline, writing, review and revision."""

import json
from pathlib import Path

import openai
from fake_models import FakeProvider, api_error, skill_replies, skill_step
from test_outputs import app_for, body, build, built, library, revise, versions, workspace

APPROACH = {
    "question": "How are T and B cells told apart?",
    "answer": "By their surface markers: CD3D for T cells, CD19 for B cells.",
    "purpose": "Scientists choosing markers for a panel.",
    "structure": "what",
    "structure_reason": "It describes how things are.",
    "cards": [
        {"claim": "CD3D marks T cells", "refs": [1], "type": "data"},
        {"claim": "CD19 marks B cells", "refs": [2], "type": "data"},
    ],
}
OUTLINE = {
    "title": "Telling immune cells apart",
    "sections": [
        {
            "heading": "Surface markers tell T and B cells apart",
            "goal": "The answer",
            "cards": [1, 2],
        },
        {"heading": "CD3D is the T-cell marker", "goal": "T cells", "cards": [1]},
        {"heading": "CD19 is the B-cell marker", "goal": "B cells", "cards": [2]},
    ],
}
REPORT = [
    "## Surface markers tell T and B cells apart\n\n"
    "CD3D marks T cells [1]; CD19 marks B cells [2].",
    "## CD3D is the T-cell marker\n\nCD3D is a marker of T cells [1].",
    "## CD19 is the B-cell marker\n\nCD19 is a marker of B cells [2].",
]
SEARCH = [{"action": "search_library", "query": "CD3D CD19 markers"}]


def stages(events: list[tuple[str, dict]]) -> list[tuple[str, str]]:
    return [(data["id"], data["state"]) for name, data in events if name == "stage"]


def test_a_report_follows_its_skill_from_the_approach_to_a_revision(tmp_path: Path) -> None:
    fake = FakeProvider(
        skill_replies(
            APPROACH,
            OUTLINE,
            REPORT,
            gather=SEARCH,
            editor={"issues": [{"section": 3, "problem": "It ends abruptly.", "fix": "Close."}]},
            reader={"core": "Markers tell cells apart.", "questions": ["Which panel?"]},
            revision={"sections": [{"section": 3, "instruction": "Say what it means."}]},
            rewrite=lambda index, prompt: (
                "## CD19 is the B-cell marker\n\nCD19 marks B cells [2], so a panel needs it."
            ),
        )
    )
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        events = build(client, base_id, body())
        version = built(events)

    assert [step for step, state in stages(events) if state == "done"] == [
        "understand",
        "structure",
        "write",
        "edit_review",
        "reader_test",
        "revise",
    ]
    approach = next(data["approach"] for name, data in events if name == "approach")
    assert approach["question"] == APPROACH["question"] and approach["structure"] == "what"
    assert version["style"] == "auto"
    provenance = version["provenance"]
    assert provenance["generator"] == "gunther.output-skills.v1"
    assert provenance["skill"] == {"name": "report", "version": 1, "skipped": []}
    assert provenance["approach"]["answer"] == APPROACH["answer"]
    assert "so a panel needs it" in version["content"]
    assert [section["checked"] for section in version["sections"]] == [True, True, True]

    steps = [skill_step(request) for request in fake.requests]
    assert {"understand", "structure", "write", "edit_review", "reader_test", "revise"} <= set(
        steps
    )
    writing = next(r for r in fake.requests if skill_step(r) == "write")
    system = str(writing["messages"][0]["content"])
    # Gunther's rules come first, then the skill's preamble, its step and its references.
    assert system.index("Gunther's rules") < system.index("# Report") < system.index("## write")
    assert "按受众调整" in system
    prompt = str(writing["messages"][-1]["content"])
    assert "Core question: How are T and B cells told apart?" in prompt
    assert "Its material cards:" in prompt
    reading = next(r for r in fake.requests if skill_step(r) == "reader_test")
    # The reader sees the text only: no method, no approach, no numbers.
    assert "# Report" not in str(reading["messages"][0]["content"])
    assert "[1]" not in str(reading["messages"][-1]["content"])
    assert "Core question" not in str(reading["messages"][-1]["content"])


SLIDES_APPROACH = {**APPROACH, "use": "talk", "pages": 6}
SLIDES_OUTLINE = {
    "title": "Markers",
    "sections": [
        {"heading": "Markers", "layout": "compare", "cards": []},
        {"heading": "CD3D and CD19 set T and B cells apart", "layout": "compare", "cards": [1, 2]},
        {"heading": "Use both in every panel", "layout": "text", "cards": [1]},
    ],
}
SLIDES = [
    "# Markers\n\nTwo markers tell the cells apart",
    "## CD3D and CD19 set T and B cells apart\n\n| | T | B |\n| --- | --- | --- |\n"
    "| Marker | CD3D [1] | CD19 [2] |\n\nNote: Start with the table [1].",
    "## Use both in every panel\n\n- CD3D for T cells [1]\n\nNote: Close here [1].",
]


def test_a_deck_writes_each_slide_by_its_own_layout(tmp_path: Path) -> None:
    fake = FakeProvider(skill_replies(SLIDES_APPROACH, SLIDES_OUTLINE, SLIDES, gather=SEARCH))
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        version = built(build(client, base_id, body(kind="slides")))

    # The first slide is the title and the last the takeaways, whatever was planned.
    assert [item.get("layout") for item in version["outline"]] == ["title", "compare", "takeaways"]
    assert version["provenance"]["use"] == "talk"
    writes = [r for r in fake.requests if skill_step(r) == "write"]
    second = str(writes[1]["messages"][0]["content"])
    assert "## compare" in second and "## flow" not in second
    assert "Layout: compare" in str(writes[1]["messages"][-1]["content"])
    assert "## takeaways" in str(writes[2]["messages"][0]["content"])


def test_what_the_person_wrote_is_kept_and_a_style_fixes_the_structure(tmp_path: Path) -> None:
    fake = FakeProvider(skill_replies(APPROACH, OUTLINE, REPORT))
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        version = built(
            build(
                client,
                base_id,
                body(style="decision_brief", approach={"question": "Which marker should we use?"}),
            )
        )
    approach = version["provenance"]["approach"]
    assert approach["question"] == "Which marker should we use?"
    assert approach["answer"] == APPROACH["answer"]
    assert approach["structure"] == "which"
    understanding = next(
        r
        for r in fake.requests
        if skill_step(r) == "understand"
        and "Write the approach" in str(r["messages"][0]["content"])
    )
    assert "keep them exactly as written" in str(understanding["messages"][-1]["content"])


def test_a_reader_that_fails_is_skipped_and_the_version_says_so(tmp_path: Path) -> None:
    refused = api_error(openai.InternalServerError, 500, "The reader is down")
    fake = FakeProvider(skill_replies(APPROACH, OUTLINE, REPORT, reader=refused))
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        events = build(client, base_id, body())
        version = built(events)
    assert ("reader_test", "failed") in stages(events)
    skipped = version["provenance"]["skill"]["skipped"]
    assert [item["step"] for item in skipped] == ["reader_test"]
    assert "The reader is down" in skipped[0]["reason"]


def test_a_skilled_version_is_revised_by_its_skill_and_keeps_its_approach(tmp_path: Path) -> None:
    fake = FakeProvider(
        skill_replies(
            APPROACH,
            OUTLINE,
            REPORT,
            rewrite=lambda index, prompt: "## CD3D is the T-cell marker\n\nCD3D marks T cells [1].",
        )
    )
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        first = built(build(client, base_id, body()))
        fake.requests.clear()
        second = built(revise(client, base_id, first["id"], "Shorter", sectionIndex=1))
        edited = client.post(
            f"/api/knowledge-bases/{base_id}/artifacts/{second['id']}/edits",
            headers=workspace(client),
            json={"clientRequestId": "edit_request_0001", "content": second["content"] + "\n"},
        ).json()

    rewriting = next(r for r in fake.requests if skill_step(r) == "revise")
    assert "Instruction: Shorter" in str(rewriting["messages"][-1]["content"])
    assert "Core question" in str(rewriting["messages"][-1]["content"])
    for version in (second, edited):
        assert version["provenance"]["skill"]["name"] == "report"
        assert version["provenance"]["approach"] == first["provenance"]["approach"]
    assert len(versions(client)) == 3


def test_the_web_is_offered_only_when_it_is_asked_for(tmp_path: Path) -> None:
    fake = FakeProvider(skill_replies(APPROACH, OUTLINE, REPORT))
    with app_for(tmp_path, fake, skills=True, tavily_api_key="tvly-test-000000000000") as client:
        base_id, _ = library(client)
        built(build(client, base_id, body()))
        offered = [
            str(r["messages"][-1]["content"])
            for r in fake.requests
            if "Choose the next action" in str(r["messages"][0]["content"])
        ]
        assert offered and all("- search_web:" not in prompt for prompt in offered)
        fake.requests.clear()
        built(build(client, base_id, body(web=True)))
        offered = [
            str(r["messages"][-1]["content"])
            for r in fake.requests
            if "Choose the next action" in str(r["messages"][0]["content"])
        ]
        assert any("- search_web:" in prompt for prompt in offered)


def test_the_developer_switch_turns_skills_off(tmp_path: Path) -> None:
    from fake_models import output_replies

    outline = {"title": "Markers", "sections": [{"heading": "T cells", "queries": ["CD3D"]}]}
    fake = FakeProvider(output_replies(outline, ["## T cells\n\nCD3D marks T cells [1]."]))
    with app_for(tmp_path, fake, skills=True) as client:
        headers = workspace(client)
        assert client.get("/api/settings/developer", headers=headers).json()["outputSkills"] is True
        saved = client.put(
            "/api/settings/developer",
            headers=headers,
            json={"traces": False, "outputSkills": False},
        ).json()
        assert saved == {"traces": False, "outputSkills": False}
        # Saving only the traces switch leaves this one as it is.
        client.put("/api/settings/developer", headers=headers, json={"traces": True})
        assert (
            client.get("/api/settings/developer", headers=headers).json()["outputSkills"] is False
        )
        base_id, _ = library(client)
        version = built(build(client, base_id, body()))
    assert version["provenance"]["generator"] == "gunther.output-agents.v1"
    assert "skill" not in version["provenance"] or version["provenance"]["skill"] is None
    assert version["style"] == "overview"
    saved_file = json.loads((tmp_path / "service-settings.json").read_text())
    assert saved_file["developer"]["output_skills"] is False


def test_reading_more_of_a_source_adds_the_passage_with_its_surroundings(tmp_path: Path) -> None:
    note = {
        "Markers": "T cells are lymphocytes.\n\nCD3D is a marker of T cells.\n\n"
        "It sits in the T-cell receptor complex.\n\nCD19 is a marker of B cells."
    }
    reading = [
        {"action": "search_library", "query": "CD3D"},
        {"action": "read_source", "query": "1"},
    ]
    fake = FakeProvider(skill_replies(APPROACH, OUTLINE, REPORT, gather=reading))
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client, notes=note)
        events = build(client, base_id, body())
        built(events)
    read = [data for name, data in events if name == "step" and data["tool"] == "read_source"]
    assert read[-1]["state"] == "done" and read[-1]["found"] == 1
    approach = next(
        r
        for r in fake.requests
        if skill_step(r) == "understand"
        and "Write the approach" in str(r["messages"][0]["content"])
    )
    assert "(with context)" in str(approach["messages"][-1]["content"])
    assert "It sits in the T-cell receptor complex." in str(approach["messages"][-1]["content"])
