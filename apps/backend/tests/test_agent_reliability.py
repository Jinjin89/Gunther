"""Coverage and resource limits must describe the work actually performed."""

import re
from types import SimpleNamespace

import openai
import pytest
from fake_models import FakeProvider, agent_replies, api_error, gateway
from pydantic import BaseModel

from gunther.agent import AskAgent, Budget, Evidence, Tool, Toolbox
from gunther.brief import Brief, SettledItem, _enforce
from gunther.llm import ModelError
from gunther.run_budget import BudgetedGateway, RunBudget


def accept_batch(request):
    prompt = str(request["messages"][-1]["content"])
    return {
        "sentences": [
            {"n": int(n), "verdict": "supported"} for n in re.findall(r"^\((\d+)\)", prompt, re.M)
        ]
    }


def test_all_75_claims_are_checked_in_batches_including_the_last_sentence():
    fake = FakeProvider(agent_replies("unused", checked=accept_batch))
    chosen = gateway(fake)
    agent = AskAgent(chosen)
    text = "\n".join(f"Observed result number {n} was measured [1]." for n in range(75))
    evidence = Evidence("library", "Trial", "All numbered results were measured.", ref=1)
    _, check, _, _ = agent.check_sentences(
        chosen.models[0], "Results?", text, {1: evidence}, limit=20
    )
    assert check._coverage["status"] == "complete"
    assert check._coverage["checked"] == 75
    assert len(fake.requests) == 4
    assert check._coverage["claims"][-1]["verdict"] == "supported"


def test_missing_verdict_is_unchecked_even_when_the_provider_returns_valid_json():
    fake = FakeProvider(
        agent_replies("unused", checked={"sentences": [{"n": 1, "verdict": "supported"}]})
    )
    chosen = gateway(fake)
    _, check, _, _ = AskAgent(chosen).check_sentences(
        chosen.models[0],
        "Results?",
        "First result [1]. Second result [1].",
        {1: Evidence("library", "Trial", "Both results.")},
    )
    assert check._coverage["status"] == "partial"
    assert check._coverage["checked"] == 1 and check._coverage["unchecked"] == 1
    assert check._coverage["claims"][1]["verdict"] == "unchecked"


def test_source_budget_splits_batches_and_never_silently_drops_a_claim():
    fake = FakeProvider(agent_replies("unused", checked=accept_batch))
    chosen = gateway(fake)
    evidence = {
        n: Evidence("library", str(n), "Quote", context="Evidence. " * 600) for n in range(1, 11)
    }
    text = "\n".join(f"Result {n} is supported [{n}]." for n in range(1, 11))
    _, check, _, _ = AskAgent(chosen).check_sentences(
        chosen.models[0], "Results?", text, evidence, limit=40
    )
    assert check._coverage["status"] == "complete" and len(fake.requests) >= 3
    oversized = "One comparison [1][2][3][4][5]."
    before = len(fake.requests)
    _, check, _, _ = AskAgent(chosen).check_sentences(
        chosen.models[0], "Results?", oversized, evidence
    )
    assert check._coverage["unchecked"] == 1 and len(fake.requests) == before
    assert "context budget" in check._coverage["claims"][0]["reason"]


def test_transform_mode_admits_no_search_in_gathering_or_post_answer_lookup():
    searches = []
    fake = FakeProvider(
        agent_replies(
            "Shortened supplied text.",
            {"action": "search_library", "query": "ignored", "mode": "transform"},
        )
    )
    chosen = gateway(fake)
    tool = Tool("search_library", "Library", lambda query: searches.append(query) or [], "library")
    result = AskAgent(chosen).run(
        "Shorten this supplied paragraph.", [], Toolbox((tool,)), chosen.models[0], "off"
    )
    assert searches == [] and result.budget_usage["used"]["searches"] == 0
    assert result.coverage["status"] == "skipped"


class StructuredReply(BaseModel):
    value: int


def test_json_repair_cannot_escape_total_model_budget():
    fake = FakeProvider("malformed", '{"value": 3}')
    chosen = gateway(fake)
    budget = RunBudget(1, 1, 1, 1)
    with pytest.raises(ModelError, match="budget"):
        BudgetedGateway(chosen, budget).complete_json(
            chosen.models[0], StructuredReply, system="Return JSON.", prompt="Three.", effort="off"
        )
    assert len(fake.requests) == 1 and budget.model_calls == 1


def test_provider_setting_retry_counts_as_an_actual_request():
    fake = FakeProvider(api_error(openai.BadRequestError, 400, "Unknown effort setting"), "Answer")
    chosen = gateway(fake)
    budget = RunBudget(1, 1, 2, 2)
    answer = BudgetedGateway(chosen, budget).complete(
        chosen.models[0], system="Answer.", messages="Hi", effort="high"
    )
    assert answer.text == "Answer"
    assert budget.model_calls == len(fake.requests) == 2


def test_post_answer_lookup_shares_the_search_budget():
    searches = []
    fake = FakeProvider(
        agent_replies(
            "One claim [?].",
            {"action": "search_library", "query": "claim"},
            {"action": "answer"},
            checked={"sentences": [{"n": 1, "verdict": "unsourced"}]},
        )
    )
    chosen = gateway(fake)
    tool = Tool("search_library", "Library", lambda query: searches.append(query) or [], "library")
    result = AskAgent(chosen).run(
        "Verify one claim", [], Toolbox((tool,)), chosen.models[0], "off", budget=Budget(searches=1)
    )
    assert len(searches) == result.budget_usage["used"]["searches"] == 1
    assert "searches" in result.budget_usage["exhausted"]


def test_identity_uses_full_quotes_or_revision_blocks_and_counts_copies_once():
    common = "Repeated opening " * 20
    one = Evidence("library", "Same title", common + "First ending.")
    two = Evidence("library", "Same title", common + "Different ending.")
    assert one.key != two.key
    cite = SimpleNamespace(
        source_id="copy-a",
        source_revision_id="revision-a",
        block_id="block-a",
        work_identifier="doi:10.1234/work",
    )
    original = Evidence("library", "Paper", "Quote", payload=(cite, None))
    other = Evidence(
        "library",
        "Download",
        "Other paragraph",
        payload=(
            SimpleNamespace(
                source_id="copy-b",
                source_revision_id="revision-b",
                block_id="block-b",
                work_identifier=cite.work_identifier,
            ),
            None,
        ),
    )
    assert original.key != other.key and original.source_key == other.source_key


def test_a_valid_reference_does_not_settle_an_unchecked_or_reworded_new_fact():
    brief = Brief(
        settled=[
            SettledItem(text="The drug cures everyone.", refs=[1], by="sources"),
            SettledItem(text="The trial measured response.", refs=[1], by="sources"),
        ]
    )
    safe = _enforce(brief, {1}, supported_claims=["The trial measured response [1]."])
    assert [item.text for item in safe.settled] == ["The trial measured response."]
    assert "The drug cures everyone." in safe.open
