"""A stand-in for OpenAI-style providers, for tests that talk to "a model".

It answers ``chat.completions.create`` the way DeepSeek, Kimi, GLM and Qwen do
(a stream of chunks, reasoning in ``reasoning_content``), records every
request, and can enforce a provider's rules, e.g. GLM refusing ``medium``.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import openai
from openai.types.chat import ChatCompletionChunk

from evals.sse import ask_turn
from gunther.extraction import CandidateAssertion, ExtractionPayload
from gunther.llm import ModelGateway, ModelInfo
from gunther.model_profiles import profile_for
from gunther.model_registry import PRESETS
from gunther.models import Source

Reply = str | dict[str, Any] | Exception | Callable[[dict[str, Any]], Any]


def assertion_payload(*claims: tuple[str, str, str, str]) -> ExtractionPayload:
    return ExtractionPayload(assertions=[
        CandidateAssertion(
            subject_label=subject, subject_type="Concept", predicate=predicate,
            object_label=object_, object_type="Concept", confidence=0.9,
            evidence_quote=quote, qualifiers=[],
        )
        for subject, predicate, object_, quote in claims
    ])


def store_saved_claims(client, source_id: str, *claims: tuple[str, str, str, str]) -> None:
    """Seed existing Analysis results for tests of readers and review routes."""
    service = client.app.state.knowledge_service
    with service.sessions.begin() as session:
        source = session.get(Source, source_id)
        assert source is not None
        service.store_source_extraction(session, source, assertion_payload(*claims))


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
    def __init__(
        self,
        *replies: Reply,
        rule: Callable[[dict[str, Any]], None] | None = None,
        write_lock_path: Path | None = None,
    ):
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        # Raises for a request this provider would refuse.
        self.rule = rule
        self.write_lock_path = write_lock_path
        self.write_lock_failures: list[str] = []

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
        if self.write_lock_path is not None:
            database = self.write_lock_path.resolve().as_uri() + "?mode=rw"
            try:
                with closing(sqlite3.connect(database, uri=True, timeout=0)) as connection:
                    connection.execute("PRAGMA busy_timeout=0")
                    connection.execute("BEGIN IMMEDIATE")
                    connection.rollback()
            except sqlite3.OperationalError as error:
                self.write_lock_failures.append(str(error))
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
    return SENTENCE_CHECKER_MARK in (
        str(request["messages"][0]["content"]) + studio_task(request)
    )


def _quiet_sentence_check(request: dict[str, Any]) -> dict[str, Any]:
    import re

    prompt = str(request["messages"][-1]["content"])
    return {
        "sentences": [
            {"n": int(n), "verdict": "supported" if re.search(r"\[\d", text) else "nonfactual"}
            for n, text in re.findall(r"^\((\d+)\) (.+)$", prompt, re.M)
        ]
    }


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


def cite_search_pool(request: dict[str, Any]) -> str:
    """Give retrieval fixtures an answer with citations to their actual passages."""

    pool = str(request["messages"][-1]["content"]).partition("\nThe pool:\n")[2]
    refs = re.findall(r"^\[(\d+)\] \(", pool, flags=re.M)
    citations = "".join(f"[{ref}]" for ref in refs)
    return f"Retrieved evidence {citations}." if refs else "No evidence found."


def ask_from_search(client, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Use a test model to cite the pool when a route fixture is testing retrieval."""

    service = client.app.state.knowledge_service
    fake = FakeProvider(agent_replies(cite_search_pool))
    with patch.object(service, "models", gateway(fake)):
        return ask_turn(client, session_id, payload)


def studio_task(request: dict[str, Any]) -> str:
    from gunther.studio.prompts import TASK_BOUNDARY

    return str(request["messages"][-1]["content"]).partition(TASK_BOUNDARY)[2]


def is_outlining(request: dict[str, Any]) -> bool:
    task = studio_task(request)
    return "planning a written report" in task or "presentation strategist" in task


WRITER_MARK = "as instructed, changing only what the instruction needs:"


def is_writing(request: dict[str, Any]) -> bool:
    return WRITER_MARK in str(request["messages"][0]["content"])


SUPPORT_MARK = "You are the support-check step"


def is_support_checking(request: dict[str, Any]) -> bool:
    return SUPPORT_MARK in str(request["messages"][0]["content"])


REPLACER_MARK = "changing one passage of"


def is_replacing(request: dict[str, Any]) -> bool:
    """A request to change one passage of a part (see artifact_steps.REPLACER)."""

    return REPLACER_MARK in str(request["messages"][0]["content"])


def output_replies(
    outline: dict[str, Any],
    written: Any,
    *,
    relevant: list[int] | None = None,
    unmarked: list[str] | None = None,
    verdict: dict[str, list[int]] | None = None,
    unsupported: Any = None,
    rewrite: Any = None,
    replacer: Reply | None = None,
) -> Callable[[dict[str, Any]], Any]:
    """Replies for a Studio build and for checking or revising saved versions.

    ``written`` is the sections' texts in order, or a function of (section index, prompt)
    that returns a reply (an exception is raised, as ``FakeProvider`` does).
    ``unsupported`` is a list of sentence numbers, or a function of the request.
    """

    import json
    import re

    def reply(request: dict[str, Any]) -> Any:
        system = str(request["messages"][0]["content"])
        prompt = str(request["messages"][-1]["content"])
        task = studio_task(request)
        if replacer is not None and is_replacing(request):
            if isinstance(replacer, Exception):
                raise replacer
            return replacer(request) if callable(replacer) else replacer
        title = outline["title"]
        rows = outline["sections"]
        if "You own the presentation purpose decision" in system + task:
            return '{"purpose":"general","reason":"A general deck"}'
        if "lead analyst" in task:
            return json.dumps({"answer": title})
        if is_outlining(request):
            if "presentation strategist" in task:
                return json.dumps({"title": title, "slides": [
                    {"title": row["heading"],
                     "layout": "title" if not i else row.get("layout", "points")}
                    for i, row in enumerate(rows)
                ]})
            return json.dumps({"title": title, "sections": rows})
        if "senior partner reviewing a storyline" in task:
            return '{"verdict":"ready"}'
        if "\n\nYour report:\n\n" in task:
            return task.rsplit("\n\nYour report:\n\n", 1)[1]
        if "presentation writer" in task or "Write the report" in task:
            deck = "presentation writer" in task
            approved = re.findall(r"^\d+\. (.+?) — goal: ", task, re.M)
            headings = approved or [row["heading"] for row in rows]
            parts = [
                written(i, f"Write section {i + 1} of {len(headings)}: {heading}\n{task}")
                if callable(written) else written[i]
                for i, heading in enumerate(headings)
            ]
            for part in parts:
                if isinstance(part, Exception):
                    raise part
            if deck:
                return "\n\n---\n\n".join(
                    part if not i or part.startswith("<!--") else "<!-- layout: points -->\n" + part
                    for i, part in enumerate(parts)
                )
            return f"# {title}\n\n" + "\n\n".join(parts)
        if fixed := re.search(
            r"Slide \d+ as written:\n(.*?)\n\nRewrite it now", system + "\n\n" + prompt, re.S
        ):
            return fixed[1]
        if "A fact-checker compared" in task:
            return task.split("Part 1:\n", 1)[1].split("\n\nUnsupported in part", 1)[0]
        if "Choose the visual style" in system + task:
            return '{"style":"light","why":"A quiet style"}'
        if "core message" in task:
            return re.search(r"^Subtitle: (.*)$", task, re.M)[1]
        if is_grading(request):
            return json.dumps(
                {"relevant": relevant if relevant is not None else list(range(1, 41))}
            )
        if is_auditing(request):
            return json.dumps({"claims": unmarked or []})
        if is_sentence_checking(request):
            quiet = _quiet_sentence_check(request)
            if task and unsupported:
                flagged = unsupported(request) if callable(unsupported) else unsupported
                for sentence in quiet["sentences"]:
                    if sentence["n"] in flagged:
                        sentence["verdict"] = "not"
            return json.dumps(quiet)
        if is_checking(request):
            return json.dumps(verdict or {"supports": [], "contradicts": []})
        if is_support_checking(request):
            flagged = unsupported(request) if callable(unsupported) else unsupported
            prompt = str(request["messages"][-1]["content"])
            numbers = [int(n) for n in re.findall(r"^\[(\d+)\]", prompt, re.M)]
            return json.dumps(
                {
                    "unsupported": flagged or [],
                    "supported": [n for n in numbers if n not in (flagged or [])],
                }
            )
        asked = re.search(
            r"^(Write|Rewrite) (?:section|slide) (\d+)(?:\.| as instructed)", system, re.M,
        )
        if asked is None:
            # Anything else a model is asked while a library is set up (reading a source).
            return "{}"
        index = int(asked.group(2)) - 1
        answer = rewrite if asked.group(1) == "Rewrite" and rewrite is not None else written
        return answer(index, system + "\n" + prompt) if callable(answer) else answer[index]

    return reply


ORCHESTRATOR_MARK = "You are the orchestrator of Gunther's Artifacts"
ANSWERER_MARK = "You are Gunther, answering a question about"


def is_orchestrating(request: dict[str, Any]) -> bool:
    """A request for the Artifacts agent's next decision (see artifact_agent.ORCHESTRATOR)."""

    return str(request["messages"][0]["content"]).startswith(ORCHESTRATOR_MARK)


def is_answering(request: dict[str, Any]) -> bool:
    """A request to answer a question about an artifact (see artifact_agent.ANSWERER)."""

    return str(request["messages"][0]["content"]).startswith(ANSWERER_MARK)


def orchestrator_replies(
    decisions: list[Any],
    inner: Callable[[dict[str, Any]], Any],
    *,
    answer: Reply = "It says so.",
) -> Callable[[dict[str, Any]], Any]:
    """Replies for a turn of the Artifacts agent: each decision call gets the next of
    ``decisions`` (a dict, or a function of the request that returns one), then the last
    again; the answerer gets ``answer``; every other request is ``inner``'s (the skill's)."""

    import json

    queue = list(decisions)

    def reply(request: dict[str, Any]) -> Any:
        if is_orchestrating(request):
            chosen = queue.pop(0) if len(queue) > 1 else queue[0]
            if callable(chosen):
                chosen = chosen(request)
            return chosen if isinstance(chosen, str) else json.dumps(chosen)
        if is_answering(request):
            return answer(request) if callable(answer) else answer
        return inner(request)

    return reply
