"""Cache contracts at the provider boundary. All replies and rendering are fake."""

import contextvars
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fake_models import FakeProvider, gateway, model
from pydantic import BaseModel
from test_studio_pipeline import DECK, FakeRenderer, pool, request_text, router

from gunther.llm import Turn
from gunther.outputs import Notes, Spec
from gunther.studio import design as D
from gunther.studio import format as fmt
from gunther.studio import pipeline as pipeline
from gunther.studio import prompts as P
from gunther.studio.pipeline import StudioAgents, material_request, warm_map

REPORT = """<!-- report: v2 -->
# Plan with the adjusted saving

## Cool roofs save less after adjustment
The adjusted saving is 9% [2].
- **A large trial**: 412 buildings were followed [1].

## Weather adjustment changes the estimate
The raw saving was 18% [2].

## Reflectivity falls over time
Albedo fell from 0.82 to 0.55 within 18 months [3].
"""


def task_of(request):
    text = request_text(request)
    return text.split(P.TASK_BOUNDARY, 1)[-1]


def step_of(request):
    task = task_of(request)
    # Repairs contain the original writing/planning instructions as well.
    for marker, step in [
        ("Your report breaks", "report repair"),
        ("A senior partner reviewed", "story revision"),
        ("lead analyst", "memo"),
        ("presentation strategist", "story"),
        ("planning a written report", "story"),
        ("demanding senior partner", "critic"),
        ("presentation writer", "deck writer"),
        ("best writer", "report writer"),
        ("sentence-checking step", "claim check"),
        ("A fact-checker", "claim repair"),
        ("A deck's subtitle", "cover"),
        ("world-class presentation designer", "design"),
        ("Choose the visual style", "style"),
        ("demanding presentation designer", "look"),
        ("Some slides break", "slide format"),
    ]:
        if marker in task:
            return step
    raise AssertionError(task[:200])


def build_router(request):
    step = step_of(request)
    if step == "critic":
        return json.dumps(
            {
                "verdict": "revise",
                "problems": [
                    {"where": "overall", "problem": "The decision is late", "fix": "Lead with it"}
                ],
            }
        )
    if step in ("story", "story revision") and "planning a written report" in task_of(request):
        return json.dumps(
            {
                "title": "Plan with the adjusted saving",
                "sections": [
                    {"heading": "Weather adjustment changes the estimate", "refs": [2]},
                    {"heading": "Reflectivity falls over time", "refs": [3]},
                ],
            }
        )
    if step == "report writer":
        # Exercise report repair without patching the format checker.
        return REPORT.replace("Weather adjustment changes the estimate", "Background")
    if step == "report repair":
        return REPORT
    if step == "look":
        return '{"findings": []}'
    return router(request)


class TrackedProvider(FakeProvider):
    """Records completion only after the final streamed chunk has been consumed."""

    def __init__(self, route):
        super().__init__(route)
        self.events = []
        self.lock = threading.Lock()

    def create(self, **request):
        step = step_of(request)
        with self.lock:
            self.events.append(("start", step))
        yield from super().create(**request)
        with self.lock:
            self.events.append(("end", step))


@pytest.mark.parametrize("kind", ["report", "slides"])
def test_build_requests_share_material_and_warm_workers(kind, tmp_path):
    fake = TrackedProvider(build_router)
    agents = StudioAgents(gateway(fake), use="talk", renderer=FakeRenderer(tmp_path, crowded=()))
    sources = pool()
    notes = Notes(knowledge=[("Preference", "Keep the distinction between raw and adjusted")])
    material = agents._notes_text(notes) + agents._material(sources.items)
    built = agents.make(
        model(), "high", Spec(kind, "collaborator", brief="A short briefing"), sources, notes
    )
    assert built.content
    expected_system, expected_prompt = material_request(material, "")
    material_calls = []
    steps = set()
    for request in fake.requests:
        step = step_of(request)
        steps.add(step)
        system = request["messages"][0]["content"]
        if step in {"design", "style", "look", "slide format"}:
            assert material not in request_text(request)
            assert "The material:\n\n" not in request_text(request)
            continue
        material_calls.append(request)
        assert system == expected_system
        assert request["messages"][1]["content"].startswith(expected_prompt)
        assert request_text(request).count(P.TASK_BOUNDARY) == 1
        if request.get("response_format"):
            assert "JSON schema:" not in system
            assert "JSON schema:" in request["messages"][1]["content"].split(P.TASK_BOUNDARY)[1]
    assert {"memo", "story", "critic", "story revision", "claim check"} <= steps
    assert material_calls
    if kind == "report":
        assert {"report writer", "report repair"} <= steps
    else:
        assert {"deck writer", "claim repair", "cover", "design", "look"} <= steps
        designs = [r["messages"][0]["content"] for r in fake.requests if step_of(r) == "design"]
        assert len({s.split("This is slide ")[0] for s in designs}) == 1
        assert "Deck outline" in designs[0].split("This is slide ")[0]
    for step in ("claim check", "design") if kind == "slides" else ("claim check",):
        events = [state for state, name in fake.events if name == step]
        assert events.count("start") > 1
        assert events[:2] == ["start", "end"]
    # Reproducible character counts, including the system, task and JSON schema;
    # excludes wire framing, images and generated output. Visible with pytest -s.
    shared = len(expected_system) + len(expected_prompt)
    for request in material_calls:
        total = sum(len(m["content"]) for m in request["messages"])
        print(f"{kind}: {step_of(request)}: shared {shared} / total {total} characters")


class Answer(BaseModel):
    answer: str


@pytest.mark.parametrize("schema_at", ["system", "end"])
def test_json_schema_placement_and_retry_preserve_history(schema_at):
    fake = FakeProvider('{"wrong": 1}', '{"answer": "ok"}')
    history = [Turn("user", "Earlier question"), Turn("assistant", "Earlier reply")]
    kwargs = {"schema_at": "end"} if schema_at == "end" else {}  # test the default too
    result, _ = gateway(fake).complete_json(
        model(), Answer, system="Shared rules", prompt="Task", history=history, **kwargs
    )
    assert result.answer == "ok"
    first, retry = [r["messages"] for r in fake.requests]
    schema = "Reply with one JSON object and nothing else. It must match this JSON schema:\n" + (
        json.dumps(Answer.model_json_schema(), ensure_ascii=False)
    )
    assert first[1:3] == [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier reply"},
    ]
    assert first[0]["content"] == (
        "Shared rules" if schema_at == "end" else "Shared rules\n\n" + schema
    )
    assert first[-1]["content"] == ("Task\n\n" + schema if schema_at == "end" else "Task")
    assert retry[: len(first)] == first
    assert retry[-1]["content"].startswith("That was not valid JSON")


def test_warm_map_runs_remaining_items_concurrently_and_keeps_context():
    context = contextvars.ContextVar("cache_test", default="missing")
    context.set("build")
    first_done = threading.Event()
    fanout = threading.Barrier(2)

    def worker(index):
        assert context.get() == "build"
        if index == 0:
            first_done.set()
        else:
            assert first_done.is_set()
            fanout.wait(timeout=5)
        return index

    assert warm_map(worker, [0, 1, 2], 2) == [0, 1, 2]
    assert warm_map(worker, [], 2) == []
    assert warm_map(worker, [0], 2) == [0]


def test_design_warms_first_actual_request_after_a_figure():
    figure = "<!-- layout: figure -->\n## A figure\n![Figure](gunther-asset:figure)"
    slides = [figure, *fmt.split_slides(DECK)[:2]]
    fake = TrackedProvider(build_router)
    D.design_slides(StudioAgents(gateway(fake)), model(), slides, "light", workers=2)
    assert fake.events[:2] == [("start", "design"), ("end", "design")]
    assert len(fake.requests) == 2


def test_large_material_keeps_original_passages_and_fixed_order(monkeypatch):
    monkeypatch.setattr(pipeline, "DIRECT_TOKENS", 1)
    monkeypatch.setattr(pipeline, "PART_TOKENS", 1)

    def route(request):
        if "research analyst reading part" in request_text(request):
            return '{"facts": [{"text": "Reading note", "refs": [1]}]}'
        return build_router(request)

    fake = FakeProvider(route)
    agents = StudioAgents(gateway(fake), design=False)
    sources = pool()
    sources.items.reverse()
    agents.make(model(), "high", Spec("report", "collaborator"), sources, Notes())
    calls = [r for r in fake.requests if "research analyst reading part" not in request_text(r)]
    prefix = calls[0]["messages"][1]["content"].split(P.TASK_BOUNDARY)[0]
    assert "Reading note" in prefix
    for item in sources.items:
        assert agents._passage(item) in prefix
    assert prefix.index("[1] (Source") < prefix.index("[2] (Source") < prefix.index("[3] (Source")
    assert all(r["messages"][1]["content"].split(P.TASK_BOUNDARY)[0] == prefix for r in calls)


def test_concurrent_checks_do_not_replace_asks_gateway():
    fake = FakeProvider(router)
    agents = StudioAgents(gateway(fake))
    original = agents.ask.gateway
    sources = pool().by_ref()
    materials = ["First build material", "Second build material"]
    with ThreadPoolExecutor(2) as workers:
        list(
            workers.map(
                lambda material: agents._claims(
                    model(), "The adjusted saving is 9% [2].", sources, material=material
                ),
                materials,
            )
        )
    assert agents.ask.gateway is original
    prefixes = [r["messages"][1]["content"].split(P.TASK_BOUNDARY)[0] for r in fake.requests]
    assert set(prefixes) == {"The material:\n\n" + m for m in materials}
