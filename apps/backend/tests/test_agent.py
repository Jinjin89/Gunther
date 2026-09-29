"""Ask's agent: intent, tools, evidence, and an answer that is never a bare "not found"."""

import json
from pathlib import Path

import httpx
import openai
from fake_models import FakeProvider, agent_replies, api_error, gateway, is_grading, is_planning
from fastapi.testclient import TestClient

from gunther import online_search
from gunther.agent import AskAgent, Evidence, ToolFailure, Tools, tidy_citations
from gunther.config import Settings
from gunther.llm import Turn
from gunther.main import create_app

SIDECAR_TOKEN = "sidecar-token-with-at-least-256-bits-000000000000000000000000"
SIDECAR = {"X-Gunther-Token": SIDECAR_TOKEN}


def passage(title: str, text: str) -> Evidence:
    return Evidence(kind="library", title=title, text=text, locator="line 1", payload=None)


def page(title: str, url: str, text: str) -> Evidence:
    return Evidence(kind="web", title=title, text=text, url=url, locator="example.org")


def run(fake: FakeProvider, tools: Tools, question: str = "What marks T cells?", history=()):
    chosen = gateway(fake)
    model = chosen.models[0]
    return AskAgent(chosen).run(question, list(history), tools, model, "high")


def writer_prompts(fake: FakeProvider) -> list[str]:
    return [
        str(r["messages"][-1]["content"])
        for r in fake.requests
        if not is_planning(r) and not is_grading(r)
    ]


def test_citations_are_kept_only_when_they_exist_and_renumbered() -> None:
    text, used = tidy_citations("A [3] and B [1, 3]; C [9] too.", 4)
    assert text == "A [1] and B [2][1]; C too."
    assert used == [3, 1]
    assert tidy_citations("No citations here.", 2) == ("No citations here.", [])


def test_small_talk_is_answered_by_the_planner_without_a_second_call() -> None:
    fake = FakeProvider(
        agent_replies(
            "unused",
            {"intent": "chat", "action": "answer", "reply": "Hi! What shall we look into?"},
        )
    )
    result = run(fake, Tools(search_library=lambda q: [passage("A", "x")], library_size=3), "hi")
    assert result.content == "Hi! What shall we look into?"
    assert result.intent == "chat" and result.evidence == [] and result.steps == []
    assert len(fake.requests) == 1


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
            {"intent": "library", "action": "search_library", "query": "T cell marker CD3D"},
            {"intent": "library", "action": "answer"},
        )
    )
    history = [Turn("user", "Tell me about CD3D"), Turn("assistant", "It is a gene.")]
    result = run(
        fake, Tools(search_library=search_library, library_size=5), "and T cells?", history
    )
    assert searched == ["T cell marker CD3D"]
    assert result.content == "CD3D marks T cells [1]. Nothing else."
    assert [item.title for item in result.evidence] == ["Cell note"]
    assert result.steps[0].found == 2
    prompt = writer_prompts(fake)[0]
    assert "[1] (library · Cell note · line 1)" in prompt
    # The planner saw the conversation, to resolve "and T cells?".
    planning = next(r for r in fake.requests if is_planning(r))
    assert any(m["content"] == "Tell me about CD3D" for m in planning["messages"])


def test_when_nothing_is_found_the_writer_is_still_asked_to_help() -> None:
    fake = FakeProvider(
        agent_replies(
            "I looked for that in your library and found nothing. In general, ...",
            {"intent": "library", "action": "search_library", "query": "quantum gravity"},
            {"intent": "library", "action": "answer"},
        )
    )
    result = run(
        fake,
        Tools(
            search_library=lambda q: [],
            library_size=5,
            web_off_reason="the Web toggle is off for this message",
        ),
    )
    assert result.content.startswith("I looked for that")
    prompt = writer_prompts(fake)[0]
    assert "none of the searches found anything relevant" in prompt
    assert "The web was not searched: the Web toggle is off" in prompt


def test_the_web_is_offered_only_when_it_is_a_tool_and_failures_are_reported() -> None:
    def broken_web(query: str, topic: str) -> list[Evidence]:
        raise ToolFailure("Tavily did not accept the API key.")

    fake = FakeProvider(
        agent_replies(
            "I could not reach the web.",
            {"intent": "web", "action": "search_web", "query": "latest release", "topic": "news"},
            {"intent": "web", "action": "answer"},
        )
    )
    result = run(fake, Tools(search_web=broken_web))
    planning = next(r for r in fake.requests if is_planning(r))
    assert "search_web: the public web" in str(planning["messages"][-1]["content"])
    assert result.steps[0].error == "Tavily did not accept the API key."
    assert "failed: Tavily did not accept the API key." in writer_prompts(fake)[0]

    fake = FakeProvider(agent_replies("ok", {"intent": "chat", "action": "answer"}))
    run(fake, Tools(search_library=lambda q: []))
    prompt = str(next(r for r in fake.requests if is_planning(r))["messages"][-1]["content"])
    assert "search_web" not in prompt.split("Actions offered:")[1].split("Done so far")[0]
    assert "The web is not available" in prompt


def test_a_search_is_never_repeated_and_the_loop_stops() -> None:
    calls: list[str] = []

    def search_library(query: str) -> list[Evidence]:
        calls.append(query)
        return []

    same = {"intent": "library", "action": "search_library", "query": "same words"}
    fake = FakeProvider(agent_replies("Answer.", same))
    result = run(fake, Tools(search_library=search_library, library_size=1))
    assert calls == ["same words"]
    assert result.content == "Answer."


def test_an_unreadable_plan_falls_back_to_searching_the_library_as_asked() -> None:
    calls: list[str] = []

    def replies(request):
        return "not json at all" if is_planning(request) else "Answer from the note [1]."

    def search_library(query: str) -> list[Evidence]:
        calls.append(query)
        return [passage("Cell note", "CD3D is a marker of T cells.")]

    fake = FakeProvider(replies)
    result = run(fake, Tools(search_library=search_library, library_size=1), "What marks T cells?")
    assert calls == ["What marks T cells?"]
    assert result.content == "Answer from the note [1]."


def test_a_model_that_cannot_answer_returns_its_error_not_an_invented_reply() -> None:
    fake = FakeProvider(api_error(openai.RateLimitError, 429, "Insufficient Balance"))
    result = run(fake, Tools(search_library=lambda q: [passage("A", "x")], library_size=1))
    assert (
        result.error
        == "DeepSeek is busy or out of credit (Insufficient Balance). Try again shortly."
    )
    assert result.content == ""


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
                "intent": "web",
                "action": "search_web",
                "query": "CD3D latest research",
            },
            {"intent": "web", "action": "answer"},
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
    assert context["intent"] == "web" and context["webSearched"] is True
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
            {"intent": "library", "action": "search_library", "query": "CD3D"},
            {"intent": "library", "action": "answer"},
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
    assert "The web is not available" in str(planning["messages"][-1]["content"])
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
            {"intent": "library", "action": "search_library", "query": "CD3D"},
            {"intent": "library", "action": "answer"},
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
    assert names[0] == "intent" and names[-1] == "done"
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


def test_home_questions_read_every_library_or_the_ones_pointed_at(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "Both notes agree [1][2].",
            {"intent": "library", "action": "search_library", "query": "CD3D marker"},
            {"intent": "library", "action": "answer"},
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
            {"intent": "library", "action": "search_library", "query": "lung epithelium"},
            {"intent": "library", "action": "answer"},
            relevant=[2],
        )
    )
    result = run(
        fake, Tools(search_library=lambda q: list(hits), library_size=5), "lung epithelial markers?"
    )
    assert [item.title for item in result.evidence] == ["Lung note"]
    assert result.steps[0].found == 1
    prompt = writer_prompts(fake)[0]
    assert "Lung note" in prompt and "T cell note" not in prompt


def test_a_grader_that_cannot_answer_keeps_what_the_search_found() -> None:
    def replies(request):
        if is_planning(request):
            return json.dumps({"intent": "library", "action": "search_library", "query": "cd3d"})
        return "not json at all" if is_grading(request) else "CD3D marks T cells [1]."

    result = run(
        FakeProvider(replies),
        Tools(
            search_library=lambda q: [passage("Cell note", "CD3D marks T cells.")], library_size=1
        ),
    )
    assert [item.title for item in result.evidence] == ["Cell note"]


def test_the_web_is_tried_when_the_library_has_nothing_relevant() -> None:
    web_queries: list[str] = []

    def search_web(query: str, topic: str) -> list[Evidence]:
        web_queries.append(query)
        return [
            page("Lung atlas", "https://example.org/lung", "AT1, AT2, club and ciliated cells.")
        ]

    fake = FakeProvider(
        agent_replies(
            "The lung epithelium has AT1, AT2, club and ciliated cells [1].",
            {"intent": "library", "action": "search_library", "query": "lung epithelium"},
            # The planner would stop here; with the web on, the agent goes on to the web.
            {"intent": "library", "action": "answer"},
            relevant=[],
        )
    )
    result = run(
        fake,
        Tools(
            search_library=lambda q: [passage("T cell note", "CD3D.")],
            search_web=search_web,
            library_size=5,
        ),
        "What are the lung epithelial cell types?",
    )
    assert web_queries == ["What are the lung epithelial cell types?"]
    assert [step.tool for step in result.steps] == ["search_library", "search_web"]
    assert result.steps[0].found == 0
    assert [item.title for item in result.evidence] == ["Lung atlas"]


def test_the_web_is_not_tried_for_small_talk_or_when_it_is_off() -> None:
    called: list[str] = []

    def search_web(query: str, topic: str) -> list[Evidence]:
        called.append(query)
        return []

    fake = FakeProvider(
        agent_replies("Hi.", {"intent": "chat", "action": "answer", "reply": "Hi!"})
    )
    run(fake, Tools(search_library=lambda q: [], search_web=search_web), "hi")
    fake = FakeProvider(
        agent_replies(
            "Nothing.",
            {"intent": "library", "action": "search_library", "query": "x"},
            {"intent": "library", "action": "answer"},
            relevant=[],
        )
    )
    result = run(fake, Tools(search_library=lambda q: [passage("A", "b")], library_size=1))
    assert called == [] and [step.tool for step in result.steps] == ["search_library"]
