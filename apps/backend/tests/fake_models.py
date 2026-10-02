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


GRADE_NOTES_MARK = "You are the grading step"


def is_grading_notes(request: dict[str, Any]) -> bool:
    return GRADE_NOTES_MARK in str(request["messages"][0]["content"])


BRIEF_KEEPER_MARK = "You are the memory step"


def is_keeping_brief(request: dict[str, Any]) -> bool:
    return BRIEF_KEEPER_MARK in str(request["messages"][0]["content"])


SENTENCE_CHECKER_MARK = "You are the sentence-checking step"


def is_sentence_checking(request: dict[str, Any]) -> bool:
    return SENTENCE_CHECKER_MARK in str(request["messages"][0]["content"])


REVISER_MARK = "You are the revising step"


def is_revising(request: dict[str, Any]) -> bool:
    return REVISER_MARK in str(request["messages"][0]["content"])


LOOKUP_MARK = "You are the look-up step"


def is_looking_up(request: dict[str, Any]) -> bool:
    return LOOKUP_MARK in str(request["messages"][0]["content"])


FRAMER_MARK = "You are the framing step"


def is_framing(request: dict[str, Any]) -> bool:
    return FRAMER_MARK in str(request["messages"][0]["content"])


def agent_replies(
    answer: Reply,
    *plans: dict[str, Any],
    relevant: list[int] | None = None,
    verdict: dict[str, list[int]] | None = None,
    unmarked: list[str] | None = None,
    checked: dict[str, Any] | Callable[[dict[str, Any]], Any] | None = None,
    lookups: dict[str, Any] | Callable[[dict[str, Any]], Any] | None = None,
    kept: list[dict[str, Any]] | Callable[[dict[str, Any]], Any] | None = None,
    brief: dict[str, Any] | Callable[[dict[str, Any]], Any] | None = None,
    revised: Reply | None = None,
    framed: dict[str, Any] | Callable[[dict[str, Any]], Any] | None = None,
) -> Callable[[dict[str, Any]], Any]:
    """Replies for Ask: each planning call gets the next plan, every other call the answer.

    With no plans the agent is told to search the library with the question, then to
    answer, and a relevance check keeps ``relevant`` (all results by default).
    ``answer`` is a reply as FakeProvider takes them (text or a callable). The sentence
    checker finds nothing to change unless ``checked`` (a Check as a dict, or a function
    of the request) says so, and the look-up judges nothing unless ``lookups`` does.
    The grading step keeps every result unless ``kept`` (a list of {"n", "serves", "says"},
    or a function of the request) says otherwise; ``relevant`` still names the numbers to keep.
    The memory step returns an empty brief unless ``brief`` (a Brief as a dict, or a
    function of the request, returning a dict or text) says otherwise.
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
        if is_grading_notes(request):
            if callable(kept):
                return kept(request)
            if kept is None:
                numbers = relevant if relevant is not None else list(range(1, 25))
                return json.dumps({"keep": [{"n": n} for n in numbers]})
            return json.dumps({"keep": kept})
        if is_auditing(request):
            return json.dumps({"claims": unmarked or []})
        if is_checking(request):
            return json.dumps(verdict or {"supports": [], "contradicts": []})
        if is_keeping_brief(request):
            held = brief(request) if callable(brief) else brief
            return json.dumps(held or {})
        if is_sentence_checking(request):
            found = checked(request) if callable(checked) else checked
            return json.dumps(found or {"sentences": []})
        if is_looking_up(request):
            found = lookups(request) if callable(lookups) else lookups
            return json.dumps(found or {"claims": []})
        if is_framing(request):
            return framed(request) if callable(framed) else json.dumps(framed or {})
        if is_revising(request) and revised is not None:
            return revised(request) if callable(revised) else revised
        return answer(request) if callable(answer) else answer

    return reply


OUTLINER_MARK = "You are the outline step"


def is_outlining(request: dict[str, Any]) -> bool:
    return OUTLINER_MARK in str(request["messages"][0]["content"])


WRITER_MARK = "You are Gunther's writer"


def is_writing(request: dict[str, Any]) -> bool:
    return WRITER_MARK in str(request["messages"][0]["content"])


SUPPORT_MARK = "You are the support-check step"


def is_support_checking(request: dict[str, Any]) -> bool:
    return SUPPORT_MARK in str(request["messages"][0]["content"])


def output_replies(
    outline: dict[str, Any],
    written: Any,
    *,
    relevant: list[int] | None = None,
    unmarked: list[str] | None = None,
    verdict: dict[str, list[int]] | None = None,
    unsupported: Any = None,
) -> Callable[[dict[str, Any]], Any]:
    """Replies for building an output: the outline, then each section as the writer is
    asked for it, and quiet checks (nothing unmarked, nothing unsupported) unless given.

    ``written`` is the sections' texts in order, or a function of (section index, prompt)
    that returns a reply (an exception is raised, as ``FakeProvider`` does).
    ``unsupported`` is a list of sentence numbers, or a function of the request.
    """

    import json
    import re

    def reply(request: dict[str, Any]) -> Any:
        if is_outlining(request):
            return json.dumps(outline)
        if is_grading(request):
            return json.dumps(
                {"relevant": relevant if relevant is not None else list(range(1, 41))}
            )
        if is_auditing(request):
            return json.dumps({"claims": unmarked or []})
        if is_checking(request):
            return json.dumps(verdict or {"supports": [], "contradicts": []})
        if is_support_checking(request):
            flagged = unsupported(request) if callable(unsupported) else unsupported
            return json.dumps({"unsupported": flagged or []})
        prompt = str(request["messages"][-1]["content"])
        asked = re.search(r"Write section (\d+) of", prompt)
        if asked is None:
            # Anything else a model is asked while a library is set up (reading a source).
            return "{}"
        index = int(asked.group(1)) - 1
        return written(index, prompt) if callable(written) else written[index]

    return reply


SKILL_MARK = "Gunther skill step: "


def skill_step(request: dict[str, Any]) -> str | None:
    """Which step of a skill a request is from (see skill_runner), if any."""

    system = str(request["messages"][0]["content"])
    if not system.startswith(SKILL_MARK):
        return None
    return system[len(SKILL_MARK) :].split("\n", 1)[0].strip()


def skill_replies(
    approach: dict[str, Any],
    outline: dict[str, Any],
    written: Any,
    *,
    gather: list[dict[str, Any]] | None = None,
    editor: Reply | None = None,
    reader: Reply | None = None,
    revision: dict[str, Any] | None = None,
    rewrite: Any = None,
    relevant: list[int] | None = None,
) -> Callable[[dict[str, Any]], Any]:
    """Replies for an output made by a skill.

    ``gather`` is what the understand step chooses to do, in turn (then "done"); the
    approach and the outline come next. ``written`` is the sections' texts in order, or a
    function of (section index, prompt). The editor and the reader find nothing unless
    given; ``revision`` is the revise step's plan ({"sections": [...]}) and ``rewrite``
    a function of (section index, prompt) for what it rewrites. The Checker's calls are
    quiet, as in ``output_replies``.
    """

    import json
    import re

    actions = list(gather or [])

    def answer(reply: Any, request: dict[str, Any]) -> Any:
        if isinstance(reply, Exception):
            raise reply
        return reply(request) if callable(reply) else json.dumps(reply)

    def reply(request: dict[str, Any]) -> Any:
        system = str(request["messages"][0]["content"])
        prompt = str(request["messages"][-1]["content"])
        step = skill_step(request)
        if step is None:
            if is_grading(request):
                return json.dumps(
                    {"relevant": relevant if relevant is not None else list(range(1, 41))}
                )
            if is_auditing(request):
                return json.dumps({"claims": []})
            if is_checking(request):
                return json.dumps({"supports": [], "contradicts": []})
            if is_support_checking(request):
                return json.dumps({"unsupported": []})
            return "{}"
        if "Choose the next action" in system:
            return json.dumps(actions.pop(0) if actions else {"action": "done"})
        if step == "understand":
            return json.dumps(approach)
        if step == "structure":
            return json.dumps(outline)
        if step == "edit_review":
            return answer(editor if editor is not None else {"issues": []}, request)
        if step == "reader_test":
            return answer(
                reader if reader is not None else {"core": approach.get("answer", "")}, request
            )
        if step == "revise" and "This section now:" not in prompt:
            return json.dumps(revision or {"sections": []})
        index = int(re.search(r"Write section (\d+) of", prompt).group(1)) - 1
        if step == "revise":
            return rewrite(index, prompt) if rewrite else "UNCHANGED"
        return written(index, prompt) if callable(written) else written[index]

    return reply
