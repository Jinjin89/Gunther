"""Outputs: reports and slides built by agents, then edited, revised and checked."""

import itertools
import json
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import openai
from fake_models import (
    FakeProvider,
    agent_replies,
    api_error,
    is_auditing,
    is_grading,
    is_outlining,
    is_support_checking,
    is_writing,
    output_replies,
)
from fastapi.testclient import TestClient
from legacy_artifacts import insert_legacy_version
from sqlalchemy import func, select, text

from gunther.config import Settings
from gunther.main import create_app
from gunther.models import Artifact, ArtifactCheck, ArtifactUnitBinding
from gunther.outputs import (
    Spec,
    cited_sentences,
    heading_of,
    section_hash,
    shape_section,
    split_document,
    title_of,
    unsource,
)

SIDECAR_TOKEN = "sidecar-token-with-at-least-256-bits-000000000000000000000000"
SIDECAR = {"X-Gunther-Token": SIDECAR_TOKEN}

NOTES = {
    "Markers": "CD3D is a marker of T cells.",
    "Receptors": "CD19 is a marker of B cells.",
}
OUTLINE = {
    "title": "Immune markers",
    "sections": [
        {"heading": "T cells", "goal": "How T cells are told apart", "queries": ["CD3D T cells"]},
        {"heading": "B cells", "goal": "How B cells are told apart", "queries": ["CD19 B cells"]},
    ],
}
ONE_SECTION = {"title": "Immune markers", "sections": [OUTLINE["sections"][0]]}
# The first section cites the second passage first, so the numbers have to be reordered.
REPORT = [
    "## T cells\n\nB cells are set apart by CD19 [2], and T cells by CD3D [1][7].",
    "## B cells\n\nCD19 marks B cells [2].",
]
SLIDES_OUTLINE = {
    "title": "Immune markers",
    "sections": [
        {"heading": "Immune markers", "goal": "", "queries": []},
        {"heading": "T cells", "goal": "The T-cell marker", "queries": ["CD3D T cells"]},
        {"heading": "Takeaways", "goal": "", "queries": []},
    ],
}
SLIDES = [
    "# Immune markers\n\nHow cells are told apart",
    "## T cells\n\n- CD3D marks T cells [1]\n- It sits on the cell surface [?]\n\n"
    "Note: The marker is found on every T cell [1].",
    "## Takeaways\n\n- Markers tell cells apart [1]\n\nNote: Close on CD3D [1].",
]

_requests = itertools.count(1)


def body(**fields) -> dict:
    return {
        "clientRequestId": f"output_request_{next(_requests):04d}",
        "kind": "report",
        "audience": "scientist",
        **fields,
    }


def app_for(tmp_path: Path, fake: FakeProvider, **overrides) -> TestClient:
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        deepseek_api_key="sk-test-0000000000001234",
        stt_provider="compatible",
        processing_worker_enabled=False,
        auth_token=SIDECAR_TOKEN,
        service_settings_file=tmp_path / "service-settings.json",
        **overrides,
    )
    return TestClient(create_app(settings, model_client_factory=fake.factory))


def workspace(client: TestClient) -> dict[str, str]:
    found = client.get("/api/workspace/bootstrap", headers=SIDECAR).json()["workspaceId"]
    return {**SIDECAR, "X-Gunther-Workspace-Id": found}


def library(
    client: TestClient, title: str = "Cells", notes: dict[str, str] | None = None
) -> tuple[str, dict[str, str]]:
    """A library with one note per source: its id, and the sources' ids by title."""

    base_id = client.post(
        "/api/knowledge-bases",
        headers=SIDECAR,
        json={"title": title, "question": f"What marks {title}?", "description": "Markers."},
    ).json()["id"]
    ids = {}
    for name, content in (NOTES if notes is None else notes).items():
        ids[name] = client.post(
            "/api/sources",
            headers=SIDECAR,
            json={"title": name, "kind": "note", "knowledgeBaseId": base_id, "content": content},
        ).json()["source"]["id"]
    return base_id, ids


def read_events(response) -> list[tuple[str, dict]]:
    events, name = [], ""
    for line in response.iter_lines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            events.append((name, json.loads(line[6:])))
    return events


def build(client: TestClient, base_id: str, request: dict, headers=None) -> list[tuple[str, dict]]:
    with client.stream(
        "POST",
        f"/api/knowledge-bases/{base_id}/outputs/build/stream",
        headers=headers or workspace(client),
        json=request,
    ) as response:
        assert response.status_code == 200, response.read()
        return read_events(response)


def built(events: list[tuple[str, dict]]) -> dict:
    assert events[-1][0] == "done", events[-1]
    return events[-1][1]


def revise(
    client: TestClient, base_id: str, artifact_id: str, instruction: str, **fields
) -> list[tuple[str, dict]]:
    with client.stream(
        "POST",
        route(base_id, artifact_id, "/revise/stream"),
        headers=workspace(client),
        json={
            "clientRequestId": f"revise_request_{next(_requests):04d}",
            "instruction": instruction,
            **fields,
        },
    ) as response:
        assert response.status_code == 200, response.read()
        return read_events(response)


@contextmanager
def built_output(tmp_path: Path, fake: FakeProvider, **fields):
    """An app with a library and one built output: (client, library id, the version)."""

    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        yield client, base_id, built(build(client, base_id, body(**fields)))


def versions(client: TestClient) -> list[Artifact]:
    with client.app.state.knowledge_service.sessions() as session:
        return list(session.scalars(select(Artifact)))


def route(base_id: str, artifact_id: str, tail: str = "") -> str:
    return f"/api/knowledge-bases/{base_id}/artifacts/{artifact_id}{tail}"


def in_background(call) -> tuple[threading.Thread, list]:
    box: list = []
    thread = threading.Thread(target=lambda: box.append(call()), daemon=True)
    thread.start()
    return thread, box


def wait_for(check, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def heading(prompt: str) -> str:
    return re.search(r"Write section \d+ of \d+: (.+)", prompt).group(1)


# The rules both sides split a document by -----------------------------------------------------
# The desktop (src/outputs/sections.test.ts) tests the same cases.


def test_a_reports_sections_start_at_level_two_headings_outside_code() -> None:
    content = (
        "# Title\n\n_Brief_\n\n## One\n\ntext\n\n```md\n## not a section\n```\n\n"
        "### Sub\n\n## Two\n\nmore\n"
    )
    preamble, sections = split_document(content, "report")
    assert preamble == "# Title\n\n_Brief_"
    assert sections == [
        "## One\n\ntext\n\n```md\n## not a section\n```\n\n### Sub",
        "## Two\n\nmore",
    ]
    assert split_document("Just words, no headings.", "report") == ("Just words, no headings.", [])


def test_slides_are_the_parts_between_lines_that_are_only_three_dashes() -> None:
    content = (
        "# Deck\n\nsub\n\n---\n\n## One\n\n- a\n\nNote: n\n\n```\n---\n```\n\n"
        "   ---  \n\n## Two\n\n---\n\n---\n"
    )
    assert split_document(content, "slides") == (
        "",
        ["# Deck\n\nsub", "## One\n\n- a\n\nNote: n\n\n```\n---\n```", "## Two"],
    )


def test_a_sections_hash_ignores_line_ends_and_outer_space() -> None:
    assert section_hash("a\r\nb\n") == section_hash("  a\nb")
    assert section_hash("a\nb") != section_hash("a\n\nb")
    assert len(section_hash("")) == 64


def test_the_title_is_the_first_top_level_heading_outside_code() -> None:
    assert title_of("```\n# not this\n```\n\n# This one\n\n# Not this either") == "This one"
    assert title_of("## only a section") is None
    assert title_of("# " + "x" * 200) == "x" * 160
    assert heading_of("\n## The heading\n\ntext") == "The heading"
    assert heading_of("# Title slide") == "Title slide"


def test_what_the_writer_returns_is_shaped_but_not_reworded() -> None:
    report = Spec("report", "scientist")
    fenced = "```markdown\n## T cells\n\ntext [1]\n\n## Extra\n\nmore\n```"
    assert shape_section(fenced, report, "T cells", False) == (
        "## T cells\n\ntext [1]\n\n### Extra\n\nmore"
    )
    assert shape_section("# T cells\n\ntext", report, "T cells", False) == "## T cells\n\ntext"
    assert shape_section("just text", report, "T cells", False) == "## T cells\n\njust text"
    deck = Spec("slides", "student")
    assert shape_section("## Deck\n\n- a\n---\n- b", deck, "Deck", False) == "## Deck\n\n- a\n- b"
    assert shape_section("## Deck\n\nsub", deck, "Deck", True) == "# Deck\n\nsub"


def test_the_sentences_that_cite_passages_are_found_with_their_numbers() -> None:
    text = "## Heading\n\nOne fact [1]. Two facts [2][3].\n- bullet [4]\nNo cite.\n"
    found = cited_sentences(text)
    assert [(chunk.strip(), numbers) for _, _, chunk, numbers in found] == [
        ("One fact [1].", [1]),
        ("Two facts [2][3].", [2, 3]),
        ("- bullet [4]", [4]),
    ]
    # A number after the full stop belongs to the sentence before it.
    [(_, _, chunk, numbers)] = cited_sentences("A fact. [5]")
    assert (chunk, numbers) == ("A fact. [5]", [5])
    assert unsource("CD3D marks T cells [1][2].\n") == "CD3D marks T cells [?].\n"


# Building --------------------------------------------------------------------------------------


def test_a_report_is_planned_researched_written_and_checked(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        headers = workspace(client)
        events = build(client, base_id, body(brief="How immune cells are told apart"))
        artifact = built(events)
        stored = client.get(route(base_id, artifact["id"]), headers=headers).json()
        listed = client.get(f"/api/knowledge-bases/{base_id}/artifacts", headers=headers).json()

    # Numbers follow reading order, and every one has its passage in the list.
    assert artifact["content"] == (
        "# Immune markers\n\n_How immune cells are told apart_\n\n"
        "## T cells\n\nB cells are set apart by CD19 [1], and T cells by CD3D [2].\n\n"
        "## B cells\n\nCD19 marks B cells [1]."
    )
    assert [(c["sourceTitle"], c["ref"]) for c in artifact["citations"]] == [
        ("Receptors", 1),
        ("Markers", 2),
    ]
    assert artifact["citations"][1]["quote"] == "CD3D is a marker of T cells."
    assert (artifact["title"], artifact["kind"], artifact["style"]) == (
        "Immune markers",
        "report",
        "overview",
    )
    assert (artifact["origin"], artifact["versionNumber"], artifact["format"]) == (
        "build",
        1,
        "report",
    )
    assert artifact["outline"] == [
        {"heading": "T cells", "goal": "How T cells are told apart"},
        {"heading": "B cells", "goal": "How B cells are told apart"},
    ]
    assert artifact["sections"] == [
        {"index": 0, "heading": "T cells", "checked": True, "issues": []},
        {"index": 1, "heading": "B cells", "checked": True, "issues": []},
    ]
    assert artifact["scope"] == {
        "mode": "library",
        "sourceIds": [],
        "unitIds": [],
        "sessionIds": [],
    }
    assert artifact["inputs"] == {"sources": 2, "units": 0, "sessions": 0}
    assert artifact["modelLabel"] == "DeepSeek · Flash"
    provenance = artifact["provenance"]
    assert (provenance["schemaVersion"], provenance["acceptedOnly"]) == (2, False)
    assert provenance["generator"] == "gunther.output-agents.v1"
    assert provenance["model"] == {"ref": "deepseek/deepseek-flash", "label": "DeepSeek · Flash",
                                   "effort": "high"}
    assert (provenance["kind"], provenance["origin"], provenance["audience"]) == (
        "report",
        "build",
        "scientist",
    )
    assert stored == artifact
    assert [(item["kind"], item["style"], item["origin"]) for item in listed] == [
        ("report", "overview", "build")
    ]
    assert "content" not in listed[0]

    # The stream said what it was doing, in order, and wrote the text as it came.
    names = [name for name, _ in events]
    assert names[0] == "outline" and names[-1] == "done"
    assert events[0][1]["title"] == "Immune markers"
    states = [(d["index"], d["state"]) for n, d in events if n == "section"]
    assert states == [
        (0, "researching"), (1, "researching"),
        (0, "writing"), (0, "checking"), (0, "done"),
        (1, "writing"), (1, "checking"), (1, "done"),
    ]  # fmt: skip
    for index in (0, 1):
        live = "".join(d["text"] for n, d in events if n == "text" and d["section"] == index)
        assert live == REPORT[index]
    searches = [d for n, d in events if n == "step" and d["state"] == "done"]
    # The library offers both notes for each search; a model grades what is relevant.
    assert [(d["query"], d["found"], d["section"]) for d in searches] == [
        ("CD3D T cells", 2, 0),
        ("CD19 B cells", 2, 1),
    ]
    assert sum(is_outlining(r) for r in fake.requests) == 1
    assert sum(is_writing(r) for r in fake.requests) == 2


def test_a_deck_is_written_slide_by_slide_with_speaker_notes(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(SLIDES_OUTLINE, SLIDES))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        events = build(client, base_id, body(kind="slides", audience="student"))
        artifact = built(events)

    assert artifact["kind"] == "slides" and artifact["style"] is None
    assert artifact["format"] == "slides"
    assert artifact["title"] == "Immune markers"
    assert artifact["content"] == "\n\n---\n\n".join(SLIDES)
    slides = split_document(artifact["content"], "slides")[1]
    assert [heading_of(slide) for slide in slides] == ["Immune markers", "T cells", "Takeaways"]
    assert "\nNote: The marker is found on every T cell [1]." in slides[1]
    assert [s["heading"] for s in artifact["sections"]] == [
        "Immune markers",
        "T cells",
        "Takeaways",
    ]
    # A claim from the model's own knowledge that no source could back stays marked.
    assert artifact["sections"][1]["issues"] == [
        {
            "claim": "- It sits on the cell surface",
            "verdict": "unverified",
            "note": "From the model's own knowledge; no source was found.",
        }
    ]
    assert [c["sourceTitle"] for c in artifact["citations"]] == ["Markers"]
    # Only the slide that makes a point searches; the title and closing slides do not.
    assert {d["section"] for n, d in events if n == "step"} == {1}
    # The title slide states nothing to check, so only the two slides with claims are audited.
    assert sum(is_auditing(r) for r in fake.requests) == 2
    assert artifact["sections"][0] == {
        "index": 0,
        "heading": "Immune markers",
        "checked": True,
        "issues": [],
    }
    writing = [r for r in fake.requests if is_writing(r)]
    title_rules = writing[0]["messages"][0]["content"]
    assert 'Format: the title slide. Write "# Immune markers"' in title_rules
    assert "3 to 5 bullets" in writing[1]["messages"][0]["content"]


def test_a_title_slide_that_is_typed_over_needs_no_check(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(SLIDES_OUTLINE, SLIDES))
    with built_output(tmp_path, fake, kind="slides", audience="student") as built_deck:
        client, base_id, first = built_deck
        headers = workspace(client)
        typed = first["content"].replace(
            "How cells are told apart", "A guide to telling cells apart"
        )
        second = client.post(
            route(base_id, first["id"], "/edits"),
            headers=headers,
            json={"clientRequestId": "edit_request_0005", "content": typed},
        ).json()
        assert [s["checked"] for s in second["sections"]] == [False, True, True]
        before = len(fake.requests)
        checked = client.post(route(base_id, second["id"], "/check"), headers=headers).json()
        # Nothing was asked of a model: a title and a line have no claims to look up.
        assert len(fake.requests) == before
        assert [s["checked"] for s in checked["sections"]] == [True, True, True]
        assert checked["content"] == typed


def test_a_plan_that_runs_on_is_cut_to_size_not_refused(tmp_path: Path) -> None:
    plan = {
        "title": "",
        "sections": [
            {
                "heading": "T cells " + "x" * 300,
                "goal": "How " + "y" * 500,
                "queries": ["CD3D T cells", "CD3D", "T cells", "T", "cells"],
            }
        ],
    }
    fake = FakeProvider(output_replies(plan, ["## T cells\n\nCD3D marks T cells [1]."]))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        events = build(client, base_id, body())
    artifact = built(events)
    [section] = events[0][1]["sections"]
    assert len(section["heading"]) == 160 and len(section["goal"]) == 400
    # With no title from the plan, the first heading names the output.
    assert artifact["title"] == section["heading"]
    # Only three of the five searches were made.
    assert len([d for n, d in events if n == "step" and d["state"] == "done"]) == 3


def test_a_selection_searches_only_what_was_chosen(tmp_path: Path) -> None:
    written = ["## T cells\n\nCD3D marks T cells [1].", "## B cells\n\nNothing was found."]
    fake = FakeProvider(output_replies(OUTLINE, written))
    with app_for(tmp_path, fake) as client:
        base_id, ids = library(client)
        other_id, other = library(client, "Other", {"Elsewhere": "CD8 marks killer T cells."})
        start = len(fake.requests)  # what a model is asked while sources are read is not ours
        artifact = built(
            build(client, base_id, body(scope={"mode": "selection", "sourceIds": [ids["Markers"]]}))
        )
        before = len(fake.requests)
        refused = build(
            client, base_id, body(scope={"mode": "selection", "sourceIds": [other["Elsewhere"]]})
        )
        nothing = build(
            client, other_id, body(scope={"mode": "selection", "unitIds": ["unt_gone"]})
        )

    assert [c["sourceTitle"] for c in artifact["citations"]] == ["Markers"]
    assert artifact["scope"]["sourceIds"] == [ids["Markers"]]
    assert artifact["inputs"] == {"sources": 1, "units": 0, "sessions": 0}
    # Nothing outside the choice reached a model: not as a passage, not as a title.
    everything = json.dumps([r["messages"] for r in fake.requests[start:before]])
    assert "CD19 is a marker" not in everything and "Receptors" not in everything
    # A source of another library is refused before any model is asked.
    [(name, error)] = refused
    assert name == "error" and error["status"] == 400
    assert error["detail"].startswith("These sources are not in this library")
    [(_, gone)] = nothing
    assert gone["status"] == 400 and gone["detail"].startswith("These saved answers")
    assert len(fake.requests) == before


def test_an_empty_library_is_not_built_from(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client, notes={})
        events = build(client, base_id, body())
    assert events == [
        (
            "error",
            {
                "status": 400,
                "detail": "There is nothing to build from yet. Add sources, or save an answer "
                "as knowledge.",
            },
        )
    ]
    assert fake.requests == []


def test_saved_knowledge_seeds_the_pool_so_its_passages_can_be_cited_without_a_search(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        session_id = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}
        ).json()["id"]
        turn = client.post(
            f"/api/sessions/{session_id}/messages", headers=SIDECAR, json={"content": "CD3D?"}
        ).json()
        unit_id = client.post(
            f"/api/sessions/{session_id}/messages/{turn['assistantMessage']['id']}/proposal",
            headers=SIDECAR,
            json={"title": "The T-cell marker"},
        ).json()["knowledgeUnitId"]
        fake.replies = [output_replies(ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."])]
        before = len(fake.requests)
        artifact = built(
            build(client, base_id, body(scope={"mode": "selection", "unitIds": [unit_id]}))
        )
        with client.app.state.knowledge_service.sessions() as session:
            bindings = session.scalars(
                select(ArtifactUnitBinding).where(ArtifactUnitBinding.artifact_id == artifact["id"])
            ).all()

    # The passage the saved answer cites is in the pool, and no search was needed for it.
    [citation] = artifact["citations"]
    assert (citation["sourceTitle"], citation["quote"]) == (
        "Markers",
        "CD3D is a marker of T cells.",
    )
    assert artifact["content"].endswith("CD3D marks T cells [1].")
    assert artifact["inputs"] == {"sources": 0, "units": 1, "sessions": 0}
    # The saved answer is pinned, as the old builder pinned them.
    assert artifact["acceptedUnitIds"] == [unit_id]
    assert [(b.unit_id, b.position) for b in bindings] == [(unit_id, 0)]
    new = fake.requests[before:]
    assert not any(is_grading(r) for r in new)
    # The planner and the writer read the saved answer as a note, not as a source.
    planning = next(r for r in new if is_outlining(r))
    assert "The T-cell marker" in planning["messages"][-1]["content"]
    writing = next(r for r in new if is_writing(r))
    assert "Saved knowledge" in writing["messages"][-1]["content"]


def test_a_saved_answers_passage_from_a_source_that_has_left_the_library_is_not_used(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake) as client:
        base_id, ids = library(client)
        session_id = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}
        ).json()["id"]
        turn = client.post(
            f"/api/sessions/{session_id}/messages", headers=SIDECAR, json={"content": "CD3D?"}
        ).json()
        unit_id = client.post(
            f"/api/sessions/{session_id}/messages/{turn['assistantMessage']['id']}/proposal",
            headers=SIDECAR,
            json={"title": "The T-cell marker"},
        ).json()["knowledgeUnitId"]
        # The source the answer cited goes to Trash; the saved answer stays.
        trashed = client.post(f"/api/sources/{ids['Markers']}/trash", headers=SIDECAR)
        assert trashed.status_code == 200
        fake.replies = [output_replies(ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."])]
        start = len(fake.requests)
        artifact = built(
            build(client, base_id, body(scope={"mode": "selection", "unitIds": [unit_id]}))
        )
    # Its passage is not the library's now: not cited, and no model was shown it.
    assert artifact["citations"] == []
    assert "[1]" not in artifact["content"]
    shown = json.dumps([r["messages"] for r in fake.requests[start:]])
    assert "CD3D is a marker of T cells." not in shown


def test_a_discussion_seeds_the_pool_too(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        session_id = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}
        ).json()["id"]
        client.post(
            f"/api/sessions/{session_id}/messages", headers=SIDECAR, json={"content": "CD3D?"}
        )
        fake.replies = [output_replies(ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."])]
        artifact = built(
            build(client, base_id, body(scope={"mode": "selection", "sessionIds": [session_id]}))
        )
    assert artifact["inputs"]["sessions"] == 1
    assert [c["sourceTitle"] for c in artifact["citations"]] == ["Markers"]
    assert artifact["acceptedUnitIds"] == []


def test_a_sentence_its_passages_do_not_state_loses_its_number_and_is_flagged(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        output_replies(ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."], unsupported=[1])
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        artifact = built(build(client, base_id, body()))
    assert artifact["content"] == "# Immune markers\n\n## T cells\n\nCD3D marks T cells [?]."
    assert artifact["citations"] == []
    assert artifact["sections"][0]["issues"] == [
        {
            "claim": "CD3D marks T cells.",
            "verdict": "unsupported",
            "note": "The sources it cites do not state this.",
        }
    ]
    check = next(r for r in fake.requests if is_support_checking(r))
    assert "cites 1: CD3D is a marker of T cells." in check["messages"][-1]["content"]


def test_a_claim_with_no_source_is_marked_and_stays_unverified(tmp_path: Path) -> None:
    written = ["## T cells\n\nCD3D marks T cells [1]. The moon is made of cheese."]
    fake = FakeProvider(
        output_replies(ONE_SECTION, written, unmarked=["The moon is made of cheese"])
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        artifact = built(build(client, base_id, body()))
    assert artifact["content"].endswith("CD3D marks T cells [1]. The moon is made of cheese [?].")
    assert artifact["sections"][0]["issues"] == [
        {
            "claim": "The moon is made of cheese",
            "verdict": "unverified",
            "note": "From the model's own knowledge; no source was found.",
        }
    ]


def test_a_claim_from_memory_that_the_sources_contradict_is_left_out(tmp_path: Path) -> None:
    written = ["## T cells\n\nCD3D marks T cells [1]. T cells are red [?]."]
    fake = FakeProvider(
        output_replies(ONE_SECTION, written, verdict={"supports": [], "contradicts": [1]})
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        artifact = built(build(client, base_id, body()))
    assert artifact["content"] == "# Immune markers\n\n## T cells\n\nCD3D marks T cells [1]."
    assert artifact["sections"][0]["issues"] == [
        {
            "claim": "T cells are red",
            "verdict": "contradicted",
            "note": "Left out: the sources disagree (Markers).",
        }
    ]


def test_without_a_model_or_when_a_model_fails_nothing_is_saved(tmp_path: Path) -> None:
    unused = FakeProvider("unused")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'one.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        deepseek_api_key=None,
        stt_provider="compatible",
        processing_worker_enabled=False,
        auth_token=SIDECAR_TOKEN,
        service_settings_file=tmp_path / "service-settings.json",
    )
    with TestClient(create_app(settings, model_client_factory=unused.factory)) as client:
        base_id, _ = library(client)
        events = build(client, base_id, body())
        assert versions(client) == []
    assert events == [
        (
            "error",
            {"status": 400, "detail": "Outputs need a model. Set one up under Settings → Models."},
        )
    ]
    assert unused.requests == []

    # A writer the provider refuses.
    broken = FakeProvider(
        output_replies(
            OUTLINE, lambda index, prompt: api_error(openai.AuthenticationError, 401, "no")
        )
    )
    with app_for(tmp_path / "two", broken) as client:
        base_id, _ = library(client)
        events = build(client, base_id, body())
        assert versions(client) == []
    assert events[-1] == (
        "error",
        {"status": 502, "detail": "DeepSeek did not accept the API key."},
    )
    assert [name for name, _ in events].count("outline") == 1

    # A planner that cannot be read.
    garbled = FakeProvider("this is not json")
    with app_for(tmp_path / "three", garbled) as client:
        base_id, _ = library(client)
        events = build(client, base_id, body())
        assert versions(client) == []
    [(name, error)] = events
    assert name == "error" and error["status"] == 502
    assert "could not read" in error["detail"]


def test_the_outputs_job_can_have_its_own_model_and_otherwise_follows_ask(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."]))
    with app_for(tmp_path, fake) as client:
        client.post(
            "/api/settings/providers",
            headers=SIDECAR,
            json={
                "kind": "kimi",
                "apiKey": "sk-test-0000000000001234",
                "models": ["kimi-k3", "kimi-k2.6"],
            },
        )
        def roles() -> dict[str, dict]:
            found = client.get("/api/settings/models", headers=SIDECAR).json()["roles"]
            return {role["id"]: role for role in found}

        saved = client.app.state.service_settings
        # Ask moves, and Outputs, with no model of its own, goes with it.
        client.put(
            "/api/settings/model-roles",
            headers=SIDECAR,
            json={"roles": {"ask": {"model": "kimi/kimi-k3", "effort": "max"}}},
        )
        assert roles()["outputs"]["model"] == "kimi/kimi-k3"
        assert roles()["outputs"]["effort"] == "high"
        base_id, _ = library(client)
        artifact = built(build(client, base_id, body()))
        assert artifact["provenance"]["model"]["ref"] == "kimi/kimi-k3"
        # Saving another job does not pin it to Ask's model.
        client.put(
            "/api/settings/model-roles",
            headers=SIDECAR,
            json={"roles": {"photos": {"model": "kimi/kimi-k3", "effort": "low"}}},
        )
        assert "outputs" not in saved.models()[1]
        # Choosing one for it does.
        client.put(
            "/api/settings/model-roles",
            headers=SIDECAR,
            json={"roles": {"outputs": {"model": "deepseek/deepseek-v4-pro", "effort": "low"}}},
        )
        client.put(
            "/api/settings/model-roles",
            headers=SIDECAR,
            json={"roles": {"ask": {"model": "deepseek/deepseek-flash", "effort": "high"}}},
        )
        assert saved.models()[1]["outputs"] == {
            "model": "deepseek/deepseek-v4-pro",
            "effort": "low",
        }
        assert roles()["outputs"]["model"] == "deepseek/deepseek-v4-pro"
        second = built(build(client, base_id, body()))
    assert second["provenance"]["model"] == {
        "ref": "deepseek/deepseek-v4-pro",
        "label": "DeepSeek · V4 Pro",
        "effort": "low",
    }


def test_the_same_request_is_answered_from_what_it_saved(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        request = body()
        first = built(build(client, base_id, request))
        calls = len(fake.requests)
        again = build(client, base_id, request)
        different = build(client, base_id, {**request, "audience": "student"})
        stored = versions(client)
    assert again == [("done", first)]
    assert len(fake.requests) == calls and len(stored) == 1
    assert different == [
        (
            "error",
            {
                "status": 409,
                "detail": "clientRequestId was already used for a different Artifact request",
            },
        )
    ]


# Rebuilding, editing, revising and checking -------------------------------------------------------


def test_a_rebuild_with_an_edited_outline_skips_the_planner_and_is_the_next_version(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        output_replies(OUTLINE, lambda index, prompt: f"## {heading(prompt)}\n\nA fact [1].")
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        first = built(build(client, base_id, body()))
        planners = sum(is_outlining(r) for r in fake.requests)
        edited = [
            {"heading": "B cells", "goal": "Start with B cells"},
            {"heading": "T cells", "goal": ""},
        ]
        second = built(
            build(client, base_id, body(outline=edited, supersedesArtifactId=first["id"]))
        )
        stale = build(client, base_id, body(supersedesArtifactId=first["id"]))

    assert planners == 1 and sum(is_outlining(r) for r in fake.requests) == 1
    assert (second["versionNumber"], second["origin"]) == (2, "rebuild")
    assert second["lineageId"] == first["lineageId"]
    assert second["supersedesArtifactId"] == first["id"]
    assert second["title"] == "Immune markers"
    assert [item["heading"] for item in second["outline"]] == ["B cells", "T cells"]
    assert [s["heading"] for s in second["sections"]] == ["B cells", "T cells"]
    # Each section was searched by its heading and goal.
    searches = [
        r["messages"][-1]["content"] for r in fake.requests if is_grading(r)
    ]
    assert any("Search: B cells Start with B cells" in prompt for prompt in searches)
    # Only the latest version can be built on.
    [(name, error)] = stale
    assert name == "error" and error["status"] == 409 and "latest version" in error["detail"]


CLAIM = "CD19 marks B cells, and a good deal more besides in some tissues"


def test_an_edit_keeps_the_typed_text_and_checks_only_what_it_did_not_change(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT, unmarked=[CLAIM]))
    with built_output(tmp_path, fake) as (client, base_id, first):
        headers = workspace(client)
        typed = first["content"].replace("CD19 marks B cells [1].", f"{CLAIM} [1].")
        assert typed != first["content"]
        request = {"clientRequestId": "edit_request_0001", "content": typed}
        response = client.post(route(base_id, first["id"], "/edits"), headers=headers, json=request)
        assert response.status_code == 201
        second = response.json()
        again = client.post(route(base_id, first["id"], "/edits"), headers=headers, json=request)
        stale = client.post(
            route(base_id, first["id"], "/edits"),
            headers=headers,
            json={"clientRequestId": "edit_request_0002", "content": typed},
        )

        # Exactly as typed; the passages are the ones the version had.
        assert second["content"] == typed
        assert second["citations"] == first["citations"]
        assert (second["origin"], second["versionNumber"]) == ("edit", 2)
        assert second["lineageId"] == first["lineageId"]
        assert second["modelLabel"] is None and second["provenance"]["model"] is None
        assert second["scope"] == first["scope"] and second["outline"] == first["outline"]
        # The section that was not touched keeps its check; the typed one is not re-checked.
        assert [s["checked"] for s in second["sections"]] == [True, False]
        assert again.status_code == 201 and again.json() == second
        assert stale.status_code == 409 and "latest" in stale.json()["detail"]
        assert client.get(route(base_id, first["id"]), headers=headers).json() == first

        # Checking again looks at the typed section only, and never changes the version.
        before = len(fake.requests)
        checked = client.post(route(base_id, second["id"], "/check"), headers=headers).json()
        calls = fake.requests[before:]
        assert [s["checked"] for s in checked["sections"]] == [True, True]
        assert checked["sections"][1]["issues"] == [
            {"claim": CLAIM, "verdict": "unsourced", "note": "No source is cited for this."}
        ]
        for field in ("content", "contentHash", "manifestHash", "citations", "versionNumber"):
            assert checked[field] == second[field]
        assert sum(is_auditing(r) for r in calls) == 1
        assert sum(is_support_checking(r) for r in calls) == 1
        # Nothing is left to check: no calls and no new findings.
        again_checked = client.post(route(base_id, second["id"], "/check"), headers=headers).json()
        assert again_checked == checked and len(fake.requests) == before + len(calls)
        with client.app.state.knowledge_service.sessions() as session:
            rows = session.scalar(
                select(func.count())
                .select_from(ArtifactCheck)
                .where(ArtifactCheck.artifact_id == second["id"])
            )
        assert rows == 2  # what the edit carried over, and what checking added


def test_a_typed_number_without_a_passage_is_flagged_when_checked(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with built_output(tmp_path, fake) as (client, base_id, first):
        headers = workspace(client)
        typed = first["content"].replace("CD19 marks B cells [1].", "CD19 marks B cells [9].")
        second = client.post(
            route(base_id, first["id"], "/edits"),
            headers=headers,
            json={"clientRequestId": "edit_request_0003", "content": typed},
        ).json()
        checked = client.post(route(base_id, second["id"], "/check"), headers=headers).json()
    assert second["content"].endswith("CD19 marks B cells [9].")
    [issue] = checked["sections"][1]["issues"]
    assert (issue["claim"], issue["verdict"]) == ("CD19 marks B cells.", "unsupported")
    assert issue["note"] == "Its number has no source in the list."


def test_a_version_from_the_old_builder_cannot_be_changed_but_can_be_rebuilt(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        session_id = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}
        ).json()["id"]
        turn = client.post(
            f"/api/sessions/{session_id}/messages", headers=SIDECAR, json={"content": "CD3D?"}
        ).json()
        unit_id = client.post(
            f"/api/sessions/{session_id}/messages/{turn['assistantMessage']['id']}/proposal",
            headers=SIDECAR,
            json={"title": "The T-cell marker"},
        ).json()["knowledgeUnitId"]
        fake.replies = [output_replies(OUTLINE, REPORT)]
        headers = workspace(client)
        legacy_id = insert_legacy_version(
            client.app.state.knowledge_service.sessions,
            workspace_id=headers["X-Gunther-Workspace-Id"],
            base_id=base_id,
            unit_id=unit_id,
            request_id="legacy_request_0001",
        )
        asked = len(fake.requests)

        edit = client.post(
            route(base_id, legacy_id, "/edits"),
            headers=headers,
            json={"clientRequestId": "edit_request_0004", "content": "# Typed"},
        )
        check = client.post(route(base_id, legacy_id, "/check"), headers=headers)
        revised = revise(client, base_id, legacy_id, "Shorter")
        # Nothing was asked of a model, and the old version is as it was.
        assert len(fake.requests) == asked
        legacy = client.get(route(base_id, legacy_id), headers=headers).json()

        # Building on it is allowed: the next version of the same output.
        rebuilt = built(build(client, base_id, body(supersedesArtifactId=legacy_id)))

    for refused in (edit, check):
        assert refused.status_code == 400 and "old builder" in refused.json()["detail"]
    assert revised[-1][0] == "error" and revised[-1][1]["status"] == 400
    assert "old builder" in revised[-1][1]["detail"]
    assert legacy["origin"] == "legacy"
    assert (rebuilt["versionNumber"], rebuilt["origin"]) == (2, "rebuild")
    assert rebuilt["lineageId"] == legacy["lineageId"]
    assert rebuilt["supersedesArtifactId"] == legacy_id


def test_a_revision_changes_one_section_and_keeps_the_checks_of_the_others(
    tmp_path: Path,
) -> None:
    def written(index: int, prompt: str) -> str:
        if "This section now:" in prompt:
            assert "Instruction: Say it more briefly" in prompt
            return "## B cells\n\nCD19 marks B cells, briefly, in the sources [1]."
        return REPORT[index]

    fake = FakeProvider(output_replies(OUTLINE, written))
    with built_output(tmp_path, fake) as (client, base_id, first):
        before = len(fake.requests)
        events = revise(client, base_id, first["id"], "Say it more briefly", sectionIndex=1)
        second = built(events)
        calls = fake.requests[before:]
        stale = revise(client, base_id, first["id"], "Again")
        assert client.get(route(base_id, first["id"]), headers=workspace(client)).json() == first

    assert (second["origin"], second["versionNumber"]) == ("revise", 2)
    assert second["lineageId"] == first["lineageId"]
    old = split_document(first["content"], "report")[1]
    new = split_document(second["content"], "report")[1]
    assert new[0] == old[0]
    assert new[1] == "## B cells\n\nCD19 marks B cells, briefly, in the sources [1]."
    assert second["content"].startswith(first["content"].split("## T cells")[0])
    # The section that was left alone kept its check; the changed one was checked again.
    assert [s["checked"] for s in second["sections"]] == [True, True]
    assert sum(is_writing(r) for r in calls) == 1
    assert sum(is_auditing(r) for r in calls) == 1
    # The numbers of the passages it already cited are unchanged.
    assert [c["sourceTitle"] for c in second["citations"]] == ["Receptors", "Markers"]
    assert any(name == "text" and d["section"] == 1 for name, d in events)
    assert not any(name == "text" and d["section"] == 0 for name, d in events)
    # Only the latest version can be revised.
    assert stale[-1][0] == "error" and stale[-1][1]["status"] == 409


def test_revising_the_whole_output_rewrites_only_the_sections_the_instruction_touches(
    tmp_path: Path,
) -> None:
    def written(index: int, prompt: str) -> str:
        if "This section now:" in prompt:
            if heading(prompt) == "T cells":
                return "UNCHANGED"
            return "## B cells\n\nOnly B cells matter here, and CD19 is their marker [1]."
        return REPORT[index]

    fake = FakeProvider(output_replies(OUTLINE, written))
    with built_output(tmp_path, fake) as (client, base_id, first):
        before = len(fake.requests)
        second = built(revise(client, base_id, first["id"], "Keep to B cells"))
        calls = fake.requests[before:]
    assert sum(is_writing(r) for r in calls) == 2
    # The section left as it was is not checked again.
    assert sum(is_auditing(r) for r in calls) == 1
    old = split_document(first["content"], "report")[1]
    new = split_document(second["content"], "report")[1]
    assert new[0] == old[0]
    assert new[1] == "## B cells\n\nOnly B cells matter here, and CD19 is their marker [1]."
    assert [s["checked"] for s in second["sections"]] == [True, True]


def test_a_version_made_by_agents_is_sealed_and_tampering_is_caught(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with built_output(tmp_path, fake) as (client, base_id, first):
        headers = workspace(client)
        sessions = client.app.state.knowledge_service.sessions

        def set_field(field: str, value) -> object:
            with sessions() as session:
                row = session.get(Artifact, first["id"])
                before = getattr(row, field)
                setattr(row, field, value)
                session.commit()
            return before

        def status() -> tuple[int, int]:
            listed = client.get(f"/api/knowledge-bases/{base_id}/artifacts", headers=headers)
            return client.get(route(base_id, first["id"]), headers=headers).status_code, (
                listed.status_code
            )

        assert status() == (200, 200)
        for field, damaged in (
            ("content", "X" + first["content"][1:]),
            ("citations_json", "[]"),
            ("scope_json", '{"mode":"library"}'),
            ("brief", "changed"),
            ("outline_json", "[]"),
            ("kind", "slides"),
            ("style", "decision_brief"),
            ("origin", "edit"),
        ):
            original = set_field(field, damaged)
            assert status() == (409, 409), field
            set_field(field, original)
            assert status() == (200, 200), field


def test_a_second_build_waits_and_a_page_that_comes_back_follows_the_first(
    tmp_path: Path,
) -> None:
    gate = threading.Event()

    def written(index: int, prompt: str) -> str:
        assert gate.wait(10)
        return REPORT[index]

    fake = FakeProvider(output_replies(OUTLINE, written))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        headers = workspace(client)
        runs = client.app.state.answer_runs
        key = f"outputs:{base_id}"
        first, first_box = in_background(lambda: build(client, base_id, body(), headers))
        wait_for(lambda: writing(runs.get(key)))
        busy = client.post(
            f"/api/knowledge-bases/{base_id}/outputs/build/stream", headers=headers, json=body()
        )
        assert busy.status_code == 409
        assert busy.json()["detail"] == "Gunther is still building an output here."

        def follow() -> list:
            with client.stream(
                "GET", f"/api/knowledge-bases/{base_id}/outputs/build/stream", headers=headers
            ) as response:
                return read_events(response)

        again, again_box = in_background(follow)
        wait_for(lambda: len(runs.get(key)._watchers) == 2)
        gate.set()
        first.join(10)
        again.join(10)
        after = client.get(
            f"/api/knowledge-bases/{base_id}/outputs/build/stream", headers=headers
        )
        assert len(versions(client)) == 1

    [followed] = again_box
    assert followed[0][0] == "resumed" and followed[-1][0] == "done"
    assert followed[-1][1]["id"] == first_box[0][-1][1]["id"]
    assert followed[1][0] == "outline"
    assert after.status_code == 204


def writing(run) -> bool:
    return run is not None and any(
        name == "section" and data["state"] == "writing" for name, data in run.events
    )


def test_stopping_a_build_saves_nothing(tmp_path: Path) -> None:
    gate = threading.Event()

    def written(index: int, prompt: str) -> str:
        assert gate.wait(10)
        return REPORT[index]

    fake = FakeProvider(output_replies(OUTLINE, written))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        headers = workspace(client)
        runs = client.app.state.answer_runs
        key = f"outputs:{base_id}"
        first, first_box = in_background(lambda: build(client, base_id, body(), headers))
        wait_for(lambda: writing(runs.get(key)))
        stopped = client.post(
            f"/api/knowledge-bases/{base_id}/outputs/build/stop", headers=headers
        )
        assert stopped.status_code == 204
        gate.set()
        first.join(10)
        assert versions(client) == []
        # Once it has ended, another can start.
        wait_for(lambda: runs.get(key) is None)
        fake.replies = [output_replies(OUTLINE, REPORT)]
        built(build(client, base_id, body()))
    assert first_box[0][-1] == ("stopped", {"type": "stopped"})


def test_deleting_a_library_forever_removes_what_the_checker_found(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with built_output(tmp_path, fake) as (client, base_id, first):
        second = built(
            build(client, base_id, body(supersedesArtifactId=first["id"]), workspace(client))
        )
        assert second["versionNumber"] == 2
        client.post(f"/api/knowledge-bases/{base_id}/trash", headers=SIDECAR)
        assert client.delete(f"/api/trash/library/{base_id}", headers=SIDECAR).status_code == 200
        with client.app.state.knowledge_service.sessions() as session:
            assert session.scalar(select(func.count()).select_from(Artifact)) == 0
            assert session.scalar(select(func.count()).select_from(ArtifactCheck)) == 0
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
