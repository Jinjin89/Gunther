"""The conversation brief: what the user wants, kept across a long conversation."""

import json
from pathlib import Path

from fake_models import FakeProvider, agent_replies, gateway, is_keeping_brief, is_planning
from test_agent import SIDECAR, app_for, ready_session

from gunther.brief import (
    Brief,
    BriefKeeper,
    SettledItem,
    Stored,
    dump,
    edited_paths,
    load,
    render,
)


def keep(
    reply, stored: Stored | None = None, refs=(1, 2), question: str = "What now?"
) -> tuple[Stored, FakeProvider]:
    fake = FakeProvider(reply)
    chosen = gateway(fake)
    updated = BriefKeeper(chosen).update(
        chosen.models[0], stored or Stored(), question, "An answer [1].", set(refs)
    )
    return updated, fake


def test_settled_needs_sources_or_the_users_agreement() -> None:
    reply = json.dumps(
        {
            "settled": [
                {"text": "Cool roofs cut heat", "by": "sources", "refs": [1, 9]},
                {"text": "Ghost source", "by": "sources", "refs": [9]},
                {"text": "No numbers at all", "by": "sources"},
                {"text": "Use the Harlow trial", "by": "you", "because": "“Yes, use  that one”"},
                {"text": "Said without a quote", "by": "you", "because": " "},
                {"text": "Words never said", "by": "you", "because": "go with Okafor"},
            ],
            "open": ["Cost?"],
        }
    )
    updated, _ = keep(reply, question="Good. yes, use that one for the council.")
    assert [(i.text, i.by, i.refs) for i in updated.brief.settled] == [
        ("Cool roofs cut heat", "sources", [1]),
        ("Use the Harlow trial", "you", []),
    ]
    assert updated.brief.open == [
        "Cost?",
        "Ghost source",
        "No numbers at all",
        "Said without a quote",
        "Words never said",
    ]
    assert updated.error is None


def test_items_the_user_edited_are_kept_as_written() -> None:
    stored = Stored(
        Brief(
            goal="Brief the mayor",
            constraints=["Under one page"],
            settled=[SettledItem(text="Roofs work", by="you", because="")],
            open=["Cost?"],
        ),
        edited=["goal", "constraints:Under one page", "settled:Roofs work"],
        through="msg_1",
    )
    reply = json.dumps(
        {
            "goal": "Write a long essay",
            "constraints": ["Cite every claim", "under one page"],
            "settled": [{"text": "roofs work", "by": "sources", "refs": [1]}],
            "open": ["Cost?", "Maintenance?"],
        }
    )
    updated, fake = keep(reply, stored)
    assert updated.brief.goal == "Brief the mayor"
    assert updated.brief.constraints == ["Under one page", "Cite every claim"]
    assert [(i.text, i.by) for i in updated.brief.settled] == [("Roofs work", "you")]
    assert updated.brief.open == ["Cost?", "Maintenance?"]
    assert updated.edited == stored.edited and updated.through == "msg_1"
    # The model is told which lines are the user's.
    prompt = str(fake.requests[0]["messages"][-1]["content"])
    assert "Goal: Brief the mayor (edited by the user)" in prompt
    assert "- Under one page (edited by the user)" in prompt


def test_a_failed_update_keeps_the_old_brief_and_says_so() -> None:
    stored = Stored(Brief(goal="Learn markers", open=["Which?"]), through="msg_1")
    updated, _ = keep("this is not a brief", stored)
    assert updated.brief == stored.brief and updated.through == "msg_1"
    assert updated.error


def test_the_brief_is_stored_and_rendered_for_a_model() -> None:
    stored = Stored(
        Brief(
            goal="Learn markers",
            constraints=["Short"],
            settled=[
                SettledItem(text="CD3D marks T cells", by="sources", refs=[1, 2]),
                SettledItem(text="Skip B cells", by="you", because="yes skip them"),
            ],
            open=["Which tissue?"],
        ),
        edited=["open:Which tissue?"],
        through="msg_2",
    )
    assert load(dump(stored)) == stored
    assert load("{}") == Stored() and load("nonsense") == Stored()
    assert render(stored.brief, stored.edited) == (
        "Goal: Learn markers\n"
        "Constraints:\n- Short\n"
        "Settled:\n- CD3D marks T cells [1][2]\n- Skip B cells (you agreed: “yes skip them”)\n"
        "Open:\n- Which tissue? (edited by the user)"
    )
    assert render(Brief()) == ""
    assert edited_paths(stored, stored.brief.model_copy(update={"goal": "Other"}))[0] == "goal"


def requests_of(fake: FakeProvider, kind) -> list[dict]:
    return [r for r in fake.requests if kind(r)]


def ask(client, session_id: str, text: str = "CD3D?") -> dict:
    return client.post(
        f"/api/sessions/{session_id}/messages", headers=SIDECAR, json={"content": text}
    ).json()


def brief_of(client, session_id: str) -> dict:
    return client.get(f"/api/sessions/{session_id}", headers=SIDECAR).json()["brief"]


def test_a_brief_behind_by_one_answer_is_caught_up_before_the_next_question(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
            brief={"goal": "Learn markers", "open": ["Which tissue?"]},
        )
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        first = ask(client, session_id)
        assert brief_of(client, session_id)["goal"] == ""  # the page has not asked yet
        assert requests_of(fake, is_keeping_brief) == []
        ask(client, session_id, "And in the lung?")
        keepers = requests_of(fake, is_keeping_brief)
        assert len(keepers) == 1
        last_planning = max(i for i, r in enumerate(fake.requests) if is_planning(r))
        assert fake.requests.index(keepers[0]) < last_planning
        # It read the first exchange, with the answer's sources in the conversation's numbers.
        prompt = str(keepers[0]["messages"][-1]["content"])
        assert "Latest message:\nCD3D?" in prompt and "CD3D marks T cells [1]." in prompt
        saved = brief_of(client, session_id)
    assert saved["goal"] == "Learn markers" and saved["open"] == ["Which tissue?"]
    assert first["assistantMessage"]["id"]


def test_refresh_folds_in_the_last_answer_once_and_a_failure_can_be_retried(
    tmp_path: Path,
) -> None:
    replies = iter(["not json", "not json", {"goal": "Learn markers"}])

    def keeper(_request):
        return next(replies)

    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
            brief=keeper,
        )
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        ask(client, session_id)
        url = f"/api/sessions/{session_id}/brief/refresh"
        failed = client.post(url, headers=SIDECAR).json()
        assert failed["error"] and failed["goal"] == ""
        assert brief_of(client, session_id)["error"]
        done = client.post(url, headers=SIDECAR).json()
        assert done["goal"] == "Learn markers" and done["error"] is None
        calls = len(requests_of(fake, is_keeping_brief))
        assert client.post(url, headers=SIDECAR).json()["goal"] == "Learn markers"
        assert len(requests_of(fake, is_keeping_brief)) == calls
        assert client.post("/api/sessions/nope/brief/refresh", headers=SIDECAR).status_code == 404


def test_the_brief_reaches_the_planner_and_writer(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
            brief={"goal": "Brief the mayor", "constraints": ["Under one page"]},
        )
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        ask(client, session_id)
        client.post(f"/api/sessions/{session_id}/brief/refresh", headers=SIDECAR)
        fake.requests.clear()
        ask(client, session_id, "And the lung?")
    planning = str(requests_of(fake, is_planning)[0]["messages"][-1]["content"])
    heading = "Conversation brief (what the user wants; follow its constraints):"
    assert planning.startswith(f"{heading}\nGoal: Brief the mayor\nConstraints:\n- Under one page")
    writing = [
        str(r["messages"][-1]["content"])
        for r in fake.requests
        if "Latest message:\nAnd the lung?" in str(r["messages"][-1]["content"])
        and not is_planning(r)
        and "The pool" in str(r["messages"][-1]["content"])
    ]
    assert writing and writing[0].startswith(heading)


def test_editing_the_brief_marks_the_changed_lines_as_the_users(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
            brief={"goal": "Learn markers", "constraints": ["Short"], "open": ["Which?"]},
        )
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        ask(client, session_id)
        client.post(f"/api/sessions/{session_id}/brief/refresh", headers=SIDECAR)
        edited = client.patch(
            f"/api/sessions/{session_id}/brief",
            headers=SIDECAR,
            json={"goal": "Learn markers", "constraints": ["Short", "For a mayor"], "open": []},
        ).json()
        assert edited["constraints"] == ["Short", "For a mayor"] and edited["open"] == []
        assert edited["edited"] == ["constraints:For a mayor"]
        # Editing does not count as folding in an answer: the next question is not behind.
        ask(client, session_id, "Next?")
        assert len(requests_of(fake, is_keeping_brief)) == 1
        assert client.patch("/api/sessions/nope/brief", headers=SIDECAR, json={}).status_code == 404


def test_a_full_open_list_does_not_break_the_update() -> None:
    # Found by the exam: eight open questions plus a conclusion moved to "open" made nine,
    # and the update raised instead of keeping the brief.
    reply = json.dumps(
        {
            "settled": [{"text": "Unsupported conclusion", "by": "sources", "refs": [9]}],
            "open": [f"Question {n}?" for n in range(1, 9)],
        }
    )
    updated, _ = keep(reply)
    assert updated.error is None
    assert updated.brief.open == [f"Question {n}?" for n in range(1, 9)]
