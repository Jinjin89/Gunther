"""The Artifacts agent's conversations on the backend: the API, bringing in a device's
conversations, recovering them from the versions that name them, and the tables."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fake_models import FakeProvider, skill_replies
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from test_output_skills import (
    APPROACH,
    OUTLINE,
    REPORT,
    SEARCH,
    SLIDES,
    SLIDES_APPROACH,
    SLIDES_OUTLINE,
)
from test_outputs import SIDECAR, app_for, body, build, built, library, revise, workspace

from gunther.artifact_workspace import (
    Pending,
    Workspace,
    dump_workspace,
    load_workspace,
)
from gunther.database import create_database_engine, create_session_factory, session_scope
from gunther.migrations import (
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    get_schema_version,
    run_migrations,
)
from gunther.models import ArtifactConversation, ArtifactMessage, Base


def conversations(base_id: str, tail: str = "") -> str:
    return f"/api/knowledge-bases/{base_id}/artifact-conversations{tail}"


def deck_replies():
    return skill_replies(SLIDES_APPROACH, SLIDES_OUTLINE, SLIDES, gather=SEARCH)


def report_replies():
    return skill_replies(APPROACH, OUTLINE, REPORT, gather=SEARCH)


def a_library(tmp_path: Path):
    """An app whose model builds decks and reports by their skills, and a library."""

    fake = FakeProvider(deck_replies())
    client = app_for(tmp_path, fake, skills=True)
    return fake, client


def make(client, fake: FakeProvider, base_id: str, replies, **fields) -> dict:
    """Build an output with these replies (each build needs its own script)."""

    fake.replies[:] = [replies]
    return built(build(client, base_id, body(**fields)))


# The API ---------------------------------------------------------------------------------------


def test_a_conversation_is_created_listed_read_renamed_and_archived(tmp_path: Path) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, ids = library(client)
        headers = workspace(client)

        asked = {"id": "device-one", "title": " Cool roofs "}
        created = client.post(conversations(base_id), headers=headers, json=asked)
        assert created.status_code == 201, created.text
        one = created.json()
        assert one["id"] == "device-one" and one["title"] == "Cool roofs"
        assert one["scope"] == {"mode": "library", "sourceIds": [], "unitIds": [], "sessionIds": []}
        assert one["brief"] == {"goal": None, "audience": None, "constraints": [], "open": []}
        assert (one["messages"], one["works"]) == ([], [])
        assert (one["active"], one["pending"]) == (None, None)
        assert (one["artifactCount"], one["waiting"], one["archived"]) == (0, False, False)
        assert {"updatedAt", "createdAt"} <= set(one)

        # A title of nothing but spaces is no title.
        blank = client.post(conversations(base_id), headers=headers, json={"title": "   "})
        assert blank.status_code == 201 and blank.json()["title"] == "New conversation"
        # Asking again for an id that exists returns that conversation, as it is.
        again = client.post(
            conversations(base_id), headers=headers, json={"id": "device-one", "title": "Other"}
        )
        assert again.status_code == 201 and again.json()["title"] == "Cool roofs"
        # With no id, the backend picks one.
        other = client.post(conversations(base_id), headers=headers, json={}).json()
        assert other["title"] == "New conversation" and other["id"] != "device-one"

        listed = client.get(conversations(base_id), headers=headers).json()
        assert [item["id"] for item in listed] == [other["id"], blank.json()["id"], "device-one"]
        assert set(listed[0]) == {"id", "title", "updatedAt", "artifactCount", "waiting"}

        read = client.get(conversations(base_id, "/device-one"), headers=headers)
        assert read.status_code == 200 and read.json() == one

        renamed = client.patch(
            conversations(base_id, "/device-one"), headers=headers, json={"title": "Roofs talk"}
        ).json()
        assert renamed["title"] == "Roofs talk" and renamed["updatedAt"] >= one["updatedAt"]
        # Newly changed comes first.
        assert [i["id"] for i in client.get(conversations(base_id), headers=headers).json()][0] == (
            "device-one"
        )

        picked = {"mode": "selection", "sourceIds": [ids["Markers"]]}
        picked = {**picked, "unitIds": [], "sessionIds": []}
        scoped = client.patch(
            conversations(base_id, "/device-one"), headers=headers, json={"scope": picked}
        )
        assert scoped.status_code == 200 and scoped.json()["scope"] == picked

        archived = client.patch(
            conversations(base_id, "/device-one"), headers=headers, json={"archived": True}
        ).json()
        assert archived["archived"] is True
        remaining = client.get(conversations(base_id), headers=headers).json()
        assert "device-one" not in [i["id"] for i in remaining] and len(remaining) == 2
        # An archived conversation can still be read.
        assert client.get(conversations(base_id, "/device-one"), headers=headers).status_code == 200


def test_what_cannot_be_done_is_refused_in_words(tmp_path: Path) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        other_id, _ = library(client, "Other")
        headers = workspace(client)
        client.post(conversations(base_id), headers=headers, json={"id": "mine"})

        assert client.get(conversations(base_id, "/nope"), headers=headers).status_code == 404
        assert client.get(conversations("no-library"), headers=headers).status_code == 404
        # A conversation belongs to its library.
        assert client.get(conversations(other_id, "/mine"), headers=headers).status_code == 404
        taken = client.post(conversations(other_id), headers=headers, json={"id": "mine"})
        assert taken.status_code == 409
        blank = client.patch(
            conversations(base_id, "/mine"), headers=headers, json={"title": "   "}
        )
        assert blank.status_code == 400
        missing = {"mode": "selection", "sourceIds": ["src_gone"], "unitIds": [], "sessionIds": []}
        gone = client.patch(
            conversations(base_id, "/mine"), headers=headers, json={"scope": missing}
        )
        assert gone.status_code == 400 and "src_gone" in gone.json()["detail"]
        # An id with odd characters, or a title that is empty, is not accepted at all.
        for bad in ({"id": "has space"}, {"id": ""}, {"title": ""}):
            assert client.post(conversations(base_id), headers=headers, json=bad).status_code == 422
        # The workspace header is needed, as for the other outputs.
        assert client.get(conversations(base_id), headers=SIDECAR).status_code == 422


def test_the_agents_question_shows_in_the_list_and_the_conversation(tmp_path: Path) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        headers = workspace(client)
        client.post(conversations(base_id), headers=headers, json={"id": "asking"})
        sessions = client.app.state.knowledge_service.sessions
        with session_scope(sessions) as session:
            row = session.get(ArtifactConversation, "asking")
            row.state_json = dump_workspace(
                Workspace(
                    pending=Pending(
                        question="Which focus?", options=["Trial", "Debate"], message_id="acm_1"
                    )
                )
            )
        summary = client.get(conversations(base_id), headers=headers).json()[0]
        assert summary["waiting"] is True
        detail = client.get(conversations(base_id, "/asking"), headers=headers).json()
        assert detail["pending"] == {
            "question": "Which focus?",
            "options": ["Trial", "Debate"],
            "messageId": "acm_1",
        }


def test_lines_typed_in_the_panel_are_marked_as_the_users(tmp_path: Path) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        headers = workspace(client)
        client.post(conversations(base_id), headers=headers, json={"id": "panel"})

        edited = client.patch(
            conversations(base_id, "/panel/brief"),
            headers=headers,
            json={
                "goal": "Explain the trial",
                "audience": "lab members",
                "constraints": ["no jargon", "short"],
                "open": ["Include costs?"],
            },
        )
        assert edited.status_code == 200, edited.text
        assert edited.json() == {
            "brief": {
                "goal": {"text": "Explain the trial", "mark": "edited"},
                "audience": {"text": "lab members", "mark": "edited"},
                "constraints": [
                    {"text": "no jargon", "mark": "edited"},
                    {"text": "short", "mark": "edited"},
                ],
                "open": [{"text": "Include costs?", "mark": "edited"}],
            },
            "spec": None,
        }
        # What is left out stays; empty text clears; a list is the whole new list.
        changed = client.patch(
            conversations(base_id, "/panel/brief"),
            headers=headers,
            json={"audience": "", "constraints": ["short"]},
        ).json()["brief"]
        assert changed["goal"]["text"] == "Explain the trial" and changed["audience"] is None
        assert [line["text"] for line in changed["constraints"]] == ["short"]
        read = client.get(conversations(base_id, "/panel"), headers=headers).json()
        assert read["brief"] == changed
        # Use, length and structure belong to an artifact, and there is none yet.
        refused = client.patch(
            conversations(base_id, "/panel/brief"), headers=headers, json={"spec": {"use": "talk"}}
        )
        assert refused.status_code == 400 and "no artifact yet" in refused.json()["detail"]
        nowhere = client.patch(conversations(base_id, "/nope/brief"), headers=headers, json={})
        assert nowhere.status_code == 404
        too_long = client.patch(
            conversations(base_id, "/panel/brief"), headers=headers, json={"goal": "x" * 301}
        )
        assert too_long.status_code == 422


# A device's conversations, brought in once -------------------------------------------------------


def device_conversation(deck_id: str, **fields) -> dict:
    return {
        "id": "device-1",
        "title": "Markers talk",
        "scope": {"mode": "library", "sourceIds": [], "unitIds": [], "sessionIds": []},
        "createdAt": "2026-10-01T09:00:00.000Z",
        "turns": [
            {
                "id": "turn-1",
                "instruction": "Slides on markers",
                "status": "saved",
                "artifactId": deck_id,
                "createdAt": "2026-10-01T09:00:05.000Z",
            },
            {
                "id": "turn-2",
                "instruction": "Make it longer",
                "status": "failed",
                "error": "The model is down",
                "createdAt": "2026-10-01T09:05:00.000Z",
            },
            {
                "id": "turn-3",
                "instruction": "Add a slide",
                "status": "stopped",
                "createdAt": "2026-10-01T09:06:00.000Z",
            },
            {
                "id": "turn-4",
                "instruction": "And another",
                "status": "running",
                "createdAt": "2026-10-01T09:07:00.000Z",
            },
        ],
        **fields,
    }


def test_a_device_conversation_becomes_messages_and_a_work_and_is_brought_in_once(
    tmp_path: Path,
) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        headers = workspace(client)
        deck = make(
            client, fake, base_id, deck_replies(), kind="slides", brief="Slides on markers",
            conversationId="device-1", conversationTitle="Markers talk",
        )

        imported = client.post(
            conversations(base_id, "/import"),
            headers=headers,
            json={"conversations": [device_conversation(deck["id"])]},
        )
        assert imported.status_code == 200, imported.text
        (summary,) = imported.json()
        assert (summary["id"], summary["title"], summary["artifactCount"]) == (
            "device-1",
            "Markers talk",
            1,
        )
        assert summary["waiting"] is False and summary["updatedAt"].startswith("2026-10-01T09:07")

        detail = client.get(conversations(base_id, "/device-1"), headers=headers).json()
        messages = detail["messages"]
        assert [(m["role"], m["kind"], m["text"]) for m in messages] == [
            ("user", "message", "Slides on markers"),
            ("assistant", "result", ""),
            ("user", "message", "Make it longer"),
            ("assistant", "failed", ""),
            ("user", "message", "Add a slide"),
            ("assistant", "stopped", ""),
            ("user", "message", "And another"),
            ("assistant", "stopped", ""),  # a turn that was running when the device went away
        ]
        assert messages[1]["artifactId"] == deck["id"]
        assert messages[1]["payload"] == {"imported": True}
        assert messages[3]["payload"] == {"imported": True, "error": "The model is down"}
        assert [m["clientRequestId"] for m in messages if m["role"] == "user"] == [
            "turn-1",
            "turn-2",
            "turn-3",
            "turn-4",
        ]
        assert messages[0]["createdAt"] == "2026-10-01T09:00:05.000Z"

        # The deck is the conversation's work, and is up to date.
        assert detail["active"] == deck["lineageId"]
        (work,) = detail["works"]
        assert (work["key"], work["kind"], work["versionId"]) == (
            deck["lineageId"],
            "slides",
            deck["id"],
        )
        assert work["spec"]["use"] == {"text": "talk", "mark": "guessed"}
        assert work["approach"] == {"state": "ok", "why": ""}
        assert work["outline"] == {"state": "ok", "why": ""}
        assert [(p["position"], p["layout"], p["state"]) for p in work["parts"]] == [
            (1, "title", "ok"),
            (2, "compare", "ok"),
            (3, "takeaways", "ok"),
        ]
        assert work["parts"][0]["itemId"] == "p1"
        assert detail["brief"]["audience"] == {"text": "scientist", "mark": "guessed"}

        # Bringing the same conversations in again changes nothing.
        before = client.get(conversations(base_id), headers=headers).json()
        second = client.post(
            conversations(base_id, "/import"),
            headers=headers,
            json={"conversations": [device_conversation(deck["id"], title="Changed")]},
        )
        assert second.status_code == 200 and second.json() == []
        assert client.get(conversations(base_id), headers=headers).json() == before
        assert client.get(conversations(base_id, "/device-1"), headers=headers).json() == detail


def test_editing_the_brief_makes_what_depends_on_it_out_of_date_and_nothing_else(
    tmp_path: Path,
) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        headers = workspace(client)
        make(
            client, fake, base_id, deck_replies(), kind="slides", brief="Slides on markers",
            conversationId="stale",
        )
        client.get(conversations(base_id), headers=headers)  # brings the deck in as a work
        url = conversations(base_id, "/stale/brief")

        changed = client.patch(url, headers=headers, json={"spec": {"use": "read", "length": "6"}})
        assert changed.status_code == 200, changed.text
        assert changed.json()["spec"]["use"] == {"text": "read", "mark": "edited"}
        (work,) = client.get(conversations(base_id, "/stale"), headers=headers).json()["works"]
        assert work["approach"] == {"state": "out_of_date", "why": "the use and the length changed"}
        assert work["outline"] == {"state": "ok", "why": ""}
        # The use is something every slide is written for; the length is not.
        assert [(p["state"], p["why"]) for p in work["parts"]] == [
            ("out_of_date", "the use changed")
        ] * 3

        # Nothing is remembered as stale: put back, it is up to date again.
        client.patch(url, headers=headers, json={"spec": {"use": "talk", "length": ""}})
        (work,) = client.get(conversations(base_id, "/stale"), headers=headers).json()["works"]
        assert work["approach"]["state"] == "ok"
        assert [p["state"] for p in work["parts"]] == ["ok", "ok", "ok"]

        # Constraints and open questions change no step.
        client.patch(url, headers=headers, json={"constraints": ["no jargon"], "open": ["Costs?"]})
        (work,) = client.get(conversations(base_id, "/stale"), headers=headers).json()["works"]
        assert work["approach"]["state"] == "ok"
        assert [p["state"] for p in work["parts"]] == ["ok", "ok", "ok"]

        # A use the deck skill does not have is refused, and nothing is changed.
        refused = client.patch(url, headers=headers, json={"spec": {"use": "forever"}})
        assert refused.status_code == 400 and "not a use" in refused.json()["detail"]

        # A new audience changes the approach and every slide.
        client.patch(url, headers=headers, json={"audience": "first-year students"})
        (work,) = client.get(conversations(base_id, "/stale"), headers=headers).json()["works"]
        assert work["approach"]["why"] == "the audience changed"
        assert [p["why"] for p in work["parts"]] == ["the audience changed"] * 3


def test_a_stored_workspace_is_read_back_exactly(tmp_path: Path) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        headers = workspace(client)
        deck = make(client, fake, base_id, deck_replies(), kind="slides", brief="Slides on markers")
        client.post(
            conversations(base_id, "/import"),
            headers=headers,
            json={"conversations": [device_conversation(deck["id"])]},
        )
        with client.app.state.knowledge_service.sessions() as session:
            row = session.get(ArtifactConversation, "device-1")
            state, brief = row.state_json, row.brief_json
        # What is stored reads back as the workspace, and writes out the same.
        assert dump_workspace(load_workspace(state)) == state
        assert len(load_workspace(state).works) == 1
        assert load_workspace(state).pool and load_workspace(state).log
        assert brief.startswith("{") and '"audience"' in brief


def test_odd_device_data_is_brought_in_as_far_as_it_can_be(tmp_path: Path) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        headers = workspace(client)
        odd = {
            "conversations": [
                {
                    "id": "odd",
                    "title": "  ",
                    # A selection with nothing in it cannot be read: the library stands in.
                    "scope": {"mode": "selection", "sourceIds": []},
                    "createdAt": "not a time",
                    "turns": [
                        # A version that is not in this library leaves no link.
                        {"id": "same", "instruction": "First ask", "status": "saved",
                         "artifactId": "art_gone"},
                        {"id": "same", "instruction": "Second ask", "status": "interrupted"},
                    ],
                },
                {"id": "empty", "turns": []},
            ]
        }
        response = client.post(conversations(base_id, "/import"), headers=headers, json=odd)
        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()] == ["odd", "empty"]
        detail = client.get(conversations(base_id, "/odd"), headers=headers).json()
        assert detail["title"] == "First ask"
        assert detail["scope"]["mode"] == "library"
        assert detail["works"] == [] and detail["active"] is None
        assert [
            (m["role"], m["kind"], m["artifactId"], m["clientRequestId"])
            for m in detail["messages"]
        ] == [
            ("user", "message", None, "same"),
            ("assistant", "result", None, None),
            ("user", "message", None, None),  # the second use of an id is not kept
            ("assistant", "stopped", None, None),
        ]
        order = [m["createdAt"] for m in detail["messages"]]
        assert order == sorted(order) and len(set(order)) == 4
        assert client.get(conversations(base_id, "/empty"), headers=headers).json()["title"] == (
            "New conversation"
        )
        # The import is refused whole when it is not well formed.
        assert client.post(
            conversations(base_id, "/import"),
            headers=headers,
            json={"conversations": [{"id": "x", "turns": [{"instruction": "a", "status": "?"}]}]},
        ).status_code == 422


def test_the_lineage_worked_on_last_is_the_active_work(tmp_path: Path) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        headers = workspace(client)
        deck = make(client, fake, base_id, deck_replies(), kind="slides", brief="Slides")
        report = make(client, fake, base_id, report_replies(), brief="A report")
        turns = [
            {"instruction": "A report", "status": "saved", "artifactId": report["id"]},
            {"instruction": "Slides", "status": "saved", "artifactId": deck["id"]},
        ]
        client.post(
            conversations(base_id, "/import"),
            headers=headers,
            json={"conversations": [{"id": "both", "turns": turns}]},
        )
        detail = client.get(conversations(base_id, "/both"), headers=headers).json()
        assert [work["kind"] for work in detail["works"]] == ["report", "slides"]
        assert detail["active"] == deck["lineageId"]
        # Both are in one pool: the second work's sources have numbers after the first's.
        with client.app.state.knowledge_service.sessions() as session:
            state = load_workspace(session.get(ArtifactConversation, "both").state_json)
        assert [item["ref"] for item in state.pool] == sorted(item["ref"] for item in state.pool)
        assert state.works[0].version_map == [1, 2]


# Conversations the versions name -----------------------------------------------------------------


def test_listing_gives_a_row_to_a_conversation_the_versions_name(tmp_path: Path) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        headers = workspace(client)

        def shortening():
            return skill_replies(
                APPROACH,
                OUTLINE,
                REPORT,
                gather=SEARCH,
                rewrite=lambda index, prompt: (
                    "## CD3D is the T-cell marker\n\nCD3D marks T cells [1]."
                ),
            )

        first = make(
            client, fake, base_id, shortening(), brief="A report on markers",
            conversationId="lost-1", conversationTitle="Lost one",
        )
        deck = make(
            client, fake, base_id, deck_replies(), kind="slides", brief="Slides on markers",
            conversationId="lost-1",
        )
        # The report is worked on again after the deck was made.
        fake.replies[:] = [shortening()]
        second = built(revise(client, base_id, first["id"], "Shorter", sectionIndex=1))
        alone = make(
            client, fake, base_id, report_replies(), brief="Another conversation",
            conversationId="lost-2",
        )
        nameless = make(client, fake, base_id, report_replies(), brief="No conversation at all")

        listed = client.get(conversations(base_id), headers=headers)
        assert listed.status_code == 200, listed.text
        found = {item["id"]: item for item in listed.json()}
        # The versions of a conversation name it; the nameless one belongs to none.
        assert set(found) == {"lost-1", "lost-2"}
        assert found["lost-1"]["title"] == "Lost one" and found["lost-1"]["artifactCount"] == 2
        assert found["lost-2"]["title"] == "Recovered conversation"
        assert found["lost-2"]["artifactCount"] == 1

        detail = client.get(conversations(base_id, "/lost-1"), headers=headers).json()
        assert [(m["role"], m["kind"], m["text"], m["artifactId"]) for m in detail["messages"]] == [
            ("user", "message", "A report on markers", None),
            ("assistant", "result", "", first["id"]),
            ("user", "message", "Slides on markers", None),
            ("assistant", "result", "", deck["id"]),
            ("user", "message", "Shorter", None),
            ("assistant", "result", "", second["id"]),
        ]
        # A deck and a report: one work each, at the newest version of its lineage, the one
        # worked on last at the end and active.
        assert [(w["kind"], w["versionId"]) for w in detail["works"]] == [
            ("slides", deck["id"]),
            ("report", second["id"]),
        ]
        assert detail["active"] == first["lineageId"] == second["lineageId"]
        assert all(p["state"] == "ok" for w in detail["works"] for p in w["parts"])
        assert detail["scope"]["mode"] == "library"
        assert alone["id"] and nameless["id"]

        # Listing again finds the rows there and adds nothing.
        again = client.get(conversations(base_id), headers=headers).json()
        assert {item["id"] for item in again} == {"lost-1", "lost-2"}
        with client.app.state.knowledge_service.sessions() as session:
            assert session.scalar(select(func.count()).select_from(ArtifactConversation)) == 2
            assert session.scalar(select(func.count()).select_from(ArtifactMessage)) == 8

        # An archived conversation keeps its row, so it is not made again.
        client.patch(conversations(base_id, "/lost-2"), headers=headers, json={"archived": True})
        assert [i["id"] for i in client.get(conversations(base_id), headers=headers).json()] == [
            "lost-1"
        ]
        with client.app.state.knowledge_service.sessions() as session:
            assert session.scalar(select(func.count()).select_from(ArtifactConversation)) == 2


def test_two_requests_for_a_missing_conversation_make_it_once(tmp_path: Path) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        make(client, fake, base_id, report_replies(), brief="A report", conversationId="twice")
        service = client.app.state.knowledge_service
        workspace_id = workspace(client)["X-Gunther-Workspace-Id"]

        def ask(_):
            return service.artifact_conversations(base_id, workspace_id)

        with ThreadPoolExecutor(max_workers=6) as pool:
            answers = list(pool.map(ask, range(6)))
        assert all([item.id for item in answer] == ["twice"] for answer in answers)
        with service.sessions() as session:
            assert session.scalar(select(func.count()).select_from(ArtifactConversation)) == 1


def test_a_conversation_that_exists_is_not_rebuilt_from_its_versions(tmp_path: Path) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        headers = workspace(client)
        make(
            client, fake, base_id, report_replies(), brief="A report", conversationId="kept",
            conversationTitle="From the version",
        )
        client.post(conversations(base_id), headers=headers, json={"id": "kept", "title": "Mine"})
        found = client.get(conversations(base_id), headers=headers).json()
        assert [(item["id"], item["title"], item["artifactCount"]) for item in found] == [
            ("kept", "Mine", 0)
        ]


# Leaving a library, and the tables ---------------------------------------------------------------


def test_deleting_a_library_forever_takes_its_conversations_and_messages(tmp_path: Path) -> None:
    fake, app = a_library(tmp_path)
    with app as client:
        base_id, _ = library(client)
        other_id, _ = library(client, "Other")
        headers = workspace(client)
        make(
            client, fake, base_id, report_replies(), brief="A report", conversationId="gone-soon"
        )
        client.get(conversations(base_id), headers=headers)  # recovers the conversation
        client.post(conversations(other_id), headers=headers, json={"id": "stays"})
        sessions = client.app.state.knowledge_service.sessions
        with session_scope(sessions) as session:
            assert session.scalar(select(func.count()).select_from(ArtifactMessage)) == 2

        trashed = client.post(f"/api/knowledge-bases/{base_id}/trash", headers=SIDECAR)
        assert trashed.status_code == 200
        deleted = client.delete(f"/api/trash/library/{base_id}", headers=SIDECAR)
        assert deleted.status_code == 200

        with session_scope(sessions) as session:
            ids = set(session.scalars(select(ArtifactConversation.id)))
            assert ids == {"stays"}
            assert session.scalar(select(func.count()).select_from(ArtifactMessage)) == 0


def make_engine(tmp_path: Path, name: str = "gunther.sqlite"):
    return create_database_engine(f"sqlite+pysqlite:///{tmp_path / name}")


def test_migration_27_creates_the_conversation_tables_on_a_new_database(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    try:
        history = run_migrations(engine, Base.metadata)
        assert (history[-1].version, history[-1].name) == (27, "artifact_conversations")
        assert LATEST_SCHEMA_VERSION == 27 and get_schema_version(engine) == 27
        inspector = inspect(engine)
        assert {c["name"] for c in inspector.get_columns("artifact_conversations")} == {
            "id", "workspace_id", "knowledge_base_id", "title", "scope_json", "brief_json",
            "state_json", "archived", "created_at", "updated_at",
        }
        assert {c["name"] for c in inspector.get_columns("artifact_messages")} == {
            "id", "conversation_id", "role", "kind", "text", "payload_json", "artifact_id",
            "client_request_id", "created_at",
        }
        assert {
            (key["constrained_columns"][0], key["referred_table"], key["options"].get("ondelete"))
            for key in inspector.get_foreign_keys("artifact_messages")
        } == {("conversation_id", "artifact_conversations", "CASCADE")}
        assert {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("artifact_messages")
        } == {("conversation_id", "client_request_id")}
        assert {
            index["name"] for index in inspector.get_indexes("artifact_conversations")
        } >= {
            "ix_artifact_conversations_workspace_id",
            "ix_artifact_conversations_knowledge_base_id",
            "ix_artifact_conversations_archived",
            "ix_artifact_conversations_updated_at",
        }
    finally:
        engine.dispose()


def test_migration_27_adds_the_tables_to_a_database_made_by_the_last_version(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path)
    try:
        run_migrations(engine, Base.metadata, MIGRATIONS[:26])
        with engine.begin() as connection:
            # Baseline adopts current metadata; remove what a v26 app never had.
            connection.exec_driver_sql("DROP TABLE artifact_messages")
            connection.exec_driver_sql("DROP TABLE artifact_conversations")
            connection.exec_driver_sql(
                "INSERT INTO knowledge_sessions (id, knowledge_base_id, title, summary, "
                "selected_source_ids_json, pinned, archived, created_at, updated_at) "
                "VALUES ('ses_old', 'kb', 'Kept', '', '[]', 0, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        assert get_schema_version(engine) == 26
        assert not inspect(engine).has_table("artifact_conversations")

        history = run_migrations(engine, Base.metadata)
        assert run_migrations(engine, Base.metadata) == history
        assert get_schema_version(engine) == 27
        inspector = inspect(engine)
        assert inspector.has_table("artifact_conversations")
        assert inspector.has_table("artifact_messages")
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT title FROM knowledge_sessions WHERE id = 'ses_old'"
            ).scalar_one() == "Kept"
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []

        # The tables work: a message belongs to its conversation and goes with it.
        sessions = create_session_factory(engine)
        with session_scope(sessions) as session:
            session.add(ArtifactConversation(id="c1", workspace_id="w", knowledge_base_id="kb"))
            session.flush()
            row = session.get(ArtifactConversation, "c1")
            assert (row.title, row.archived, row.brief_json, row.state_json) == (
                "New conversation",
                False,
                "{}",
                "{}",
            )
            assert '"mode":"library"' in row.scope_json
            for message_id, request_id in (("m1", "r1"), ("m2", None), ("m3", None)):
                session.add(
                    ArtifactMessage(
                        id=message_id,
                        conversation_id="c1",
                        role="user",
                        kind="message",
                        client_request_id=request_id,
                    )
                )
        with pytest.raises(IntegrityError), session_scope(sessions) as session:
            session.add(
                ArtifactMessage(
                    id="m4",
                    conversation_id="c1",
                    role="user",
                    kind="message",
                    client_request_id="r1",
                )
            )
            session.flush()
        with session_scope(sessions) as session:
            session.delete(session.get(ArtifactConversation, "c1"))
        with session_scope(sessions) as session:
            assert session.scalar(select(func.count()).select_from(ArtifactMessage)) == 0
    finally:
        engine.dispose()
