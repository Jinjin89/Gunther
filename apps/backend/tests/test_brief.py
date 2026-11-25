"""The conversation brief: what the user wants, kept across a long conversation."""

import json
from dataclasses import replace
from pathlib import Path

from fake_models import FakeProvider, agent_replies, gateway, is_keeping_brief, is_planning
from test_agent import SIDECAR, app_for, ready_session

from evals.sse import ask_turn
from gunther.brief import (
    Brief,
    BriefKeeper,
    SettledItem,
    Stored,
    diff_brief,
    dump,
    edited_paths,
    load,
    render,
)
from gunther.models import KnowledgeSession


def keep(
    reply, stored: Stored | None = None, refs=(1, 2), question: str = "What now?",
    answered_id: str = "msg_2",
) -> tuple[Stored, FakeProvider]:
    fake = FakeProvider(reply)
    chosen = gateway(fake)
    updated = BriefKeeper(chosen).update(
        chosen.models[0], stored or Stored(), question, "An answer [1].", set(refs),
        answered_id=answered_id,
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
            settled=[SettledItem(text="Roofs work", by="you", message_id="msg_1")],
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
    assert [(i.text, i.by, i.message_id) for i in updated.brief.settled] == [
        ("Roofs work", "you", "msg_1")
    ]
    assert updated.brief.open == ["Cost?", "Maintenance?"]
    assert updated.edited == stored.edited and updated.through == "msg_2"
    # The model is told which lines are the user's.
    prompt = str(fake.requests[0]["messages"][-1]["content"])
    assert "Goal: Brief the mayor (edited by the user)" in prompt
    assert "- Under one page (edited by the user)" in prompt


def test_a_failed_update_keeps_the_old_brief_and_says_so() -> None:
    before = Brief(goal="Learn markers", open=["Which?"])
    stored = Stored(before, through="msg_1", last_change=diff_brief(Brief(), before, "msg_1"))
    updated, _ = keep("this is not a brief", stored)
    assert updated.error
    assert replace(updated, error=None) == stored


def test_keeper_records_changes_from_the_merged_brief() -> None:
    before = Brief(
        goal="Explain the measurements",
        constraints=["Use metric units", "Limit to one page"],
        settled=[
            SettledItem(text="Cooling was measured", by="sources", refs=[1], message_id="msg_1")
        ],
        open=["What is the cost?", "When was it measured?"],
    )
    reply = json.dumps({
        "goal": "Explain the pilot's measurements",
        "constraints": ["use METRIC  units", "Include uncertainty", "State the sample size"],
        "settled": [
            {"text": "cooling was  measured", "by": "sources", "refs": [1]},
            {"text": "Measurements began in June", "by": "sources", "refs": [2],
             "message_id": "not-from-the-answer"},
            {"text": "Costs are known", "by": "sources", "refs": [9]},
        ],
        "open": ["What is the  cost?", "How long did the trial run?"],
    })
    updated, fake = keep(reply, Stored(before, through="msg_1"))
    assert updated.through == "msg_2"
    assert [item.message_id for item in updated.brief.settled] == ["msg_1", "msg_2"]
    assert updated.last_change is not None
    assert updated.last_change.model_dump() == {
        "message_id": "msg_2",
        "goal_changed": True,
        "added": {
            "constraints": ["Include uncertainty", "State the sample size"],
            "settled": ["Measurements began in June"],
            "open": ["How long did the trial run?", "Costs are known"],
        },
        "removed": {"constraints": ["Limit to one page"], "open": ["When was it measured?"]},
    }
    system = str(fake.requests[0]["messages"][0]["content"])
    schema = json.loads(system.split("JSON schema:\n", 1)[1])
    assert schema["$defs"]["SettledItem"]["properties"].keys() == {"text", "refs", "by", "because"}
    assert load(dump(updated)) == updated

    unchanged, _ = keep(reply, updated, answered_id="msg_3")
    assert unchanged.through == "msg_3" and unchanged.last_change is None
    assert unchanged.brief == updated.brief
    same_goal = before.model_copy(update={"goal": "Explain the  MEASUREMENTS"})
    assert diff_brief(before, same_goal, "msg_2") is None


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
    old_json = json.dumps({
        "brief": stored.brief.model_dump(exclude={"settled": {"__all__": {"message_id"}}}),
        "edited": stored.edited,
        "through": "msg_2",
    })
    assert load(old_json) == stored
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
    return ask_turn(client, session_id, {"content": text}, headers=SIDECAR)


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
    assert saved["throughMessageId"] == first["assistantMessage"]["id"]


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
        answer_id = ask(client, session_id)["assistantMessage"]["id"]
        url = f"/api/sessions/{session_id}/brief/refresh"
        failed = client.post(url, headers=SIDECAR).json()
        assert failed["error"] and failed["goal"] == ""
        assert failed["throughMessageId"] is None and failed["lastChange"] is None
        assert brief_of(client, session_id)["error"]
        done = client.post(url, headers=SIDECAR).json()
        assert done["goal"] == "Learn markers" and done["error"] is None
        assert done["throughMessageId"] == answer_id
        assert done["lastChange"] == {
            "messageId": answer_id,
            "goalChanged": True,
            "added": {"constraints": [], "settled": [], "open": []},
            "removed": {"constraints": [], "open": []},
        }
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


def test_brief_patch_schema_declares_optional_message_id(tmp_path: Path) -> None:
    with app_for(tmp_path, FakeProvider()) as client:
        document = client.app.openapi()
    models = document["components"]["schemas"]
    patch = document["paths"]["/api/sessions/{session_id}/brief"]["patch"]
    request = patch["requestBody"]["content"]["application/json"]["schema"]
    brief_schema = models[request["$ref"].rsplit("/", 1)[-1]]
    item = brief_schema["properties"]["settled"]["items"]
    settled_schema = models[item["$ref"].rsplit("/", 1)[-1]]
    assert settled_schema["properties"]["messageId"]["anyOf"] == [
        {"type": "string"}, {"type": "null"},
    ]
    assert "messageId" not in settled_schema["required"]


def test_editing_only_constraints_keeps_two_lines_from_one_answer(tmp_path: Path) -> None:
    settled = [
        {"text": "Keep the comparison", "by": "you", "because": "Keep the comparison"},
        {"text": "Give the sample size", "by": "you", "because": "Give the sample size"},
    ]
    fake = FakeProvider(agent_replies("I'll include both.", brief={"settled": settled}))
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        url = f"/api/sessions/{session_id}/brief"
        turn = ask(client, session_id, "Keep the comparison. Give the sample size.")
        answer_id = turn["assistantMessage"]["id"]
        before = client.post(f"{url}/refresh", headers=SIDECAR).raise_for_status().json()
        assert [item["messageId"] for item in before["settled"]] == [answer_id, answer_id]

        edited = client.patch(
            url,
            headers=SIDECAR,
            json={"constraints": ["Use plain language"], "settled": before["settled"]},
        ).raise_for_status().json()
        assert edited["settled"] == before["settled"]
        assert edited["edited"] == ["constraints:Use plain language"]
        assert brief_of(client, session_id) == edited


def test_editing_the_brief_marks_the_changed_lines_as_the_users(tmp_path: Path) -> None:
    lead = {"text": "Lead with the assay", "by": "you", "because": "Lead with the assay"}
    panel = {"text": "Use the tissue panel", "by": "you", "because": "Use the tissue panel"}
    brief = {"goal": "Learn markers", "constraints": ["Short"], "open": ["Which?"]}
    keeper_replies = iter([{**brief, "settled": [lead]}, {**brief, "settled": [lead, panel]}])
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
            brief=lambda _: next(keeper_replies),
        )
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        url = f"/api/sessions/{session_id}/brief"
        first_id = ask(client, session_id, "Lead with the assay.")["assistantMessage"]["id"]
        client.post(f"{url}/refresh", headers=SIDECAR).raise_for_status()
        second_id = ask(client, session_id, "Lead with the assay. Use the tissue panel.")[
            "assistantMessage"
        ]["id"]
        before = client.post(f"{url}/refresh", headers=SIDECAR).raise_for_status().json()
        saved_lead, saved_panel = before["settled"]
        assert first_id != second_id
        assert [saved_lead["messageId"], saved_panel["messageId"]] == [first_id, second_id]
        edited = client.patch(
            url,
            headers=SIDECAR,
            json={
                "goal": "Learn markers", "constraints": ["Short", "For a mayor"], "open": [],
                "settled": [
                    {**saved_lead, "text": "Use the tissue\npanel"},
                    saved_panel,
                    {**saved_lead, "messageId": "foreign-message"},
                    {"text": "Compare each sample", "by": "you"},
                ],
            },
        ).raise_for_status().json()
        assert edited["constraints"] == ["Short", "For a mayor"] and edited["open"] == []
        assert [(item["text"], item["messageId"]) for item in edited["settled"]] == [
            ("Use the tissue panel", first_id),
            ("Use the tissue panel", second_id),
            ("Lead with the assay", None),
            ("Compare each sample", None),
        ]
        assert edited["edited"] == [
            "constraints:For a mayor",
            "settled:Use the tissue panel",
            "settled:Lead with the assay",
            "settled:Compare each sample",
        ]
        assert edited["throughMessageId"] == second_id
        assert edited["lastChange"] == before["lastChange"]
        assert brief_of(client, session_id) == edited
        # Editing does not count as folding in an answer: the next question is not behind.
        ask(client, session_id, "Next?")
        assert len(requests_of(fake, is_keeping_brief)) == 2
        assert client.patch("/api/sessions/nope/brief", headers=SIDECAR, json={}).status_code == 404


def test_a_stale_refresh_cannot_replace_the_cursor_after_an_unchanged_fold(tmp_path: Path) -> None:
    fake = FakeProvider(agent_replies("No sources are needed.", brief={"goal": "Understand this"}))
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        url = f"/api/sessions/{session_id}/brief/refresh"
        ask(client, session_id)
        client.post(url, headers=SIDECAR).raise_for_status()
        service = client.app.state.knowledge_service
        with service.sessions() as session:
            snapshot = load(session.get(KnowledgeSession, session_id).brief_json)

        answer_id = ask(client, session_id, "More?")["assistantMessage"]["id"]
        saved = client.post(url, headers=SIDECAR).raise_for_status().json()
        assert saved["throughMessageId"] == answer_id and saved["lastChange"] is None

        stale = replace(snapshot, error="Couldn't reach the model")
        returned = service._save_brief(session_id, stale, expect=snapshot)
        assert returned.model_dump(by_alias=True) == saved
        assert brief_of(client, session_id) == saved


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
