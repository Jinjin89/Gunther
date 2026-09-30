"""Ask's agent: tools, the source pool, cited answers, and claims from the model's own knowledge."""

import asyncio
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
from fake_models import (
    FakeProvider,
    agent_replies,
    api_error,
    gateway,
    is_auditing,
    is_checking,
    is_grading,
    is_planning,
)
from fastapi.testclient import TestClient

from gunther import online_search
from gunther.agent import (
    AskAgent,
    Evidence,
    Tool,
    Toolbox,
    ToolFailure,
    check_citations,
    leads_in,
    writer_prompt,
)
from gunther.answer_runs import AnswerRun
from gunther.config import Settings
from gunther.llm import Turn
from gunther.main import create_app
from gunther.service import conversation_history

SIDECAR_TOKEN = "sidecar-token-with-at-least-256-bits-000000000000000000000000"
SIDECAR = {"X-Gunther-Token": SIDECAR_TOKEN}


def passage(title: str, text: str, ref: int | None = None) -> Evidence:
    return Evidence(kind="library", title=title, text=text, locator="line 1", ref=ref)


def page(title: str, url: str, text: str) -> Evidence:
    return Evidence(kind="web", title=title, text=text, url=url, locator="example.org")


def lib(search) -> Tool:
    return Tool(
        "search_library", "the user's library, 3 sources", search, "your library", grade=True
    )


def web(search) -> Tool:
    return Tool("search_web", "the public web", search, "the web")


def box(*tools: Tool, notes: tuple[str, ...] = ()) -> Toolbox:
    return Toolbox(tuple(tools), notes)


def run(
    fake: FakeProvider,
    toolbox: Toolbox,
    question: str = "What marks T cells?",
    history=(),
    **kwargs,
):
    chosen = gateway(fake)
    model = chosen.models[0]
    return AskAgent(chosen).run(question, list(history), toolbox, model, "high", **kwargs)


def writer_prompts(fake: FakeProvider) -> list[str]:
    return [
        str(r["messages"][-1]["content"])
        for r in fake.requests
        if not is_planning(r) and not is_grading(r) and not is_checking(r) and not is_auditing(r)
    ]


def test_citations_are_kept_only_when_they_exist_and_keep_their_numbers() -> None:
    text, used = check_citations("A [3] and B [1, 3]; C [9] too.", {1, 3, 4})
    assert text == "A [3] and B [1][3]; C too."
    assert used == [3, 1]
    assert check_citations("No citations here.", {1}) == ("No citations here.", [])
    assert check_citations("Own knowledge [?].", {1}) == ("Own knowledge [?].", [])


def test_leads_are_the_claims_marked_as_the_models_own() -> None:
    text = "Sourced fact [1]. Water boils at 100 C [?]. 另一个说法[?]。Third [?]."
    assert [claim for _, _, claim in leads_in(text, 2)] == [
        "Water boils at 100 C",
        "另一个说法",
    ]
    assert leads_in("Nothing marked [1].", 3) == []


def test_small_talk_needs_no_search_and_no_sources() -> None:
    fake = FakeProvider(agent_replies("Hi! What shall we look into?", {"action": "answer"}))
    result = run(fake, box(lib(lambda q: [passage("A", "x")])), "hi")
    assert result.content == "Hi! What shall we look into?"
    assert result.evidence == [] and result.steps == []
    assert len(writer_prompts(fake)) == 1


def test_library_question_searches_with_a_rewritten_query_then_answers_with_citations() -> None:
    searched: list[str] = []

    def search_library(query: str) -> list[Evidence]:
        searched.append(query)
        return [
            passage("Cell note", "CD3D is a marker of T cells."),
            passage("Other", "Unrelated."),
        ]

    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1]. Nothing else [7].",
            {"action": "search_library", "query": "T cell marker CD3D"},
            {"action": "answer"},
        )
    )
    history = [Turn("user", "Tell me about CD3D"), Turn("assistant", "It is a gene.")]
    result = run(fake, box(lib(search_library)), "and T cells?", history)
    assert searched == ["T cell marker CD3D"]
    assert result.content == "CD3D marks T cells [1]. Nothing else."
    assert [(item.title, item.ref) for item in result.evidence] == [("Cell note", 1)]
    assert result.steps[0].found == 2
    assert "[1] (library · Cell note · line 1)" in writer_prompts(fake)[0]
    # The planner saw the conversation, to resolve "and T cells?".
    planning = next(r for r in fake.requests if is_planning(r))
    assert any(m["content"] == "Tell me about CD3D" for m in planning["messages"])


def test_when_nothing_is_found_the_writer_is_still_asked_to_help() -> None:
    fake = FakeProvider(
        agent_replies(
            "I looked for that in your library and found nothing.",
            {"action": "search_library", "query": "quantum gravity"},
            {"action": "answer"},
        )
    )
    result = run(
        fake,
        box(lib(lambda q: []), notes=("the web was not searched: the Web toggle is off",)),
    )
    assert result.content.startswith("I looked for that")
    prompt = writer_prompts(fake)[0]
    assert "none of the searches found anything relevant" in prompt
    assert "the web was not searched: the Web toggle is off" in prompt


def test_tool_failures_are_reported_and_only_offered_tools_are_listed() -> None:
    def broken_web(query: str) -> list[Evidence]:
        raise ToolFailure("Tavily did not accept the API key.")

    fake = FakeProvider(
        agent_replies(
            "I could not reach the web.",
            {"action": "search_web", "query": "latest release"},
            {"action": "answer"},
        )
    )
    result = run(fake, box(web(broken_web)))
    planning = next(r for r in fake.requests if is_planning(r))
    assert "search_web: the public web" in str(planning["messages"][-1]["content"])
    assert result.steps[0].error == "Tavily did not accept the API key."
    assert "failed: Tavily did not accept the API key." in writer_prompts(fake)[0]

    fake = FakeProvider(agent_replies("ok", {"action": "answer"}))
    run(fake, box(lib(lambda q: [])))
    prompt = str(next(r for r in fake.requests if is_planning(r))["messages"][-1]["content"])
    assert "search_web" not in prompt


def test_a_search_is_never_repeated_and_the_loop_stops() -> None:
    calls: list[str] = []

    def search_library(query: str) -> list[Evidence]:
        calls.append(query)
        return []

    same = {"action": "search_library", "query": "same words"}
    fake = FakeProvider(agent_replies("Answer.", same))
    result = run(fake, box(lib(search_library)))
    assert calls == ["same words"]
    assert result.content == "Answer."


def test_an_unreadable_plan_falls_back_to_searching_the_first_tool_as_asked() -> None:
    calls: list[str] = []

    def replies(request):
        return "not json at all" if is_planning(request) else "Answer from the note [1]."

    def search_library(query: str) -> list[Evidence]:
        calls.append(query)
        return [passage("Cell note", "CD3D is a marker of T cells.")]

    result = run(FakeProvider(replies), box(lib(search_library)), "What marks T cells?")
    assert calls == ["What marks T cells?"]
    assert result.content == "Answer from the note [1]."


def test_a_model_that_cannot_answer_returns_its_error_not_an_invented_reply() -> None:
    fake = FakeProvider(api_error(openai.RateLimitError, 429, "Insufficient Balance"))
    result = run(fake, box(lib(lambda q: [passage("A", "x")])))
    assert (
        result.error
        == "DeepSeek is busy or out of credit (Insufficient Balance). Try again shortly."
    )
    assert result.content == ""


def test_the_pool_keeps_numbers_and_a_search_only_adds_what_is_new() -> None:
    site = Evidence(kind="web", title="Site", text="x", url="https://a.org", ref=5)
    held = [passage("Cell note", "CD3D marks T cells.", ref=4), site]
    searched: list[str] = []

    def search_library(query: str) -> list[Evidence]:
        searched.append(query)
        return [passage("Cell note", "CD3D marks T cells."), passage("Lung note", "AT2 cells.")]

    fake = FakeProvider(
        agent_replies(
            "CD3D [4] and AT2 [6]; the site [5].",
            {"action": "search_library", "query": "lung"},
            {"action": "answer"},
        )
    )
    result = run(fake, box(lib(search_library)), "and the lung?", pool=held)
    # The known passage keeps [4]; only the lung note is new and follows the highest number.
    # Hidden ids stay 4, 6, 5; the answer shows them as 1, 2, 3 in the order it cites them.
    assert [item.ref for item in result.evidence] == [4, 6, 5]
    assert result.content == "CD3D [1] and AT2 [2]; the site [3]."
    assert result.steps[0].found == 2
    prompt = writer_prompts(fake)[0]
    assert "[4] (library · Cell note" in prompt and "[6] (library · Lung note" in prompt
    planning = next(r for r in fake.requests if is_planning(r))
    assert "[5] web · Site" in str(planning["messages"][-1]["content"])


def test_a_follow_up_can_answer_from_the_pool_without_searching() -> None:
    held = [passage("Cell note", "CD3D marks T cells.", ref=2)]
    calls: list[str] = []
    fake = FakeProvider(agent_replies("In short: CD3D [2].", {"action": "answer"}))
    result = run(fake, box(lib(lambda q: calls.append(q) or [])), "shorter", pool=held)
    assert calls == [] and result.steps == []
    assert [item.ref for item in result.evidence] == [2]


def claim_run(verdict: dict, found: list[Evidence], answer: str):
    fake = FakeProvider(agent_replies(answer, {"action": "answer"}, verdict=verdict))
    result = run(fake, box(web(lambda q: list(found))), "Tell me")
    return fake, result


def test_a_claim_from_the_models_own_knowledge_gets_a_source_when_one_is_found() -> None:
    fake, result = claim_run(
        {"supports": [1], "contradicts": []},
        [page("Physics", "https://a.org/p", "Water boils at 100 C at sea level.")],
        "Water boils at 100 C [?]. It is a common fact.",
    )
    assert result.content == "Water boils at 100 C [1]. It is a common fact."
    assert [(item.ref, item.kind) for item in result.evidence] == [(1, "web")]
    assert any("Checked the web" in step.label for step in result.steps)
    assert sum(is_checking(r) for r in fake.requests) == 1


def test_a_claim_with_no_source_stays_marked_and_one_that_sources_contradict_is_left_out() -> None:
    _, result = claim_run(
        {"supports": [], "contradicts": []},
        [page("Other", "https://a.org/o", "Unrelated.")],
        "It is likely so [?].",
    )
    assert result.content == "It is likely so [?]." and result.evidence == []

    _, result = claim_run(
        {"supports": [], "contradicts": [1]},
        [page("Physics", "https://a.org/p", "Water boils at 90 C on this mountain.")],
        "Intro [?]. Water boils at 100 C here [?]. Closing.",
    )
    assert "100 C" not in result.content and "Closing." in result.content
    assert any("sources disagree" in note for note in result.notes)
    # Only cited sources are listed, and none cites a claim that was left out.
    assert result.evidence == []


def test_a_claim_left_with_no_source_and_no_marker_is_found_and_checked() -> None:
    answer = "The marker is CD3D [1]. Water boils at 100 C. It was named in 1990."
    fake = FakeProvider(
        agent_replies(
            answer,
            {"action": "search_library", "query": "cd3d"},
            {"action": "answer"},
            unmarked=["Water boils at 100 C.", "It was named in 1990."],
            verdict={"supports": [], "contradicts": []},
        )
    )
    result = run(fake, box(lib(lambda q: [passage("Cell note", "CD3D marks T cells.")])))
    assert (
        result.content
        == "The marker is CD3D [1]. Water boils at 100 C [?]. It was named in 1990 [?]."
    )
    assert sum(is_checking(r) for r in fake.requests) == 2
    # A claim the audit names that is already marked, or short small talk, is left alone.
    fake = FakeProvider(agent_replies("Hi!", {"action": "answer"}, unmarked=["Hi!"]))
    assert run(fake, box(lib(lambda q: []))).content == "Hi!"
    assert sum(is_auditing(r) for r in fake.requests) == 0


def test_at_most_a_few_claims_are_checked_and_style_never_overrides_the_rules() -> None:
    fake, result = claim_run(
        {"supports": [], "contradicts": []},
        [page("X", "https://a.org/x", "x")],
        "A [?]. B [?]. C [?]. D [?].",
    )
    assert sum(is_checking(r) for r in fake.requests) == 3
    assert result.content == "A [?]. B [?]. C [?]. D [?]."
    concise = writer_prompt("concise")
    assert "As short as the question allows" in concise and "the rules win" in concise
    assert writer_prompt("nonsense") == writer_prompt("balanced")
    assert "[?]" in writer_prompt(None) and "right after it" in writer_prompt(None)


# Through the API ----------------------------------------------------------------------


def app_for(tmp_path: Path, fake: FakeProvider, **overrides) -> TestClient:
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


def ready_session(client: TestClient) -> str:
    base_id = client.post(
        "/api/knowledge-bases",
        headers=SIDECAR,
        json={"title": "Cells", "question": "What marks cells?", "description": "Markers."},
    ).json()["id"]
    client.post(
        "/api/sources",
        headers=SIDECAR,
        json={
            "title": "Markers",
            "kind": "note",
            "knowledgeBaseId": base_id,
            "content": "CD3D is a marker of T cells.",
        },
    )
    return client.post(f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}).json()[
        "id"
    ]


def test_web_results_become_citations_and_the_steps_are_kept(tmp_path: Path, monkeypatch) -> None:
    def fake_post(self, body):
        assert body["query"] == "CD3D latest research"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "CD3D review",
                        "url": "https://www.example.org/cd3d",
                        "content": "A 2026 review of CD3D in T cells.",
                    }
                ]
            },
        )

    monkeypatch.setattr(online_search.TavilySearch, "_post", fake_post)
    fake = FakeProvider(
        agent_replies(
            "CD3D is covered by a recent review [1].",
            {
                "action": "search_web",
                "query": "CD3D latest research",
            },
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake, tavily_api_key="tvly-test-0000000000001234") as client:
        session_id = ready_session(client)
        reply = client.post(
            f"/api/sessions/{session_id}/messages",
            headers=SIDECAR,
            json={"content": "What is new about CD3D?", "web": True},
        ).json()["assistantMessage"]
    assert reply["content"] == "CD3D is covered by a recent review [1]."
    [citation] = reply["citations"]
    assert citation["kind"] == "web" and citation["url"] == "https://www.example.org/cd3d"
    assert citation["locator"] == "example.org" and citation["sourceId"] == ""
    context = reply["context"]
    assert context["webSearched"] is True
    assert context["steps"][0]["tool"] == "search_web" and context["steps"][0]["found"] == 1
    assert context["responderMode"] == "model"


def test_the_web_stays_off_unless_the_question_asks_for_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        online_search.TavilySearch,
        "_post",
        lambda self, body: (_ for _ in ()).throw(AssertionError),
    )
    fake = FakeProvider(
        agent_replies(
            "From your notes [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake, tavily_api_key="tvly-test-0000000000001234") as client:
        session_id = ready_session(client)
        reply = client.post(
            f"/api/sessions/{session_id}/messages",
            headers=SIDECAR,
            json={"content": "CD3D?"},
        ).json()["assistantMessage"]
    planning = next(r for r in fake.requests if is_planning(r))
    assert "the web was not searched" in str(planning["messages"][-1]["content"])
    assert reply["context"]["webSearched"] is False
    assert reply["citations"][0]["kind"] == "library"


def test_without_a_model_the_reply_says_a_model_is_needed(tmp_path: Path) -> None:
    fake = FakeProvider("unused")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        deepseek_api_key=None,
        stt_provider="compatible",
        processing_worker_enabled=False,
        auth_token=SIDECAR_TOKEN,
    )
    with TestClient(create_app(settings, model_client_factory=fake.factory)) as client:
        session_id = ready_session(client)
        reply = client.post(
            f"/api/sessions/{session_id}/messages", headers=SIDECAR, json={"content": "CD3D?"}
        ).json()["assistantMessage"]
    assert "Settings → Models" in reply["context"]["modelError"]
    assert fake.requests == []
    assert json.dumps(reply)  # serialisable


# Streaming and Home --------------------------------------------------------------------


def read_events(response) -> list[tuple[str, dict]]:
    events, name = [], ""
    for line in response.iter_lines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            events.append((name, json.loads(line[6:])))
    return events


def test_an_answer_can_be_streamed_as_it_is_worked_out(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        with client.stream(
            "POST",
            f"/api/sessions/{session_id}/messages/stream",
            headers=SIDECAR,
            json={"content": "CD3D?"},
        ) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            events = read_events(response)
        saved = client.get(f"/api/sessions/{session_id}", headers=SIDECAR).json()
    names = [name for name, _ in events]
    assert names[-1] == "done" and "intent" not in names
    steps = [data for name, data in events if name == "step"]
    assert [s["state"] for s in steps] == ["running", "done"] and steps[1]["found"] == 1
    assert "".join(data["text"] for name, data in events if name == "text") == (
        "CD3D marks T cells [1]."
    )
    turn = events[-1][1]
    assert turn["assistantMessage"]["content"] == "CD3D marks T cells [1]."
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]


def test_a_streamed_question_that_cannot_be_answered_says_why(tmp_path: Path) -> None:
    fake = FakeProvider("unused")
    with (
        app_for(tmp_path, fake) as client,
        client.stream(
            "POST",
            "/api/sessions/ses_missing/messages/stream",
            headers=SIDECAR,
            json={"content": "hello"},
        ) as response,
    ):
        events = read_events(response)
    assert events == [("error", {"status": 404, "detail": "Session ses_missing was not found"})]


def held_answers(client: TestClient, gate: threading.Event):
    """The service's answering, made to pause after "Part one" until ``gate`` opens."""

    service = client.app.state.knowledge_service
    real = service.create_session_turn

    def answer(session_id, payload, hear):
        hear({"type": "step", "state": "running", "tool": "search_library", "label": "Library"})
        hear({"type": "text", "text": "Part "})
        hear({"type": "text", "text": "one "})
        assert gate.wait(10)
        hear({"type": "text", "text": "part two."})
        # The real turn is saved; only its own progress events are left out.
        return real(
            session_id, payload, lambda event: hear(event) if event["type"] == "saving" else None
        )

    service.create_session_turn = answer


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


def stream(client: TestClient, method: str, url: str, **kwargs) -> list[tuple[str, dict]]:
    with client.stream(method, url, headers=SIDECAR, **kwargs) as response:
        return read_events(response)


def test_an_answer_goes_on_when_the_page_leaves_and_can_be_followed_again(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(agent_replies("Saved answer.", {"action": "answer"}))
    gate = threading.Event()
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        held_answers(client, gate)
        runs = client.app.state.answer_runs
        first, first_events = in_background(
            lambda: stream(
                client, "POST", f"/api/sessions/{session_id}/messages/stream",
                json={"content": "CD3D?"},
            )
        )
        wait_for(lambda: runs.get(session_id) is not None and len(runs.get(session_id).events) >= 3)
        # One answer at a time per conversation.
        busy = client.post(
            f"/api/sessions/{session_id}/messages/stream",
            headers=SIDECAR,
            json={"content": "Again?"},
        )
        assert busy.status_code == 409
        # A page that comes back is told the question and what it missed, then the rest.
        again, again_events = in_background(
            lambda: stream(client, "GET", f"/api/sessions/{session_id}/answer/stream")
        )
        wait_for(lambda: len(runs.get(session_id)._watchers) == 2)
        gate.set()
        first.join(10)
        again.join(10)
        [followed] = again_events
        saved = client.get(f"/api/sessions/{session_id}", headers=SIDECAR).json()
        after = client.get(f"/api/sessions/{session_id}/answer/stream", headers=SIDECAR)
    names = [name for name, _ in followed]
    assert names[0] == "resumed" and followed[0][1]["question"] == "CD3D?"
    assert names[1] == "step" and names[-1] == "done"
    texts = [data["text"] for name, data in followed if name == "text"]
    assert texts == ["Part one ", "part two."]
    assert first_events[0][-1][0] == "done"
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert after.status_code == 204


def test_only_stop_ends_an_answer_early_and_keeps_the_question(tmp_path: Path) -> None:
    fake = FakeProvider("unused")
    gate = threading.Event()
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        held_answers(client, gate)
        runs = client.app.state.answer_runs
        first, first_events = in_background(
            lambda: stream(
                client, "POST", f"/api/sessions/{session_id}/messages/stream",
                json={"content": "Stop me?"},
            )
        )
        wait_for(lambda: runs.get(session_id) is not None and len(runs.get(session_id).events) >= 3)
        stopped = client.post(f"/api/sessions/{session_id}/answer/stop", headers=SIDECAR)
        assert stopped.status_code == 204
        gate.set()
        first.join(10)
        saved = client.get(f"/api/sessions/{session_id}", headers=SIDECAR).json()
    [events] = first_events
    assert events[-1][0] == "stopped"
    [message] = saved["messages"]
    assert message["content"] == "Stop me?" and message["context"]["interrupted"] == "stopped"


def test_a_reader_leaving_only_stops_listening() -> None:
    run = AnswerRun("ses_1", "Why?")
    run.publish("text", {"type": "text", "text": "Half "})

    async def leave_after_one() -> list:
        seen = []
        stream = run.follow()
        async for event in stream:
            seen.append(event)
            break
        await stream.aclose()
        return seen

    assert asyncio.run(leave_after_one()) == [("text", {"type": "text", "text": "Half "})]
    assert not run.stop_requested.is_set() and run._watchers == []
    run.publish("text", {"type": "text", "text": "and whole."})
    run.publish("done", {"ok": True})

    async def come_back() -> list:
        return [event async for event in run.follow(resumed=True)]

    back = asyncio.run(come_back())
    assert back[0][0] == "resumed"
    assert back[1:] == [
        ("text", {"type": "text", "text": "Half and whole."}),
        ("done", {"ok": True}),
    ]


def test_a_stopped_question_stays_in_the_history_but_not_in_what_the_model_reads(
    tmp_path: Path,
) -> None:
    fake = FakeProvider("unused")
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        service = client.app.state.knowledge_service
        service.record_interrupted_question(session_id, "  Why did I stop?  ", "stopped")
        saved = client.get(f"/api/sessions/{session_id}", headers=SIDECAR).json()
        stored = SimpleNamespace(
            role="user", content="q", context_json='{"interrupted": "stopped"}'
        )
        history = conversation_history([stored])
    [message] = saved["messages"]
    assert message["role"] == "user" and message["content"] == "Why did I stop?"
    assert message["context"]["interrupted"] == "stopped"
    assert saved["messageCount"] == 1
    assert history == []


def test_home_questions_read_every_library_or_the_ones_pointed_at(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "Both notes agree [1][2].",
            {"action": "search_library", "query": "CD3D marker"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake) as client:
        first = client.post(
            "/api/knowledge-bases",
            headers=SIDECAR,
            json={"title": "Cells", "question": "Cells?", "description": "Cells."},
        ).json()["id"]
        second = client.post(
            "/api/knowledge-bases",
            headers=SIDECAR,
            json={"title": "Genes", "question": "Genes?", "description": "Genes."},
        ).json()["id"]
        for base_id, title, text in (
            (first, "Cell note", "CD3D is a marker of T cells."),
            (second, "Gene note", "CD3D is a marker gene on chromosome 11."),
        ):
            client.post(
                "/api/sources",
                headers=SIDECAR,
                json={"title": title, "kind": "note", "knowledgeBaseId": base_id, "content": text},
            )
        home = client.post("/api/knowledge-bases/@home/sessions", headers=SIDECAR, json={})
        assert home.status_code == 201
        session_id = home.json()["id"]

        def ask(**extra) -> dict:
            response = client.post(
                f"/api/sessions/{session_id}/messages",
                headers=SIDECAR,
                json={"content": "What are the CD3D markers?", **extra},
            )
            assert response.status_code == 200, response.text
            return response.json()["assistantMessage"]

        everywhere = ask()
        assert {c["sourceTitle"] for c in everywhere["citations"]} == {"Cell note", "Gene note"}
        assert everywhere["context"]["sourcesConsidered"] == 2
        only_genes = ask(knowledgeBaseIds=[second])
        assert only_genes["context"]["sourcesConsidered"] == 1
        assert {c["sourceTitle"] for c in only_genes["citations"]} <= {"Gene note"}

        # Home's conversations are listed for Home, and are not found as a library's.
        listed = client.get("/api/knowledge-bases/@home/sessions", headers=SIDECAR).json()
        assert [item["id"] for item in listed] == [session_id]
        assert client.get(f"/api/knowledge-bases/{first}/sessions", headers=SIDECAR).json() == []
        assert client.get("/api/search?q=CD3D", headers=SIDECAR).status_code == 200

        filed = client.post(
            f"/api/sessions/{session_id}/file", headers=SIDECAR, json={"knowledgeBaseId": first}
        )
        assert filed.status_code == 200 and filed.json()["knowledgeBaseId"] == first
        assert client.get("/api/knowledge-bases/@home/sessions", headers=SIDECAR).json() == []
        kept = client.get(f"/api/sessions/{session_id}", headers=SIDECAR).json()
        assert len(kept["messages"]) == 4
        # A library conversation cannot be filed again, and the target must exist.
        again = client.post(
            f"/api/sessions/{session_id}/file", headers=SIDECAR, json={"knowledgeBaseId": second}
        )
        assert again.status_code == 404


def test_library_hits_about_something_else_are_dropped_before_the_answer() -> None:
    hits = [
        passage("T cell note", "CD3D marks T cells."),
        passage("Lung note", "AT2 cells make surfactant."),
    ]
    fake = FakeProvider(
        agent_replies(
            "AT2 cells make surfactant [1].",
            {"action": "search_library", "query": "lung epithelium"},
            {"action": "answer"},
            relevant=[2],
        )
    )
    result = run(fake, box(lib(lambda q: list(hits))), "lung epithelial markers?")
    assert [item.title for item in result.evidence] == ["Lung note"]
    assert result.steps[0].found == 1
    prompt = writer_prompts(fake)[0]
    assert "Lung note" in prompt and "T cell note" not in prompt


def test_a_grader_that_cannot_answer_keeps_what_the_search_found() -> None:
    def replies(request):
        if is_planning(request):
            return json.dumps({"action": "search_library", "query": "cd3d"})
        return "not json at all" if is_grading(request) else "CD3D marks T cells [1]."

    result = run(
        FakeProvider(replies),
        box(lib(lambda q: [passage("Cell note", "CD3D marks T cells.")])),
    )
    assert [item.title for item in result.evidence] == ["Cell note"]


def test_another_tool_can_be_tried_when_the_first_finds_nothing_relevant() -> None:
    web_queries: list[str] = []

    def search_web(query: str) -> list[Evidence]:
        web_queries.append(query)
        return [
            page("Lung atlas", "https://example.org/lung", "AT1, AT2, club and ciliated cells.")
        ]

    fake = FakeProvider(
        agent_replies(
            "The lung epithelium has AT1, AT2, club and ciliated cells [1].",
            {"action": "search_library", "query": "lung epithelium"},
            {"action": "search_web", "query": "lung epithelium cell types"},
            {"action": "answer"},
            relevant=[],
        )
    )
    result = run(
        fake,
        box(lib(lambda q: [passage("T cell note", "CD3D.")]), web(search_web)),
        "What are the lung epithelial cell types?",
    )
    assert web_queries == ["lung epithelium cell types"]
    assert [step.tool for step in result.steps] == ["search_library", "search_web"]
    assert result.steps[0].found == 0
    assert [item.title for item in result.evidence] == ["Lung atlas"]


def test_sources_are_numbered_in_the_order_the_text_cites_them_and_uncited_ones_dropped() -> None:
    found = [
        passage("First", "one"),
        passage("Second", "two"),
        passage("Third", "three"),
        passage("Unused", "four"),
    ]
    fake = FakeProvider(
        agent_replies(
            "Third comes first [3]. Then the first [1, 3]. Last the second [2].",
            {"action": "search_library", "query": "x"},
            {"action": "answer"},
        )
    )
    result = run(fake, box(lib(lambda q: list(found))))
    assert result.content == "Third comes first [1]. Then the first [2][1]. Last the second [3]."
    assert [item.title for item in result.evidence] == ["Third", "First", "Second"]
    assert [item.ref for item in result.evidence] == [3, 1, 2]


def test_renumbering_and_the_history_the_model_reads_use_the_same_ids() -> None:
    from gunther.agent import renumber_citations
    from gunther.models import SessionMessage
    from gunther.service import conversation_history

    assert renumber_citations("A [6]. B [4][6]. C [?].", [6, 4]) == "A [1]. B [2][1]. C [?]."

    def cited(ref: int | None) -> dict:
        return {
            "id": f"cit_{ref}",
            "source_id": "s",
            "source_title": "S",
            "quote": "q",
            "locator": "l",
            "status": "provisional",
            "confidence": 0.0,
            "ref": ref,
        }

    def answer(content: str, refs: list[int | None]) -> SessionMessage:
        return SessionMessage(
            id="m",
            session_id="x",
            role="assistant",
            content=content,
            citations_json=json.dumps([cited(ref) for ref in refs]),
            context_json="{}",
            created_at=datetime(2026, 1, 1),
        )

    # The answer shows [1][2]; the model reads the pool's ids for those sources.
    [turn] = conversation_history([answer("A [1]. B [2]. C [?].", [6, 4])])
    assert turn.content == "A [6]. B [4]. C [?]."
    # Before the pool there were no ids, so the markers are left out.
    [older] = conversation_history([answer("A [1]. B [2].", [None, None])])
    assert older.content == "A. B."
