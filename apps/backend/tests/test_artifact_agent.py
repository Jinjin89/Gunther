# ruff: noqa: E501
"""The Artifacts agent: the orchestrator's decisions, what code lets through, what each action
does to the workspace, and what a turn costs. The models are fakes with scripted decisions."""

import itertools
import json
import re
import threading
from pathlib import Path
from typing import Any

import pytest
from fake_models import (
    FakeProvider,
    is_answering,
    is_orchestrating,
    is_sentence_checking,
    is_support_checking,
    orchestrator_replies,
    output_replies,
    studio_task,
)
from sqlalchemy import select
from test_artifact_steps import replacing
from test_output_skills import (
    SLIDES,
    SLIDES_APPROACH,
    SLIDES_OUTLINE,
    saved_skill_output,
)
from test_outputs import (
    app_for,
    in_background,
    library,
    read_events,
    route,
    saved_output,
    wait_for,
    workspace,
)

from gunther.agent import checker_inventory
from gunther.artifact_agent import (
    Act,
    AgentLimits,
    Gate,
    Numbering,
    View,
    audience_of,
    build_view,
    ranges,
    validate,
)
from gunther.artifact_workspace import (
    ConversationBrief,
    Line,
    Pending,
    Workspace,
    evidence_from_citation,
)
from gunther.checks import claim_text
from gunther.llm import ModelError
from gunther.models import (
    Artifact,
    ArtifactCheck,
    ArtifactConversation,
    ArtifactMessage,
    SourceIndexHead,
    SourceRevision,
)
from gunther.outputs import (
    Pool,
    check_heading_refs,
    section_hash,
    split_document,
    support_inventory,
)
from gunther.schemas import ConversationCitationOut
from gunther.service import KnowledgeService, manifest_hash_of

_turns = itertools.count(1)


def conversations(base_id: str, tail: str = "") -> str:
    return f"/api/knowledge-bases/{base_id}/artifact-conversations{tail}"


# A conversation with a deck in it ----------------------------------------------------------------

REWRITTEN = (
    "## CD3D and CD19 set T and B cells apart\n\n- CD3D marks T cells [1]\n- CD19 marks B cells [2]"
)


def deck_replies(written: Any = SLIDES, **more: Any):
    return output_replies(SLIDES_OUTLINE, written, **more)


def script(decisions: list[Any], inner=None, **more: Any):
    return orchestrator_replies(decisions, inner or deck_replies(), **more)


def a_deck(client, fake: FakeProvider, base_id: str, cid: str = "talk") -> str:
    """A saved Skills deck, which the conversation picks up as its work."""

    fake.replies[:] = [deck_replies()]
    version = saved_skill_output(
        client, base_id, kind="slides", conversationId=cid, conversationTitle="Markers talk",
    )
    client.get(conversations(base_id), headers=workspace(client))  # gives it its row and work
    return version["id"]


def turn(client, base_id: str, cid: str, text: str, kind: str = "message", **fields: Any):
    request = {
        "clientRequestId": f"turn_request_{next(_turns):04d}",
        "kind": kind,
        "text": text,
        **fields,
    }
    with client.stream(
        "POST",
        conversations(base_id, f"/{cid}/messages/stream"),
        headers=workspace(client),
        json=request,
    ) as response:
        assert response.status_code == 200, response.read()
        return read_events(response)


def done(events: list[tuple[str, dict]]) -> dict:
    assert events[-1][0] == "done", events[-1]
    return events[-1][1]


def kept(client, cid: str) -> tuple[Workspace, ConversationBrief]:
    with client.app.state.knowledge_service.sessions() as session:
        row = session.get(ArtifactConversation, cid)
        return KnowledgeService._load_workspace(row)


def versions_of(client) -> list[Artifact]:
    with client.app.state.knowledge_service.sessions() as session:
        return list(session.scalars(select(Artifact).order_by(Artifact.created_at)))


def names(events: list[tuple[str, dict]], kind: str, state: str | None = None) -> list[str]:
    return [
        data["label"]
        for name, data in events
        if name == kind and (state is None or data.get("state") == state)
    ]


def requests_since(fake: FakeProvider, before: int) -> list[dict]:
    return fake.requests[before:]


def calls(fake: FakeProvider, before: int) -> int:
    return len(fake.requests) - before


def part_writes(fake: FakeProvider, before: int) -> list[str]:
    return [
        request["messages"][0]["content"] for request in fake.requests[before:]
        if "working on one slide" in request["messages"][0]["content"]
    ]


def works_text(client, cid: str) -> list[str]:
    workspace_, _ = kept(client, cid)
    work = workspace_.works[0]
    return [work.parts[item.id].text for item in work.outline]


def seen_by_the_orchestrator(fake: FakeProvider, before: int = 0) -> list[str]:
    return [
        str(request["messages"][-1]["content"])
        for request in fake.requests[before:]
        if is_orchestrating(request)
    ]


@pytest.mark.parametrize("mode", ["selection", "library"])
@pytest.mark.parametrize("processing", ["ready", "partial"])
def test_selected_documents_reach_the_first_decision_before_evidence_is_loaded(
    tmp_path: Path, mode: str, processing: str
) -> None:
    selected = "Insulated rooms stayed cooler during the afternoon trial."
    fake = FakeProvider(script([{"actions": [{"do": "reply", "text": "Material is available."}]}]))
    with app_for(tmp_path, fake) as client:
        base_id, ids = library(client, title="Experiments", notes={
            "Thermal measurements": selected,
            "Unselected garden notes": "Watering changed the growth rate.",
        })
        with client.app.state.knowledge_service.sessions() as session:
            head = session.get(SourceIndexHead, ids["Thermal measurements"])
            session.get(SourceRevision, head.revision_id).state = processing
            session.commit()
        scope = {"mode": mode, "sourceIds": [ids["Thermal measurements"]]}
        created = client.post(
            conversations(base_id), headers=workspace(client),
            json={"id": "material-first", "scope": scope},
        )
        assert created.status_code == 201, created.text
        done(turn(client, base_id, "material-first", "Create slides from the selected material."))
        first = seen_by_the_orchestrator(fake)[0]
        assert "Material available in the chosen scope" in first
        assert "Thermal measurements" in first and selected in first
        assert "no evidence passages loaded yet; the material above is available" in first
        assert ("Unselected garden notes" in first) == (mode == "library")


def test_studio_understand_and_replan_refresh_the_approach_after_narrowing_scope(tmp_path: Path) -> None:
    def analyst(request):
        prompt = str(request["messages"][-1]["content"])
        assert "lead analyst" in prompt and "Focus for this reading: operating cost" in prompt
        assert "[2] (Cost records" in prompt and "The repairs cost 70 dollars" in prompt
        assert "Garden" not in prompt
        return json.dumps({
            "answer": "The repairs cost 70 dollars.",
            "insights": [{"claim": "Repair cost", "refs": [2, 999]}],
        })

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        settings = client.get("/api/settings/developer", headers=workspace(client)).json()
        assert settings == {"traces": False}
        base_id, ids = library(client, notes={
            "Estimates": "Estimates are collected separately.",
            "Cost records": "The repairs cost 70 dollars.", "Garden": "Grass seed grew.",
        })
        fake.replies[:] = [orchestrator_replies([{"actions": [
            {"do": "select", "artifact": "new:report"},
            {"do": "understand", "focus": "operating cost"},
            {"do": "reply", "text": "The approach is ready."},
        ]}], analyst)]
        client.post(conversations(base_id), headers=workspace(client), json={
            "id": "understand",
            "scope": {"mode": "selection", "sourceIds": [ids["Estimates"], ids["Cost records"]]},
        }).raise_for_status()
        result = done(turn(client, base_id, "understand", "Understand the operating cost first."))
        assert result["outcome"] == "replied" and result["artifact"] is None
        workspace_, _ = kept(client, "understand")
        (work,) = workspace_.works
        assert work.approach["method"] == "studio" and work.approach["storyline"] is None
        assert work.outline == [] and work.parts == {} and versions_of(client) == []
        (citation,) = workspace_.pool
        assert citation["source_id"] == ids["Cost records"] and citation["ref"] == 1
        assert work.cards[0].refs == [citation["ref"]]
        assert work.approach["memo"]["insights"][0]["refs"] == [citation["ref"]]

        narrowed = {"mode": "selection", "sourceIds": [ids["Estimates"]]}
        client.patch(conversations(base_id, "/understand"), headers=workspace(client), json={
            "scope": narrowed,
        }).raise_for_status()
        tasks = []

        def replan(request):
            prompt = str(request["messages"][-1]["content"])
            assert "The repairs cost 70 dollars" not in prompt and "Cost records" not in prompt
            if "lead analyst" in prompt:
                tasks.append("understand")
                (ref,) = re.findall(r"\[(\d+)\] \(Estimates", prompt)
                return json.dumps({
                    "answer": "Estimates are collected separately.",
                    "insights": [{"claim": "Separate estimates", "refs": [int(ref)]}],
                })
            if "planning a written report" in prompt:
                tasks.append("plan")
                assert "Answer: Estimates are collected separately." in prompt
                return '{"title":"Estimates","sections":[{"heading":"How estimates are collected"}]}'
            assert "senior partner reviewing a storyline" in prompt
            return '{"verdict":"ready"}'

        fake.replies[:] = [orchestrator_replies([{"actions": [
            {"do": "plan", "change": "whole"},
            {"do": "reply", "text": "The outline is ready."},
        ]}], replan)]
        result = done(turn(client, base_id, "understand", "Plan from the remaining source."))
        assert result["outcome"] == "replied" and result["artifact"] is None
        assert tasks == ["understand", "plan"]
        after, _ = kept(client, "understand")
        (work,) = after.works
        assert work.approach["answer"] == "Estimates are collected separately."
        (card,) = work.cards
        assert card.claim == "Separate estimates" and card.refs == [2]
        assert after.pool[1]["source_id"] == ids["Estimates"]


def test_studio_replan_migrates_a_saved_plan_within_its_constraints(tmp_path: Path) -> None:
    headings = [row["heading"] for row in SLIDES_OUTLINE["sections"]]
    goals = ["Set the topic", "Compare the measurements", "Draw the conclusion"]
    plan = {**SLIDES_OUTLINE, "sections": [
        {**row, "goal": goal, "id": f"saved-{n}"}
        for n, (row, goal) in enumerate(zip(SLIDES_OUTLINE["sections"], goals, strict=True))
    ]}
    instruction = "Retitle the comparison and replace the last slide with limits under its old title"
    counts = "请做3页幻灯片，用2张图，演讲8分钟。"
    retitled = "What the measurements tell us"
    valid = False

    def planner(request):
        prompt = str(request["messages"][-1]["content"])
        if "senior partner reviewing a storyline" in prompt:
            return '{"verdict": "ready"}'
        assert "presentation strategist" in prompt and "lead analyst" not in prompt
        assert counts in prompt and "Avoid jargon" in prompt
        assert "requested_count" in prompt and "requested_minutes" in prompt
        assert instruction in prompt and "Garden" not in prompt
        assert json.dumps([
            {"id": f"saved-{n}", "heading": h, "goal": g}
            for n, (h, g) in enumerate(zip(headings, goals, strict=True))
        ]) in prompt
        assert "Old planner message" not in prompt and "introduces the distinction" not in prompt
        (ref,) = re.findall(r"\[(\d+)\] \(Receptors", prompt)
        slides = [
            {"title": headings[0], "job": goals[0], "layout": "title", "id": "saved-0"},
            {"title": retitled, "job": goals[1], "layout": "points", "refs": [int(ref)], "id": "saved-1"},
            {"title": headings[2], "job": "Explain the limits", "id": None, "refs": [999]},
        ]
        return json.dumps({
            "title": "Proposed title", "slides": slides if valid else slides[:2],
            "requested_count": 3, "requested_minutes": 8,
        })

    def decisions():
        return orchestrator_replies([{"actions": [
            {"do": "plan", "change": "whole", "instruction": instruction},
            {"do": "reply", "text": "The outline is ready."},
        ]}], planner)

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        service = client.app.state.knowledge_service
        base_id, ids = library(client, notes={
            "Cells": "CD3D marks T cells.", "Receptors": "CD19 marks B cells.",
            "Garden": "Grass seed grew.",
        })
        headers = workspace(client)
        scope = {"mode": "selection", "sourceIds": [ids["Cells"], ids["Receptors"]]}
        first = saved_output(client, base_id, plan, SLIDES, kind="slides", scope=scope,
                             approach=SLIDES_APPROACH, conversationId="replan")
        with service.sessions() as session:
            row = session.get(Artifact, first["id"])
            outline = json.loads(row.outline_json)
            outline[1].update(message="Old planner message", layout_locked=True, design_locked=True)
            row.outline_json = json.dumps(outline)
            row.manifest_hash = manifest_hash_of(row)
            session.commit()
        client.get(conversations(base_id), headers=headers).raise_for_status()
        url = conversations(base_id, "/replan")
        client.patch(url + "/brief", headers=headers, json={
            "constraints": [counts, "Avoid jargon"],
        }).raise_for_status()
        before, _ = kept(client, "replan")
        fake.replies[:] = [decisions()]
        failed = turn(client, base_id, "replan", instruction)
        assert any("The brief asks for 3" in data.get("note", "") for _, data in failed)
        assert not any(event == "outline" for event, _ in failed)
        assert kept(client, "replan")[0].works == before.works

        valid = True
        fake.replies[:] = [decisions()]
        completed = done(turn(client, base_id, "replan", instruction))
        assert completed["outcome"] == "replied" and completed["artifact"] is None
        after, _ = kept(client, "replan")
        (work,) = after.works
        assert (work.key, work.title, work.version_id) == (
            first["lineageId"], first["title"], first["id"],
        )
        assert work.approach["method"] == "studio" and work.version_map == before.works[0].version_map
        assert [(item.heading, item.goal) for item in work.outline] == list(zip(
            [headings[0], retitled, headings[2]], [*goals[:2], "Explain the limits"], strict=True,
        ))
        story = work.approach["storyline"]
        assert (story["requested_count"], story["requested_minutes"]) == (3, 8)
        assert [item.id for item in work.outline[:2]] == ["saved-0", "saved-1"]
        assert work.outline[2].id != "saved-2"
        for item in work.outline[:2]:
            assert work.parts[item.id].text == before.works[0].parts[item.id].text
            assert work.parts[item.id].checked == before.works[0].parts[item.id].checked
            assert not (item.layout_locked or item.design_locked or item.design or item.message or item.link)
        assert not work.outline[2].refs and work.outline[2].id not in work.parts
        assert work.outline[1].refs and all(row["source_id"] != ids["Garden"] for row in after.pool)
        assert [v.id for v in versions_of(client)] == [first["id"]]
        detail = client.get(url, headers=headers).json()
        assert detail["scope"]["sourceIds"] == scope["sourceIds"]
        assert [part["state"] for part in detail["works"][0]["parts"]] == [
            "ok", "out_of_date", "not_written",
        ]


def test_a_vague_request_is_asked_about_and_the_answer_makes_the_deck(tmp_path: Path) -> None:
    """§2.9, examples 1 and 2: start an artifact, look at the library, ask; the answer resumes
    the turn: understand, plan, write, review, save."""

    first = [
        {
            "brief": [
                {"field": "audience", "text": "lab members", "quote": "my lab meeting"},
            ],
            "actions": [
                {"do": "select", "artifact": "new:slides"},
                {"do": "search_library", "query": "CD3D markers"},
            ],
        },
        {
            "actions": [
                {
                    "do": "ask",
                    "question": "Focus on the Velmora trial results, or the whole debate?",
                    "options": ["Trial", "Whole debate", "Just start"],
                }
            ]
        },
    ]
    second = [
        {
            "brief": [
                {"field": "goal", "text": "The Velmora trial results", "quote": "Trial"},
                {"field": "length", "text": "about 10 minutes", "quote": "about 10 minutes"},
            ],
            "actions": [
                {"do": "understand"},
                {"do": "plan", "change": "whole"},
            ],
        },
        {"actions": [{"do": "write", "parts": [1, 2, 3]}]},
        {"actions": [{"do": "review"}]},
        {"actions": [{"do": "save", "text": "Made the first version of the talk."}]},
    ]
    fake = FakeProvider(script(first))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        client.post(conversations(base_id), headers=workspace(client), json={"id": "roofs"})

        events = turn(
            client, base_id, "roofs", "Slides on cool roofs for my lab meeting", web=False
        )
        end = done(events)
        assert end["outcome"] == "asked"
        assert end["question"] == {
            "text": "Focus on the Velmora trial results, or the whole debate?",
            "options": ["Trial", "Whole debate", "Just start"],
        }
        assert end["artifact"] is None and end["changes"] == []
        assert end["message"]["kind"] == "question"
        # The events: the turn, the brief, each decision with its actions, the question.
        assert events[0][0] == "turn" and events[0][1]["conversationId"] == "roofs"
        assert "brief" in [name for name, _ in events]
        decisions = [data for name, data in events if name == "decision"]
        assert [[a["do"] for a in d["actions"]] for d in decisions] == [
            ["select", "search_library"],
            ["ask"],
        ]
        assert names(events, "action", "done")[0] == "Started a deck"
        assert any(
            label.startswith("Searched your library for “CD3D markers”")
            for label in names(events, "action", "done")
        )
        assert [data["text"] for name, data in events if name == "question"] == [
            "Focus on the Velmora trial results, or the whole debate?"
        ]
        kept_workspace, brief = kept(client, "roofs")
        assert brief.audience == Line(text="lab members", mark="you")
        [work] = kept_workspace.works
        assert work.key.startswith("draft_") and work.spec.kind == "slides"
        assert kept_workspace.pending is not None and kept_workspace.asked_in_a_row == 1
        assert kept_workspace.pending.message_id == end["message"]["id"]
        assert len(kept_workspace.pool) >= 1
        listed = client.get(conversations(base_id), headers=workspace(client)).json()
        assert listed[0]["waiting"] is True and listed[0]["title"].startswith("Slides on cool")

        # The answer resumes it and the deck is made.
        fake.replies[:] = [script(second)]
        before = len(fake.requests)
        events = turn(client, base_id, "roofs", "Trial, about 10 minutes", kind="answer")
        end = done(events)
        assert end["outcome"] == "saved" and end["artifact"]["versionNumber"] == 1
        assert end["reply"] == "Made the first version of the talk."
        provenance = end["artifact"]["provenance"]
        assert provenance["generator"] == "gunther.artifact-agent.v1"
        assert provenance["conversationId"] == "roofs" and provenance["reply"] == end["reply"]
        assert provenance["request"] == "Trial, about 10 minutes"
        assert "work" not in provenance
        assert provenance["skill"]["name"] == "studio" and provenance["audience"] == "scientist"
        assert provenance["use"] == "talk"
        assert end["artifact"]["origin"] == "build"
        assert [c["what"] for c in end["changes"]] == ["added", "added", "added"]
        # The first decision of the answer saw the question and the answer.
        view = seen_by_the_orchestrator(fake, before)[0]
        assert 'You asked: "Focus on the Velmora trial results' in view
        assert "Latest message (answer to your question):" in view
        kept_workspace, brief = kept(client, "roofs")
        [work] = kept_workspace.works
        assert work.key == end["artifact"]["lineageId"] and work.version_id == end["artifact"]["id"]
        assert kept_workspace.active == work.key and kept_workspace.pending is None
        assert kept_workspace.asked_in_a_row == 0
        assert work.version_map and work.skipped == []
        assert brief.goal == Line(text="The Velmora trial results", mark="you")
        assert work.spec.length == Line(text="about 10 minutes", mark="you")
        assert kept_workspace.log[-1].startswith("v1 (deck): Made the first version of the talk.")
        assert kept_workspace.log[-1].endswith("added 1–3")
        assert len(part_writes(fake, before)) == 3
        # The version has the deck, a marker on each slide, and its work is sealed with it.
        [saved] = versions_of(client)
        assert json.loads(saved.provenance_json)["work"]["key"].startswith("draft_")
        assert len(split_document(saved.content, "slides")[1]) == 3


# Changes to a deck that exists ---------------------------------------------------------------------

SHORT_TWO = (
    "## CD3D and CD19 set T and B cells apart\n\n| | T | B |\n| --- | --- | --- |\n"
    "| Marker | CD3D [1] | CD19 [2] |"
)
SHORT_THREE = "## Use both in every panel\n\n- CD3D for T cells [1]"


def shortening(index: int, prompt: str) -> str:
    return {1: SHORT_TWO, 2: SHORT_THREE}[index]


def by_passage(content: str, citations: list[dict], kind: str = "slides") -> list[str]:
    """The parts of a version with each source number replaced by what the source quotes, which
    is what a part is when saving has renumbered the sources."""

    quote = {c["ref"]: c["quote"] for c in citations}
    return [
        re.sub(r"\[(\d+)\]", lambda m: f"[{quote.get(int(m.group(1)), m.group(1))}]", part)
        for part in split_document(content, kind)[1]
    ]


def head_of(client, base_id: str, artifact_id: str) -> dict:
    return client.get(
        f"/api/knowledge-bases/{base_id}/artifacts/{artifact_id}", headers=workspace(client)
    ).json()


def test_a_slide_that_is_too_long_is_rewritten_alone_and_saved(tmp_path: Path) -> None:
    """§2.9 example 4: one write with an instruction, then save. The other slides are the same,
    byte for byte in the workspace and by passage in the version, and it costs few calls."""

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        first = a_deck(client, fake, base_id)
        before_parts = works_text(client, "talk")
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "write", "parts": [2], "instruction": "shorter"}]},
                    {"actions": [{"do": "save", "text": "Shortened slide 2."}]},
                ],
                deck_replies(rewrite=shortening),
            )
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Slide 2 is too long")
        end = done(events)
        used = calls(fake, start)
        assert end["outcome"] == "saved" and end["artifact"]["versionNumber"] == 2
        assert end["artifact"]["origin"] == "revise"
        assert end["artifact"]["supersedesArtifactId"] == first
        assert used <= 8, used
        assert [(c["position"], c["was"], c["what"]) for c in end["changes"]] == [
            (2, 2, "rewritten")
        ]
        assert end["changes"][0]["note"] == "shorter"
        assert names(events, "action", "done") == ["Rewrote slide 2", "Saved a version"]
        # Only the writer of slide 2 was asked, and the model's decisions were two.
        writing = part_writes(fake, start)
        assert len(writing) == 1 and "Rewrite slide 2 as instructed" in writing[0]
        assert len(seen_by_the_orchestrator(fake, start)) == 2
        after_parts = works_text(client, "talk")
        assert after_parts[0] == before_parts[0] and after_parts[2] == before_parts[2]
        assert after_parts[1] != before_parts[1] and "| Marker |" in after_parts[1]
        old, new = head_of(client, base_id, first), end["artifact"]
        old_parts, new_parts = (
            by_passage(old["content"], old["citations"]),
            by_passage(new["content"], new["citations"]),
        )
        assert new_parts[0] == old_parts[0] and new_parts[2] == old_parts[2]
        assert new_parts[1] != old_parts[1]
        # The step lines are kept with the answer, and the change log with the workspace.
        stored, _ = kept(client, "talk")
        assert stored.log[-1] == "v2 (deck): Shortened slide 2. · changed 2"
        assert [s["label"] for s in end["message"]["payload"]["steps"]] == names(
            events, "action", "done"
        )


@pytest.mark.parametrize("result", ["rewritten", "unchanged", "foreign_figure"])
def test_imported_parts_use_only_the_selected_evidence_for_writes(tmp_path: Path, result: str):
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        first = a_deck(client, fake, base)
        before = kept(client, "talk")[0].works[0]
        original = head_of(client, base, first)
        selected, excluded = original["citations"]
        pool_ref, excluded_ref = before.version_map
        assert not before.approach.get("method")
        client.patch(conversations(base, "/talk"), headers=workspace(client), json={
            "scope": {"mode": "selection", "sourceIds": [selected["sourceId"]]},
        }).raise_for_status()

        def rewrite(index: int, prompt: str) -> str:
            assert index == 1
            material = prompt.split("The material:\n\n", 1)[1]
            assert selected["sourceTitle"] in material and excluded["sourceTitle"] not in material
            if result == "unchanged":
                return "UNCHANGED"
            if result == "foreign_figure":
                return "## Evidence\n\n![Another figure](gunther-asset:outside)"
            return f"## Evidence\n\nThe selected marker is recorded [{pool_ref}][{excluded_ref}]."

        terminal = {"do": "save" if result == "rewritten" else "reply", "text": "Finished."}
        fake.replies[:] = [script([{"actions": [
            {"do": "write", "parts": [2], "instruction": "Use the selected evidence"},
            terminal,
        ]}], deck_replies(rewrite=rewrite))]
        events = turn(client, base, "talk", "Rewrite slide 2 with the selected source.")
        after = kept(client, "talk")[0].works[0]
        if result == "rewritten":
            assert done(events)["outcome"] == "saved"
            assert after.outline[1].refs == [pool_ref]
            assert f"[{excluded_ref}]" not in after.parts[before.outline[1].id].text
            for item in (before.outline[0], before.outline[2]):
                assert after.parts[item.id] == before.parts[item.id]
        else:
            assert done(events)["outcome"] == "replied"
            assert after.parts == before.parts and after.version_id == first
            if result == "foreign_figure":
                assert any("outside the chosen material" in note for note in failures(events))
            else:
                assert failures(events) == []


def test_a_failed_review_leaves_the_imported_parts_and_checks_in_place(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        first = a_deck(client, fake, base)
        original = kept(client, "talk")[0].works[0]

        def review(request):
            assert "senior partner reading a finished" in request["messages"][0]["content"]
            raise ModelError("Reviewer unavailable")

        fake.replies[:] = [script([{"actions": [
            {"do": "review", "parts": [2]},
            {"do": "reply", "text": "The reviewer was unavailable."},
        ]}], review)]
        events = turn(client, base, "talk", "Review slide 2.")
        assert done(events)["outcome"] == "replied"
        assert failures(events) == ["Reviewer unavailable"]
        after = kept(client, "talk")[0].works[0]
        assert after.parts == original.parts and after.version_id == first
        assert after.skipped[-1]["label"] == "Review unavailable"


def test_the_one_before_it_is_found_from_the_changes_so_far(tmp_path: Path) -> None:
    """§2.9 example 5: the change log says the last version changed slide 3, so "the one before
    it" is slide 2."""

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "write", "parts": [3], "instruction": "shorter"}]},
                    {"actions": [{"do": "save", "text": "Shortened slide 3."}]},
                ],
                deck_replies(rewrite=shortening),
            )
        ]
        assert done(turn(client, base_id, "talk", "Shorten slide 3"))["outcome"] == "saved"

        def second(request: dict) -> dict:
            view = str(request["messages"][-1]["content"])
            assert "- v2 (deck): Shortened slide 3. · changed 3" in view
            assert "You: Shorten slide 3\nGunther: Shortened slide 3." in view
            return {"actions": [{"do": "write", "parts": [2], "instruction": "shorter"}]}

        fake.replies[:] = [
            script(
                [second, {"actions": [{"do": "save", "text": "Shortened slide 2 instead."}]}],
                deck_replies(rewrite=shortening),
            )
        ]
        end = done(turn(client, base_id, "talk", "No, the one before it"))
        assert end["artifact"]["versionNumber"] == 3
        assert [(c["position"], c["what"]) for c in end["changes"]] == [(2, "rewritten")]


def test_a_changed_goal_makes_only_what_depends_on_it_out_of_date(tmp_path: Path) -> None:
    """§2.9 example 3: the goal changes, the approach is out of date, and only the slides whose
    plans changed are written again."""

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        first = a_deck(client, fake, base_id)
        before_parts = works_text(client, "talk")
        outline = kept(client, "talk")[0].works[0].outline

        def costing(index: int, prompt: str) -> str:
            return SLIDES[index].replace("Marker |", "Cost |").replace("T cells", "T-cell cost")

        writer = deck_replies(written=costing)

        def inner(request):
            task = studio_task(request)
            if "lead analyst" in task:
                return '{"answer":"Compare coating and labour costs."}'
            if "presentation strategist" in task:
                return json.dumps({"title": "Markers", "slides": [
                    {"id": item.id, "title": item.heading, "layout": layout,
                     "job": item.goal if i == 0 else "Explain the cost"}
                    for i, (item, layout) in enumerate(zip(
                        outline, ("title", "compare", "points"), strict=True,
                    ))
                ]})
            return writer(request)
        fake.replies[:] = [
            script(
                [
                    {
                        "brief": [
                            {"field": "goal", "text": "Cost", "quote": "cost instead"},
                        ],
                        "actions": [
                            {"do": "search_library", "query": "CD3D markers"},
                            {"do": "understand", "focus": "cost"},
                            {"do": "plan", "change": "whole"},
                        ],
                    },
                    {"actions": [{"do": "write", "parts": [2, 3]}]},
                    {"actions": [{"do": "save", "text": "Refocused on cost."}]},
                ],
                inner,
            )
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Focus on cost instead")
        end = done(events)
        assert end["outcome"] == "saved", (
            end["reply"] + "\n" + "\n".join(str(d) for n, d in events if n == "action")
        )
        assert [c["position"] for c in end["changes"]] == [2, 3]
        views = seen_by_the_orchestrator(fake, start)
        assert "- goal: Cost (you said)" in views[1] and "Approach: up to date." in views[1]
        # After the plan, slides 2 and 3 are out of date and the title slide is not.
        assert "2 [compare] CD3D and CD19 set T and B cells apart · out of date" in views[1]
        assert "1 [title] Markers · ok" in views[1]
        assert len(part_writes(fake, start)) == 2
        assert not any("Write slide 1." in text for text in part_writes(fake, start))
        assert works_text(client, "talk")[0] == before_parts[0]
        assert head_of(client, base_id, first)["versionNumber"] == 1


def test_a_question_about_the_deck_is_answered_and_saves_nothing(tmp_path: Path) -> None:
    """§2.9 example 6: the answer names the source by the number the user sees, a number that
    is not one of the sources is dropped, and no version is made."""

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [{"actions": [{"do": "answer", "about": "which source says CD3D marks T cells"}]}],
                answer="Markers says CD3D marks T cells [1][9].",
            )
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Which source says CD3D marks T cells?")
        end = done(events)
        assert end["outcome"] == "answered" and end["artifact"] is None
        assert end["reply"] == "Markers says CD3D marks T cells [1]."
        assert end["message"]["kind"] == "answer" and end["changes"] == []
        assert len(versions_of(client)) == 1
        assert calls(fake, start) <= 3
        [asked] = [r for r in fake.requests[start:] if is_answering(r)]
        prompt = str(asked["messages"][-1]["content"])
        assert "[1] (library) Markers: CD3D is a marker of T cells." in prompt
        assert "The question: Which source says CD3D marks T cells?" in prompt
        assert [name for name, _ in events].count("reply") >= 1
        assert names(events, "action", "done") == ["Answered"]
        stored, _ = kept(client, "talk")
        assert stored.log == [log for log in stored.log if not log.startswith("v2")]


# The guards: what code lets through ---------------------------------------------------------------


def failures(events: list[tuple[str, dict]]) -> list[str]:
    return [data["note"] for name, data in events if name == "action" and data["state"] == "failed"]


def states(events: list[tuple[str, dict]]) -> list[str]:
    return [
        data["state"] for name, data in events if name == "action" and data["state"] != "running"
    ]


def test_a_third_question_in_a_row_is_refused(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    ask = {"do": "ask", "question": "Which one?", "options": ["A", "B"]}
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        counts = []
        for round_ in range(3):
            fake.replies[:] = [
                script(
                    [
                        {"actions": [ask]},
                        {"actions": [{"do": "reply", "text": "Going on with my best reading."}]},
                    ]
                )
            ]
            start = len(fake.requests)
            events = turn(client, base_id, "talk", f"Change the costs slide {round_}")
            counts.append((done(events)["outcome"], failures(events)))
            stored, _ = kept(client, "talk")
            if round_ < 2:
                assert stored.asked_in_a_row == round_ + 1 and stored.pending is not None
        assert counts[0] == ("asked", []) and counts[1] == ("asked", [])
        outcome, refused = counts[2]
        assert outcome == "replied" and "2 questions in a row is enough" in refused[0]
        # The refusal is in the next view as "Not done", and the counter starts again.
        assert (
            "Not done:\n- Asking you a question: 2 questions in a row"
            in (seen_by_the_orchestrator(fake, start)[1])
        )
        stored, _ = kept(client, "talk")
        assert stored.asked_in_a_row == 0 and stored.pending is None


def test_just_start_turns_off_asking_for_that_message(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "ask", "question": "Which focus?", "options": ["A"]}]},
                    {"actions": [{"do": "reply", "text": "Starting with my best reading."}]},
                ]
            )
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "", kind="start")
        end = done(events)
        assert end["outcome"] == "replied"
        assert "just start" in failures(events)[0]
        views = seen_by_the_orchestrator(fake, start)
        assert views[0].endswith('Latest message ("just start"):\nJust start')
        assert "Not done:\n- Asking you a question: The user said" in views[1]
        stored, _ = kept(client, "talk")
        assert stored.pending is None


def test_saving_is_refused_while_a_part_is_out_of_date(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {
                        "brief": [
                            {
                                "field": "audience",
                                "text": "first-year students",
                                "quote": "first-year students",
                            }
                        ],
                        "actions": [{"do": "save", "text": "Made it for students."}],
                    },
                    {"actions": [{"do": "reply", "text": "It needs rewriting first."}]},
                ]
            )
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Make it for first-year students")
        end = done(events)
        assert end["outcome"] == "replied" and end["artifact"] is None
        [refused] = failures(events)
        assert refused.startswith("It can't be saved yet:") and "is out of date" in refused
        view = seen_by_the_orchestrator(fake, start)[1]
        assert "- audience: first-year students (you said)" in view
        assert "out of date: the audience changed" in view
        assert len(versions_of(client)) == 1
        stored, brief = kept(client, "talk")
        assert brief.audience == Line(text="first-year students", mark="you")


def test_nothing_is_saved_when_nothing_changed(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "save", "text": "Saved."}]},
                    {"actions": [{"do": "reply", "text": "Nothing needed to change."}]},
                ]
            )
        ]
        events = turn(client, base_id, "talk", "Is it fine?")
        assert done(events)["outcome"] == "replied"
        assert failures(events) == ["Nothing has changed since the saved version; reply instead."]
        assert len(versions_of(client)) == 1


def test_a_repeated_action_is_skipped(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    search = {"do": "search_library", "query": "CD3D markers"}
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {"actions": [search, search]},
                    {"actions": [search, {"do": "reply", "text": "Looked."}]},
                ]
            )
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Look for CD3D")
        assert states(events) == ["done", "skipped", "skipped", "done"]
        assert done(events)["outcome"] == "replied"
        # One search was run, so one grading call was made.
        graded = [r for r in fake.requests[start:] if "relevance step" in str(r["messages"][0])]
        assert len(graded) == 1


def test_a_position_that_does_not_exist_becomes_a_note_and_a_new_decision(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "write", "parts": [9], "instruction": "shorter"}]},
                    {"actions": [{"do": "reply", "text": "There is no slide 9."}]},
                ]
            )
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Shorten slide 9")
        assert failures(events) == ["There is no slide 9; there are 3."]
        assert done(events)["outcome"] == "replied"
        views = seen_by_the_orchestrator(fake, start)
        assert (
            len(views) == 2
            and "Not done:\n- Writing slide 9: There is no slide 9; there are 3." in views[1]
        )
        assert "Not done:" not in views[0]
        assert len(versions_of(client)) == 1


def test_a_decision_with_no_action_is_asked_again_once_and_then_fails(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script([{"actions": []}, {"actions": [{"do": "reply", "text": "Done."}]}])
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Hello")
        assert done(events)["outcome"] == "replied"
        views = seen_by_the_orchestrator(fake, start)
        assert len(views) == 2 and views[1].count("Choose at least one action") == 1
        # Twice in a row fails the turn, saying so, and keeps the message.
        fake.replies[:] = [script([{"actions": []}])]
        events = turn(client, base_id, "talk", "Hello again")
        assert events[-1][0] == "error" and events[-1][1]["status"] == 502
        assert "chose no action" in events[-1][1]["detail"]
        with client.app.state.knowledge_service.sessions() as session:
            last = session.scalars(
                select(ArtifactMessage).order_by(ArtifactMessage.created_at.desc()).limit(2)
            ).all()
        assert {m.kind for m in last} == {"message", "failed"}
        assert all(m.client_request_id is None for m in last)


def test_stop_keeps_what_was_found_and_saves_no_version(tmp_path: Path) -> None:
    gate = threading.Event()

    def waiting(request: dict) -> dict:
        assert gate.wait(10)
        return {"actions": [{"do": "reply", "text": "Never said."}]}

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script([{"actions": [{"do": "search_library", "query": "CD3D markers"}]}, waiting])
        ]
        headers = workspace(client)
        runs = client.app.state.answer_runs
        key = f"outputs:{base_id}"
        first, box = in_background(lambda: turn(client, base_id, "talk", "Look for CD3D"))
        wait_for(
            lambda: (
                runs.get(key) is not None
                and any(n == "action" and d["state"] == "done" for n, d in runs.get(key).events)
            )
        )
        stopped = client.post(f"/api/knowledge-bases/{base_id}/outputs/build/stop", headers=headers)
        assert stopped.status_code == 204
        gate.set()
        first.join(10)
        assert box[0][-1][0] == "stopped"
        assert len(versions_of(client)) == 1
        stored, _ = kept(client, "talk")
        assert len(stored.pool) >= 1  # what was found stays
        detail = client.get(conversations(base_id, "/talk"), headers=headers).json()
        assert [m["kind"] for m in detail["messages"]][-2:] == ["message", "stopped"]
        assert detail["messages"][-2]["clientRequestId"] is None


def test_follow_gets_the_new_done_payload(tmp_path: Path) -> None:
    gate = threading.Event()

    def waiting(request: dict) -> dict:
        assert gate.wait(10)
        return {"actions": [{"do": "reply", "text": "All good."}]}

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [script([waiting])]
        headers = workspace(client)
        runs = client.app.state.answer_runs
        key = f"outputs:{base_id}"
        first, first_box = in_background(lambda: turn(client, base_id, "talk", "How is it?"))
        wait_for(lambda: runs.get(key) is not None and runs.get(key).events)

        busy = client.post(
            conversations(base_id, "/talk/messages/stream"),
            headers=headers,
            json={"clientRequestId": "turn_request_busy1", "text": "Again"},
        )
        assert busy.status_code == 409

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
    [followed] = again_box
    assert followed[0][0] == "resumed" and followed[-1][0] == "done"
    end = followed[-1][1]
    assert end["outcome"] == "replied" and end["reply"] == "All good."
    assert end == first_box[0][-1][1] and "message" in end


def test_the_same_request_id_returns_the_first_result_and_runs_nothing(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [script([{"actions": [{"do": "reply", "text": "All good."}]}])]
        request = {"clientRequestId": "turn_request_same1", "text": "How is it?"}
        url = conversations(base_id, "/talk/messages/stream")

        def send(**fields: Any) -> list:
            with client.stream(
                "POST", url, headers=workspace(client), json={**request, **fields}
            ) as response:
                return read_events(response)

        first = done(send())
        start = len(fake.requests)
        again = done(send())
        assert again == first and len(fake.requests) == start
        with client.app.state.knowledge_service.sessions() as session:
            asked = session.scalars(
                select(ArtifactMessage).where(ArtifactMessage.role == "user")
            ).all()
        assert len(asked) == 1
        different = send(text="Something else")
        assert different[-1][0] == "error" and different[-1][1]["status"] == 409


def test_a_saved_turn_repeated_returns_the_version(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "write", "parts": [2], "instruction": "shorter"}]},
                    {"actions": [{"do": "save", "text": "Shortened slide 2."}]},
                ],
                deck_replies(rewrite=shortening),
            )
        ]
        request = {"clientRequestId": "turn_request_save1", "text": "Shorten 2"}
        url = conversations(base_id, "/talk/messages/stream")
        with client.stream("POST", url, headers=workspace(client), json=request) as response:
            first = done(read_events(response))
        with client.stream("POST", url, headers=workspace(client), json=request) as response:
            again = done(read_events(response))
        assert first["artifact"]["id"] == again["artifact"]["id"]
        assert again["outcome"] == "saved" and again["changes"] == first["changes"]
        assert len(versions_of(client)) == 2


# The view ---------------------------------------------------------------------------------------------


def a_view(**more: Any) -> View:
    from test_artifact_steps import passage
    from test_artifact_workspace import made

    brief, work = made("slides")
    pool = Pool(160)
    for title, quote in (("Brandt 2022", "Peak heat fell."), ("Harlow 2024", "Labour is 60%.")):
        pool.adopt(passage(title, quote))
    # The version's first source is the pool's second: the user sees pool source 2 as [1].
    work.version_map = [2, 1]
    note = "Note: " + " ".join(["word"] * 40)
    work.parts["p2"].text = f"<!-- layout: big_number -->\n## Heat fell\n\n**3 °C** [1]\n\n{note}"
    work.parts["p3"].inputs = "stale"
    workspace_ = Workspace(
        works=[work],
        active=work.key,
        log=[
            "v1 (deck): First version. · added 1–3",
            "v2 (deck): Heat on a big number. · changed 2",
        ],
    )
    fields: dict[str, Any] = {
        "library": "Cells",
        "web": False,
        "workspace": workspace_,
        "brief": brief,
        "pool": pool,
        "versions": {work.key: 2},
        "history": [("Shorten 2", "Shortened slide 2.")],
        "notes": ["Wrote slide 3"],
        "not_done": ["Writing slide 9: There is no slide 9; there are 3."],
        "left": (9, 150, 6),
        "kind": "message",
        "text": "No, the one before it",
        "focus": {"section": 2, "quote": "Heat", "selected": True},
    }
    return View(**{**fields, **more})


def test_the_view_has_its_headings_in_order_and_the_numbers_the_user_sees() -> None:
    words = " ".join(["word"] * 40)
    cut = f"Note: {words}"[:149].rstrip() + "…"
    expected = f"""\
Library: Cells. Web: off.
slides in Studio: understand → plan → write → review. Layouts: title, section, summary, stat, stats, chart, table, compare, process, timeline, quote, figure, figure_detail, statement, points, actions. Uses: talk, read
Brief (you said = the user's words; edited = the user typed it; guessed = yours):
- goal: Explain the Velmora trial (you said)
- audience: lab members (you said)
- constraint: at most 3 bullets (you said)
Artifacts: [1] deck "The trial" version 2 (active)
The active deck "The trial": use talk (guessed) · length 10 minutes (guessed)
Approach: up to date.
  Question: What did the trial find? / Answer: A 3 degree drop (2 sources found since it was written)
Parts:
  1 [title] The trial · ok
      ## The trial
      It is so [2].
  2 [big_number] Heat fell · not re-checked · sources 2 · cards C1
      ## Heat fell
      **3 °C** [2]
      {cut}
  3 [bar_chart] Labour costs most · out of date: what it was made from changed · sources 1 · cards C2
      ## Labour costs most
      It is so [1].
Pointed at (selected): part 2, in full; selected text: “Heat”
      ## Heat fell

      **3 °C** [2]

      Note: {words}
Pool: 2 sources; newest: [1] Harlow 2024 (library) · [2] Brandt 2022 (library)
Changes so far:
- v1 (deck): First version. · added 1–3
- v2 (deck): Heat on a big number. · changed 2
Conversation:
You: Shorten 2
Gunther: Shortened slide 2.
This turn so far:
- Wrote slide 3
Not done:
- Writing slide 9: There is no slide 9; there are 3.
Budget left: 9 decisions · 150 model calls · 6 searches
Latest message (message):
No, the one before it"""
    # The sources a part cites are the version's numbers: pool 1 is [2], pool 2 is [1].
    assert build_view(a_view()) == expected


def test_a_reports_view_numbers_its_paragraphs_and_a_question_is_shown_with_its_answer() -> None:
    from test_artifact_workspace import made

    brief, work = made("report")
    work.parts["p2"].text = "## Heat fell\n\nFirst paragraph [1].\n\nSecond one [2]. " + "x" * 300
    pending = Pending(question="Which focus?", options=["Trial", "Debate"], message_id="acm_1")
    workspace_ = Workspace(works=[work], active=work.key, pending=pending)
    text = build_view(
        View(
            "Cells",
            True,
            workspace_,
            brief,
            Pool(160),
            {},
            [],
            [],
            [],
            (12, 200, 8),
            "answer",
            "Trial",
            None,
            asked=pending,
        )  # fmt: skip
    )
    assert "Web: on." in text.splitlines()[0]
    assert text.splitlines()[1].startswith("report in Studio:") and "Structures: what," in text
    assert 'Artifacts: [1] report "The trial" not saved yet (active)' in text
    assert (
        "  2 Heat fell · not re-checked · sources 1, 2 · cards C1\n      ¶1 First paragraph [1].\n      ¶2 Second one"
        in text
    )
    assert "…" in text.split("¶2 ")[1].splitlines()[0]
    assert 'You asked: "Which focus?" Options: Trial | Debate (this message answers it)' in text
    assert "Latest message (answer to your question):\nTrial" in text
    # With no active artifact, both output kinds are available.
    empty = build_view(
        View("Cells", False, Workspace(), ConversationBrief(), Pool(160), {}, [], [], [], (12, 200, 8), "message", "Hi")
    )  # fmt: skip
    assert "Artifacts: none yet." in empty and "- (nothing yet)" in empty
    assert empty.count(" in Studio: understand") == 2 and "Pointed at" not in empty


def test_numbers_are_the_versions_and_sources_it_does_not_cite_follow() -> None:
    numbering = Numbering([7, 3], [1, 3, 7, 9, 12])
    assert numbering.order == [7, 3, 1, 9, 12]
    assert (numbering.view(7), numbering.view(3), numbering.view(12)) == (1, 2, 5)
    assert numbering.pool(1) == 7 and numbering.pool(5) == 12 and numbering.pool(6) is None
    assert numbering.text("A [3][7], B [3, 9] and [p:12] but [99] [?].") == (
        "A [2][1], B [2][4] and [p:5] but [99] [?]."
    )
    assert ranges([14, 15, 16, 17, 18, 19]) == "14–19"
    assert ranges([5, 3, 4, 9, 1]) == "1, 3–5, 9" and ranges([2, 3]) == "2, 3" and ranges([]) == ""


def test_the_audience_a_version_records_comes_from_the_words_the_user_gave() -> None:
    def of(words: str | None) -> str:
        brief = ConversationBrief(audience=Line(text=words) if words else None)
        return audience_of(brief)

    assert of("first-year students") == "student" and of("a class of undergrads") == "student"
    assert of("my team and the funders") == "collaborator" and of("partner labs") == "collaborator"
    assert of("lab members") == "scientist" and of(None) == "scientist"
    assert of("学生") == "student" and of("团队") == "collaborator"


# What is checked before an action runs ---------------------------------------------------------------


def a_gate(**more: Any) -> Gate:
    from test_artifact_workspace import made

    brief, work = made("slides")
    fields: dict[str, Any] = {
        "workspace": Workspace(works=[work], active=work.key),
        "brief": brief,
        "view_ids": [item.id for item in work.outline],
        "kind": "message",
        "web_ok": False,
        "web_reason": "web search is not set up",
        "numbers": 4,
        "limits": AgentLimits(),
    }
    return Gate(**{**fields, **more})


CHECKS = [
    ({"do": "ask", "question": "Which?", "options": ["A", "B"]}, None),
    ({"do": "ask"}, "A question needs words."),
    ({"do": "answer"}, "Say what to answer"),
    ({"do": "answer", "about": "the 62%"}, None),
    ({"do": "search_library"}, "A search needs a query."),
    ({"do": "search_web", "query": "cost"}, "The web is not available: web search is not set up"),
    ({"do": "read_source", "query": "9"}, "There is no source 9"),
    ({"do": "read_source", "query": "3"}, None),
    ({"do": "reply"}, "A reply needs words."),
    ({"do": "select", "artifact": "new:poster"}, "Choose an artifact by its number"),
    ({"do": "select", "artifact": "1"}, None),
    ({"do": "select", "artifact": "new:report"}, None),
    ({"do": "understand"}, None),
    ({"do": "plan"}, "plan needs a change"),
    ({"do": "plan", "change": "whole"}, None),
    ({"do": "plan", "change": "insert", "after": 4, "heading": "X", "layout": "text"}, "no place after 4"),
    ({"do": "plan", "change": "insert", "after": 1, "layout": "text"}, "needs a heading"),
    ({"do": "plan", "change": "insert", "after": 1, "heading": "X"}, None),
    ({"do": "plan", "change": "insert", "after": 1, "heading": "X", "layout": "title"}, "one title slide"),
    ({"do": "plan", "change": "insert", "after": 1, "heading": "X", "layout": "points"}, None),
    ({"do": "plan", "change": "delete", "item": 1}, "The title slide stays first."),
    ({"do": "plan", "change": "delete", "item": 4}, "There is no slide 4; there are 3."),
    ({"do": "plan", "change": "delete", "item": 2}, None),
    ({"do": "plan", "change": "move", "item": 2}, "no place after None"),
    ({"do": "plan", "change": "move", "item": 2, "after": 3}, None),
    ({"do": "plan", "change": "move", "item": 1, "after": 3}, "The title slide stays first."),
    ({"do": "plan", "change": "item", "item": 2}, "Say what to change"),
    ({"do": "plan", "change": "item", "item": 2, "layout": "pie"}, "is not one of the deck's layouts"),
    ({"do": "plan", "change": "item", "item": 2, "layout": "process"}, None),
    ({"do": "plan", "change": "item", "item": 2, "accent": "blue"}, "Only a deck's title slide"),
    ({"do": "plan", "change": "item", "item": 1, "accent": "pink"}, "is not an accent"),
    ({"do": "plan", "change": "item", "item": 1, "accent": "blue"}, None),
    ({"do": "plan", "change": "title"}, "Say the new title"),
    ({"do": "plan", "change": "title", "heading": "Cool roofs"}, None),
    ({"do": "write"}, "Name the slides"),
    ({"do": "write", "parts": [0]}, "There is no slide 0; there are 3."),
    ({"do": "write", "parts": [2, 3], "instruction": "shorter"}, None),
    ({"do": "check", "parts": [1, 2]}, None),
    ({"do": "edit", "item": 2, "passage": "It is so", "instruction": ""}, "Say what to change"),
    ({"do": "edit", "item": 2, "passage": "Nothing like it", "instruction": "x"}, "Couldn't find that passage in part 2."),
    ({"do": "edit", "item": 2, "passage": "It is so", "instruction": "Say more"}, None),
    ({"do": "review"}, None),
    ({"do": "review", "parts": [7]}, "There is no slide 7"),
    ({"do": "save"}, "Say in one sentence what changed"),
    ({"do": "save", "text": "Done."}, None),
]  # fmt: skip


@pytest.mark.parametrize(("action", "problem"), CHECKS)
def test_each_action_is_checked_against_the_workspace(action: dict, problem: str | None) -> None:
    found = validate(Act(**action), a_gate())
    if problem is None:
        assert found is None, found
    else:
        assert found is not None and problem in found


def test_actions_that_need_an_artifact_are_refused_without_one() -> None:
    empty = a_gate(workspace=Workspace())
    for action in (
        {"do": "understand"},
        {"do": "write", "parts": [1]},
        {"do": "save", "text": "x"},
    ):
        assert "There is no artifact yet" in (validate(Act(**action), empty) or "")
    assert validate(Act(do="search_library", query="x"), empty) is None
    assert validate(Act(do="select", artifact="new:slides"), empty) is None
    assert validate(Act(do="select", artifact="1"), empty) is not None


def test_checks_that_depend_on_the_state_of_the_work() -> None:
    gate = a_gate(web_ok=True)
    assert validate(Act(do="search_web", query="cost"), gate) is None
    # Planning from an approach that is out of date wastes a call; understand comes first.
    gate.brief.audience = Line(text="first-year students", mark="you")
    assert "The approach is out of date" in (validate(Act(do="plan", change="whole"), gate) or "")
    assert "can't be saved yet" in (validate(Act(do="save", text="x"), gate) or "")
    # A third question is refused, and "just start" refuses any.
    gate.workspace.asked_in_a_row = 2
    assert "2 questions in a row" in (validate(Act(do="ask", question="Q"), gate) or "")
    gate.workspace.asked_in_a_row = 0
    assert validate(Act(do="ask", question="Q"), a_gate(kind="start")) is not None
    # A part that is not written cannot be edited, checked or reviewed.
    work = gate.work
    assert work is not None
    del work.parts["p3"]
    assert "Slide 3 is not written yet" in (validate(Act(do="check", parts=[3]), gate) or "")
    assert "Slide 3 is not written yet" in (
        validate(Act(do="edit", item=3, passage="x", instruction="y"), gate) or ""
    )
    assert "Slide 3 is not written yet; write it first" in (validate(Act(do="review"), gate) or "")
    # A report has no layouts.
    from test_artifact_workspace import made

    brief, report = made("report")
    for item in report.outline:
        item.layout = ""
    report_gate = a_gate(
        workspace=Workspace(works=[report], active=report.key),
        brief=brief,
        view_ids=[i.id for i in report.outline],
    )
    wanted = Act(do="plan", change="item", item=2, layout="text")
    assert "no layout" in (validate(wanted, report_gate) or "")
    assert validate(Act(do="plan", change="delete", item=1), report_gate) is None


# What each action does to the workspace ----------------------------------------------------------------

NEW_TITLE = "# Telling T and B cells apart\n\nTwo markers tell the cells apart"


def retitling(index: int, prompt: str) -> str:
    return NEW_TITLE if index == 0 else SLIDES[index]


def test_a_slide_is_added_and_the_other_slides_are_not_touched(tmp_path: Path) -> None:
    inserted = (
        "## The 2024 trial\n\n- CD3D was counted in 412 cells [1]\n\nNote: See the trial [1]."
    )

    def written(index: int, prompt: str) -> str:
        return inserted if "Write slide 2. Its title: The 2024 trial." in prompt else SLIDES[index]

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        first = a_deck(client, fake, base_id)
        before = works_text(client, "talk")
        fake.replies[:] = [
            script(
                [
                    {
                        "actions": [
                            {
                                "do": "plan",
                                "change": "insert",
                                "after": 1,
                                "heading": "The 2024 trial",
                                "goal": "The newest result",
                            },
                            # After the insert, slide 2 is the new one.
                            {"do": "write", "parts": [2]},
                        ]
                    },
                    {"actions": [{"do": "save", "text": "Added a slide on the 2024 trial."}]},
                ],
                deck_replies(written=written),
            )
        ]
        events = turn(client, base_id, "talk", "Add a slide on the 2024 trial after the title")
        end = done(events)
        assert failures(events) == []
        assert end["outcome"] == "saved", end["reply"]
        assert [(c["position"], c["was"], c["what"]) for c in end["changes"]] == [
            (2, None, "added")
        ]
        assert end["changes"][0]["heading"] == "The 2024 trial"
        after = works_text(client, "talk")
        assert [after[0], after[2], after[3]] == [before[0], before[1], before[2]]
        assert end["artifact"]["supersedesArtifactId"] == first
        assert len(split_document(end["artifact"]["content"], "slides")[1]) == 4
        assert "added 2" in kept(client, "talk")[0].log[-1]


def test_a_title_and_an_accent_are_set_by_code_and_a_slide_can_be_moved_and_removed(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        before = works_text(client, "talk")
        fake.replies[:] = [
            script(
                [
                    {
                        "actions": [
                            {"do": "plan", "change": "item", "item": 1, "accent": "blue"},
                            {
                                "do": "plan",
                                "change": "title",
                                "heading": "Telling T and B cells apart",
                            },
                        ]
                    },
                    {"actions": [{"do": "write", "parts": [1]}]},
                    {"actions": [{"do": "plan", "change": "move", "item": 3, "after": 1}]},
                    {
                        "actions": [
                            {"do": "save", "text": "Renamed the deck and moved the closing slide."}
                        ]
                    },
                ],
                deck_replies(written=retitling),
            )
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Make it blue, call it Telling T and B cells apart")
        end = done(events)
        assert end["outcome"] == "saved", end["reply"]
        # The accent is code only: no model was asked for it. The title slide had to be
        # written again for its new heading, and it kept the accent.
        slides = split_document(end["artifact"]["content"], "slides")[1]
        assert slides[0].startswith("<!-- layout: title; accent: blue; design: v1 -->\n# Telling T and B cells")
        assert end["artifact"]["title"] == "Telling T and B cells apart"
        stored, _ = kept(client, "talk")
        assert stored.works[0].title == "Telling T and B cells apart"
        # Part 3 moved up behind the title (the closing slide keeps its place only in the plan's
        # rules; the person asked for it to move).
        assert {(c["what"], c["was"]) for c in end["changes"]} >= {("rewritten", 1), ("moved", 3)}
        assert works_text(client, "talk")[1] == before[2]
        writer_prompts = part_writes(fake, start)
        assert len(writer_prompts) == 1
        assert "Write slide 1. Its title: Telling T and B cells apart." in writer_prompts[0]


def test_a_slide_is_removed_by_code_alone(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "plan", "change": "delete", "item": 2}]},
                    {"actions": [{"do": "save", "text": "Removed the table slide."}]},
                ]
            )
        ]
        start = len(fake.requests)
        end = done(turn(client, base_id, "talk", "Drop slide 2"))
        assert calls(fake, start) == 2  # the two decisions, nothing else
        assert [(c["position"], c["was"], c["what"]) for c in end["changes"]] == [
            (None, 2, "deleted")
        ]
        assert len(split_document(end["artifact"]["content"], "slides")[1]) == 2
        assert kept(client, "talk")[0].log[-1].endswith("removed 2")


def test_text_the_user_typed_is_checked_again_without_being_rewritten(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        first = a_deck(client, fake, base_id)
        old = head_of(client, base_id, first)
        typed = old["content"].replace("Use both in every panel", "Use both in each panel")
        edited = client.post(
            f"/api/knowledge-bases/{base_id}/artifacts/{first}/edits",
            headers=workspace(client),
            json={"clientRequestId": "typed_by_hand_01", "content": typed},
        )
        assert edited.status_code == 201
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "check", "parts": [3]}]},
                    {"actions": [{"do": "reply", "text": "I checked slide 3 again."}]},
                ]
            )
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Check slide 3 again")
        assert done(events)["outcome"] == "replied"
        # The hand edit was brought in before the turn, and not-rechecked text is what was checked.
        view = seen_by_the_orchestrator(fake, start)[0]
        assert "Use both in each panel · not re-checked" in view
        assert names(events, "action", "done")[0] == "Checked slide 3"
        stored, brief = kept(client, "talk")
        work = stored.works[0]
        part = work.parts[work.outline[2].id]
        assert "each panel" in part.text and part.issues is not None
        assert part.checked == section_hash(part.text)
        assert len(versions_of(client)) == 2  # the hand edit; the turn saved nothing
        assert 3 <= calls(fake, start) <= 8


def test_one_passage_is_changed_and_checked(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    row = "| Marker | CD3D [1] | CD19 [2] |"
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        before = works_text(client, "talk")
        fake.replies[:] = [
            script(
                [
                    {
                        "actions": [
                            {
                                "do": "edit",
                                "item": 2,
                                "passage": "| Marker | CD3D | CD19 |",
                                "instruction": "say gene",
                            }
                        ]
                    },
                    {"actions": [{"do": "save", "text": "Named the row."}]},
                ],
                deck_replies(replacer=replacing({row: "| Gene | CD3D [1] | CD19 [2] |"})),
            )
        ]
        start = len(fake.requests)
        end = done(turn(client, base_id, "talk", "Call the row Gene"))
        assert end["outcome"] == "saved", end["reply"]
        assert [(c["position"], c["what"], c["note"]) for c in end["changes"]] == [
            (2, "edited", "say gene")
        ]
        after = works_text(client, "talk")
        assert after[1] == before[1].replace(row, "| Gene | CD3D [1] | CD19 [2] |")
        assert after[0] == before[0] and after[2] == before[2]
        assert calls(fake, start) <= 8


def test_studio_report_edit_checks_only_the_changed_paragraph(tmp_path: Path) -> None:
    first = "The initial estimate has uncertain support [1]."
    second = "Use the estimate as a planning input [1]."
    revised = "Use the estimate as guidance for planning [1]."
    text = f"## Use the estimates\n\n{first}\n\n{second}"
    issue = {"claim": first, "verdict": "unsupported", "note": "The source does not state it."}
    fake = FakeProvider(script([{"actions": [
        {"do": "edit", "item": 1, "passage": second, "instruction": "Rephrase for clarity"},
        {"do": "save", "text": "Edited the second paragraph."},
    ]}], deck_replies(replacer=replacing({second: revised}))))
    with app_for(tmp_path, fake) as client:
        service = client.app.state.knowledge_service
        base, _ = library(client, notes={"Planning": "The estimate guides planning."})
        saved = saved_output(client, base, {"title": "Plan", "sections": [
            {"heading": "Use the estimates"},
        ]}, [text], conversationId="paragraph-edit")
        with service.sessions() as session:
            row = session.scalar(select(ArtifactCheck).where(ArtifactCheck.artifact_id == saved["id"]))
            checks = json.loads(row.checks_json)
            checks["sections"][0].pop("verdicts")
            checks["sections"][0]["issues"] = [issue]
            row.checks_json = json.dumps(checks)
            session.commit()
        client.get(conversations(base), headers=workspace(client)).raise_for_status()

        result = done(turn(client, base, "paragraph-edit", "Rephrase the second paragraph."))
        assert result["outcome"] == "saved"
        assert split_document(result["artifact"]["content"], "report")[1] == [
            text.replace(second, revised)
        ]
        assert result["artifact"]["sections"][0]["issues"] == [issue]
        prompts = [request["messages"][-1]["content"] for request in fake.requests
                   if is_sentence_checking(request)]
        assert len(prompts) == 1 and revised in prompts[0] and first not in prompts[0]


@pytest.mark.parametrize("repeat", [False, True], ids=["different-paragraphs", "same-claim-other-evidence"])
def test_studio_edit_keeps_whole_section_coverage_and_retries_untouched_unchecked_claim(
    tmp_path, repeat,
):
    ref = 2 if repeat else 1
    first = "The original estimate used three readings [1]."
    second = f"Use the estimate as a planning input [{ref}]."
    revised = first.replace("[1]", "[2]") if repeat else "Use the estimate for planning [1]."
    heading = "Use the estimates"
    text = f"## {heading}\n\n{first}\n\n{second}"
    fake = FakeProvider(script([{"actions": [
        {"do": "edit", "item": 1, "passage": second, "instruction": "Rephrase for clarity"},
        {"do": "save", "text": "Edited the second paragraph."},
    ]}], deck_replies(replacer=replacing({second: revised}))))
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={
            "Planning": "The estimate guides planning.",
            "Measurements": "The original estimate used three readings.",
        })
        headers = workspace(client)
        saved = saved_output(client, base, {"title": "Plan", "sections": [{"heading": heading}]},
                             [text], conversationId="paragraph-edit")
        evidence = [evidence_from_citation(ConversationCitationOut.model_validate(c))
                    for c in saved["citations"]]
        by_ref = {item.ref: item for item in evidence}
        checker = checker_inventory(check_heading_refs(text, text.split("\n")), by_ref)
        support = support_inventory(text, by_ref)
        issue = {"claim": claim_text(first), "verdict": "unsupported",
                 "note": "The sources it cites do not state this."}
        for row in checker.values():
            if row["text"] != first:
                row["verdict"] = "supported"
        for row in support.values():
            if row["text"] == first:
                row.update(verdict="unsupported", issue=issue)
            else:
                row["verdict"] = "supported"
        with client.app.state.knowledge_service.sessions() as session:
            row = session.scalar(select(ArtifactCheck).where(ArtifactCheck.artifact_id == saved["id"]))
            checks = json.loads(row.checks_json)
            checks["sections"][0]["verdicts"] = checker | support
            row.checks_json = json.dumps(checks)
            session.commit()
        client.get(conversations(base), headers=headers).raise_for_status()

        result = done(turn(client, base, "paragraph-edit", "Rephrase the second paragraph."))
        assert result["outcome"] == "saved"
        version = result["artifact"]
        [section] = version["sections"]
        coverage = section["coverage"]
        assert not section["checked"] and coverage["status"] == "partial"
        assert section["issues"] == [issue]
        assert [(c["text"], c["verdict"]) for c in coverage["claims"] if not c["text"].startswith("##")] == [
            (first, "unchecked"), (revised, "supported"),
        ]
        assert (coverage["considered"], coverage["checked"], coverage["unchecked"]) == (3, 2, 1)
        assert coverage["answerHash"] == section_hash(text.replace(second, revised))
        [edit_prompt] = [r["messages"][-1]["content"] for r in fake.requests if is_sentence_checking(r)]
        assert re.findall(r"^\(\d+\) (.*)$", edit_prompt, re.M) == [revised]

        fake.replies[:] = [deck_replies()]
        start = len(fake.requests)
        retried = client.post(route(base, version["id"], "/check"), headers=headers).raise_for_status().json()
        calls = fake.requests[start:]
        [retry_prompt] = [r["messages"][-1]["content"] for r in calls if is_sentence_checking(r)]
        assert re.findall(r"^\(\d+\) (.*)$", retry_prompt, re.M) == [first]
        [support_prompt] = [r["messages"][-1]["content"] for r in calls if is_support_checking(r)]
        assert re.findall(r"^\[\d+\] (.*)$", support_prompt, re.M) == [claim_text(revised)]
        assert f"cites {ref}:" in support_prompt
        [checked] = retried["sections"]
        assert checked["checked"] and checked["coverage"]["checked"] == 3
        assert checked["issues"] == [issue]
        assert retried["content"] == version["content"]


@pytest.mark.parametrize(
    "operation, verdict, multi_series",
    [
        ("edit", "supported", False), ("edit", "unchecked", False), ("edit", "not", False),
        ("write", "supported", False), ("write", "not", False), ("write", "supported", True),
    ],
    ids=["verified-notes", "unchecked-row", "unsupported-edit", "studio-marker",
         "unsupported-write", "two-series"],
)
def test_a_legacy_chart_revision_keeps_the_renderer_format(
    tmp_path: Path, operation: str, verdict: str, multi_series: bool,
) -> None:
    table = (
        "## Rates vary by time of day\n\n| Time | Rate (%) |\n| --- | --- |\n"
        "| Morning | 62 [1] |\n| Afternoon | 41 [1] |"
    )
    revised_table = (
        "## Rates vary by time of day\n\n| Time | Rate (%) | Adjusted rate (%) |\n| --- | --- | --- |\n"
        "| Morning | 62 [1] | 54 [1] |\n| Afternoon | 41 [1] | 35 [1] |"
    ) if multi_series else table
    before, after = "Note: Read the table.", "Note: Compare both rows."
    span, replacement = before, after
    if verdict in {"not", "unchecked"}:
        span, replacement = "| Afternoon | 41 [1] |", "| Afternoon | 70 [1] |"
        revised_table = revised_table.replace(span, replacement)
    revised_notes = before if operation == "edit" and verdict != "supported" else after
    revised = f"<!-- layout: chart; chart: bar -->\n{revised_table}\n\n{revised_notes}"
    reply = deck_replies(replacer=replacing({span: replacement}), rewrite=lambda _i, _p: revised)

    def inner(request):
        answer = reply(request)
        if verdict != "supported" and is_sentence_checking(request):
            prompt = request["messages"][-1]["content"]
            row_number = int(re.search(r"^\((\d+)\) \| Afternoon", prompt, re.M)[1])
            results = [row for row in json.loads(answer)["sentences"] if row["n"] != row_number]
            if verdict == "not":
                results.append({"n": row_number, "verdict": "not"})
            return json.dumps({"sentences": results})
        return answer

    action = ({"do": "edit", "item": 2, "passage": span} if operation == "edit"
              else {"do": "write", "parts": [2]})
    fake = FakeProvider(script([{"actions": [
        {**action, "instruction": "Clarify the notes"},
        {"do": "save", "text": "Edited the speaker notes."},
    ]}], inner))
    with app_for(tmp_path, fake) as client:
        base, _ = library(client, notes={"Rates": (
            "Morning rate was 62%, adjusted rate 54%. Afternoon rate was 41%, adjusted rate 35%."
        )})
        outline = {**SLIDES_OUTLINE, "sections": [
            SLIDES_OUTLINE["sections"][0], {"heading": "Rates vary by time of day", "layout": "bar_chart"},
            SLIDES_OUTLINE["sections"][2],
        ]}
        saved = saved_output(client, base, outline, [SLIDES[0], f"{table}\n\n{before}", SLIDES[2]],
                             approach=SLIDES_APPROACH, kind="slides", conversationId="chart-edit")
        client.get(conversations(base), headers=workspace(client)).raise_for_status()

        result = done(turn(client, base, "chart-edit", "Clarify the notes of slide 2."))
        assert result["outcome"] == "saved", result
        cover, chart, close = split_document(result["artifact"]["content"], "slides")[1]
        old_cover, _, old_close = split_document(saved["content"], "slides")[1]
        assert cover == old_cover and "design: v1" in cover and close == old_close
        layout = "bar_chart" if verdict == "supported" and not multi_series else "compare"
        assert chart == f"<!-- layout: {layout} -->\n{revised_table}\n\n{revised_notes}"
        claim_rows = result["artifact"]["sections"][1]["coverage"]["claims"]
        assert {tuple(claim["tableRow"]): claim["verdict"]
                for claim in claim_rows if "tableRow" in claim} == {
            (0, 2): "supported", (0, 3): verdict,
        }
        if verdict != "supported":
            assert any("drawn as a table" in note["label"]
                       for note in result["artifact"]["provenance"]["skill"]["skipped"])


def test_the_budget_pauses_a_turn_and_continue_picks_it_up(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        service = client.app.state.knowledge_service
        service.artifact_limits = AgentLimits(decisions=2)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "search_library", "query": "CD3D markers"}]},
                    {"actions": [{"do": "search_library", "query": "CD19 markers"}]},
                ]
            )
        ]
        events = turn(client, base_id, "talk", "Find out everything")
        end = done(events)
        assert end["outcome"] == "paused" and end["artifact"] is None
        assert "used all 2 decisions" in end["reply"] and "continue" in end["reply"]
        assert end["message"]["kind"] == "paused"
        stored, _ = kept(client, "talk")
        assert stored.pending is None
        # "Continue" goes on from the workspace, with a fresh budget.
        service.artifact_limits = AgentLimits()
        fake.replies[:] = [script([{"actions": [{"do": "reply", "text": "That is all I found."}]}])]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "", kind="continue")
        assert done(events)["outcome"] == "replied"
        assert 'Latest message ("continue"):\nContinue' in seen_by_the_orchestrator(fake, start)[0]


def test_a_finished_change_is_saved_when_the_budget_runs_out(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        client.app.state.knowledge_service.artifact_limits = AgentLimits(decisions=2)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "write", "parts": [2], "instruction": "shorter"}]},
                    {"actions": [{"do": "search_library", "query": "CD3D markers"}]},
                ],
                deck_replies(rewrite=shortening),
            )
        ]
        end = done(turn(client, base_id, "talk", "Shorten 2 and look around"))
        assert end["outcome"] == "saved" and end["artifact"]["versionNumber"] == 2
        assert end["reply"] == "Rewrote 2."
        assert kept(client, "talk")[0].log[-1] == "v2 (deck): Rewrote 2. · changed 2"


def test_an_action_that_needs_more_calls_than_are_left_is_not_started(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        client.app.state.knowledge_service.artifact_limits = AgentLimits(calls=4)
        fake.replies[:] = [
            script([{"actions": [{"do": "write", "parts": [2, 3], "instruction": "shorter"}]}])
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Shorten them")
        end = done(events)
        assert end["outcome"] == "paused"
        assert failures(events) == ["Not enough model calls are left in this turn for that."]
        assert calls(fake, start) == 1


def test_what_the_user_typed_in_the_panel_during_a_turn_is_kept(tmp_path: Path) -> None:
    from gunther.schemas import EditArtifactBriefInput

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        service = client.app.state.knowledge_service
        workspace_id = workspace(client)["X-Gunther-Workspace-Id"]

        def typing(request: dict) -> dict:
            service.edit_artifact_brief(
                base_id,
                workspace_id,
                "talk",
                EditArtifactBriefInput(goal="The goal I typed", spec={"length": "5 slides"}),
            )
            return {"actions": [{"do": "reply", "text": "Noted."}]}

        fake.replies[:] = [
            script(
                [
                    {
                        "brief": [
                            {"field": "goal", "text": "Cost", "quote": "cost"},
                            {"field": "constraint", "text": "no jargon", "quote": "no jargon"},
                        ],
                        "actions": [{"do": "search_library", "query": "CD3D markers"}],
                    },
                    typing,
                ]
            )
        ]
        events = turn(client, base_id, "talk", "Focus on cost, no jargon")
        assert done(events)["outcome"] == "replied"
        stored, brief = kept(client, "talk")
        assert brief.goal == Line(text="The goal I typed", mark="edited")
        assert [line.text for line in brief.constraints] == ["no jargon"]
        assert stored.works[0].spec.length == Line(text="5 slides", mark="edited")


def test_a_report_made_from_a_deck_starts_from_the_decks_approach(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {
                        "brief": [
                            {"field": "length", "text": "3 pages", "quote": "3 pages"},
                            {"field": "structure", "text": "decision", "quote": ""},
                        ],
                        "actions": [{"do": "select", "artifact": "new:report"}],
                    },
                    {"actions": [{"do": "select", "artifact": "1"}]},
                    {"actions": [{"do": "reply", "text": "Started the report."}]},
                ]
            )
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Now a 3 pages report from this deck")
        assert done(events)["outcome"] == "replied"
        stored, _ = kept(client, "talk")
        deck, report = stored.works
        assert report.spec.kind == "report" and report.key.startswith("draft_")
        # The select ran first, so the length landed on the report, not on the deck.
        assert report.spec.length == Line(text="3 pages", mark="you")
        assert deck.spec.length != Line(text="3 pages", mark="you")
        assert report.approach == deck.approach and report.approach is not deck.approach
        assert stored.active == deck.key  # the second decision selected the first artifact
        view = seen_by_the_orchestrator(fake, start)[1]
        assert '[2] report "untitled" not saved yet (active)' in view
        assert "report in Studio:" in view and "Structures:" in view
        assert "Choosing" not in view


def test_the_web_is_refused_when_it_is_not_set_up_and_works_when_it_is(tmp_path: Path) -> None:
    from gunther.schemas import WebSearchOut, WebSearchSourceOut

    class Web:
        mode = "tavily"
        available = True

        def search(self, query: str, **_: Any) -> WebSearchOut:
            return WebSearchOut(
                query=query,
                answer="",
                mode="tavily",
                message=None,
                sources=[
                    WebSearchSourceOut(
                        title="Cool roofs", url="https://example.org/roofs", snippet="Roofs cool."
                    )
                ],
            )

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "search_web", "query": "cool roofs"}]},
                    {"actions": [{"do": "reply", "text": "No web."}]},
                ]
            )
        ]
        refused = turn(client, base_id, "talk", "Search the web", web=True)
        assert failures(refused) == [
            "The web is not available: web search is not set up (Settings → Web search)."
        ]
        client.app.state.knowledge_service.web_search = Web()
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "search_web", "query": "cool roofs"}]},
                    {"actions": [{"do": "reply", "text": "Found one."}]},
                ]
            )
        ]
        off = turn(client, base_id, "talk", "Search the web", web=False)
        assert "the Web toggle is off for this message" in failures(off)[0]
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "search_web", "query": "cool roofs"}]},
                    {"actions": [{"do": "reply", "text": "Found one."}]},
                ]
            )
        ]
        events = turn(client, base_id, "talk", "Search the web", web=True)
        assert failures(events) == [] and done(events)["outcome"] == "replied"
        assert "Searched the web for “cool roofs” · 1 found" in names(events, "action", "done")[0]
        stored, _ = kept(client, "talk")
        assert any(item["url"] == "https://example.org/roofs" for item in stored.pool)


def test_more_of_a_source_is_read_and_actions_after_the_end_of_a_turn_are_dropped(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "read_source", "query": "1"}]},
                    {
                        "actions": [
                            {"do": "reply", "text": "Read it."},
                            {"do": "search_library", "query": "never run"},
                        ]
                    },
                ]
            )
        ]
        events = turn(client, base_id, "talk", "Read more of source 1")
        assert done(events)["outcome"] == "replied" and failures(events) == []
        assert names(events, "action", "done")[0].startswith("Read around source 1") or (
            names(events, "action", "done")[0].startswith("Read more around source 1")
        )
        assert states(events)[-1] == "skipped"
        assert not [r for r in fake.requests if "never run" in str(r)]


def test_a_turn_that_cannot_start_says_why(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        missing = turn(client, base_id, "no-such", "Hello")
        assert missing[-1][0] == "error" and missing[-1][1]["status"] == 404
        bad = client.post(
            conversations(base_id, "/no-such/messages/stream"),
            headers=workspace(client),
            json={"clientRequestId": "turn_request_bad1", "text": "   "},
        )
        assert bad.status_code == 422
        empty_id, _ = library(client, "Empty", notes={})
        client.post(conversations(empty_id), headers=workspace(client), json={"id": "none"})
        nothing = turn(client, empty_id, "none", "Make slides")
        assert nothing[-1][0] == "error" and nothing[-1][1]["status"] == 400
        assert "nothing to build from" in nothing[-1][1]["detail"].lower()


def test_a_reply_after_a_change_is_refused_until_it_is_saved(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    row = "| Marker | CD3D [1] | CD19 [2] |"
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {
                        "actions": [
                            {"do": "edit", "item": 2, "passage": row, "instruction": "say gene"},
                            {"do": "reply", "text": "I named the row."},
                        ]
                    },
                    {"actions": [{"do": "save", "text": "Named the row."}]},
                ],
                deck_replies(replacer=replacing({row: "| Gene | CD3D [1] | CD19 [2] |"})),
            )
        ]
        start = len(fake.requests)
        events = turn(client, base_id, "talk", "Call the row Gene")
        end = done(events)
        assert end["outcome"] == "saved" and end["reply"] == "Named the row."
        assert "save it instead" in seen_by_the_orchestrator(fake, start)[1]


def test_a_passage_may_be_a_paragraph_number_or_an_opening_cut_short() -> None:
    from gunther.artifact_agent import resolve_passage

    report = "## Heat\n\nFirst paragraph [1].\n\nSecond paragraph, which is long [2].\n\nThird."
    assert resolve_passage("¶2", report, False) == "Second paragraph, which is long [2]."
    assert resolve_passage("¶9", report, False) == "¶9"
    assert (
        resolve_passage("¶2 Second paragraph, which…", report, False)
        == "Second paragraph, which is long [2]."
    )
    slide = "## Heat\n\n- One [1]\n\nNote: Say this first.\n\nThen say that."
    assert (
        resolve_passage("Note: Say this…", slide, True) == "Note: Say this first.\n\nThen say that."
    )
    assert resolve_passage("- One [1]", slide, True) == "- One [1]"


def test_a_request_about_one_part_leaves_the_whole_works_brief_alone(tmp_path: Path) -> None:
    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {
                        "brief": [
                            {"field": "audience", "text": "first-year students", "quote": ""}
                        ],
                        "actions": [
                            {"do": "edit", "item": 2, "passage": ROW, "instruction": "simpler"}
                        ],
                    },
                    {"actions": [{"do": "save", "text": "Made slide 2 simpler."}]},
                ],
                deck_replies(replacer=replacing({ROW: "| Gene | CD3D [1] | CD19 [2] |"})),
            )
        ]
        start = len(fake.requests)
        end = done(turn(client, base_id, "talk", "Make this one simpler for students"))
        assert end["outcome"] == "saved", end["reply"]
        _, brief = kept(client, "talk")
        assert brief.audience is None or "first-year" not in brief.audience.text
        assert "not the whole work" in seen_by_the_orchestrator(fake, start)[1]


ROW = "| Marker | CD3D [1] | CD19 [2] |"


def test_a_search_passage_without_its_block_is_found_in_the_block_it_starts_in(
    tmp_path: Path,
) -> None:
    from gunther.models import ContentBlock, SourceRevision
    from gunther.schemas import ConversationCitationOut

    fake = FakeProvider(deck_replies())
    with app_for(tmp_path, fake) as client:
        library(client)
        service = client.app.state.knowledge_service
        with service.sessions() as session:
            block = max(session.scalars(select(ContentBlock)), key=lambda b: len(b.content))
            source_id = session.get(SourceRevision, block.revision_id).source_id
            words = " ".join(block.content.split())
            piece = ConversationCitationOut.model_construct(
                source_id=source_id, source_revision_id=None, block_id=None, quote=words[3:]
            )
            found = KnowledgeService._passage_block(session, piece)
            assert found is not None and found.id == block.id
            short = ConversationCitationOut.model_construct(
                source_id=source_id, source_revision_id=None, block_id=None, quote="too short"
            )
            assert KnowledgeService._passage_block(session, short) is None
