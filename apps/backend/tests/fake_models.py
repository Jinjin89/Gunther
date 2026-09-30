"""A stand-in for OpenAI-style providers, for tests that talk to "a model".

It answers ``chat.completions.create`` the way DeepSeek, Kimi, GLM and Qwen do
(a stream of chunks, reasoning in ``reasoning_content``), records every
request, and can enforce a provider's rules, e.g. GLM refusing ``medium``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import openai
from openai.types.chat import ChatCompletionChunk

from gunther.llm import ModelGateway, ModelInfo
from gunther.model_profiles import profile_for
from gunther.model_registry import PRESETS

Reply = str | dict[str, Any] | Exception | Callable[[dict[str, Any]], Any]


def api_error(kind: type[openai.APIStatusError], status: int, message: str) -> Exception:
    response = httpx.Response(status, request=httpx.Request("POST", "https://model.test/v1"))
    return kind(message, response=response, body={"error": {"message": message}})


def _chunks(content: str, reasoning: str | None, finish: str) -> Iterator[ChatCompletionChunk]:
    def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> ChatCompletionChunk:
        return ChatCompletionChunk.model_validate(
            {
                "id": "chunk",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "fake",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
        )

    if reasoning:
        yield chunk({"role": "assistant", "reasoning_content": reasoning})
    for start in range(0, len(content), 7):
        yield chunk({"content": content[start : start + 7]})
    yield chunk({}, finish)


class FakeProvider:
    def __init__(self, *replies: Reply, rule: Callable[[dict[str, Any]], None] | None = None):
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        # Raises for a request this provider would refuse.
        self.rule = rule

    # Looks like an OpenAI client: client.chat.completions.create(...)
    @property
    def chat(self) -> FakeProvider:
        return self

    @property
    def completions(self) -> FakeProvider:
        return self

    def factory(self, _model: ModelInfo) -> FakeProvider:
        return self

    def create(self, **request: Any) -> Iterator[ChatCompletionChunk]:
        self.requests.append(request)
        if self.rule:
            self.rule(request)
        # Replies are used in turn; the last one keeps answering.
        reply: Any = self.replies.pop(0) if len(self.replies) > 1 else (self.replies or ["OK"])[0]
        if callable(reply) and not isinstance(reply, Exception):
            reply = reply(request)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, str):
            reply = {"content": reply}
        return _chunks(
            reply.get("content", ""), reply.get("reasoning"), reply.get("finish", "stop")
        )


def model(
    model_id: str = "deepseek-flash",
    *,
    kind: str = "deepseek",
    provider_id: str | None = None,
    name: str | None = None,
    label: str | None = None,
    **profile: Any,
) -> ModelInfo:
    return ModelInfo(
        provider_id=provider_id or kind,
        provider_name=name or PRESETS[kind].name,
        provider_kind=kind,
        model_id=model_id,
        label=label or model_id,
        base_url="https://model.test/v1",
        api_key="sk-test",
        profile=profile_for(kind, model_id, **profile),
    )


def gateway(
    fake: FakeProvider,
    *models: ModelInfo,
    roles: dict[str, tuple[str, str]] | None = None,
) -> ModelGateway:
    models = models or (model(),)
    first = models[0].ref
    return ModelGateway(
        models,
        roles
        if roles is not None
        else {"analysis": (first, "low"), "ask": (first, "high"), "photos": (first, "low")},
        client_factory=fake.factory,
    )


PLANNER_MARK = "You are the planning step"


def is_planning(request: dict[str, Any]) -> bool:
    return PLANNER_MARK in str(request["messages"][0]["content"])


GRADER_MARK = "You are the relevance step"


def is_grading(request: dict[str, Any]) -> bool:
    return GRADER_MARK in str(request["messages"][0]["content"])


CHECKER_MARK = "You are the checking step"


def is_checking(request: dict[str, Any]) -> bool:
    return CHECKER_MARK in str(request["messages"][0]["content"])


AUDITOR_MARK = "You are the audit step"


def is_auditing(request: dict[str, Any]) -> bool:
    return AUDITOR_MARK in str(request["messages"][0]["content"])


def agent_replies(
    answer: Reply,
    *plans: dict[str, Any],
    relevant: list[int] | None = None,
    verdict: dict[str, list[int]] | None = None,
    unmarked: list[str] | None = None,
) -> Callable[[dict[str, Any]], Any]:
    """Replies for Ask: each planning call gets the next plan, every other call the answer.

    With no plans the agent is told to search the library with the question, then to
    answer, and a relevance check keeps ``relevant`` (all results by default).
    ``answer`` is a reply as FakeProvider takes them (text or a callable).
    """

    import json

    queue = list(plans) or [
        {"action": "search_library", "query": ""},
        {"action": "answer"},
    ]

    def reply(request: dict[str, Any]) -> Any:
        if is_planning(request):
            plan = queue.pop(0) if len(queue) > 1 else queue[0]
            return json.dumps(plan)
        if is_grading(request):
            # By default the grader keeps everything the search found.
            return json.dumps(
                {"relevant": relevant if relevant is not None else list(range(1, 25))}
            )
        if is_auditing(request):
            return json.dumps({"claims": unmarked or []})
        if is_checking(request):
            return json.dumps(verdict or {"supports": [], "contradicts": []})
        return answer(request) if callable(answer) else answer

    return reply
