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
    orchestrator_replies,
    skill_replies,
)
from sqlalchemy import select
from test_artifact_steps import replacing
from test_output_skills import (
    SEARCH,
    SLIDES,
    SLIDES_APPROACH,
    SLIDES_OUTLINE,
)
from test_outputs import (
    app_for,
    body,
    build,
    built,
    in_background,
    library,
    read_events,
    wait_for,
    workspace,
)

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
)
from gunther.models import Artifact, ArtifactConversation, ArtifactMessage
from gunther.outputs import Pool, section_hash, split_document
from gunther.service import KnowledgeService

_turns = itertools.count(1)


def conversations(base_id: str, tail: str = "") -> str:
    return f"/api/knowledge-bases/{base_id}/artifact-conversations{tail}"


# A conversation with a deck in it ----------------------------------------------------------------

REWRITTEN = (
    "## CD3D and CD19 set T and B cells apart\n\n- CD3D marks T cells [1]\n- CD19 marks B cells [2]"
)


def skill(written: Any = SLIDES, **more: Any):
    return skill_replies(SLIDES_APPROACH, SLIDES_OUTLINE, written, gather=SEARCH, **more)


def script(decisions: list[Any], inner=None, **more: Any):
    return orchestrator_replies(decisions, inner or skill(), **more)


def a_deck(client, fake: FakeProvider, base_id: str, cid: str = "talk") -> str:
    """A deck made the old way for a conversation, which the agent then finds as its work."""

    fake.replies[:] = [skill()]
    version = built(
        build(
            client,
            base_id,
            body(kind="slides", conversationId=cid, conversationTitle="Markers talk"),
        )
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
    with app_for(tmp_path, fake, skills=True) as client:
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
        assert provenance["skill"]["name"] == "slides" and provenance["audience"] == "scientist"
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
        assert calls(fake, before) > 20
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

    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        first = a_deck(client, fake, base_id)
        before_parts = works_text(client, "talk")
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "write", "parts": [2], "instruction": "shorter"}]},
                    {"actions": [{"do": "save", "text": "Shortened slide 2."}]},
                ],
                skill(rewrite=shortening),
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
        writing = [
            r for r in fake.requests[start:] if "Write section" in str(r["messages"][-1]["content"])
        ]
        assert len(writing) == 1 and "Write section 2 of 3" in str(
            writing[0]["messages"][-1]["content"]
        )
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


def test_the_one_before_it_is_found_from_the_changes_so_far(tmp_path: Path) -> None:
    """§2.9 example 5: the change log says the last version changed slide 3, so "the one before
    it" is slide 2."""

    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "write", "parts": [3], "instruction": "shorter"}]},
                    {"actions": [{"do": "save", "text": "Shortened slide 3."}]},
                ],
                skill(rewrite=shortening),
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
                skill(rewrite=shortening),
            )
        ]
        end = done(turn(client, base_id, "talk", "No, the one before it"))
        assert end["artifact"]["versionNumber"] == 3
        assert [(c["position"], c["what"]) for c in end["changes"]] == [(2, "rewritten")]


ALTERED = {
    **SLIDES_APPROACH,
    "cards": [
        {"claim": "Cost is mostly the coating", "refs": [1], "type": "data"},
        {"claim": "Labour is the rest", "refs": [2], "type": "data"},
    ],
}


def answering_understand(inner, answer):
    def reply(request: dict) -> Any:
        system = str(request["messages"][0]["content"])
        if "Gunther skill step: understand" in system and "Write the approach" in system:
            return json.dumps(answer)
        return inner(request)

    return reply


def test_a_changed_goal_makes_only_what_depends_on_it_out_of_date(tmp_path: Path) -> None:
    """§2.9 example 3: the goal changes, the approach is out of date, and only the slides whose
    cards changed are written again; the title slide is untouched."""

    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        first = a_deck(client, fake, base_id)
        before_parts = works_text(client, "talk")

        def costing(index: int, prompt: str) -> str:
            return SLIDES[index].replace("Marker |", "Cost |").replace("T cells", "T-cell cost")

        inner = answering_understand(skill(written=costing), ALTERED)
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
        writing = [
            str(r["messages"][-1]["content"])
            for r in fake.requests[start:]
            if "Write section" in str(r["messages"][-1]["content"])
        ]
        assert not any("Write section 1 of" in text for text in writing)
        assert works_text(client, "talk")[0] == before_parts[0]
        assert head_of(client, base_id, first)["versionNumber"] == 1


def test_a_question_about_the_deck_is_answered_and_saves_nothing(tmp_path: Path) -> None:
    """§2.9 example 6: the answer names the source by the number the user sees, a number that
    is not one of the sources is dropped, and no version is made."""

    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    ask = {"do": "ask", "question": "Which one?", "options": ["A", "B"]}
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    search = {"do": "search_library", "query": "CD3D markers"}
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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

    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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

    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "write", "parts": [2], "instruction": "shorter"}]},
                    {"actions": [{"do": "save", "text": "Shortened slide 2."}]},
                ],
                skill(rewrite=shortening),
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
slides skill: understand → plan → write → review. Layouts: title, big_number, compare, two_column, flow, timeline, bar_chart, line_chart, cards, quote, figure, diagram, text, composed, takeaways. Uses: talk, read
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
    assert text.splitlines()[1].startswith("report skill:") and "Structures: what," in text
    assert 'Artifacts: [1] report "The trial" not saved yet (active)' in text
    assert (
        "  2 Heat fell · not re-checked · sources 1, 2 · cards C1\n      ¶1 First paragraph [1].\n      ¶2 Second one"
        in text
    )
    assert "…" in text.split("¶2 ")[1].splitlines()[0]
    assert 'You asked: "Which focus?" Options: Trial | Debate (this message answers it)' in text
    assert "Latest message (answer to your question):\nTrial" in text
    # With no artifact both skills are offered, and nothing is pointed at.
    empty = build_view(
        View("Cells", False, Workspace(), ConversationBrief(), Pool(160), {}, [], [], [], (12, 200, 8), "message", "Hi")
    )  # fmt: skip
    assert "Artifacts: none yet." in empty and "- (nothing yet)" in empty
    assert empty.count(" skill: understand") == 2 and "Pointed at" not in empty


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
    ({"do": "plan", "change": "insert", "after": 1, "heading": "X"}, "is not one of the deck's layouts"),
    ({"do": "plan", "change": "insert", "after": 1, "heading": "X", "layout": "title"}, "one title slide"),
    ({"do": "plan", "change": "insert", "after": 1, "heading": "X", "layout": "text"}, None),
    ({"do": "plan", "change": "delete", "item": 1}, "The title slide stays first."),
    ({"do": "plan", "change": "delete", "item": 4}, "There is no slide 4; there are 3."),
    ({"do": "plan", "change": "delete", "item": 2}, None),
    ({"do": "plan", "change": "move", "item": 2}, "no place after None"),
    ({"do": "plan", "change": "move", "item": 2, "after": 3}, None),
    ({"do": "plan", "change": "move", "item": 1, "after": 3}, "The title slide stays first."),
    ({"do": "plan", "change": "item", "item": 2}, "Say what to change"),
    ({"do": "plan", "change": "item", "item": 2, "layout": "pie"}, "is not one of the deck's layouts"),
    ({"do": "plan", "change": "item", "item": 2, "layout": "flow"}, None),
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
        return inserted if "Write section 2 of 4" in prompt else SLIDES[min(index, 2)]

    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
                                "layout": "text",
                            },
                            # After the insert, slide 2 is the new one.
                            {"do": "write", "parts": [2]},
                        ]
                    },
                    {"actions": [{"do": "save", "text": "Added a slide on the 2024 trial."}]},
                ],
                skill(written=written),
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
                skill(written=retitling),
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
        writer_prompts = [
            str(r["messages"][-1]["content"])
            for r in fake.requests[start:]
            if not is_orchestrating(r)
            and "Write section " in str(r["messages"][-1]["content"])
        ]
        assert len(writer_prompts) == 1
        assert "Write section 1 of 3: Telling T and B cells apart" in writer_prompts[0]


def test_a_slide_is_removed_by_code_alone(tmp_path: Path) -> None:
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    row = "| Marker | CD3D [1] | CD19 [2] |"
    with app_for(tmp_path, fake, skills=True) as client:
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
                skill(replacer=replacing({row: "| Gene | CD3D [1] | CD19 [2] |"})),
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


def test_an_edit_that_breaks_the_form_of_a_layout_is_written_again_or_drawn_as_text(
    tmp_path: Path,
) -> None:
    from test_artifact_steps import steps_for

    row = "| Marker | CD3D [1] | CD19 [2] |"
    deck_fake = FakeProvider(skill(replacer=replacing({row: "CD3D and CD19 mark cells."})))
    steps = steps_for(deck_fake, "slides")
    steps.understand()
    steps.plan_whole()
    steps.write([item.id for item in steps.work.outline])
    second = steps.work.outline[1].id
    assert steps.edit(second, row, "say it in words") is True
    # The slide was written again once (as the skill's fit does), and still had no table.
    assert layout_of_part(steps.work.parts[second].text) == "text"
    assert [note["label"] for note in steps.work.skipped] == ["Slide 2 drawn as text"]
    assert steps.work.outline[1].layout == "compare"

    again = skill(
        replacer=replacing({row: "CD3D and CD19 mark cells."}), rewrite=lambda i, p: SLIDES[1]
    )
    steps = steps_for(FakeProvider(again), "slides")
    steps.understand()
    steps.plan_whole()
    steps.write([item.id for item in steps.work.outline])
    second = steps.work.outline[1].id
    steps.edit(second, row, "say it in words")
    assert layout_of_part(steps.work.parts[second].text) == "compare"
    assert steps.work.skipped == []


def layout_of_part(text: str) -> str | None:
    from gunther.outputs import layout_of

    return layout_of(text)[0]


def test_the_budget_pauses_a_turn_and_continue_picks_it_up(tmp_path: Path) -> None:
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
        assert "I ran out of room" in end["reply"] and "continue" in end["reply"]
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        a_deck(client, fake, base_id)
        client.app.state.knowledge_service.artifact_limits = AgentLimits(decisions=2)
        fake.replies[:] = [
            script(
                [
                    {"actions": [{"do": "write", "parts": [2], "instruction": "shorter"}]},
                    {"actions": [{"do": "search_library", "query": "CD3D markers"}]},
                ],
                skill(rewrite=shortening),
            )
        ]
        end = done(turn(client, base_id, "talk", "Shorten 2 and look around"))
        assert end["outcome"] == "saved" and end["artifact"]["versionNumber"] == 2
        assert end["reply"] == "Rewrote 2."
        assert kept(client, "talk")[0].log[-1] == "v2 (deck): Rewrote 2. · changed 2"


def test_an_action_that_needs_more_calls_than_are_left_is_not_started(tmp_path: Path) -> None:
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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

    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
        assert "report skill:" in view and "Structures:" in view
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

    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
    fake = FakeProvider(skill())
    row = "| Marker | CD3D [1] | CD19 [2] |"
    with app_for(tmp_path, fake, skills=True) as client:
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
                skill(replacer=replacing({row: "| Gene | CD3D [1] | CD19 [2] |"})),
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
    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
                skill(replacer=replacing({ROW: "| Gene | CD3D [1] | CD19 [2] |"})),
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

    fake = FakeProvider(skill())
    with app_for(tmp_path, fake, skills=True) as client:
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
