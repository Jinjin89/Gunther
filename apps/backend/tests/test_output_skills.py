"""Outputs made by following a skill: approach, outline, writing, review and revision."""

import json
from pathlib import Path

import openai
from fake_models import FakeProvider, api_error, gateway, skill_replies, skill_step
from test_outputs import app_for, body, build, built, library, revise, versions, workspace

from gunther.agent import Evidence
from gunther.outputs import Pool, Spec, layout_of, split_document
from gunther.schemas import ConversationCitationOut
from gunther.skill_runner import Approach, Card, SkillAgents
from gunther.skillbook import skill_for

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
    assert provenance["skill"] == {"name": "report", "version": 2, "skipped": []}
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
    # The skill's own polishing may only improve how the text reads.
    polishing = next(
        r
        for r in fake.requests
        if skill_step(r) == "revise" and "Instruction:" in str(r["messages"][-1]["content"])
    )
    assert "Editorial revision must not add" in str(polishing["messages"][0]["content"])


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
    assert version["provenance"]["skill"] == {"name": "slides", "version": 3, "skipped": []}
    # Every slide starts with the marker of its layout. Code wrote it: no model was shown one,
    # and the sections are the slides under their markers.
    slides = split_document(version["content"], "slides")[1]
    assert [layout_of(slide)[0] for slide in slides] == ["title", "compare", "takeaways"]
    assert slides[1].startswith("<!-- layout: compare -->\n## CD3D and CD19 set T and B cells")
    assert [section["heading"] for section in version["sections"]] == [
        "Markers",
        "CD3D and CD19 set T and B cells apart",
        "Use both in every panel",
    ]
    assert all(section["checked"] for section in version["sections"])
    assert not any("<!--" in str(request["messages"]) for request in fake.requests)
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
    # The person's own revision may add facts: the polishing pass's rule is not given to it.
    assert "Editorial revision must not add" not in str(rewriting["messages"][0]["content"])
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


# A slide's layout: its marker, and the form its body takes ---------------------------------------

RATES_NOTE = {"Measured rates": "CD3D cohort rate was 62%. CD19 cohort rate was 41%."}
RATES_OUTLINE = {
    "title": "Rates",
    "sections": [
        {"heading": "Rates", "layout": "title", "cards": []},
        {"heading": "CD3D is the more common marker", "layout": "bar_chart", "cards": [1]},
        {"heading": "Use both markers in every panel", "layout": "takeaways", "cards": [1]},
    ],
}
RATES_TITLE = "# Rates\n\nTwo cohort rates"
ONE_ROW = (
    "## CD3D is the more common marker\n\n| Marker | Rate (%) |\n| --- | --- |\n| CD3D | 62 [1] |"
)
TWO_ROWS = ONE_ROW + "\n| CD19 | 41 [1] |"
RATES_CLOSE = "## Use both markers in every panel\n\n- CD3D was 62% [1]\n\nNote: Close here [1]."
RATES_SEARCH = [{"action": "search_library", "query": "cohort rate"}]


def rates(rewrite) -> FakeProvider:
    return FakeProvider(
        skill_replies(
            {**APPROACH, "use": "talk", "cards": [{"claim": "CD3D is common", "refs": [1]}]},
            RATES_OUTLINE,
            [RATES_TITLE, ONE_ROW, RATES_CLOSE],
            gather=RATES_SEARCH,
            rewrite=rewrite,
        )
    )


def written_again(fake: FakeProvider) -> list[dict]:
    """The requests that asked a slide to be written again (they carry the instruction)."""

    return [
        request
        for request in fake.requests
        if skill_step(request) == "revise" and "Instruction:" in str(request["messages"][-1])
    ]


def test_a_slide_that_does_not_fit_its_layout_is_written_again_once_with_the_problem(
    tmp_path: Path,
) -> None:
    fake = rates(lambda index, prompt: TWO_ROWS)
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client, notes=RATES_NOTE)
        events = build(client, base_id, body(kind="slides"))
        version = built(events)

    # A chart of one row is no chart; the second try has two, and keeps its layout.
    slides = split_document(version["content"], "slides")[1]
    assert slides[1] == "<!-- layout: bar_chart -->\n" + TWO_ROWS
    assert version["provenance"]["skill"]["skipped"] == []
    [again] = written_again(fake)
    prompt = str(again["messages"][-1]["content"])
    assert "Instruction: The slide does not take the form its layout asks for." in prompt
    assert "a table of 2 to 6 rows; this one has 1" in prompt
    # An ordinary rewrite, not the editorial one, of the slide as the model saw it: no marker.
    assert "Editorial revision must not add" not in str(again["messages"][0]["content"])
    assert "This section now:\n## CD3D is the more common marker" in prompt
    assert "Layout: bar_chart" in prompt
    assert not any("<!--" in str(request["messages"]) for request in fake.requests)
    states = [(data["index"], data["state"]) for name, data in events if name == "section"]
    assert states.count((1, "revising")) == 1


def test_a_slide_that_still_does_not_fit_is_drawn_as_text_and_the_version_says_so(
    tmp_path: Path,
) -> None:
    fake = rates(lambda index, prompt: ONE_ROW)
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client, notes=RATES_NOTE)
        version = built(build(client, base_id, body(kind="slides")))

    slides = split_document(version["content"], "slides")[1]
    assert slides[1] == "<!-- layout: text -->\n" + ONE_ROW
    assert [layout_of(slide)[0] for slide in slides] == ["title", "text", "takeaways"]
    [note] = version["provenance"]["skill"]["skipped"]
    assert (note["step"], note["label"]) == ("layout", "Slide 2 drawn as text")
    assert "a table of 2 to 6 rows; this one has 1" in note["reason"]
    # Written again once, not more; and the plan still says what was planned.
    assert len(written_again(fake)) == 1
    layouts = [item.get("layout") for item in version["outline"]]
    assert layouts == ["title", "bar_chart", "takeaways"]
    assert [section["checked"] for section in version["sections"]] == [True, True, True]


def test_the_skills_own_revision_keeps_a_slide_drawn_as_text_as_text(tmp_path: Path) -> None:
    def rewrite(index: int, prompt: str) -> str:
        if "does not take the form" in prompt:
            return ONE_ROW  # still not a chart
        return ONE_ROW.replace("Marker", "Cohort")

    fake = FakeProvider(
        skill_replies(
            {**APPROACH, "use": "talk", "cards": [{"claim": "CD3D is common", "refs": [1]}]},
            RATES_OUTLINE,
            [RATES_TITLE, ONE_ROW, RATES_CLOSE],
            gather=RATES_SEARCH,
            editor={"issues": [{"section": 2, "problem": "The first column is vague."}]},
            revision={"sections": [{"section": 2, "instruction": "Name the first column better."}]},
            rewrite=rewrite,
        )
    )
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client, notes=RATES_NOTE)
        version = built(build(client, base_id, body(kind="slides")))

    # The chart fell back to text at the first writing; the revision wrote it as text, so it
    # fits and is not made a chart again, nor drawn as text a second time.
    slides = split_document(version["content"], "slides")[1]
    assert slides[1] == "<!-- layout: text -->\n" + ONE_ROW.replace("Marker", "Cohort")
    assert [item["step"] for item in version["provenance"]["skill"]["skipped"]] == ["layout"]
    assert len(written_again(fake)) == 2


def test_a_slide_that_fits_is_not_written_again(tmp_path: Path) -> None:
    fake = FakeProvider(
        skill_replies(
            {**APPROACH, "use": "talk", "cards": [{"claim": "CD3D is common", "refs": [1]}]},
            RATES_OUTLINE,
            [RATES_TITLE, TWO_ROWS, RATES_CLOSE],
            gather=RATES_SEARCH,
        )
    )
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client, notes=RATES_NOTE)
        version = built(build(client, base_id, body(kind="slides")))
    assert not written_again(fake)
    assert version["provenance"]["skill"]["skipped"] == []


def test_a_revision_takes_a_slides_layout_from_its_marker_and_keeps_the_titles_accent(
    tmp_path: Path,
) -> None:
    def rewrite(index: int, prompt: str) -> str:
        if index == 0:
            return "# Markers\n\nA new message"
        return (
            "## CD3D and CD19 set T and B cells apart\n\n- CD3D marks T cells [1]\n"
            "- CD19 marks B cells [2]\n\nNote: Say it as a list [1]."
        )

    fake = FakeProvider(
        skill_replies(SLIDES_APPROACH, SLIDES_OUTLINE, SLIDES, gather=SEARCH, rewrite=rewrite)
    )
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        first = built(build(client, base_id, body(kind="slides")))
        # The person changed this slide's layout, and picked an accent for the title slide, by hand.
        typed = first["content"].replace("<!-- layout: compare -->", "<!-- layout: cards -->")
        typed = typed.replace("<!-- layout: title -->", "<!-- layout: title; accent: blue -->")
        assert typed.count("layout: cards") == 1 and "accent: blue" in typed
        second = client.post(
            f"/api/knowledge-bases/{base_id}/artifacts/{first['id']}/edits",
            headers=workspace(client),
            json={"clientRequestId": "edit_request_0006", "content": typed},
        ).json()
        fake.requests.clear()
        third = built(revise(client, base_id, second["id"], "Say it as a list", sectionIndex=1))
        fourth = built(revise(client, base_id, third["id"], "A new message", sectionIndex=0))

    old = split_document(second["content"], "slides")[1]
    middle = split_document(third["content"], "slides")[1]
    last = split_document(fourth["content"], "slides")[1]
    # The slide was rewritten as the layout it now has; the others are as they were.
    assert middle[1].startswith("<!-- layout: cards -->\n## CD3D and CD19 set T and B cells apart")
    assert (middle[0], middle[2]) == (old[0], old[2])
    assert last[0] == "<!-- layout: title; accent: blue -->\n# Markers\n\nA new message"
    assert (last[1], last[2]) == (middle[1], middle[2])
    asked = written_again(fake)
    assert "Layout: cards" in str(asked[0]["messages"][-1]["content"])
    assert "Layout: title" in str(asked[1]["messages"][-1]["content"])
    assert not any("<!--" in str(request["messages"]) for request in fake.requests)
    # What was planned is still the plan.
    assert [item.get("layout") for item in fourth["outline"]] == ["title", "compare", "takeaways"]


def test_a_figure_slide_needs_a_figure_kept_with_its_material() -> None:
    plan = {
        "title": "Curves",
        "sections": [
            {"heading": "Curves", "layout": "title", "cards": []},
            {"heading": "The curve rises after week four", "layout": "figure", "cards": [1]},
            {"heading": "Nothing was kept for this slide", "layout": "figure", "cards": [2]},
            {"heading": "The steps were the same in both", "layout": "flow", "cards": [2]},
            {"heading": "Three causes were found", "layout": "cards", "cards": [2]},
            {"heading": "Takeaways", "layout": "takeaways", "cards": [1]},
        ],
    }
    fake = FakeProvider(json.dumps(plan))
    chosen = gateway(fake)
    agents = SkillAgents(chosen, skill_for("slides"))
    agents.approach = Approach("Q", "A", "P", "what")
    agents.cards = [Card(claim="The curve rises", refs=[1]), Card(claim="Another claim", refs=[2])]
    citation = ConversationCitationOut(
        id="cit_1",
        source_id="src_1",
        source_title="Paper",
        quote="Figure 2 shows the curve.",
        locator="p. 3",
        status="provisional",
        confidence=0.0,
        figures=[{"assetId": "ast_1", "caption": "Figure 2: the curve"}],
    )
    pool = Pool(10)
    pool.adopt(Evidence("library", "Paper", "Figure 2 shows the curve.", payload=(citation, None)))
    pool.adopt(Evidence("library", "Notes", "Nothing is drawn here."))

    title, planned = agents.structure(
        chosen.models[0], "off", Spec("slides", "scientist"), lambda event: None, pool=pool
    )

    assert [item.layout for item in planned] == [
        "title", "figure", "text", "flow", "cards", "takeaways",
    ]  # fmt: skip
    assert agents.skipped == [
        {
            "step": "layout",
            "label": "Slide 3 drawn as text",
            "reason": "No figure is kept with the material this slide uses.",
        }
    ]
    # The planner was told which cards come with a figure.
    prompt = str(fake.requests[0]["messages"][-1]["content"])
    assert "Figures kept with the material" in prompt
    assert "- C1: Figure 2: the curve" in prompt and "- C2:" not in prompt
