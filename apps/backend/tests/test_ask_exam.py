"""The Ask exam (evals/ask): its cases, its scoring, and one run with stand-in models."""

import json
from pathlib import Path

import pytest
from fake_models import FakeProvider, agent_replies
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from evals.ask import score
from evals.ask.run import Configuration, Options, run_case, run_exam


def case(**fields) -> score.Case:
    return score.Case(id="X1", category="lookup", question="Q?", **fields)


def outcome(answer: str, *, steps=(), titles=(), **context) -> score.Outcome:
    return score.Outcome(
        answer=answer,
        citations=[
            {"sourceTitle": title, "kind": "library", "ref": number}
            for number, title in enumerate(titles, start=1)
        ],
        context={"steps": list(steps), **context},
    )


@pytest.mark.parametrize(
    ("terminal", "failure", "line_end"),
    [
        ("done", "", "\n"),
        ("done", "", "\r"),
        ("done", "", "\r\n"),
        ("error", "Ask error 400. Invalid question", "\r\n"),
        ("stopped", "The answer was stopped.", "\r\n"),
        (None, "The answer stream ended without a terminal event.", "\r\n"),
    ],
    ids=["done-lf", "done-cr", "done-crlf", "error", "stopped", "no-terminal"],
)
def test_runner_requires_a_saved_turn_from_the_answer_stream(terminal, failure, line_end):
    app = FastAPI()
    asked, refreshed, traced = [], [], []
    answer = "First line\u2028Second line\u2029Next paragraph"

    @app.post("/api/knowledge-bases/library/sessions")
    def session():
        return {"id": "session"}

    @app.post("/api/sessions/session/messages/stream")
    def stream(payload: dict):
        asked.append(payload["content"])
        chunks = [line_end.join([
            ": keepalive", "", "event: text", 'data: {"text":"Draft only"}', "", "",
        ])]
        data = {
            "done": {"assistantMessage": {
                "id": "message", "content": answer, "citations": [], "context": {},
            }},
            "error": {"status": 400, "detail": "Invalid question"},
            "stopped": {},
        }
        if terminal:
            lines = json.dumps(data[terminal], ensure_ascii=False, indent=2).split("\n")
            chunks.append(line_end.join([
                f"event: {terminal}", *(f"data: {line}" for line in lines), "", "",
            ]))
        return StreamingResponse(iter(chunks), media_type="text/event-stream")

    @app.post("/api/sessions/session/brief/refresh")
    def refresh():
        refreshed.append(len(asked))
        return {}

    @app.get("/api/sessions/session/messages/message/trace")
    def trace():
        traced.append(asked[-1])
        return {"steps": [{"usage": {"total_tokens": 19}}]}

    config = Configuration("fake/model", None, "low", "off")
    with TestClient(app) as client:
        result = run_case(client, {}, "library", case(history=("Earlier?",)), config)

    if failure:
        assert result.failed == failure and not result.answer
        assert asked == ["Earlier?"] and refreshed == traced == []
    else:
        assert not result.failed and result.answer == answer
        assert (result.calls, result.tokens) == (1, 19)
        assert asked == ["Earlier?", "Q?"] and refreshed == [1, 2] and traced == ["Q?"]


LOOKED_UP = {"tool": "search_library", "label": "Searched your library for: x"}
CHECKED = {"tool": "search_library", "label": "Checked your library for a claim: x"}


def test_cases_file_loads_and_every_case_is_valid() -> None:
    cases = score.load_cases()
    assert len(cases) == 29
    assert len({c.id for c in cases}) == 29
    assert {c.category for c in cases} <= score.CATEGORIES
    # Every title a case names is a note of the demo library.
    titles = set(score.library_texts())
    assert len(titles) == 10
    for c in cases:
        assert set(c.cite_any) <= titles and set(c.must_not_cite) <= titles | {"*"}
    # Every case carries every field of the contract, even if it is left empty.
    raw = score.CASES_FILE.read_text(encoding="utf-8")
    assert raw.count("[[case]]") == 29
    for field in score.Case.__dataclass_fields__:
        assert raw.count(f"\n{field} = ") == 29, field


def test_demo_library_keeps_its_only_here_facts_in_one_file() -> None:
    texts = score.library_texts()
    only_here = {
        "€14 per square metre": "Velmora Climate Plan 2026",
        "+1.2%": "The winter heating penalty of cool roofs (Brandt 2022)",
        "0.55": "Velmora Cool Roof Trial (Harlow 2024)",
        "dust and soot": "Velmora Cool Roof Trial (Harlow 2024)",
    }
    for fact, title in only_here.items():
        assert [t for t, text in texts.items() if fact in text] == [title], fact


def test_deterministic_checks() -> None:
    lookup = case(search="must", cite_any=("A",), must_not_cite=("Trap",), max_unverified=1)
    good = outcome("Fact [1].", steps=[LOOKED_UP], titles=["A"])
    assert all(score.checks(lookup, good).values())

    # Only claim checks: the question itself was not looked up.
    only_checks = outcome("Fact [1].", steps=[CHECKED], titles=["A"])
    assert score.checks(lookup, only_checks)["searched"] is False
    assert score.checks(case(search="must_not"), only_checks)["searched"] is True

    trapped = outcome("Fact [1][2].", steps=[LOOKED_UP], titles=["A", "Trap"])
    assert score.checks(lookup, trapped)["no_trap"] is False
    anything = case(must_not_cite=("*",))
    assert score.checks(anything, trapped)["no_trap"] is False
    assert score.checks(anything, outcome("None.", titles=[]))["no_trap"] is True

    assert (
        score.checks(lookup, outcome("Fact [?] and more [?].", titles=["A"]))["unverified"] is False
    )
    assert score.checks(case(), outcome("x", modelError="boom"))["error"] is False
    assert (
        score.checks(case(must_not_contain=("Albedo",)), outcome("the albedo"))["without 'Albedo'"]
        is False
    )
    web = score.Outcome(answer="x", citations=[{"kind": "web", "sourceTitle": "p", "ref": 1}])
    assert score.checks(case(cite_web=True), web)["cited_web"] is True


def test_words_count_chinese_characters_one_by_one() -> None:
    assert score.word_count("one two three") == 3
    assert score.word_count("广州试点 pilot") == 5
    long_chinese = "试" * 41
    assert score.checks(case(max_words=40), outcome(long_chinese))["words"] is False
    assert score.checks(case(max_words=40), outcome("试" * 40))["words"] is True


def test_sentences_keep_citations_after_the_full_stop_with_their_sentence() -> None:
    assert score.sentences("Roofs help. [1] They cost money [2]. Done.") == [
        "Roofs help. [1]",
        "They cost money [2].",
        "Done.",
    ]


def test_ask_scoring_counts_unverified_marks_in_prose():
    answer = "See `a[?]` and $$x[?]$$. Fact [?].\n```\ncode[?]\n```"
    assert score.unverified(answer) == 1
    assert score.sentences(answer) == ["See `a[?]` and $$x[?]$$.", "Fact [?]."]


def test_judgement_decides_pass() -> None:
    quiet = score.Judgement(
        support=[score.SupportVerdict(n=1, verdict="supported")], mentioned=[True]
    )
    needing = case(must_mention=("a point",))
    assert score.failures(needing, {"answered": True}, quiet) == []

    one_not = score.Judgement(support=[score.SupportVerdict(n=1, verdict="not")], mentioned=[True])
    assert score.failures(needing, {}, one_not) == ["support"]
    assert score.failures(
        needing, {}, score.Judgement(unmarked=["A claim."], mentioned=[True])
    ) == ["unmarked"]
    assert score.failures(needing, {}, score.Judgement(mentioned=[False])) == ["mentions"]
    assert score.failures(needing, {}, score.Judgement()) == ["mentions"]
    claims = case(must_not_claim=("a falsehood",))
    assert score.failures(claims, {}, score.Judgement(claimed=[True])) == ["claims"]
    assert score.failures(claims, {}, None) == ["judge"]
    # A failed deterministic check fails the case even when the judge is happy.
    assert score.failures(needing, {"searched": False}, quiet) == ["searched"]


def test_judge_prompt_shows_whole_demo_files_and_web_quotes() -> None:
    library = {"Note": "# Note\nThe whole file text."}
    found = score.Outcome(
        answer="Roofs help [1]. Online too [2].",
        citations=[
            {"kind": "library", "sourceTitle": "Note", "quote": "a short quote", "ref": 1},
            {"kind": "web", "sourceTitle": "Page", "quote": "a web quote", "ref": 2},
        ],
    )
    prompt = score.judge_prompt(case(must_mention=("m",), must_not_claim=("c",)), found, library)
    assert "The whole file text." in prompt and "a short quote" not in prompt
    assert "a web quote" in prompt and "1. Roofs help [1]." in prompt


def test_judge_prompt_numbers_sources_as_the_answer_does_not_by_pool_id() -> None:
    # A follow-up: the answer's [1] and [2] are the conversation's sources 7 and 3, and two
    # passages come from the same demo file.
    library = {"Note": "# Note\nThe whole file text.", "Other": "# Other\nOther text."}
    found = score.Outcome(
        answer="First [1]. Second [2]. Third [3].",
        citations=[
            {"kind": "library", "sourceTitle": "Note", "quote": "q1", "ref": 7},
            {"kind": "library", "sourceTitle": "Other", "quote": "q2", "ref": 3},
            {"kind": "library", "sourceTitle": "Note", "quote": "q3", "ref": 9},
        ],
    )
    prompt = score.judge_prompt(case(), found, library)
    assert "[1][3] Note\n# Note\nThe whole file text." in prompt
    assert "[2] Other\n# Other" in prompt
    assert "[7]" not in prompt and "[9]" not in prompt
    assert prompt.count("The whole file text.") == 1


JUDGEMENT = {
    "support": [{"n": 1, "verdict": "supported"}],
    "unmarked": [],
    "mentioned": [True, True],
    "claimed": [],
}


def run_with_fakes(tmp_path: Path, options: Options, extra: str = ""):
    library = tmp_path / "library"
    library.mkdir()
    (library / "trial.md").write_text(
        "# The trial\n\nThe trial covered 412 buildings over three summers.\n", encoding="utf-8"
    )
    (library / "plan.md").write_text(
        "# The plan\n\nThe plan wants 30% of roofs coated by 2030.\n", encoding="utf-8"
    )
    cases = tmp_path / "cases.toml"
    cases.write_text(
        """
[[case]]
id = "L1"
category = "lookup"
question = "How many buildings were in the trial?"
search = "must"
cite_any = ["The trial"]
must_mention = ["412"]

[[case]]
id = "R1"
category = "rework"
history = ["What did the trial cover?"]
question = "Shorten that."
search = "must_not"
max_unverified = 0
"""
        + extra,
        encoding="utf-8",
    )

    def reply(request):
        system = str(request["messages"][0]["content"])
        if system.startswith("Extract explicit, evidence-backed knowledge assertions."):
            return "Analysis unavailable in this fixture."
        if system.startswith("You grade one answer"):
            return json.dumps(JUDGEMENT)
        return agent_replies(
            "The trial covered 412 buildings [1].",
            {"action": "search_library", "query": "trial"},
            {"action": "answer"},
        )(request)

    fake = FakeProvider(reply)
    messages = []
    folder = run_exam(
        options,
        model_client_factory=fake.factory,
        overrides={
            "deepseek_api_key": "sk-test-0000000000001234",
            "stt_provider": "compatible",
            "tavily_api_key": None,
        },
        results_root=tmp_path / "results",
        library_dir=library,
        cases_file=cases,
        say=messages.append,
    )
    return folder


def test_runner_end_to_end_with_fake_models(tmp_path: Path) -> None:
    folder = run_with_fakes(tmp_path, Options(label="fake"))
    assert (folder / "report.md").exists() and (folder / "raw.json").exists()
    raw = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    assert any("extract_assertions: The trial" in note for note in raw["notes"])
    results = raw["configurations"][0]["results"]
    assert set(results) == {"L1", "R1"}
    assert results["L1"]["checks"]["searched"] is True
    assert results["L1"]["outcome"]["citations"][0]["sourceTitle"] == "The trial"
    assert results["L1"]["outcome"]["context"]["skill"] is None
    assert results["L1"]["failures"] == []
    # The second case asks a question first, then rework: the last answer is scored.
    assert results["R1"]["outcome"]["answer"]
    assert raw["configurations"][0]["card"]["cases"] == 2
    assert "Cases passed" in (folder / "report.md").read_text(encoding="utf-8")
    assert "sk-test" not in (folder / "raw.json").read_text(encoding="utf-8")
    # The seeded library is kept for the next run.
    assert any(p.name.startswith("template-") for p in (tmp_path / "results").iterdir())


def test_repeating_a_case_gives_a_pass_rate_and_names_the_cases_that_disagreed(
    tmp_path: Path,
) -> None:
    folder = run_with_fakes(tmp_path, Options(label="fake", repeat=2, only=["L1"]))
    raw = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    configuration = raw["configurations"][0]
    assert set(configuration["results"]) == {"L1#1", "L1#2"}
    assert configuration["card"]["cases"] == 1 and configuration["card"]["runs"] == 2
    assert configuration["card"]["passed"] == 1.0 and configuration["card"]["disagreed"] == {}
    from evals.ask.report import scorecard
    from evals.ask.score import load_cases

    cases = {case.id: case for case in load_cases(tmp_path / "cases.toml")}
    results = configuration["results"]
    results["L1#2"] = {**results["L1#2"], "failures": ["did not mention 412"]}
    card = scorecard(cases, results)
    assert card["passed"] == 0.5 and card["disagreed"] == {"L1": "1/2"}


def test_agent_efforts_reach_the_agent(tmp_path: Path, monkeypatch) -> None:
    from fake_models import gateway

    from gunther import ask_turns as service_module
    from gunther.agent import AskAgent
    from gunther.evidence import Toolbox
    from gunther.service import KnowledgeService

    seen = {}

    class Spy(AskAgent):
        def __init__(self, *args, **kwargs):
            seen.update({key: value for key, value in kwargs.items() if key.endswith("effort")})
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(service_module, "AskAgent", Spy)
    fake = FakeProvider(agent_replies("Hello.", {"action": "answer"}))
    chosen = gateway(fake)
    knowledge = KnowledgeService(None, None)  # type: ignore[arg-type]
    knowledge.models = chosen
    knowledge._answer("hi", [], Toolbox(()), None, None)
    # The defaults Phase 6 chose: thinking a little while planning, not while checking.
    assert seen == {"plan_effort": "low", "check_effort": "off"}
    knowledge.agent_efforts = ("off", "medium")
    knowledge._answer("hi", [], Toolbox(()), None, None)
    assert seen == {"plan_effort": "off", "check_effort": "medium"}


def test_the_exam_lists_cases_skipped_without_tavily(tmp_path: Path) -> None:
    extra = """
[[case]]
id = "W1"
category = "web"
question = "What are the latest findings from outside the library?"
web = true
needs = ["tavily"]
"""
    folder = run_with_fakes(tmp_path, Options(label="fake", only=["W1", "L1"]), extra)
    raw = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    assert set(raw["configurations"][0]["results"]) == {"L1"}
    assert "W1 needs tavily" in (folder / "report.md").read_text(encoding="utf-8")


def test_research_cases_ask_first_or_stay_in_their_budget() -> None:
    by_id = {c.id: c for c in score.load_cases()}
    first, standard, web = by_id["RS1"], by_id["RS2"], by_id["RS3"]
    assert first.skill == "research" and first.expect_state == "asking"
    assert first.search == "must_not"
    assert standard.budget == "standard" and not standard.web
    assert standard.max_searches == 8 and web.max_searches == 8
    assert web.web and web.needs == ("tavily",) and web.cite_web
    assert {c.category for c in (first, standard, web)} == {"research"}
    asked = {"state": "asking"}
    assert score.checks(first, score.Outcome(context={"research": asked, "steps": []}))["state"]
    assert not score.checks(first, score.Outcome(context={"research": {"state": "done"}}))["state"]
