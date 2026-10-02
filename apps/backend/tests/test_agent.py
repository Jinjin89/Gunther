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
    is_framing,
    is_grading,
    is_grading_notes,
    is_keeping_brief,
    is_looking_up,
    is_planning,
    is_revising,
    is_sentence_checking,
)
from fastapi.testclient import TestClient

from gunther import online_search
from gunther.agent import (
    AskAgent,
    Budget,
    Evidence,
    Tool,
    Toolbox,
    ToolFailure,
    _aligned_notes,
    check_citations,
    collapse_citations,
    leads_in,
    renumber_citations,
    sentence_spans,
    writer_prompt,
)
from gunther.answer_runs import AnswerRun
from gunther.config import Settings
from gunther.llm import Turn
from gunther.main import create_app
from gunther.service import conversation_history
from gunther.skillbook import ask_skills
from gunther.web_capture import (
    PublicWebUrlPolicy,
    WebCaptureError,
    WebFetchResponse,
    fetch_public_page,
)

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
        if not is_planning(r)
        and not is_grading(r)
        and not is_grading_notes(r)
        and not is_checking(r)
        and not is_auditing(r)
        and not is_sentence_checking(r)
        and not is_looking_up(r)
        and not is_keeping_brief(r)
        and not is_revising(r)
        and not is_framing(r)
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


def facts(*numbers: int) -> dict:
    """A checker that lists these sentences as facts with no source."""
    return {"sentences": [{"n": n, "verdict": "unsourced"} for n in numbers]}


def claim_run(lookups: dict, found: list[Evidence], answer: str, listed: tuple[int, ...] = (1,)):
    fake = FakeProvider(
        agent_replies(answer, {"action": "answer"}, lookups=lookups, checked=facts(*listed))
    )
    result = run(fake, box(web(lambda q: list(found))), "Tell me")
    return fake, result


def test_a_claim_from_the_models_own_knowledge_gets_a_source_when_one_is_found() -> None:
    fake, result = claim_run(
        {"claims": [{"claim": 1, "supports": [1], "contradicts": []}]},
        [page("Physics", "https://a.org/p", "Water boils at 100 C at sea level.")],
        "Water boils at 100 C [?]. It is a common fact.",
    )
    assert result.content == "Water boils at 100 C [1]. It is a common fact."
    assert [(item.ref, item.kind) for item in result.evidence] == [(1, "web")]
    assert any("Checked the web" in step.label for step in result.steps)
    assert sum(is_looking_up(r) for r in fake.requests) == 1


def test_a_claim_with_no_source_stays_marked_and_a_contradicted_one_is_kept_as_disputed() -> None:
    _, result = claim_run(
        {"claims": [{"claim": 1, "supports": [], "contradicts": []}]},
        [page("Other", "https://a.org/o", "Unrelated.")],
        "It is likely so [?].",
    )
    assert result.content == "It is likely so [?]." and result.evidence == []

    _, result = claim_run(
        {"claims": [{"claim": 2, "supports": [], "contradicts": [1]}]},
        [page("Physics", "https://a.org/p", "Water boils at 90 C on this mountain.")],
        "Intro [?]. Water boils at 100 C here [?]. Closing.",
        (1, 2),
    )
    assert result.content == "Intro [?]. Water boils at 100 C here [d:1]. Closing."
    assert any("marked disputed (Physics)" in note for note in result.notes)
    assert [(item.ref, item.title) for item in result.evidence] == [(1, "Physics")]


def test_a_claim_left_with_no_source_and_no_marker_is_found_and_checked() -> None:
    answer = "The marker is CD3D [1]. Water boils at 100 C. It was named in 1990."
    fake = FakeProvider(
        agent_replies(
            answer,
            {"action": "search_library", "query": "cd3d"},
            {"action": "answer"},
            checked={
                "sentences": [
                    {"n": 1, "verdict": "supported"},
                    {"n": 2, "verdict": "unsourced"},
                    {"n": 3, "verdict": "unsourced"},
                ]
            },
        )
    )
    result = run(fake, box(lib(lambda q: [passage("Cell note", "CD3D marks T cells.")])))
    assert (
        result.content
        == "The marker is CD3D [1]. Water boils at 100 C [?]. It was named in 1990 [?]."
    )
    assert result.checked
    assert sum(is_sentence_checking(r) for r in fake.requests) == 1
    # The marked claims were looked up together, after the check, in one call.
    assert sum(is_looking_up(r) for r in fake.requests) == 1
    # A sentence the writer already marked is left alone, however the checker judges it.
    fake = FakeProvider(
        agent_replies(
            "It is so [?].",
            {"action": "answer"},
            checked={"sentences": [{"n": 1, "verdict": "unsourced"}]},
        )
    )
    assert run(fake, box(lib(lambda q: []))).content == "It is so [?]."


def test_at_most_a_few_claims_are_looked_up_and_style_never_overrides_the_rules() -> None:
    fake, result = claim_run(
        {"claims": []},
        [page("X", "https://a.org/x", "x")],
        "A [?]. B [?]. C [?]. D [?].",
        (1, 2, 3, 4),
    )
    [request] = [r for r in fake.requests if is_looking_up(r)]
    prompt = str(request["messages"][-1]["content"])
    assert "Claim 3:" in prompt and "Claim 4:" not in prompt
    assert sum(1 for step in result.steps if step.label.startswith("Checked")) == 3
    assert result.content == "A [?]. B [?]. C [?]. D [?]."
    concise = writer_prompt("concise")
    assert "As short as the question allows" in concise and "the rules win" in concise
    assert writer_prompt("nonsense") == writer_prompt("balanced")
    assert "[?]" in writer_prompt(None) and "right after it" in writer_prompt(None)
    assert "Never put [?] on a sentence that cites a source" in writer_prompt(None)


def test_a_statement_about_the_sources_marked_by_the_writer_is_not_looked_up_or_removed() -> None:
    fake = FakeProvider(
        agent_replies(
            "The sources give no figure for offices [?]. Trials covered flats [1].",
            {"action": "search_library", "query": "offices"},
            {"action": "answer"},
            checked={"sentences": [{"n": 2, "verdict": "supported"}]},
            lookups={"claims": [{"claim": 1, "contradicts": [1]}]},
        )
    )
    result = run(fake, box(lib(lambda q: [passage("Trial", "Trials covered flats.")])))
    assert result.content == "The sources give no figure for offices [?]. Trials covered flats [1]."
    assert not any(is_looking_up(r) for r in fake.requests)
    assert not any(step.label.startswith("Checked") for step in result.steps)
    assert not result.notes


def test_a_fact_written_from_memory_is_looked_up_when_the_checker_lists_it() -> None:
    fake = FakeProvider(
        agent_replies(
            "Water boils at 100 C [?]. The sources give no figure for offices [?].",
            {"action": "answer"},
            checked=facts(1),
            lookups={"claims": [{"claim": 1, "supports": [1]}]},
        )
    )
    result = run(
        fake, box(web(lambda q: [page("Physics", "https://a.org/p", "Water boils at 100 C.")]))
    )
    assert result.content == "Water boils at 100 C [1]. The sources give no figure for offices [?]."
    [request] = [r for r in fake.requests if is_looking_up(r)]
    assert "Claim 1: Water boils at 100 C" in str(request["messages"][-1]["content"])
    assert "Claim 2" not in str(request["messages"][-1]["content"])


def test_look_ups_are_judged_in_one_call() -> None:
    fake, _ = claim_run(
        {"claims": []},
        [page("X", "https://a.org/x", "x")],
        "A [?]. B [?]. C [?].",
        (1, 2, 3),
    )
    assert sum(is_looking_up(r) for r in fake.requests) == 1
    prompt = str(next(r for r in fake.requests if is_looking_up(r))["messages"][-1]["content"])
    assert all(f"Claim {k}: " in prompt for k in (1, 2, 3))


def test_a_rework_reply_is_not_checked_and_nothing_is_removed() -> None:
    answer = "Water boils at 100 C. Mount Everest is 8849 m high."
    fake = FakeProvider(agent_replies(answer, {"action": "answer", "new_facts": False}))
    result = run(fake, box(lib(lambda q: [])), "Translate that")
    assert result.content == answer and not result.checked
    assert not any(is_sentence_checking(r) or is_looking_up(r) for r in fake.requests)
    assert "add no new facts" in writer_prompts(fake)[0]


def test_a_rework_reply_is_not_looked_up_either() -> None:
    # Checks follow the same decision as searching: a marked claim in a rework reply keeps
    # its mark and nothing is searched for it.
    searched = []
    fake = FakeProvider(
        agent_replies(
            "Shorter: it boils at 100 C [?].",
            {"action": "answer", "new_facts": False},
            lookups={"claims": [{"claim": 1, "supports": [1]}]},
        )
    )
    result = run(
        fake,
        box(web(lambda q: searched.append(q) or [page("Physics", "https://a.org/p", "x")])),
    )
    assert result.content == "Shorter: it boils at 100 C [?]."
    assert searched == []
    assert not any(is_sentence_checking(r) or is_looking_up(r) for r in fake.requests)


def test_a_factual_reply_with_no_search_is_still_checked() -> None:
    fake = FakeProvider(
        agent_replies(
            "Water boils at 100 C.",
            {"action": "answer"},
            checked={"sentences": [{"n": 1, "verdict": "unsourced"}]},
        )
    )
    result = run(fake, box(lib(lambda q: [])))
    assert result.checked and result.content == "Water boils at 100 C [?]."
    assert sum(is_sentence_checking(r) for r in fake.requests) == 1


def test_a_citation_the_source_does_not_support_becomes_unverified_and_is_looked_up() -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks B cells [1], and it is sparse [1][1].",
            {"action": "search_library", "query": "cd3d"},
            {"action": "answer"},
            checked={"sentences": [{"n": 1, "verdict": "not"}]},
        )
    )
    result = run(fake, box(lib(lambda q: [passage("Cell note", "CD3D marks T cells.")])))
    assert result.content == "CD3D marks B cells, and it is sparse [?]."
    assert result.evidence == []
    assert sum(is_looking_up(r) for r in fake.requests) == 1
    checking = next(r for r in fake.requests if is_sentence_checking(r))
    prompt = str(checking["messages"][-1]["content"])
    assert "(1) CD3D marks B cells [1]" in prompt and "[1] (library · Cell note" in prompt


def test_unsourced_sentences_found_by_the_checker_are_marked_and_looked_up() -> None:
    fake = FakeProvider(
        agent_replies(
            "The marker is CD3D [1]. Water boils at 100 C.",
            {"action": "search_library", "query": "cd3d"},
            {"action": "answer"},
            checked={"sentences": [{"n": 2, "verdict": "unsourced"}]},
            lookups={"claims": [{"claim": 1, "supports": [2]}]},
        )
    )
    result = run(
        fake,
        box(
            lib(lambda q: [passage("Cell note", "CD3D marks T cells.")]),
            web(lambda q: [page("Physics", "https://a.org/p", "Water boils at 100 C.")]),
        ),
    )
    assert result.content == "The marker is CD3D [1]. Water boils at 100 C [2]."
    assert [item.kind for item in result.evidence] == ["library", "web"]


def test_inference_and_partly_supported_are_labelled_and_survive_renumbering() -> None:
    held = [passage("A", "Alpha.", ref=7), passage("B", "Beta.", ref=9)]
    fake = FakeProvider(
        agent_replies(
            "A follows from the notes [7]. B is half stated [9]. C is stated [9].",
            {"action": "answer"},
            checked={
                "sentences": [
                    {"n": 1, "verdict": "inference"},
                    {"n": 2, "verdict": "partly", "missing": "no date"},
                    {"n": 3, "verdict": "supported"},
                ]
            },
        )
    )
    result = run(fake, box(lib(lambda q: [])), pool=held)
    assert result.content == (
        "A follows from the notes [i:1]. B is half stated [p:2]. C is stated [2]."
    )
    assert [item.ref for item in result.evidence] == [7, 9]
    assert result.support_notes == ("no date",)


def test_a_failed_check_keeps_the_answer_and_says_so() -> None:
    inner = agent_replies("Water boils at 100 C.", {"action": "answer"})

    def replies(request):
        if is_sentence_checking(request):
            return api_error(openai.RateLimitError, 429, "Insufficient Balance")
        return inner(request)

    result = run(FakeProvider(replies), box(lib(lambda q: [])))
    assert result.content == "Water boils at 100 C." and not result.checked
    assert any(note.startswith("Citations were not checked:") for note in result.notes)


def test_the_history_the_model_reads_turns_labels_back_into_pool_numbers() -> None:
    from gunther.models import SessionMessage

    refs = [6, 4]
    message = SessionMessage(
        id="m",
        session_id="x",
        role="assistant",
        content="A [i:1] [p:2]. B [2]. C [?].",
        citations_json=json.dumps(
            [
                {
                    "id": f"cit_{ref}",
                    "source_id": "s",
                    "source_title": "S",
                    "quote": "q",
                    "locator": "l",
                    "status": "provisional",
                    "confidence": 0.0,
                    "ref": ref,
                }
                for ref in refs
            ]
        ),
        context_json="{}",
        created_at=datetime(2026, 1, 1),
    )
    [turn] = conversation_history([message])
    assert turn.content == "A [6] [4]. B [4]. C [?]."


def test_check_citations_and_renumbering_handle_labels() -> None:
    text, used = check_citations("A [i:3] and B [p:9][1, 3]; C [?].", {1, 3})
    assert text == "A [i:3] and B [1][3]; C [?]."
    assert used == [3, 1]
    assert renumber_citations("A [i:6] B [p:4][6] C [?]", [6, 4]) == "A [i:1] B [p:2][1] C [?]"


def test_disputed_marks_are_kept_counted_renumbered_and_collapsed() -> None:
    text, used = check_citations("A [d:3] and B [d:9][1]; C [?].", {1, 3})
    assert text == "A [d:3] and B [1]; C [?]."
    assert used == [3, 1]
    assert renumber_citations("A [d:6] B [d:4][6]", [6, 4]) == "A [d:1] B [d:2][1]"
    assert collapse_citations("A [d:2] [2][d:3] [d:3].") == "A [d:2][d:3]."
    text = "A fact. [d:1] Another [?]."
    assert [text[a:b].strip() for a, b in sentence_spans(text)] == ["A fact. [d:1]", "Another [?]."]
    assert _aligned_notes("A [d:1] [p:2]. B [p:2].", ["x", "y"], "A [d:1] [p:2]. B.") == ("x",)


def test_runs_of_citations_are_collapsed_and_sentences_are_split_with_their_marks() -> None:
    assert collapse_citations("A [2] [3][2]. B [4][2] [4][2]. C [1] [2][3][1].") == (
        "A [2][3]. B [4][2]. C [1][2][3]."
    )
    assert collapse_citations("A [i:2] [2] and [p:3], [3].") == "A [i:2] and [p:3], [3]."
    assert collapse_citations("One [?] [1]. Two [1, 2, 1].") == "One [?] [1]. Two [1][2]."
    text = "# Title\n\nA fact. [3] Another [?]. Last\n---\n| a |\n|---|\n- item [1]\n"
    spans = [text[a:b].strip() for a, b in sentence_spans(text)]
    assert spans == ["# Title", "A fact. [3]", "Another [?].", "Last", "| a |", "- item [1]"]


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


def test_the_checker_s_labels_and_notes_are_saved_with_the_answer(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
            checked={"sentences": [{"n": 1, "verdict": "partly", "missing": "no species"}]},
        )
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        sent = client.post(
            f"/api/sessions/{session_id}/messages", headers=SIDECAR, json={"content": "CD3D?"}
        ).json()
    answer = sent["assistantMessage"]
    assert answer["content"] == "CD3D marks T cells [p:1]."
    assert answer["context"]["checked"] is True
    assert answer["context"]["supportNotes"] == ["no species"]


def test_notes_follow_the_partly_supported_markers_that_are_left() -> None:
    from gunther.agent import _aligned_notes

    before = "A [p:9]. B [p:2]. C [p:9]."
    assert _aligned_notes(before, ["x", "y", "z"], "A. B [p:2]. C.") == ("y",)
    assert _aligned_notes(before, ["x", "y", "z"], before) == ("x", "y", "z")


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
        return "not json at all" if is_grading_notes(request) else "CD3D marks T cells [1]."

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
            kept=lambda request: json.dumps(
                {"keep": [{"n": 1}] if "Lung atlas" in request["messages"][-1]["content"] else []}
            ),
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
    [disputed] = conversation_history([answer("A [d:1]. B [2].", [6, 4])])
    assert disputed.content == "A (disputed by [6]). B [4]."


def acts(*actions: dict) -> dict:
    return {"actions": list(actions)}


def search(tool: str, query: str) -> dict:
    return {"action": tool, "query": query}


def test_a_plan_can_run_several_actions_in_order() -> None:
    order: list[str] = []

    def search_library(query: str) -> list[Evidence]:
        order.append(f"library:{query}")
        return [passage("Cell note", "CD3D marks T cells.")]

    def search_web(query: str) -> list[Evidence]:
        order.append(f"web:{query}")
        return [page("Review", "https://example.org/r", "A review of CD3D.")]

    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1] and has a review [2].",
            acts(
                search("search_library", "cd3d"),
                search("search_web", "cd3d review"),
                {"action": "answer"},
            ),
        )
    )
    result = run(fake, box(lib(search_library), web(search_web)))
    assert order == ["library:cd3d", "web:cd3d review"]
    assert [step.tool for step in result.steps] == ["search_library", "search_web"]
    # "answer" ended gathering: the plan was asked once.
    assert sum(is_planning(r) for r in fake.requests) == 1


def test_the_budget_ends_gathering() -> None:
    queries: list[str] = []

    def search_library(query: str) -> list[Evidence]:
        queries.append(query)
        return [passage(f"Note {len(queries)}", f"Fact {len(queries)}.")]

    plans = [acts(search("search_library", f"query {n}")) for n in range(1, 8)]
    fake = FakeProvider(agent_replies("Fact 1 [1].", *plans))
    run(fake, box(lib(search_library)), budget=Budget(searches=2))
    assert queries == ["query 1", "query 2"]

    queries.clear()
    fake = FakeProvider(agent_replies("Fact 1 [1].", *plans))
    run(fake, box(lib(search_library)), budget=Budget(calls=3))
    # A plan and its grading are two calls: one more plan, then the budget is spent.
    assert queries == ["query 1", "query 2"]
    assert sum(is_planning(r) for r in fake.requests) == 2

    # A round that runs nothing ends gathering too, even with budget left.
    queries.clear()
    fake = FakeProvider(agent_replies("Hello.", acts(search("no_such_tool", "x"))))
    run(fake, box(lib(search_library)))
    assert sum(is_planning(r) for r in fake.requests) == 1


def test_results_of_a_round_are_graded_once_and_notes_are_kept() -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            acts(search("search_library", "cd3d"), search("search_web", "cd3d")),
            acts({"action": "answer"}),
            kept=[
                {"n": 1, "says": "CD3D is a T cell marker."},
                {"n": 3, "says": "A review of CD3D."},
            ],
        )
    )
    result = run(
        fake,
        box(
            lib(lambda q: [passage("Cell note", "CD3D marks T cells."), passage("Other", "Nope.")]),
            web(lambda q: [page("Review", "https://example.org/r", "A review of CD3D.")]),
        ),
    )
    graded = [r for r in fake.requests if is_grading_notes(r)]
    assert len(graded) == 1
    listing = str(graded[0]["messages"][-1]["content"])
    assert "[1] Cell note" in listing and "[3] Review" in listing
    assert [(f.title, f.says) for f in result.work.findings] == [
        ("Cell note", "CD3D is a T cell marker."),
        ("Review", "A review of CD3D."),
    ]
    # The planner is shown the notes on the next round.
    again = [r for r in fake.requests if is_planning(r)][1]["messages"][-1]["content"]
    assert "Notes:" in again and "[1] CD3D is a T cell marker." in again
    assert "Budget left: searches 2/4, reads 3/3" in again
    # Only what was kept is in the pool.
    assert [item.title for item in result.evidence] == ["Cell note"]


def test_a_grader_that_fails_keeps_every_result_and_no_notes() -> None:
    def replies(request):
        if is_planning(request):
            return json.dumps(acts(search("search_library", "cd3d")))
        return "not json" if is_grading_notes(request) else "CD3D marks T cells [1]."

    result = run(
        FakeProvider(replies), box(lib(lambda q: [passage("Cell note", "CD3D marks T cells.")]))
    )
    assert [item.title for item in result.evidence] == ["Cell note"]
    assert result.work is None


def reading(fetched: list[Evidence]):
    """A toolbox whose reader returns a longer text, and the readings it was asked for."""

    asked: list[int | None] = []

    def read(item: Evidence) -> str:
        asked.append(item.ref)
        return f"All of {item.title}: much more text."

    return Toolbox((lib(lambda q: fetched),), reader=read), asked


def test_only_pool_sources_can_be_read() -> None:
    toolbox, asked = reading([passage("Cell note", "CD3D marks T cells.")])
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            acts(search("search_library", "cd3d")),
            acts(
                search("read_source", "9"),
                search("read_source", "https://evil.example/secret"),
                search("read_source", "1"),
            ),
            acts({"action": "answer"}),
        )
    )
    result = run(fake, toolbox)
    # The reader only ever saw source 1; the number not in the pool and the URL the model
    # typed were refused without reaching it.
    assert asked == [1]
    errors = [step.error for step in result.steps if step.tool == "read_source"]
    assert errors[0] == "No source [9] in this conversation"
    assert errors[1] and "in this conversation" in errors[1]
    assert errors[2] is None
    prompt = writer_prompts(fake)[0]
    assert "All of Cell note: much more text." in prompt


def test_a_reader_that_fails_is_a_failed_step_and_the_answer_goes_on() -> None:
    def read(item: Evidence) -> str:
        raise ToolFailure("The web is off for this message")

    toolbox = Toolbox((lib(lambda q: [passage("Cell note", "CD3D.")]),), reader=read)
    fake = FakeProvider(
        agent_replies(
            "CD3D [1].",
            acts(search("search_library", "cd3d")),
            acts(search("read_source", "1")),
            acts({"action": "answer"}),
        )
    )
    result = run(fake, toolbox)
    assert result.steps[-1].tool == "read_source"
    assert result.steps[-1].error == "The web is off for this message"
    assert result.content == "CD3D [1]."


def test_read_source_is_not_offered_without_a_reader_or_a_pool() -> None:
    fake = FakeProvider(agent_replies("Hi.", {"action": "answer"}))
    run(fake, box(lib(lambda q: [])))
    toolbox, _ = reading([])
    run(fake, toolbox)
    plans = [str(r["messages"][-1]["content"]) for r in fake.requests if is_planning(r)]
    assert all("read_source" not in plan for plan in plans)


class Pages:
    """A resolver and fetcher for web pages, recording what was fetched."""

    def __init__(self, address: str = "93.184.216.34", body: bytes = b"<p>Full page text.</p>"):
        self.address = address
        self.body = body
        self.fetched: list[str] = []

    def resolve(self, host: str, port: int) -> list[str]:
        return [self.address]

    def fetch(self, target, *, timeout_seconds: float) -> WebFetchResponse:
        self.fetched.append(target.url)
        return WebFetchResponse(200, {"content-type": "text/html"}, self.body)


def web_app(tmp_path: Path, fake: FakeProvider, pages: Pages) -> TestClient:
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
        tavily_api_key="tvly-test-0000000000001234",
    )
    return TestClient(
        create_app(
            settings,
            model_client_factory=fake.factory,
            web_capture_fetcher=pages,
            web_capture_resolver=pages,
        )
    )


def read_a_page(tmp_path: Path, monkeypatch, pages: Pages, *, web_on: bool) -> tuple[dict, list]:
    """Find a web page in one turn, then read it further in the next."""

    monkeypatch.setattr(
        online_search.TavilySearch,
        "_post",
        lambda self, body: httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Report", "url": "https://example.org/r", "content": "A summary."}
                ]
            },
        ),
    )
    fake = FakeProvider(
        agent_replies(
            "A report exists [1].",
            acts(search("search_web", "report")),
            acts({"action": "answer"}),
            acts(search("read_source", "1")),
            acts({"action": "answer"}),
        )
    )
    with web_app(tmp_path, fake, pages) as client:
        session_id = ready_session(client)
        url = f"/api/sessions/{session_id}/messages"
        client.post(url, headers=SIDECAR, json={"content": "Any report?", "web": True})
        second = client.post(url, headers=SIDECAR, json={"content": "More?", "web": web_on})
    return second.json()["assistantMessage"], fake.requests


def test_reading_a_web_page_needs_the_web_switch(tmp_path: Path, monkeypatch) -> None:
    pages = Pages()
    reply, requests = read_a_page(tmp_path, monkeypatch, pages, web_on=False)
    [step] = [s for s in reply["context"]["steps"] if s["tool"] == "read_source"]
    assert step["error"] == "The web is off for this message"
    assert pages.fetched == []

    pages = Pages()
    (tmp_path / "on").mkdir()
    reply, requests = read_a_page(tmp_path / "on", monkeypatch, pages, web_on=True)
    [step] = [s for s in reply["context"]["steps"] if s["tool"] == "read_source"]
    assert step["error"] is None and step["found"] == 1
    assert pages.fetched == ["https://example.org/r"]
    writing = [str(r["messages"][-1]["content"]) for r in requests if not is_planning(r)
               and not is_grading_notes(r) and not is_sentence_checking(r)
               and not is_looking_up(r)]
    assert "Full page text." in writing[-1]
    # The saved citation keeps its short quote, not the page.
    assert reply["citations"][0]["quote"] == "A summary."


def test_reading_a_private_address_is_refused(tmp_path: Path, monkeypatch) -> None:
    pages = Pages(address="10.0.0.1")
    reply, _ = read_a_page(tmp_path, monkeypatch, pages, web_on=True)
    [step] = [s for s in reply["context"]["steps"] if s["tool"] == "read_source"]
    assert step["error"] == "Web capture only connects to publicly routable addresses"
    assert pages.fetched == []


def test_a_page_read_for_ask_follows_the_capture_rules() -> None:
    pages = Pages(body=b"<title>T</title><p>Hello there.</p>")
    policy = PublicWebUrlPolicy(pages)
    text, title = fetch_public_page("https://example.org/a", policy, pages)
    assert "Hello there." in text and title == "T"

    class Redirects(Pages):
        def fetch(self, target, *, timeout_seconds: float) -> WebFetchResponse:
            self.fetched.append(target.url)
            return WebFetchResponse(302, {"location": "http://192.168.0.5/x"}, b"")

    redirecting = Redirects()
    try:
        fetch_public_page("https://example.org/a", PublicWebUrlPolicy(redirecting), redirecting)
    except WebCaptureError as error:
        assert "publicly routable" in str(error)
    else:
        raise AssertionError("a redirect to a private address was followed")
    assert redirecting.fetched == ["https://example.org/a"]

    big = Pages(body=b"x" * (17 * 1024 * 1024))
    try:
        fetch_public_page("https://example.org/a", PublicWebUrlPolicy(big), big)
    except WebCaptureError as error:
        assert "too large" in str(error)
    else:
        raise AssertionError("an oversized page was read")


def read_note(tmp_path: Path, content: str, query: str) -> tuple[str, dict]:
    """Ask once over a library holding one note: it searches, then reads source 1."""

    fake = FakeProvider(
        agent_replies(
            "Answer [1].",
            acts(search("search_library", query)),
            acts(search("read_source", "1")),
            acts({"action": "answer"}),
        )
    )
    with app_for(tmp_path, fake) as client:
        base_id = client.post(
            "/api/knowledge-bases",
            headers=SIDECAR,
            json={"title": "Trial", "question": "What happened?", "description": "A trial."},
        ).json()["id"]
        client.post(
            "/api/sources",
            headers=SIDECAR,
            json={"title": "Trial", "kind": "note", "knowledgeBaseId": base_id, "content": content},
        )
        session_id = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}
        ).json()["id"]
        reply = client.post(
            f"/api/sessions/{session_id}/messages", headers=SIDECAR, json={"content": "What?"}
        ).json()["assistantMessage"]
    writing = [
        str(r["messages"][-1]["content"])
        for r in fake.requests
        if str(r["messages"][-1]["content"]).startswith("Latest message")
    ]
    return writing[-1], reply


def test_read_source_reads_the_section_around_a_passage(tmp_path: Path) -> None:
    note = (
        "# Trial\n\n## Method\n\nThe trial started with albedo 0.9.\n\nIt ran for 18 months.\n\n"
        "Panels were cleaned monthly.\n\n## Results\n\nAlbedo fell to 0.55."
    )
    prompt, reply = read_note(tmp_path, note, "started with albedo")
    # The passage is the first of its section; the block two further on is read too, and
    # the next section is not.
    assert "Panels were cleaned monthly." in prompt
    assert "Albedo fell to 0.55." not in prompt
    assert reply["context"]["steps"][-1]["found"] == 1
    # What is saved with the answer is still the short passage.
    assert reply["citations"][0]["quote"] == "The trial started with albedo 0.9."


def test_read_source_without_heading_paths_reads_nearby_blocks(tmp_path: Path) -> None:
    note = "\n\n".join(f"Paragraph {n}." for n in range(1, 10)).replace(
        "Paragraph 5.", "Paragraph 5 has the zebra fact."
    )
    prompt, _ = read_note(tmp_path, note, "zebra fact")
    for n in (2, 3, 4, 6, 7, 8):
        assert f"Paragraph {n}." in prompt
    assert "Paragraph 1." not in prompt and "Paragraph 9." not in prompt


def test_a_saved_answer_keeps_what_each_source_said(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            acts(search("search_library", "CD3D")),
            acts({"action": "answer"}),
            kept=[{"n": 1, "says": "CD3D marks T cells."}],
        )
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        reply = client.post(
            f"/api/sessions/{session_id}/messages", headers=SIDECAR, json={"content": "CD3D?"}
        ).json()["assistantMessage"]
    assert reply["context"]["work"] == {
        "subQuestions": [],
        "findings": [{"ref": 1, "serves": "", "says": "CD3D marks T cells."}],
    }


# Skills ---------------------------------------------------------------------------------------


def system_of(request: dict) -> str:
    return str(request["messages"][0]["content"])


def writing_system(fake: FakeProvider) -> str:
    systems = (system_of(r) for r in fake.requests)
    return next(text for text in systems if text.startswith("You are Gunther, a research"))


COMPARE = ask_skills()["compare"]
METHOD = "Method (from the skill “Compare sources”)"


def test_a_skill_adds_its_method_to_the_planner_and_writer() -> None:
    fake = FakeProvider(
        agent_replies(
            "A says 18% [1]. B says 9% [1].",
            {"action": "search_library", "query": "cooling"},
            {"action": "answer"},
        )
    )
    result = run(fake, box(lib(lambda q: [passage("Trial", "18%")])), skill=COMPARE)
    planner = next(system_of(r) for r in fake.requests if is_planning(r))
    assert METHOD in planner and "Find each position's own source first" in planner
    assert "Never merge two sources" not in planner  # the writing section is not the planner's
    assert "If the method conflicts with the rules above, the rules win." in planner
    writer = writing_system(fake)
    assert METHOD in writer and "Never merge two sources into one claim" in writer
    assert writer.index("You are Gunther") < writer.index(METHOD)
    assert result.skill is COMPARE and not result.skill_auto


def test_a_skill_with_no_budgets_uses_asks_default_one() -> None:
    from gunther.skillbook import AskSkill

    assert COMPARE.budget() == Budget() and COMPARE.budget("deep") == Budget()
    own = AskSkill(
        "x", 1, "xx", "X", "x", False, {"standard": Budget(searches=2), "deep": Budget(searches=9)},
        "", {}, (),
    )  # fmt: skip
    assert own.budget().searches == 2 and own.budget("deep").searches == 9


ASKED_CHECKLIST = "each numbered item of this checklist"
CHECKED_ANSWER = "Harlow found 18% [1]. Okafor found 9% [1]."


def method_check(passed: tuple[bool, ...], sentences: list[dict] | None = None):
    """A sentence checker that answers the checklist only when it is asked for it."""

    def reply(request: dict) -> dict:
        found = {"sentences": sentences or []}
        if ASKED_CHECKLIST in system_of(request):
            found["checklist"] = [
                {"item": n, "passed": ok, "why": "" if ok else f"item {n} is missing"}
                for n, ok in enumerate(passed, start=1)
            ]
        return found

    return reply


def skilled(answer, checked, revised=None, plan=None, **kwargs):
    fake = FakeProvider(
        agent_replies(
            answer,
            plan or {"action": "search_library", "query": "x"},
            {"action": "answer"},
            checked=checked,
            revised=revised,
        )
    )
    result = run(fake, box(lib(lambda q: [passage("Trial", "18%")])), skill=COMPARE, **kwargs)
    return fake, result


def test_a_failed_checklist_item_revises_once_then_checks_sentences_again() -> None:
    fake, result = skilled(
        CHECKED_ANSWER,
        method_check((True, False, True)),
        revised=f"{CHECKED_ANSWER} The weather adjustment explains it [1].",
    )
    assert result.content.endswith("The weather adjustment explains it [1].")
    checks = [r for r in fake.requests if is_sentence_checking(r)]
    assert len(checks) == 2
    assert ASKED_CHECKLIST in system_of(checks[0])
    assert ASKED_CHECKLIST not in system_of(checks[1])
    assert "weather adjustment" in str(checks[1]["messages"][-1]["content"])
    [revising] = [r for r in fake.requests if is_revising(r)]
    prompt = str(revising["messages"][-1]["content"])
    assert "Answer:\n" + CHECKED_ANSWER in prompt
    assert "Items not passed:\n- Every disagreement names a likely reason" in prompt
    assert "item 2 is missing" in prompt and "The pool:" in prompt
    labels = [step.label for step in result.steps]
    assert labels[-2:] == ["Checked the method", "Revised to follow the method"]
    assert result.checked


def test_an_answer_that_passes_the_checklist_is_not_revised() -> None:
    fake, result = skilled(CHECKED_ANSWER, method_check((True, True, True)))
    assert not any(is_revising(r) for r in fake.requests)
    assert sum(is_sentence_checking(r) for r in fake.requests) == 1
    assert [step.label for step in result.steps][-1] == "Checked the method"


def test_an_answer_is_revised_at_most_once() -> None:
    # Whatever the second check says, it does not ask for the checklist, so no second go.
    fake, _ = skilled(CHECKED_ANSWER, method_check((False, False, False)), revised="Better [1].")
    assert sum(is_revising(r) for r in fake.requests) == 1
    assert sum(is_sentence_checking(r) for r in fake.requests) == 2


def test_a_reviser_that_cannot_answer_leaves_the_answer_and_says_so() -> None:
    _, result = skilled(
        CHECKED_ANSWER,
        method_check((False, True, True)),
        revised=lambda r: api_error(openai.BadRequestError, 400, "no"),
    )
    assert result.content == CHECKED_ANSWER
    assert any(n.startswith("The answer was not revised to follow") for n in result.notes)
    assert result.steps[-1].label == "Revised to follow the method" and result.steps[-1].error


def test_a_skill_answer_is_always_checked() -> None:
    # A plan that only reworks text skips the checker; with a skill it does not.
    answer = {"action": "answer", "new_facts": False}
    fake, result = skilled("Shorter: 18% [?].", method_check((True, True, True)), plan=answer)
    assert result.checked and any(is_sentence_checking(r) for r in fake.requests)
    assert "add no new facts" not in writer_prompts(fake)[0]


def test_a_rework_reply_without_a_skill_is_still_not_checked() -> None:
    fake = FakeProvider(agent_replies("Shorter.", {"action": "answer", "new_facts": False}))
    result = run(fake, box(lib(lambda q: [])))
    assert not result.checked and result.skill is None


def test_without_a_skill_nothing_about_a_method_is_sent() -> None:
    fake = FakeProvider(agent_replies("Hi!", {"action": "answer"}))
    run(fake, box(lib(lambda q: [])), skills={})
    for request in fake.requests:
        assert "Method (from" not in system_of(request)
        assert "Skills you may use" not in system_of(request)


def test_auto_match_picks_a_skill_when_it_is_offered_and_plans_again_with_it() -> None:
    searched: list[str] = []
    fake = FakeProvider(
        agent_replies(
            "A [1]. B [1].",
            {"action": "search_library", "query": "ignored", "skill": "compare"},
            {"action": "search_library", "query": "Harlow vs Okafor"},
            {"action": "answer"},
        )
    )
    toolbox = box(lib(lambda q: searched.append(q) or [passage("Trial", "18%")]))
    result = run(fake, toolbox, skills=ask_skills())
    planners = [system_of(r) for r in fake.requests if is_planning(r)]
    assert "Skills you may use" in planners[0] and "- compare: " in planners[0]
    assert METHOD not in planners[0] and METHOD in planners[1]
    assert "Skills you may use" not in planners[1]
    assert searched == ["Harlow vs Okafor"]  # the first plan's actions never ran
    assert result.skill is COMPARE and result.skill_auto
    assert METHOD in writing_system(fake)


def test_auto_match_ignores_a_skill_it_does_not_know() -> None:
    fake = FakeProvider(
        agent_replies("A.", {"action": "answer", "skill": "poetry"}, {"action": "answer"})
    )
    result = run(fake, box(lib(lambda q: [])), skills=ask_skills())
    assert result.skill is None and sum(is_planning(r) for r in fake.requests) == 1


def test_a_chosen_skill_is_not_second_guessed_by_auto_match() -> None:
    fake = FakeProvider(agent_replies("A.", {"action": "answer", "skill": "compare"}))
    result = run(fake, box(lib(lambda q: [])), skill=COMPARE, skills=ask_skills())
    assert not result.skill_auto
    assert not any("Skills you may use" in system_of(r) for r in fake.requests)


def ask(client: TestClient, session_id: str, **body):
    return client.post(
        f"/api/sessions/{session_id}/messages",
        headers=SIDECAR,
        json={"content": "Compare them", **body},
    )


def test_unknown_skill_is_refused(tmp_path: Path) -> None:
    fake = FakeProvider(agent_replies("x", {"action": "answer"}))
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        asked = len(fake.requests)
        refused = ask(client, session_id, skill="nope")
        assert refused.status_code == 400 and refused.json()["detail"] == "Unknown skill: /nope"
        assert len(fake.requests) == asked  # refused before any model was asked
        assert ask(client, session_id, budget="huge").status_code == 422


def test_the_skills_menu_lists_every_ask_skill(tmp_path: Path) -> None:
    with app_for(tmp_path, FakeProvider("x")) as client:
        listed = client.get("/api/ask/skills", headers=SIDECAR).json()
    assert [item["command"] for item in listed] == ["compare", "research"]
    assert listed[0]["title"] == "Compare sources" and listed[0]["budgets"] == []
    assert listed[1]["title"] == "Deep research" and listed[1]["budgets"] == ["standard", "deep"]


def test_an_answer_keeps_the_skill_it_followed(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "A [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
            checked=method_check((True, True, True)),
        )
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        answer = ask(client, session_id, skill="compare").json()["assistantMessage"]
        plain = ask(client, session_id).json()["assistantMessage"]
    assert answer["context"]["skill"] == {
        "name": "compare",
        "version": 1,
        "title": "Compare sources",
        "auto": False,
    }
    assert plain["context"]["skill"] is None


def test_auto_match_is_off_by_default(tmp_path: Path) -> None:
    fake = FakeProvider(agent_replies("A.", {"action": "answer", "skill": "compare"}))
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        answer = ask(client, session_id).json()["assistantMessage"]
    assert not any("Skills you may use" in system_of(r) for r in fake.requests)
    assert answer["context"]["skill"] is None


def test_auto_match_picks_a_skill_when_on(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "A [1].",
            {"action": "answer", "skill": "compare"},
            {"action": "answer"},
            checked=method_check((True, True, True)),
        )
    )
    with app_for(tmp_path, fake, ask_auto_skills=True) as client:
        session_id = ready_session(client)
        answer = ask(client, session_id).json()["assistantMessage"]
    assert any("Skills you may use" in system_of(r) for r in fake.requests)
    assert answer["context"]["skill"]["name"] == "compare"
    assert answer["context"]["skill"]["auto"] is True


# Research -------------------------------------------------------------------------------------

RESEARCH = ask_skills()["research"]
FRAME = {
    "clear": True,
    "core_question": "How well do cool roofs work in Velmora?",
    "sub_questions": [
        {"text": "What did the trial measure?", "query": "trial results"},
        {"text": "Do other sources disagree?", "query": "reanalysis"},
        {"text": "What are the downsides?", "query": "downsides"},
    ],
    "done_when": "the energy saving is supported by two sources",
}


def doc(query: str, n: int = 1) -> list[Evidence]:
    """Results that say which query found them, so a grader can tell what they serve."""

    return [passage(f"Doc {query} {k}", f"About {query} {k}.") for k in range(1, n + 1)]


def serving(request: dict) -> str:
    """A grader that keeps every result and says it serves the sub-question its query names."""

    import re

    prompt = str(request["messages"][-1]["content"])
    queries = {"trial results": "q1", "reanalysis": "q2", "downsides": "q3"}
    keep = []
    for n, title in re.findall(r"^\[(\d+)\] (.*?):", prompt.split("Results:")[-1], flags=re.M):
        query = title.removeprefix("Doc ").rsplit(" ", 1)[0]
        keep.append({"n": int(n), "serves": queries.get(query, ""), "says": f"{title} says so"})
    return json.dumps({"keep": keep})


def researched(*plans, framed=None, library=None, web_tool=None, **kwargs):
    fake = FakeProvider(
        agent_replies(
            "The trial found 18% [1].",
            *(plans or ({"action": "answer"},)),
            framed=FRAME if framed is None else framed,
            kept=serving,
        )
    )
    tools = [lib(library or (lambda q: doc(q)))]
    if web_tool:
        tools.append(web_tool)
    events: list[dict] = []
    result = run(
        fake, box(*tools), "Research cool roofs", skill=RESEARCH, events=events.append, **kwargs
    )
    return fake, result, events


def test_an_ambiguous_research_question_asks_first_and_searches_nothing() -> None:
    searched: list[str] = []

    def search(query: str) -> list[Evidence]:
        searched.append(query)
        return doc(query)

    fake = FakeProvider(
        agent_replies(
            "never written",
            framed={"clear": False, "ask_user": ["Which options?", "For which years?"]},
        )
    )
    events: list[dict] = []
    result = run(
        fake, box(lib(search)), "Compare the options", skill=RESEARCH, events=events.append
    )
    assert searched == [] and result.evidence == [] and result.error is None
    assert result.content == "- Which options?\n- For which years?"
    assert result.research["state"] == "asking" and result.research["budget"] == "standard"
    assert not any(is_planning(r) for r in fake.requests) and writer_prompts(fake) == []
    assert not any(e["type"] == "research_plan" for e in events)
    assert not result.checked and result.work is None


def test_the_framer_knows_what_the_library_is_about() -> None:
    # Without it, "cool roofs in Velmora" was answered with "Which Velmora do you mean?".
    fake = FakeProvider(
        agent_replies("never written", framed={"clear": False, "ask_user": ["Which?"]})
    )
    run(fake, box(lib(lambda q: [])), "How well do cool roofs work here?", skill=RESEARCH)
    [framing] = [r for r in fake.requests if is_framing(r)]
    prompt = str(framing["messages"][-1]["content"])
    assert "What can be searched:" in prompt
    assert "- search_library: the user's library, 3 sources" in prompt


def test_one_question_back_is_shown_as_it_is() -> None:
    fake = FakeProvider(agent_replies("x", framed={"clear": False, "ask_user": ["Which city?"]}))
    result = run(fake, box(lib(lambda q: [])), "Compare", skill=RESEARCH)
    assert result.content == "Which city?"


def test_a_failed_framing_is_the_answer_and_nothing_is_searched() -> None:
    searched: list[str] = []
    fake = FakeProvider(
        agent_replies(
            "x", framed=lambda r: api_error(openai.AuthenticationError, 401, "The key was refused")
        )
    )
    result = run(fake, box(lib(lambda q: searched.append(q) or [])), skill=RESEARCH)
    assert "API key" in (result.error or "") and searched == [] and result.content == ""
    assert writer_prompts(fake) == [] and not any(is_planning(r) for r in fake.requests)


def test_a_framing_with_no_sub_questions_is_an_error_not_a_made_up_plan() -> None:
    fake = FakeProvider(agent_replies("x", framed={"clear": True, "sub_questions": []}))
    result = run(fake, box(lib(lambda q: [])), skill=RESEARCH)
    assert result.error and result.content == ""


def test_research_searches_every_sub_question_first_then_plans() -> None:
    searched: list[str] = []

    def search(query: str) -> list[Evidence]:
        searched.append(query)
        return doc(query)

    fake, result, events = researched(
        {"action": "search_library", "query": "more on downsides"},
        {"action": "answer"},
        library=search,
    )
    assert searched[:3] == ["trial results", "reanalysis", "downsides"]
    assert searched[3:] == ["more on downsides"]
    order = ["frame" if is_framing(r) else "grade" if is_grading_notes(r) else "plan"
             for r in fake.requests if is_framing(r) or is_grading_notes(r) or is_planning(r)]
    assert order[:3] == ["frame", "grade", "plan"]  # one grading round, then the first plan
    grading = next(r for r in fake.requests if is_grading_notes(r))
    assert "q1: What did the trial measure?" in str(grading["messages"][-1]["content"])
    planner = next(system_of(r) for r in fake.requests if is_planning(r))
    assert "This is a research question." in planner
    assert "Method (from the skill “Deep research”)" in planner and "Search wide first" in planner
    prompt = str(next(r for r in fake.requests if is_planning(r))["messages"][-1]["content"])
    assert "Core question: How well do cool roofs work in Velmora?" in prompt
    assert "Done when: the energy saving is supported by two sources" in prompt
    assert "q1 (one source): What did the trial measure?" in prompt
    plan = next(e for e in events if e["type"] == "research_plan")
    assert plan["coreQuestion"] == FRAME["core_question"] and plan["budget"] == "standard"
    assert [q["id"] for q in plan["subQuestions"]] == ["q1", "q2", "q3"]
    assert plan["limits"] == {"searches": 8, "reads": 4}
    assert result.research["state"] == "done"
    assert result.research["used"] == {"searches": 4, "reads": 0}
    assert [s.id for s in result.work.sub_questions] == ["q1", "q2", "q3"]
    writer = writer_prompts(fake)[0]
    assert "This is a research answer." in writer and "q2: Do other sources disagree?" in writer
    assert result.checked and any(is_sentence_checking(r) for r in fake.requests)


def test_the_web_fills_only_sub_questions_the_library_left_thin() -> None:
    web_queries: list[str] = []

    def library(query: str) -> list[Evidence]:
        return doc(query, 2 if query == "trial results" else 1 if query == "reanalysis" else 0)

    def search_web(query: str) -> list[Evidence]:
        web_queries.append(query)
        return [page(f"Web {query}", f"https://example.org/{len(web_queries)}", "web text")]

    fake, result, events = researched(
        library=library, web_tool=web(search_web)
    )
    assert web_queries == ["reanalysis", "downsides"]  # q1 has two library findings
    assert sum(is_grading_notes(r) for r in fake.requests) == 2  # library round, web round
    assert result.research["used"]["searches"] == 5


def test_with_no_web_tool_the_web_is_never_searched() -> None:
    fake, result, events = researched()
    assert not any(step.tool == "search_web" for step in result.steps)
    assert not any(e.get("tool") == "search_web" for e in events)


def test_a_sub_question_never_repeats_a_search() -> None:
    searched: list[str] = []
    same = {"clear": True, "sub_questions": [
        {"text": "A?", "query": "same words"}, {"text": "B?", "query": "same words"}
    ], "core_question": "c", "done_when": "d"}
    fake, result, _ = researched(
        framed=same, library=lambda q: searched.append(q) or doc(q)
    )
    assert searched == ["same words"]


def test_research_stays_within_its_budget() -> None:
    for name, searches in (("standard", 8), ("deep", 16)):
        searched: list[str] = []

        def search(query: str, searched=searched) -> list[Evidence]:
            searched.append(query)
            return doc(query)

        plans = [{"action": "search_library", "query": f"narrow {n}"} for n in range(30)]
        fake, result, events = researched(*plans, library=search, budget_name=name)
        assert len(searched) == searches, name
        assert result.research["budget"] == name
        assert result.research["limits"]["searches"] == searches
        last = [e for e in events if e["type"] == "step"][-1]
        assert last["used"]["searches"] == [searches, searches] and last["used"]["reads"][1] == (
            4 if name == "standard" else 8
        )
        gathering = sum(
            is_framing(r) or is_planning(r) or is_grading_notes(r) for r in fake.requests
        )
        assert gathering <= (30 if name == "standard" else 50)


def test_a_deep_budget_allows_more_sub_questions_than_a_standard_one() -> None:
    many = {**FRAME, "sub_questions": [{"text": f"Q{n}?", "query": f"q {n}"} for n in range(8)]}
    _, standard, _ = researched(framed=many)
    _, deep, _ = researched(framed=many, budget_name="deep")
    assert len(standard.work.sub_questions) == 5 and len(deep.work.sub_questions) == 8


def test_the_framer_is_told_how_many_sub_questions_to_make() -> None:
    fake, _, _ = researched()
    framing = next(system_of(r) for r in fake.requests if is_framing(r))
    assert framing.startswith("You are the framing step")
    assert "split the question into 3–5 sub-questions" in framing
    assert "Method (from the skill “Deep research”)" in framing


def test_stopping_during_gathering_writes_from_what_was_found() -> None:
    asked = {"n": 0}

    def stopping() -> bool:
        asked["n"] += 1
        return asked["n"] > 2  # frame, then the first search go on; the second is stopped

    searched: list[str] = []

    def search(query: str) -> list[Evidence]:
        searched.append(query)
        return doc(query)

    fake, result, _ = researched(library=search, stopping=stopping)
    assert searched == ["trial results"]
    assert result.research["state"] == "stopped_early"
    assert "Stopped early: written from what was found so far." in result.notes
    assert result.content and result.evidence  # written, from the one result it had
    assert sum(is_grading_notes(r) for r in fake.requests) == 0  # no call spent on grading
    assert not any(is_planning(r) for r in fake.requests)


def test_a_normal_question_is_not_research() -> None:
    fake = FakeProvider(agent_replies("A [1].", {"action": "search_library"}, {"action": "answer"}))
    result = run(fake, box(lib(lambda q: doc(q))))
    assert result.research is None and not any(is_framing(r) for r in fake.requests)


def held_research(client: TestClient, gate: threading.Event):
    """A turn that starts writing, then pauses until ``gate`` opens (like held_answers)."""

    service = client.app.state.knowledge_service
    real = service.create_session_turn

    def answer(session_id, payload, hear, stopping=None):
        hear({"type": "step", "state": "running", "tool": "search_library", "label": "Library"})
        hear({"type": "text", "text": "Part "})
        hear({"type": "text", "text": "one "})
        assert gate.wait(10)
        hear({"type": "text", "text": "part two."})
        return real(
            session_id, payload, lambda event: hear(event) if event["type"] == "saving" else None
        )

    service.create_session_turn = answer


def test_stop_during_writing_still_drops_a_research_answer(tmp_path: Path) -> None:
    fake = FakeProvider("unused")
    gate = threading.Event()
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        held_research(client, gate)
        runs = client.app.state.answer_runs
        first, first_events = in_background(
            lambda: stream(
                client, "POST", f"/api/sessions/{session_id}/messages/stream",
                json={"content": "Why?", "skill": "research"},
            )
        )
        wait_for(lambda: runs.get(session_id) is not None and len(runs.get(session_id).events) >= 3)
        client.post(f"/api/sessions/{session_id}/answer/stop", headers=SIDECAR)
        gate.set()
        first.join(10)
        saved = client.get(f"/api/sessions/{session_id}", headers=SIDECAR).json()
    assert first_events[0][-1][0] == "stopped"
    [message] = saved["messages"]
    assert message["context"]["interrupted"] == "stopped"


def test_stop_during_research_writes_from_what_was_found(tmp_path: Path) -> None:
    gate, started = threading.Event(), threading.Event()

    def framed(request: dict) -> str:
        started.set()
        assert gate.wait(10)
        return json.dumps(FRAME)

    fake = FakeProvider(
        agent_replies("Written from little [1].", framed=framed, checked=None)
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        first, first_events = in_background(
            lambda: stream(
                client, "POST", f"/api/sessions/{session_id}/messages/stream",
                json={"content": "Research cool roofs", "skill": "research", "budget": "deep"},
            )
        )
        assert started.wait(10)
        assert client.post(
            f"/api/sessions/{session_id}/answer/stop", headers=SIDECAR
        ).status_code == 204
        gate.set()
        first.join(15)
    [events] = first_events
    names = [name for name, _ in events]
    assert names[-1] == "done" and "stopped" not in names and "research_plan" in names
    answer = events[-1][1]["assistantMessage"]
    assert answer["content"].startswith("Written from little")
    research = answer["context"]["research"]
    assert research["state"] == "stopped_early" and research["budget"] == "deep"
    assert research["used"] == {"searches": 0, "reads": 0}
    assert "Stopped early: written from what was found so far." in answer["context"]["notes"]


def test_the_research_state_is_kept_with_the_saved_answer(tmp_path: Path) -> None:
    fake = FakeProvider(agent_replies("Cool roofs help [1].", framed=FRAME, kept=serving))
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        answer = ask(client, session_id, skill="research").json()["assistantMessage"]
    research = answer["context"]["research"]
    assert research["state"] == "done" and research["coreQuestion"] == FRAME["core_question"]
    assert research["doneWhen"] == FRAME["done_when"]
    assert answer["context"]["skill"]["name"] == "research"
    assert [q["id"] for q in answer["context"]["work"]["subQuestions"]] == ["q1", "q2", "q3"]
    plain = ask(client, session_id).json()["assistantMessage"]
    assert plain["context"]["research"] is None


def test_an_asking_answer_is_saved_and_the_next_turn_can_answer_it(tmp_path: Path) -> None:
    first = {"clear": False, "ask_user": ["Which options?"]}
    replies = [first, FRAME]
    fake = FakeProvider(
        agent_replies("Cool roofs help [1].", framed=lambda r: json.dumps(replies.pop(0)))
    )
    with app_for(tmp_path, fake) as client:
        session_id = ready_session(client)
        asking = ask(client, session_id, skill="research").json()["assistantMessage"]
        assert asking["content"] == "Which options?"
        assert asking["context"]["research"]["state"] == "asking" and asking["citations"] == []
        done = ask(client, session_id, skill="research", content="Cool roofs and green roofs")
    assert done.json()["assistantMessage"]["context"]["research"]["state"] == "done"
    framing = [r for r in fake.requests if is_framing(r)][-1]
    roles = [m["role"] for m in framing["messages"]]
    assert "assistant" in roles  # the second framing sees the question it asked


def test_auto_match_that_picks_research_frames_the_question_before_searching() -> None:
    searched: list[str] = []

    def search(query: str) -> list[Evidence]:
        searched.append(query)
        return doc(query)

    fake = FakeProvider(
        agent_replies(
            "Cool roofs help [1].",
            {"action": "search_library", "query": "ignored", "skill": "research"},
            {"action": "answer"},
            framed=FRAME,
            kept=serving,
        )
    )
    result = run(fake, box(lib(search)), skills=ask_skills())
    assert result.skill is RESEARCH and result.skill_auto
    assert searched == ["trial results", "reanalysis", "downsides"]  # not the first plan's search
    assert result.research["state"] == "done" and len(result.work.sub_questions) == 3
