"""Outputs: reports and slides built by agents, then edited, revised and checked."""

import hashlib
import itertools
import json
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import openai
import pytest
from fake_models import (
    FakeProvider,
    agent_replies,
    api_error,
    gateway,
    is_auditing,
    is_grading,
    is_outlining,
    is_support_checking,
    orchestrator_replies,
    studio_task,
)
from fake_models import (
    output_replies as legacy_output_replies,
)
from fastapi.testclient import TestClient
from legacy_artifacts import insert_legacy_version
from sqlalchemy import event, func, select, text

from evals.sse import ask_turn, read_events
from gunther.artifact_actions import Act, Decision
from gunther.artifact_agent import ArtifactAgent
from gunther.artifact_manifest import artifact_manifest_hash
from gunther.artifact_read import artifact_detail
from gunther.artifact_versions import output_library
from gunther.checks import coverage_of, verdict, verdicts_of
from gunther.citations import _marked_unsourced
from gunther.claim_checks import checker_inventory
from gunther.config import Settings
from gunther.evidence import Evidence
from gunther.llm import ModelError
from gunther.main import create_app
from gunther.models import (
    Artifact,
    ArtifactCheck,
    ArtifactConversation,
    ArtifactUnitBinding,
    Source,
)
from gunther.output_document import assemble_output
from gunther.output_types import output_type_for
from gunther.outputs import (
    Draft,
    OutputLimits,
    Planned,
    Pool,
    Spec,
    _marked,
    cited_sentences,
    diagram_as_text,
    diagram_fallback_reason,
    heading_of,
    layout_of,
    section_hash,
    split_document,
    title_of,
    with_layout,
)
from gunther.schemas import BuildOutputInput, OutputProvenanceOut, OutputScope, PlanPreferencesOut
from gunther.studio import format as slide_format
from gunther.studio.work import StudioWorkAgents

SIDECAR_TOKEN = "sidecar-token-with-at-least-256-bits-000000000000000000000000"
SIDECAR = {"X-Gunther-Token": SIDECAR_TOKEN}

NOTES = {
    "Markers": "CD3D is a marker of T cells.",
    "Receptors": "CD19 is a marker of B cells.",
}
OUTLINE = {
    "title": "Immune markers",
    "sections": [
        {"heading": "T cells", "goal": "How T cells are told apart", "queries": ["CD3D T cells"]},
        {"heading": "B cells", "goal": "How B cells are told apart", "queries": ["CD19 B cells"]},
    ],
}
ONE_SECTION = {"title": "Immune markers", "sections": [OUTLINE["sections"][0]]}
# The first section cites the second passage first, so the numbers have to be reordered.
REPORT = [
    "## T cells\n\nB cells are set apart by CD19 [2], and T cells by CD3D [1][7].",
    "## B cells\n\nCD19 marks B cells [2].",
]
SLIDES_OUTLINE = {
    "title": "Immune markers",
    "sections": [
        {"heading": "Immune markers", "goal": "", "queries": []},
        {"heading": "T cells", "goal": "The T-cell marker", "queries": ["CD3D T cells"]},
        {"heading": "Takeaways", "goal": "", "queries": []},
    ],
}
SLIDES = [
    "# Immune markers\n\nHow cells are told apart",
    "## T cells\n\n- CD3D marks T cells [1]\n- It sits on the cell surface [?]\n\n"
    "Note: The marker is found on every T cell [1].",
    "## Takeaways\n\n- Markers tell cells apart [1]\n\nNote: Close on CD3D [1].",
]

_requests = itertools.count(1)


def output_replies(outline, written, **options):
    """Run the usual fake, with the approved JSON rows driving the staged writer."""
    reply = legacy_output_replies(outline, written, **options)

    def respond(request):
        task = studio_task(request)
        if is_outlining(request) and "Keep the summary in the summary fields." in task:
            story = json.loads(reply(request))
            summary, *sections = story["sections"]
            return json.dumps(story | {
                "summary_heading": summary["heading"], "summary_id": summary.get("id"),
                "summary_job": summary.get("goal", ""), "sections": sections,
            })
        deck = "Use the approved slide titles in order:" in task
        marker = ("Use the approved slide titles in order:" if deck
                  else "Use exactly these approved section headings in order.")
        _, found, tail = task.partition(marker)
        if not found or not tail.lstrip().startswith("[") or "\n\nYour report:\n\n" in task:
            return reply(request)
        rows, _ = json.JSONDecoder().raw_decode(tail.lstrip())
        parts = []
        for index, row in enumerate(rows):
            part = written(
                index, f"Write section {index + 1} of {len(rows)}: {row['heading']}\n{task}",
            ) if callable(written) else written[index]
            if isinstance(part, Exception):
                raise part
            if deck:
                layout = row["layout"] or ("title" if index == 0 else "points")
                part = slide_format.with_marker(part, layout)
            parts.append(part)
        if deck:
            return "\n\n---\n\n".join(parts)
        return f"# {outline['title']}\n\n" + "\n\n".join(parts)

    return respond


def body(**fields) -> dict:
    return {
        "clientRequestId": f"output_request_{next(_requests):04d}",
        "kind": "report",
        "audience": "scientist",
        **fields,
    }


def app_for(tmp_path: Path, fake: FakeProvider, **overrides) -> TestClient:
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        deepseek_api_key="sk-test-0000000000001234",
        stt_provider="compatible",
        processing_worker_enabled=False,
        slide_review_enabled=overrides.pop("slide_review_enabled", False),
        auth_token=SIDECAR_TOKEN,
        service_settings_file=tmp_path / "service-settings.json",
        **overrides,
    )
    return TestClient(create_app(settings, model_client_factory=fake.factory))


def workspace(client: TestClient) -> dict[str, str]:
    found = client.get("/api/workspace/bootstrap", headers=SIDECAR).json()["workspaceId"]
    return {**SIDECAR, "X-Gunther-Workspace-Id": found}


def library(
    client: TestClient, title: str = "Cells", notes: dict[str, str] | None = None
) -> tuple[str, dict[str, str]]:
    """A library with one note per source: its id, and the sources' ids by title."""

    base_id = client.post(
        "/api/knowledge-bases",
        headers=SIDECAR,
        json={"title": title, "question": f"What marks {title}?", "description": "Markers."},
    ).json()["id"]
    ids = {}
    for name, content in (NOTES if notes is None else notes).items():
        ids[name] = client.post(
            "/api/sources",
            headers=SIDECAR,
            json={"title": name, "kind": "note", "knowledgeBaseId": base_id, "content": content},
        ).json()["source"]["id"]
    return base_id, ids


def legacy_build(
    client: TestClient, base_id: str, request: dict, headers=None,
) -> list[tuple[str, dict]]:
    with client.stream(
        "POST",
        f"/api/knowledge-bases/{base_id}/outputs/build/stream",
        headers=headers or workspace(client),
        json=request,
    ) as response:
        assert response.status_code == 200, response.read()
        return read_events(response)


def output_turn(client, base_id, conversation_id, payload, headers=None):
    with client.stream(
        "POST",
        f"/api/knowledge-bases/{base_id}/artifact-conversations/{conversation_id}/messages/stream",
        headers=headers or workspace(client), json=payload,
    ) as response:
        if response.status_code != 200:
            response.read()
            return [("error", {"status": response.status_code, **response.json()})]
        return read_events(response)


def send_plan(client, base_id, cid, request_id, setup, headers=None, outline=None):
    plan_id = f"draft_{request_id}"
    initial = {
        "kind": "plan", "clientRequestId": f"plan_{request_id}", "planId": plan_id,
        "setup": setup,
    }
    if outline is not None:
        initial["initialOutline"] = outline
    # These tests script the Studio tasks. Orchestrator decisions have their own route tests.
    decision = Decision(actions=[Act(do="plan", change="whole")])
    with patch.object(ArtifactAgent, "_decide", return_value=decision):
        events = output_turn(client, base_id, cid, initial, headers)
    return plan_id, events


def plan_output(client, base_id, request, headers=None):
    legacy = BuildOutputInput.model_validate(request)
    conversation_id = legacy.conversation_id or f"conv_{legacy.client_request_id}"
    if legacy.conversation_title:
        client.post(
            f"/api/knowledge-bases/{base_id}/artifact-conversations",
            headers=headers or workspace(client),
            json={"id": conversation_id, "title": legacy.conversation_title},
        ).raise_for_status()
    setup = {
        "type": output_type_for(legacy.kind, legacy.style).id,
        "focus": legacy.brief, "audience": legacy.audience, "title": legacy.title,
        "length": legacy.length, "language": legacy.language, "theme": legacy.theme,
        "use": legacy.use if legacy.use != "auto" else None,
        "designVersion": legacy.design_version, "web": legacy.web,
        "scope": legacy.scope.model_dump(mode="json", by_alias=True),
        "model": legacy.model, "effort": legacy.effort,
    }
    outline = None
    if legacy.outline:
        outline = [
            {"id": row.id or f"part_{index}", "heading": row.heading, "goal": row.goal,
             "layout": row.layout, "notes": row.notes}
            for index, row in enumerate(legacy.outline, 1)
        ]
    plan_id, events = send_plan(
        client, base_id, conversation_id, legacy.client_request_id, setup, headers, outline,
    )
    return conversation_id, plan_id, events


def approve_output(client, base_id, cid, plan_id, request_id, planned, headers=None):
    return output_turn(client, base_id, cid, {
        "kind": "approve", "clientRequestId": request_id, "planId": plan_id,
        "planRevision": planned["plan"]["planRevision"],
    }, headers)


def approve_events(client, base_id, cid, plan_id, request_id, events, headers=None):
    name, planned = events[-1]
    if name != "done":
        return events
    assert planned["outcome"] == "planned", planned
    assert planned["artifact"] is None, planned
    approval = approve_output(client, base_id, cid, plan_id, request_id, planned, headers)
    return [*events[:-1], *approval]


def plan_and_approve(
    client, base_id, setup, *, conversation_id=None, request_id=None, headers=None, outline=None,
):
    """Run the real creation turns with a test-scripted Plan decision."""
    request_id = request_id or body()["clientRequestId"]
    cid = conversation_id or f"conv_{request_id}"
    plan_id, events = send_plan(
        client, base_id, cid, request_id, {"scope": {"mode": "library"}, **setup},
        headers, outline,
    )
    return approve_events(client, base_id, cid, plan_id, request_id, events, headers)


def slide_outline(content: str) -> list[dict]:
    slides = map(slide_format.parse_slide, slide_format.split_slides(content))
    return [
        {"id": f"slide_{index}", "heading": slide.title, "goal": "",
         "layout": slide.layout, "notes": ""}
        for index, slide in enumerate(slides, 1)
    ]


def build(client: TestClient, base_id: str, request: dict, headers=None) -> list[tuple[str, dict]]:
    """Create a version through Plan and Approve or the verified-parent compatibility route."""
    if request.get("supersedesArtifactId"):
        return legacy_build(client, base_id, request, headers)
    cid, plan_id, events = plan_output(client, base_id, request, headers)
    result = approve_events(
        client, base_id, cid, plan_id, request["clientRequestId"], events, headers,
    )
    if result[-1][0] == "done":
        result[-1] = ("done", result[-1][1]["artifact"])
    return result


def built(events: list[tuple[str, dict]]) -> dict:
    assert events[-1][0] == "done", events[-1]
    return events[-1][1]


def refine(client, base_id, fake, artifact, instruction, section, *, before=()):
    """Script a conversation turn that rewrites a selected part and saves it."""
    path = f"/api/knowledge-bases/{base_id}/artifact-conversations"
    headers = workspace(client)
    cid = client.post(path, headers=headers, json={
        "id": artifact["conversationId"] or f"refine_{next(_requests):04d}",
    }).raise_for_status().json()["id"]
    [reply] = fake.replies
    fake.replies[:] = [orchestrator_replies([
        {"actions": [*before, {"do": "write", "parts": [section], "instruction": instruction}]},
        {"actions": [{"do": "save", "text": instruction}]},
    ], reply)]
    try:
        with client.stream("POST", f"{path}/{cid}/messages/stream", headers=headers, json={
            "clientRequestId": f"output_refine_{next(_requests):04d}", "text": instruction,
            "targetLineageId": artifact["lineageId"], "focus": {"section": section},
        }) as response:
            response.raise_for_status()
            result = built(read_events(response))
        assert result["outcome"] == "saved", result
        return result["artifact"]
    finally:
        fake.replies[:] = [reply]


@contextmanager
def built_output(tmp_path: Path, fake: FakeProvider, **fields):
    """An app with a sealed version of the earlier writer's output."""

    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        yield client, base_id, saved_output(client, base_id, **fields)


def saved_output(
    client, base_id, outline=None, written=None, *, approach=None, fingerprint=None,
    preferences: PlanPreferencesOut | None = None, **fields,
):
    """Store test content directly for tests of readers, edits, Check and Revise."""
    service = client.app.state.knowledge_service
    workspace_id = workspace(client)["X-Gunther-Workspace-Id"]
    payload = BuildOutputInput.model_validate(body(**fields))
    deck = payload.kind == "slides"
    outline = outline or (SLIDES_OUTLINE if deck else OUTLINE)
    written = written or (SLIDES if deck else REPORT)
    title = outline["title"]
    style = "overview" if not approach and payload.style == "auto" else payload.style
    spec = Spec(payload.kind, payload.audience, style, payload.brief, theme=payload.theme)
    with service.sessions() as session:
        limits = OutputLimits()
        material = service._output_material(session, base_id, payload.scope, allow_empty=True)
        pool = Pool(limits.pool)
        for seed in material.seeds[:limits.seed]:
            pool.adopt(seed)
        service._studio_reading(session, material.source_revisions, pool, pinned=material.seeds)
    plans, drafts = [], []
    for index, (row, part) in enumerate(zip(outline["sections"], written, strict=True)):
        layout = row.get("layout", "") if deck and approach else ""
        if deck and approach:
            layout = "title" if index == 0 else (
                "takeaways" if index == len(written) - 1 else layout
            )
        if layout:
            part = with_layout(part, layout, theme=payload.theme, design_version=1)
        identity = row.get("id") or (f"p{index + 1}" if deck and approach else "")
        plans.append(Planned(row["heading"], row.get("goal", ""), layout=layout, id=identity))
        states = checker_inventory(_marked(part).body, pool.by_ref())
        drafts.append(Draft(part, {key: {**row, "verdict": "supported"}
                                   for key, row in states.items()}))
    preamble = "" if deck else f"# {title}"
    if preamble and payload.brief:
        preamble += f"\n\n_{payload.brief}_"
    model, effort = service._output_model(payload.model, payload.effort)
    version = assemble_output(
        spec, title, preamble, drafts, pool, plans, model, effort
    )
    provenance = {"conversation_id": payload.conversation_id,
                  "conversation_title": payload.conversation_title, "request": payload.brief}
    if approach:
        if deck:
            for row, plan in zip(outline["sections"], version.outline, strict=True):
                if layout := plan.get("layout"):
                    plan["design"] = {
                        "intent": plan.get("goal") or plan["heading"],
                        "visual": "Use the assigned content in a clear visual hierarchy.",
                        "composition": "standard",
                        "emphasis": "", "notes": "", "layout": layout,
                    }
                if row.get("link"):
                    plan["link"] = row["link"]
        provenance.update(generator="gunther.output-skills.v1", approach=approach,
                          skill={"name": payload.kind, "version": (11 if deck else 2)},
                          use=approach.get("use"))
    digest = hashlib.sha256(version.content.encode()).hexdigest()
    pinned = [snap.unit_id for snap in material.snapshots]
    style = output_type_for(payload.kind, style).id
    with service.sessions.begin() as session:
        row = OutputProvenanceOut(
            schema_version=2, workspace_id=workspace_id, knowledge_base_id=base_id,
            knowledge_base_question=output_library(session, workspace_id, base_id).question,
            accepted_only=False, accepted_unit_ids=pinned,
            revision_ids=[snap.revision_id for snap in material.snapshots],
            kind=payload.kind, style=style, audience=payload.audience, brief=payload.brief,
            origin="build", **({"generator": "gunther.output-agents.v1"} | provenance),
        ).model_dump(mode="json", exclude_none=True)
        if preferences is not None:
            row["effectivePreferences"] = preferences.model_dump(mode="json", by_alias=True)
        # A historical v2 fixture predates approval. Insert its sealed representation directly.
        artifact = Artifact(
            id=f"art_{uuid4().hex}", lineage_id=f"arl_{uuid4().hex}", version_number=1,
            workspace_id=workspace_id, knowledge_base_id=base_id,
            client_request_id=payload.client_request_id,
            request_fingerprint=fingerprint or payload.client_request_id,
            format=payload.kind, kind=payload.kind, style=style, audience=payload.audience,
            title=title, brief=payload.brief, origin="build", content=version.content,
            content_hash=digest, provenance_json=json.dumps(row),
            accepted_unit_ids_json=json.dumps(pinned),
            revision_snapshot_json=json.dumps([snap.model_dump() for snap in material.snapshots]),
            scope_json=payload.scope.model_dump_json(), inputs_json=json.dumps(material.inputs),
            outline_json=json.dumps(version.outline),
            citations_json=json.dumps([c.model_dump() for c in version.citations]),
        )
        artifact.manifest_hash = artifact_manifest_hash(artifact.__getattribute__)
        session.add(artifact)
        session.flush()
        session.add_all([
            ArtifactUnitBinding(
                id=f"aub_{uuid4().hex}", artifact_id=artifact.id, position=position,
                unit_id=snap.unit_id, revision_id=snap.revision_id, content_hash=snap.content_hash,
            )
            for position, snap in enumerate(material.snapshots)
        ])
        session.add(ArtifactCheck(
            id=f"chk_{uuid4().hex}", artifact_id=artifact.id, content_hash=digest,
            checks_json=json.dumps({"sections": version.checks}),
        ))
        session.flush()
        return artifact_detail(session, artifact).model_dump(mode="json", by_alias=True)


def versions(client: TestClient) -> list[Artifact]:
    with client.app.state.knowledge_service.sessions() as session:
        return list(session.scalars(select(Artifact)))


def route(base_id: str, artifact_id: str, tail: str = "") -> str:
    return f"/api/knowledge-bases/{base_id}/artifacts/{artifact_id}{tail}"


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


def heading(prompt: str) -> str:
    return re.search(r"Write section \d+ of \d+: (.+)", prompt).group(1)


# The rules both sides split a document by -----------------------------------------------------
# The desktop (src/outputs/sections.test.ts) tests the same cases.


def test_a_reports_sections_start_at_level_two_headings_outside_code() -> None:
    content = (
        "# Title\n\n_Brief_\n\n## One\n\ntext\n\n```md\n## not a section\n```\n\n"
        "### Sub\n\n## Two\n\nmore\n"
    )
    preamble, sections = split_document(content, "report")
    assert preamble == "# Title\n\n_Brief_"
    assert sections == [
        "## One\n\ntext\n\n```md\n## not a section\n```\n\n### Sub",
        "## Two\n\nmore",
    ]
    assert split_document("Just words, no headings.", "report") == ("Just words, no headings.", [])


def test_slides_are_the_parts_between_lines_that_are_only_three_dashes() -> None:
    content = (
        "# Deck\n\nsub\n\n---\n\n## One\n\n- a\n\nNote: n\n\n```\n---\n```\n\n"
        "   ---  \n\n## Two\n\n---\n\n---\n"
    )
    assert split_document(content, "slides") == (
        "",
        ["# Deck\n\nsub", "## One\n\n- a\n\nNote: n\n\n```\n---\n```", "## Two"],
    )


def test_a_sections_hash_ignores_line_ends_and_outer_space() -> None:
    assert section_hash("a\r\nb\n") == section_hash("  a\nb")
    assert section_hash("a\nb") != section_hash("a\n\nb")
    assert len(section_hash("")) == 64


def test_the_title_is_the_first_top_level_heading_outside_code() -> None:
    assert title_of("```\n# not this\n```\n\n# This one\n\n# Not this either") == "This one"
    assert title_of("## only a section") is None
    assert title_of("# " + "x" * 200) == "x" * 160
    assert heading_of("\n## The heading\n\ntext") == "The heading"
    assert heading_of("# Title slide") == "Title slide"
    # A slide's heading is the first line under its marker.
    assert heading_of("<!-- layout: compare -->\n## The claim\n\ntext") == "The claim"
    assert heading_of("<!-- layout: title; accent: blue -->\n# Title slide") == "Title slide"


# A slide's marker: (text, its layout, its accent, the slide without the marker). The desktop
# (src/outputs/sections.test.ts) tests the same list.
MARKER_CASES = [
    (
        "<!-- layout: compare -->\n## A claim\n\n| a | b |",
        "compare",
        None,
        "## A claim\n\n| a | b |",
    ),
    (
        "<!-- layout: title; accent: blue -->\n# Deck\n\nOne line",
        "title",
        "blue",
        "# Deck\n\nOne line",
    ),
    ("## No marker\n\n- a", None, None, "## No marker\n\n- a"),
    ("<!--layout:big_number-->\n## Tight\n\n**62%**", "big_number", None, "## Tight\n\n**62%**"),
    ("\n\n  <!-- layout: text -->\n\n\n## Blank lines", "text", None, "## Blank lines"),
    ("<!-- layout: text -->\r\n## Windows", "text", None, "## Windows"),
    ("<!-- layout: text -->", "text", None, ""),
    # Only the title slide has an accent, and only one of the six.
    ("<!-- layout: title; accent: pink -->\n# Deck", "title", None, "# Deck"),
    ("<!-- layout: compare; accent: blue -->\n## A claim", "compare", None, "## A claim"),
    # Not a marker: not the first line, not the whole line, not a layout, not one line.
    ("## A\n\n<!-- layout: text -->\n- a", None, None, "## A\n\n<!-- layout: text -->\n- a"),
    ("<!-- layout: text --> ## A", None, None, "<!-- layout: text --> ## A"),
    ("<!-- note: layout -->\n## A", None, None, "<!-- note: layout -->\n## A"),
    ("<!-- layout: Text -->\n## A", None, None, "<!-- layout: Text -->\n## A"),
    ("<!--\nlayout: text -->\n## A", None, None, "<!--\nlayout: text -->\n## A"),
    ("```\n<!-- layout: text -->\n```", None, None, "```\n<!-- layout: text -->\n```"),
]


@pytest.mark.parametrize(("text", "layout", "accent", "body"), MARKER_CASES)
def test_a_slides_marker_says_its_layout_and_is_no_part_of_the_slide(
    text: str, layout: str | None, accent: str | None, body: str
) -> None:
    assert layout_of(text) == (layout, body)
    # with_layout keeps the accent a title slide had, which shows what the marker carried.
    first = with_layout(text, "title").splitlines()[0]
    assert first == "<!-- layout: title" + (f"; accent: {accent}" if accent else "") + " -->"


def test_a_marker_is_put_first_and_only_the_title_slide_keeps_an_accent() -> None:
    assert with_layout("## A\n\n- a", "flow") == "<!-- layout: flow -->\n## A\n\n- a"
    assert with_layout("<!-- layout: cards -->\n## A", "flow") == "<!-- layout: flow -->\n## A"
    title = "<!-- layout: title; accent: teal -->\n# T\n\nmessage"
    assert with_layout(title, "title") == title
    assert with_layout(title, "text") == "<!-- layout: text -->\n# T\n\nmessage"
    assert with_layout("# T", "title", "blue") == "<!-- layout: title; accent: blue -->\n# T"
    assert with_layout("# T", "title", "pink") == "<!-- layout: title -->\n# T"
    assert with_layout("# T", "compare", "blue") == "<!-- layout: compare -->\n# T"
    assert with_layout("", "text") == "<!-- layout: text -->"


def test_a_theme_survives_layout_and_accent_rewrites() -> None:
    title = "<!-- layout: title; accent: teal; theme: editorial -->\n# T\n\nmessage"
    assert _marked(title).theme == "editorial"
    assert with_layout(title, "title") == title
    assert (
        with_layout(title, "title", "blue")
        == "<!-- layout: title; accent: blue; theme: editorial -->\n# T\n\nmessage"
    )
    assert with_layout(title, "text") == "<!-- layout: text -->\n# T\n\nmessage"
    assert _marked("<!-- layout: title; theme: future -->\n# T").theme is None


def test_a_composition_marker_is_per_slide_and_survives_accent_theme_and_body_rewrites() -> None:
    quote = "<!-- layout: quote; composition: split -->\n## T\n\n> Exact words\n\n— Speaker [1]"
    assert _marked(quote).composition == "split"
    assert with_layout(quote, "quote", composition="spotlight").startswith(
        "<!-- layout: quote; composition: spotlight -->"
    )
    title = "<!-- layout: title; theme: editorial -->\n# Deck"
    assert with_layout(title, "title", "blue", composition="standard").startswith(
        "<!-- layout: title; accent: blue; theme: editorial -->"
    )
    assert (
        _marked("<!-- layout: compare; composition: split -->\n## Table").composition == "standard"
    )


def test_a_diagram_draws_only_when_every_relationship_is_exactly_supported() -> None:
    text = (
        "<!-- layout: diagram -->\n## A leads to B\n"
        "| From | Relationship | To |\n| --- | --- | --- |\n| A | leads to [1] | B |"
    )
    def checked(claim: str, outcome: str) -> Draft:
        return Draft(text, verdicts_of(verdict("checker", {
            "text": claim, "refs": [1], "verdict": outcome,
        })))

    supported = checked("A | leads to | B", "supported")
    assert diagram_fallback_reason(supported) is None
    unsupported = checked("A | leads to | B", "unsupported")
    assert diagram_fallback_reason(unsupported)
    assert "| A | leads to [1] | B |" in diagram_as_text(unsupported).text
    assert diagram_fallback_reason(checked("Next process runs", "supported"))
    assert diagram_fallback_reason(checked("A | leads to | B", "unchecked"))


def test_a_deck_keeps_each_slides_marker_with_it_and_the_hash_covers_it() -> None:
    content = (
        "<!-- layout: title; accent: blue -->\n# Deck\n\nsub\n\n---\n\n"
        "<!-- layout: flow -->\n## One\n\n1. a\n2. b"
    )
    sections = split_document(content, "slides")[1]
    assert sections == [
        "<!-- layout: title; accent: blue -->\n# Deck\n\nsub",
        "<!-- layout: flow -->\n## One\n\n1. a\n2. b",
    ]
    bare = "## One\n\n1. a\n2. b"
    assert section_hash(sections[1]) != section_hash(bare)
    assert section_hash(with_layout(bare, "flow")) == section_hash(sections[1])
    assert title_of(content) == "Deck"


def test_the_sentences_that_cite_passages_are_found_with_their_numbers() -> None:
    text = "## Heading\n\nOne fact [1]. Two facts [2][3].\n- bullet [4]\nNo cite.\n"
    found = cited_sentences(text)
    assert [(chunk.strip(), numbers) for _, _, chunk, numbers in found] == [
        ("One fact [1].", [1]),
        ("Two facts [2][3].", [2, 3]),
        ("- bullet [4]", [4]),
    ]
    # A number after the full stop belongs to the sentence before it.
    [(_, _, chunk, numbers)] = cited_sentences("A fact. [5]")
    assert (chunk, numbers) == ("A fact. [5]", [5])
    assert _marked_unsourced("CD3D marks T cells [1][2].\n") == "CD3D marks T cells [?].\n"


def test_a_slides_marker_is_no_sentence_and_the_positions_stay_those_of_the_slide() -> None:
    slide = "<!-- layout: text -->\n## Heading\n\nOne fact [1]. Two facts [2][3]."
    found = cited_sentences(slide)
    assert [(slide[begin:end], numbers) for begin, end, _, numbers in found] == [
        ("One fact [1].", [1]),
        (" Two facts [2][3].", [2, 3]),
    ]
    assert [chunk for _, _, chunk, _ in found] == [slide[begin:end] for begin, end, _, _ in found]


def test_a_continuation_line_citation_stays_with_its_sentence() -> None:
    for source in ("One fact.\n[1]", "One fact\r\n[1]"):
        assert cited_sentences(source) == [(0, len(source), source, [1])]
    assert cited_sentences("One fact.\n\n[1]") == []
    assert cited_sentences("One fact.\n```\nx[7]\n```\n[1]") == []


def test_the_checker_reads_a_slide_under_its_marker_and_gives_the_marker_back() -> None:
    slide = (
        "<!-- layout: text -->\n## T cells\n\n- CD3D marks T cells [1]\n\nNote: Close on CD3D [1]."
    )
    pool = Pool(10)
    pool.adopt(Evidence("library", "Markers", "CD3D is a marker of T cells."))

    quiet = FakeProvider(output_replies(OUTLINE, REPORT))
    agents = StudioWorkAgents(gateway(quiet), "slides")
    model = agents.gateway.models[0]
    draft = agents.check_built(model, slide, pool, 0)
    assert draft.text == slide and draft.issues == []
    assert draft.coverage["status"] == "complete"
    assert draft.coverage["answerHash"] == section_hash(slide)
    assert all("layout" not in claim["text"] for claim in draft.coverage["claims"])
    retried = Draft(slide, agents.check_unbuilt(model, slide, pool.by_ref()))
    assert retried.issues == []
    coverage = coverage_of(retried.verdicts, section_hash(slide))
    assert coverage["answerHash"] == section_hash(slide)
    assert all("layout" not in claim["text"] for claim in coverage["claims"])
    # Nothing the models were asked had the marker in it.
    assert quiet.requests and not any("<!--" in str(r["messages"]) for r in quiet.requests)

    flagging = FakeProvider('{"sentences":[{"n":1,"verdict":"not"},{"n":2,"verdict":"supported"}]}')
    flagger = StudioWorkAgents(gateway(flagging), "slides")
    flagged = flagger.check_built(model, slide, pool, 0)
    assert flagged.text == slide
    assert "unsupported" in [issue.verdict for issue in flagged.issues]
    assert not any("<!--" in str(r["messages"]) for r in flagging.requests)


# Building --------------------------------------------------------------------------------------


def test_a_studio_report_is_planned_written_checked_and_saved(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        headers = workspace(client)
        events = build(client, base_id, body(brief="How immune cells are told apart"))
        artifact = built(events)
        stored = client.get(route(base_id, artifact["id"]), headers=headers).json()
        listed = client.get(f"/api/knowledge-bases/{base_id}/artifacts", headers=headers).json()

    assert artifact["content"].startswith("<!-- report: v2 -->\n# Immune markers")
    assert "B cells are set apart by CD19 [1], and T cells by CD3D [2]." in artifact["content"]
    assert artifact["content"].endswith("CD19 marks B cells [1].")
    assert [(c["sourceTitle"], c["ref"]) for c in artifact["citations"]] == [
        ("Receptors", 1),
        ("Markers", 2),
    ]
    assert artifact["citations"][1]["quote"] == "CD3D is a marker of T cells."
    assert (artifact["title"], artifact["kind"], artifact["style"]) == (
        "Immune markers",
        "report",
        "report",
    )
    assert (artifact["origin"], artifact["versionNumber"], artifact["format"]) == (
        "build",
        1,
        "report",
    )
    assert [item["heading"] for item in artifact["outline"]] == ["T cells", "B cells"]
    assert [{k: v for k, v in s.items() if k != "coverage"} for s in artifact["sections"]] == [
        {"index": 0, "heading": "T cells", "checked": True, "issues": []},
        {"index": 1, "heading": "B cells", "checked": True, "issues": []},
    ]
    assert all(s["coverage"]["status"] == "complete" for s in artifact["sections"])
    assert artifact["scope"] == {
        "mode": "library",
        "sourceIds": [],
        "unitIds": [],
        "sessionIds": [],
    }
    assert artifact["inputs"] == {"sources": 2, "units": 0, "sessions": 0}
    assert artifact["modelLabel"] == "DeepSeek · Flash"
    provenance = artifact["provenance"]
    assert (provenance["schemaVersion"], provenance["acceptedOnly"]) == (2, False)
    assert provenance["generator"] == "gunther.studio.v1"
    assert provenance["model"] == {
        "ref": "deepseek/deepseek-flash",
        "label": "DeepSeek · Flash",
        "effort": "high",
    }
    assert (provenance["kind"], provenance["origin"], provenance["audience"]) == (
        "report",
        "build",
        "scientist",
    )
    assert stored == artifact
    assert [(item["kind"], item["style"], item["origin"]) for item in listed] == [
        ("report", "report", "build")
    ]
    assert "content" not in listed[0]

    assert events[-1][0] == "done"
    outline = next(data for name, data in events if name == "outline")
    assert outline["title"] == "Immune markers"
    stages = [data["id"] for name, data in events if name == "stage" and data["state"] == "done"]
    assert stages == ["memo", "story", "write", "verify"]
    assert sum(is_outlining(r) for r in fake.requests) == 1


def test_a_studio_deck_keeps_its_speaker_notes(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(SLIDES_OUTLINE, SLIDES))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        events = build(client, base_id, body(kind="slides", audience="student"))
        artifact = built(events)

    assert artifact["kind"] == artifact["style"] == "slides"
    assert artifact["format"] == "slides"
    assert artifact["title"] == "Immune markers"
    assert "layout: title; design: v2" in artifact["content"]
    slides = split_document(artifact["content"], "slides")[1]
    assert [heading_of(slide) for slide in slides] == ["Immune markers", "T cells", "Takeaways"]
    assert "\nNote: The marker is found on every T cell [1]." in slides[1]
    assert [s["heading"] for s in artifact["sections"]] == [
        "Immune markers",
        "T cells",
        "Takeaways",
    ]
    assert [c["sourceTitle"] for c in artifact["citations"]] == ["Markers"]
    assert artifact["provenance"]["generator"] == "gunther.studio.v1"
    assert artifact["sections"][0]["checked"]


def test_a_title_slide_that_is_typed_over_is_checked(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(SLIDES_OUTLINE, SLIDES))
    with built_output(tmp_path, fake, kind="slides", audience="student") as built_deck:
        client, base_id, first = built_deck
        headers = workspace(client)
        typed = first["content"].replace(
            "How cells are told apart", "A guide to telling cells apart"
        )
        second = client.post(
            route(base_id, first["id"], "/edits"),
            headers=headers,
            json={"clientRequestId": "edit_request_0005", "content": typed},
        ).json()
        assert [s["checked"] for s in second["sections"]] == [False, True, True]
        before = len(fake.requests)
        checked = client.post(route(base_id, second["id"], "/check"), headers=headers).json()
        # The audit determines whether the edited title contains factual claims.
        assert len(fake.requests) == before + 2
        assert [s["checked"] for s in checked["sections"]] == [True, True, True]
        assert checked["content"] == typed


@pytest.mark.parametrize(("owned", "theme", "cover_design"), [
    (False, "keynote", "v1"),
    (True, "ink", "v2"),
], ids=["legacy-cover-sealed-brief", "studio-cover-live-brief"])
def test_manual_slide_commands_use_effective_preferences_and_check_again_checks_evidence(
    tmp_path: Path, monkeypatch, owned: bool, theme: str, cover_design: str,
) -> None:
    from gunther.slide_review import review_deck
    from gunther.studio import design

    fake = FakeProvider(output_replies(SLIDES_OUTLINE, SLIDES))
    calls = []
    restyle_deck = design.restyle

    def review(built, *_args, spec, **_kwargs):
        calls.append(("review", spec.theme, spec.emphasize, spec.avoid))
        return built

    def restyle(agents, model, slides, style, *, spec, **kwargs):
        calls.append(("restyle", spec.theme, spec.emphasize, spec.avoid))
        return restyle_deck(agents, model, slides, style, spec=spec, **kwargs)

    monkeypatch.setattr(f"{review_deck.__module__}.review_deck", review)
    monkeypatch.setattr(design, "restyle", restyle)
    sealed = PlanPreferencesOut(
        emphasize=[{"id": "em_old", "text": "Original framing", "mark": "edited"}],
        avoid=[{"id": "av_old", "text": "Original exclusion", "mark": "edited"}],
    )
    live = PlanPreferencesOut(
        emphasize=[{"id": "em_live", "text": "Current framing", "mark": "edited"}],
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        headers = workspace(client)
        cid = "slide_preferences" if owned else None
        service = client.app.state.knowledge_service
        if owned:
            client.post(f"/api/knowledge-bases/{base_id}/artifact-conversations",
                        headers=headers, json={"id": cid}).raise_for_status()
            with service.sessions.begin() as session:
                session.get(ArtifactConversation, cid).brief_json = live.model_dump_json()
        cover = slide_format.with_marker(SLIDES[0], "title", design=cover_design, theme=theme)
        first = saved_output(
            client, base_id, kind="slides", written=[cover, *SLIDES[1:]],
            conversationId=cid, preferences=sealed,
        )

        def cover_attrs(artifact):
            slides = split_document(artifact["content"], "slides")[1]
            return slide_format.parse_slide(slides[0]).attrs

        def command(artifact, operation, **fields):
            path = route(base_id, artifact["id"], f"/{operation}/stream")
            with client.stream("POST", path, headers=headers, json={
                "clientRequestId": f"pref_{operation}", **fields,
            }) as response:
                response.raise_for_status()
                return built(read_events(response))

        reviewed = command(first, "visual-review")
        restyled = command(reviewed, "restyle", style="dark")
        assert restyled["versionNumber"] == 3 and restyled["lineageId"] == first["lineageId"]
        effective = live if owned else sealed
        expected = (
            theme, tuple(line.text for line in effective.emphasize),
            tuple(line.text for line in effective.avoid),
        )
        assert calls == [("review", *expected), ("restyle", *expected)]
        assert cover_attrs(reviewed) == cover_attrs(first)
        assert cover_attrs(restyled) == {**cover_attrs(first), "style": "dark"}
        for version in (reviewed, restyled):
            assert version["provenance"]["preferences"] == effective.model_dump(by_alias=True)

        if owned:
            effective = PlanPreferencesOut()
            with service.sessions.begin() as session:
                session.get(ArtifactConversation, cid).brief_json = effective.model_dump_json()
        typed = restyled["content"].replace("CD3D marks T cells", "CD3D identifies T cells", 1)
        assert typed != restyled["content"]
        before_edit = len(fake.requests)
        edited = client.post(route(base_id, restyled["id"], "/edits"), headers=headers, json={
            "clientRequestId": "literal_after_restyle", "content": typed,
        }).raise_for_status().json()
        assert len(fake.requests) == before_edit
        assert edited["provenance"]["preferences"] == effective.model_dump(by_alias=True)
        assert cover_attrs(edited) == cover_attrs(restyled) and edited["content"] == typed
        before = len(fake.requests)
        checked = client.post(
            route(base_id, edited["id"], "/check"), headers=headers,
        ).raise_for_status().json()
        prompts = json.dumps(fake.requests[before:])
        assert len(fake.requests) > before and "CD3D identifies T cells" in prompts
        assert all(text not in prompts for text in ("Current framing", "Original framing",
                                                   "Original exclusion"))
        assert checked["content"] == typed and checked["manifestHash"] == edited["manifestHash"]


def test_visual_review_cannot_save_a_changed_cover_theme(tmp_path: Path, monkeypatch) -> None:
    from gunther import slide_review

    def repair_cover(built, *_args, **_kwargs):
        # A composition-only repair can reach the legacy layout writer on the Studio cover.
        slide_id = built.outline[0].get("id") or "slide-1"
        repair = slide_review.LayoutRepair(slideId=slide_id, composition="standard")
        return slide_review.apply_repairs(
            built, slide_review.VisualRepairs(pages=[repair]), {slide_id},
        )

    monkeypatch.setattr(slide_review, "review_deck", repair_cover)
    fake = FakeProvider(output_replies(SLIDES_OUTLINE, SLIDES))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        headers = workspace(client)
        cover = slide_format.with_marker(SLIDES[0], "title", design="v2", theme="ink")
        initial = saved_output(client, base_id, written=[cover, *SLIDES[1:]], kind="slides")

        with client.stream(
            "POST", route(base_id, initial["id"], "/visual-review/stream"),
            headers=headers, json={"clientRequestId": "review_cover_theme"},
        ) as response:
            response.raise_for_status()
            [(name, error)] = read_events(response)
        assert name == "error" and error["status"] == 502 and "cover theme" in error["detail"]
        assert [version.id for version in versions(client)] == [initial["id"]]


def test_audio_retry_uses_ordered_voices_without_rewriting_the_sealed_script(
    tmp_path: Path, monkeypatch,
) -> None:
    from test_output_audio import EVIDENCE, SCRIPT, VOICES, configure, model_reply, retry

    def reply(request):
        if "Propose the direction for Audio overview" in studio_task(request):
            return json.dumps({
                "title": "Cell fates", "summary": "Review the finding", "angle": "The trial",
                "coverage": [{"text": "Cell differentiation"}],
            })
        return model_reply(request)

    fake = FakeProvider(reply)
    with app_for(tmp_path, fake, ai_summaries="off") as client:
        calls = configure(client, monkeypatch, tmp_path / "gunther.sqlite", fail=True)
        base_id, _ = library(client, notes={"Trial": EVIDENCE})
        headers = workspace(client)
        cid, plan_id = "audio_preferences", "draft_audio_preferences"
        voices = VOICES[:2][::-1]
        with patch.object(ArtifactAgent, "_decide", return_value=Decision(
            actions=[Act(do="plan", change="whole")],
        )):
            planned = built(output_turn(client, base_id, cid, {
                "kind": "plan", "clientRequestId": "plan_audio_retry", "planId": plan_id,
                "setup": {"type": "audio_overview", "scope": {"mode": "library"},
                          "voices": voices},
            }, headers))
        assert calls == [] and versions(client) == []
        result = built(approve_output(
            client, base_id, cid, plan_id, "approve_audio_retry", planned, headers,
        ))["artifact"]
        assert result["content"] == SCRIPT and result["audio"]["status"] == "error"
        assert result["provenance"]["creationPlan"]["options"]["voices"] == voices
        before = len(fake.requests)
        ready = retry(client, route(base_id, result["id"]), headers)
        assert ready["audio"]["status"] == "ready" and ready["audio"]["voices"] == voices
        assert [voice for voice, _ in calls[1:]] == [voices[0], voices[1], voices[0]]
        assert len(fake.requests) == before and len(versions(client)) == 1
        assert ready["contentHash"] == ready["audio"]["contentHash"] == result["contentHash"]
        assert ready["manifestHash"] == result["manifestHash"]


def test_a_selection_searches_only_what_was_chosen(tmp_path: Path) -> None:
    written = ["## T cells\n\nCD3D marks T cells [1].", "## B cells\n\nNothing was found."]
    fake = FakeProvider(output_replies(OUTLINE, written))
    with app_for(tmp_path, fake) as client:
        base_id, ids = library(client)
        other_id, other = library(client, "Other", {"Elsewhere": "CD8 marks killer T cells."})
        start = len(fake.requests)  # what a model is asked while sources are read is not ours
        request = body(scope={"mode": "selection", "sourceIds": [ids["Markers"]]},
                       librarySupplements=True)
        assert "librarySupplements" not in BuildOutputInput.model_json_schema()["properties"]
        artifact = built(build(client, base_id, request))
        assert not artifact["provenance"]["librarySupplements"]
        before = len(fake.requests)
        refused = build(
            client, base_id, body(scope={"mode": "selection", "sourceIds": [other["Elsewhere"]]})
        )
        nothing = build(
            client, other_id, body(scope={"mode": "selection", "unitIds": ["unt_gone"]})
        )

    assert [c["sourceTitle"] for c in artifact["citations"]] == ["Markers"]
    assert artifact["scope"]["sourceIds"] == [ids["Markers"]]
    assert artifact["inputs"] == {"sources": 1, "units": 0, "sessions": 0}
    # Nothing outside the choice reached a model: not as a passage, not as a title.
    everything = json.dumps([r["messages"] for r in fake.requests[start:before]])
    assert "CD19 is a marker" not in everything and "Receptors" not in everything
    # A source of another library is refused before any model is asked.
    [(name, error)] = refused
    assert name == "error" and error["status"] == 422
    assert error["issues"][0]["loc"] == ["options", "scope"]
    [(_, gone)] = nothing
    assert gone["status"] == 422 and gone["issues"][0]["loc"] == ["options", "scope"]
    assert len(fake.requests) == before


def test_an_empty_library_is_not_built_from(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client, notes={})
        events = build(client, base_id, body())
    [(name, error)] = events
    assert name == "error" and error["status"] == 422
    assert error["detail"] == "There is nothing to build from yet. Add sources."
    assert fake.requests == []


@pytest.mark.parametrize("entry", ["build", "conversation"])
def test_saved_knowledge_seeds_the_pool_so_its_passages_can_be_cited_without_a_search(
    tmp_path: Path, entry: str,
) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        session_id = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}
        ).json()["id"]
        turn = ask_turn(client, session_id, {"content": "CD3D?"}, headers=SIDECAR)
        message_id = turn["assistantMessage"]["id"]
        unit_id = client.post(
            f"/api/sessions/{session_id}/messages/{message_id}/proposal",
            headers=SIDECAR,
            json={"title": "The T-cell marker"},
        ).json()["knowledgeUnitId"]
        scope = {"mode": "selection", "unitIds": [unit_id]}
        writer = output_replies(ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."])
        before = len(fake.requests)
        if entry == "build":
            fake.replies[:] = [writer]
            artifact = built(build(client, base_id, body(scope=scope)))
        else:
            fake.replies[:] = [orchestrator_replies([{"actions": [
                {"do": "select", "artifact": "new:report"},
                {"do": "plan", "change": "whole"},
            ]}], writer)]
            headers = workspace(client)
            path = f"/api/knowledge-bases/{base_id}/artifact-conversations"
            conversation = client.post(path, headers=headers, json={"scope": scope})
            cid = conversation.raise_for_status().json()["id"]
            response = client.post(
                f"{path}/{cid}/messages/stream", headers=headers,
                json={"kind": "start", "text": "Make a report", "clientRequestId": "saved-unit"},
            )
            planned = built(read_events(response.raise_for_status()))
            assert planned["outcome"] == "planned" and versions(client) == []
            artifact = built(approve_output(
                client, base_id, cid, planned["plan"]["planId"], "approve_saved_unit", planned,
            ))["artifact"]
        with client.app.state.knowledge_service.sessions() as session:
            bindings = session.scalars(
                select(ArtifactUnitBinding).where(ArtifactUnitBinding.artifact_id == artifact["id"])
            ).all()

    pinned = turn["assistantMessage"]["citations"][0]
    assert artifact["citations"][0]["sourceRevisionId"] == pinned["sourceRevisionId"]
    assert artifact["citations"][0]["blockId"] == pinned["blockId"]
    [citation] = artifact["citations"]
    assert (citation["sourceTitle"], citation["quote"]) == (
        "Markers",
        "CD3D is a marker of T cells.",
    )
    assert artifact["content"].endswith("CD3D marks T cells [1].")
    assert artifact["inputs"] == {"sources": 0, "units": 1, "sessions": 0}
    # The saved answer is pinned, as the old builder pinned them.
    assert artifact["acceptedUnitIds"] == [unit_id]
    assert [(b.unit_id, b.position) for b in bindings] == [(unit_id, 0)]
    [snapshot] = artifact["unitSnapshots"]
    assert (snapshot["unitId"], snapshot["sourceMessageId"], snapshot["evidenceCount"]) == (
        unit_id, message_id, 1,
    )
    assert artifact["provenance"]["revisionIds"] == [snapshot["revisionId"]]
    new = fake.requests[before:]
    assert not any(is_grading(r) for r in new)
    # The planner and the writer read the saved answer as a note, not as a source.
    planning = next(r for r in new if is_outlining(r))
    assert "The T-cell marker" in planning["messages"][-1]["content"]
    writing = next(r for r in new if "Write the report" in studio_task(r))
    assert "saved notes (context, never cited)" in writing["messages"][-1]["content"]


def test_a_saved_answers_passage_from_a_source_that_has_left_the_library_is_not_used(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake) as client:
        base_id, ids = library(client)
        session_id = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}
        ).json()["id"]
        turn = ask_turn(client, session_id, {"content": "CD3D?"}, headers=SIDECAR)
        unit_id = client.post(
            f"/api/sessions/{session_id}/messages/{turn['assistantMessage']['id']}/proposal",
            headers=SIDECAR,
            json={"title": "The T-cell marker"},
        ).json()["knowledgeUnitId"]
        # The source the answer cited goes to Trash; the saved answer stays.
        trashed = client.post(f"/api/sources/{ids['Markers']}/trash", headers=SIDECAR)
        assert trashed.status_code == 200
        fake.replies = [output_replies(ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."])]
        start = len(fake.requests)
        artifact = built(
            build(client, base_id, body(scope={"mode": "selection", "unitIds": [unit_id]}))
        )
        with client.app.state.knowledge_service.sessions() as session:
            assert session.get(Source, ids["Markers"]).trashed_at is not None
    assert artifact["content"].endswith("CD3D marks T cells.")
    assert artifact["acceptedUnitIds"] == [unit_id]
    assert artifact["citations"] == []
    assert "[1]" not in artifact["content"]
    shown = json.dumps([r["messages"] for r in fake.requests[start:]])
    assert "The T-cell marker" in shown and "CD3D marks T cells" in shown
    assert "CD3D is a marker of T cells." not in shown


def test_a_discussion_seeds_the_pool_too(tmp_path: Path) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        session_id = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}
        ).json()["id"]
        turn = ask_turn(client, session_id, {"content": "CD3D?"}, headers=SIDECAR)
        fake.replies = [output_replies(ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."])]
        artifact = built(
            build(client, base_id, body(scope={"mode": "selection", "sessionIds": [session_id]}))
        )
    assert artifact["inputs"]["sessions"] == 1
    assert [c["sourceTitle"] for c in artifact["citations"]] == ["Markers"]
    pinned = turn["assistantMessage"]["citations"][0]
    assert artifact["citations"][0]["sourceRevisionId"] == pinned["sourceRevisionId"]
    assert artifact["citations"][0]["quote"] == pinned["quote"]
    assert artifact["acceptedUnitIds"] == []


def test_delete_forever_drops_cached_passage_before_grouped_read(tmp_path: Path) -> None:
    fake = FakeProvider(agent_replies(
        "CD19 marks B cells [1].",
        {"action": "search_library", "query": "CD19"},
        {"action": "answer"},
    ))
    with app_for(tmp_path, fake) as client:
        base_id, ids = library(client)
        source_id = ids["Markers"]
        discussion = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={},
        ).raise_for_status().json()["id"]
        ask_turn(client, discussion, {"content": "CD19?"}, headers=SIDECAR)
        service = client.app.state.knowledge_service
        with service.sessions() as session:
            scope = OutputScope(mode="selection", source_ids=[source_id], session_ids=[discussion])
            material = service._output_material(session, base_id, scope)
            [seed] = material.seeds
            assert seed.payload[0].source_id == ids["Receptors"]
            pool = Pool(OutputLimits().pool)
            for seed in material.seeds:
                pool.adopt(seed)
            assert service._studio_reading(
                session, material.source_revisions, pool, pinned=material.seeds,
            ) == []
            [cached] = [item for item in pool.items if item.payload[0].source_id == source_id]

        client.post(f"/api/sources/{source_id}/trash", headers=SIDECAR).raise_for_status()
        client.delete(f"/api/trash/source/{source_id}", headers=SIDECAR).raise_for_status()
        with service.sessions() as session:
            gaps = service._studio_reading(
                session, material.source_revisions, pool, pinned=material.seeds,
            )

    assert gaps == [f"Source {source_id} is selected but unavailable, so it was not read."]
    assert cached.ref not in pool.by_ref()
    assert set(pool.known) == {seed.key}


def test_the_studio_report_can_record_an_unsupported_claim(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(
        ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."], unsupported=[1, 2]
    ))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        artifact = built(build(client, base_id, body()))
    assert any(issue["verdict"] == "unsupported" for issue in artifact["sections"][0]["issues"])


def test_without_a_model_or_when_a_model_fails_nothing_is_saved(tmp_path: Path) -> None:
    unused = FakeProvider("unused")
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'one.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        deepseek_api_key=None,
        stt_provider="compatible",
        processing_worker_enabled=False,
        auth_token=SIDECAR_TOKEN,
        service_settings_file=tmp_path / "service-settings.json",
    )
    with TestClient(create_app(settings, model_client_factory=unused.factory)) as client:
        base_id, _ = library(client)
        events = build(client, base_id, body())
        assert versions(client) == []
    assert events[-1] == (
        "error",
        {"status": 400, "detail": "Outputs need a model. Set one up under Settings → Models."},
    )
    assert unused.requests == []

    # A writer the provider refuses.
    broken = FakeProvider(
        output_replies(
            OUTLINE, lambda index, prompt: api_error(openai.AuthenticationError, 401, "no")
        )
    )
    with app_for(tmp_path / "two", broken) as client:
        base_id, _ = library(client)
        events = build(client, base_id, body())
        assert versions(client) == []
    assert events[-1] == (
        "error",
        {"status": 502, "detail": "DeepSeek did not accept the API key."},
    )
    assert [name for name, _ in events].count("outline") == 1

    # A planner that cannot be read.
    garbled = FakeProvider("this is not json")
    with app_for(tmp_path / "three", garbled) as client:
        base_id, _ = library(client)
        events = build(client, base_id, body())
        assert versions(client) == []
    name, error = events[-1]
    assert name == "error" and error["status"] == 502
    assert "could not read" in error["detail"]


def test_the_outputs_job_can_have_its_own_model_and_otherwise_follows_ask(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(ONE_SECTION, ["## T cells\n\nCD3D marks T cells [1]."]))
    with app_for(tmp_path, fake) as client:
        client.post(
            "/api/settings/providers",
            headers=SIDECAR,
            json={
                "kind": "kimi",
                "apiKey": "sk-test-0000000000001234",
                "models": ["kimi-k3", "kimi-k2.6"],
            },
        )

        def roles() -> dict[str, dict]:
            found = client.get("/api/settings/models", headers=SIDECAR).json()["roles"]
            return {role["id"]: role for role in found}

        saved = client.app.state.service_settings
        # Ask moves, and Outputs, with no model of its own, goes with it.
        client.put(
            "/api/settings/model-roles",
            headers=SIDECAR,
            json={"roles": {"ask": {"model": "kimi/kimi-k3", "effort": "max"}}},
        )
        assert roles()["outputs"]["model"] == "kimi/kimi-k3"
        assert roles()["outputs"]["effort"] == "high"
        base_id, _ = library(client)
        artifact = built(build(client, base_id, body()))
        assert artifact["provenance"]["model"]["ref"] == "kimi/kimi-k3"
        # Saving another job does not pin it to Ask's model.
        client.put(
            "/api/settings/model-roles",
            headers=SIDECAR,
            json={"roles": {"photos": {"model": "kimi/kimi-k3", "effort": "low"}}},
        )
        assert "outputs" not in saved.models()[1]
        # Choosing one for it does.
        client.put(
            "/api/settings/model-roles",
            headers=SIDECAR,
            json={"roles": {"outputs": {"model": "deepseek/deepseek-v4-pro", "effort": "low"}}},
        )
        client.put(
            "/api/settings/model-roles",
            headers=SIDECAR,
            json={"roles": {"ask": {"model": "deepseek/deepseek-flash", "effort": "high"}}},
        )
        assert saved.models()[1]["outputs"] == {
            "model": "deepseek/deepseek-v4-pro",
            "effort": "low",
        }
        assert roles()["outputs"]["model"] == "deepseek/deepseek-v4-pro"
        second = built(build(client, base_id, body()))
    assert second["provenance"]["model"] == {
        "ref": "deepseek/deepseek-v4-pro",
        "label": "DeepSeek · V4 Pro",
        "effort": "low",
    }


@pytest.mark.parametrize("with_supplements_field", [True, False])
def test_the_legacy_build_replays_the_original_sealed_version(
    tmp_path: Path, with_supplements_field: bool,
) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        request = body(style="overview", approach={"question": "What marks the cells?"},
                       librarySupplements=True)
        service = client.app.state.knowledge_service
        headers = workspace(client)
        fields = service._output_request_fields(BuildOutputInput.model_validate(request))
        if not with_supplements_field:
            fields.pop("library_supplements")
        fingerprint = service._output_fingerprint(
            headers["X-Gunther-Workspace-Id"], base_id, "build", fields,
        )
        first = saved_output(client, base_id, fingerprint=fingerprint, **request)
        calls = len(fake.requests)
        again = legacy_build(client, base_id, request, headers)
        different = legacy_build(client, base_id, {**request, "audience": "student"}, headers)
        stored = versions(client)
    assert again == [("done", first)]
    assert len(fake.requests) == calls and len(stored) == 1
    assert different == [
        (
            "error",
            {
                "status": 409,
                "detail": "clientRequestId was already used for a different Artifact request",
            },
        )
    ]


@pytest.mark.parametrize("fixed_outline", [False, True])
def test_fresh_legacy_build_requires_a_plan_without_creating_work_or_a_job(
    tmp_path: Path, fixed_outline: bool,
) -> None:
    fake = FakeProvider()
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        headers = workspace(client)
        request = body(conversationId="compat_headless")
        if fixed_outline:
            request["outline"] = [{"heading": "Results", "goal": "Evidence"}]
        before = len(fake.requests)
        [(name, error)] = legacy_build(client, base_id, request, headers)
        assert name == "error" and (error["status"], error["code"]) == (409, "plan_required")
        assert versions(client) == [] and len(fake.requests) == before
        assert "output-conv:compat_headless" not in activity_of(client)
        with client.app.state.knowledge_service.sessions() as session:
            assert session.get(ArtifactConversation, "compat_headless") is None
        [(name, invisible)] = legacy_build(client, "absent_library", request, headers)
        assert name == "error" and invisible["status"] == 404


# Rebuilding, editing, revising and checking -------------------------------------------------------


def test_a_rebuild_gives_the_edited_outline_to_studio_and_is_the_next_version(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        output_replies(OUTLINE, lambda index, prompt: f"## {heading(prompt)}\n\nA fact [1].")
    )
    with app_for(tmp_path, fake) as client:
        base_id, sources = library(client)
        first = built(build(client, base_id, body(
            scope={"mode": "selection", "sourceIds": [sources["Markers"]]},
        )))
        planners = sum(is_outlining(r) for r in fake.requests)
        edited = [
            {"heading": "B cells", "goal": "Start with B cells", "notes": "Describe the assay"},
            {"heading": "T cells", "goal": ""},
        ]
        request = body(outline=edited, supersedesArtifactId=first["id"])
        second = built(legacy_build(client, base_id, request))
        stale = legacy_build(client, base_id, body(supersedesArtifactId=first["id"]))
        assert legacy_build(client, base_id, request) == [("done", second)]
        [(name, error)] = legacy_build(client, base_id, request | {"librarySupplements": True})
        assert name == "error" and error["status"] == 409

    assert planners == 1 and sum(is_outlining(r) for r in fake.requests) == planners
    assert (second["versionNumber"], second["origin"]) == (2, "rebuild")
    assert second["lineageId"] == first["lineageId"]
    assert second["supersedesArtifactId"] == first["id"]
    assert second["scope"] == first["scope"]
    assert second["title"] == "Immune markers"
    assert [item["heading"] for item in second["outline"]] == ["B cells", "T cells"]
    assert [s["heading"] for s in second["sections"]] == ["B cells", "T cells"]
    writing = [studio_task(r) for r in fake.requests if "Write the report" in studio_task(r)]
    assert '"heading": "B cells", "goal": "Start with B cells"' in writing[-1]
    assert '"notes": "Describe the assay"' in writing[-1]
    assert second["outline"][0]["notes"] == "Describe the assay"
    # Only the latest version can be built on.
    [(name, error)] = stale
    assert name == "error" and error["status"] == 409 and "latest version" in error["detail"]


def test_verified_slide_rebuild_keeps_the_saved_decks_rendered_style_and_theme(tmp_path: Path):
    fake = FakeProvider(output_replies(SLIDES_OUTLINE, SLIDES))
    cover = slide_format.with_marker(SLIDES[0], "title", design="v2", theme="ink", style="dark")
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        first = saved_output(client, base_id, written=[cover, *SLIDES[1:]], kind="slides")
        second = built(legacy_build(
            client, base_id, body(kind="slides", supersedesArtifactId=first["id"]),
        ))
    assert second["lineageId"] == first["lineageId"] and second["versionNumber"] == 2
    slides = split_document(second["content"], "slides")[1]
    attrs = slide_format.parse_slide(slides[0]).attrs
    assert {name: attrs[name] for name in ("design", "theme", "style")} == {
        "design": "v2", "theme": "ink", "style": "dark",
    }
    assert all("Choose the visual style" not in studio_task(request) for request in fake.requests)


CLAIM = "CD19 marks B cells, and a good deal more besides in some tissues"


@contextmanager
def check_reads(client: TestClient):
    with client.app.state.knowledge_service.sessions() as session:
        engine = session.get_bind()
    reads = []

    def record_read(_connection, _cursor, statement, parameters, _context, _executemany):
        if statement.startswith("SELECT") and "FROM artifact_checks" in statement:
            reads.append(parameters)

    event.listen(engine, "before_cursor_execute", record_read)
    try:
        yield reads
    finally:
        event.remove(engine, "before_cursor_execute", record_read)


def test_an_edit_keeps_the_typed_text_and_checks_only_what_it_did_not_change(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT, unmarked=[CLAIM]))
    with built_output(tmp_path, fake) as (client, base_id, first):
        headers = workspace(client)
        typed = first["content"].replace("CD19 marks B cells [1].", f"{CLAIM} [1].")
        assert typed != first["content"]
        request = {"clientRequestId": "edit_request_0001", "content": typed}
        response = client.post(route(base_id, first["id"], "/edits"), headers=headers, json=request)
        assert response.status_code == 201
        second = response.json()
        again = client.post(route(base_id, first["id"], "/edits"), headers=headers, json=request)
        stale = client.post(
            route(base_id, first["id"], "/edits"),
            headers=headers,
            json={"clientRequestId": "edit_request_0002", "content": typed},
        )

        # Exactly as typed; the passages are the ones the version had.
        assert second["content"] == typed
        assert second["citations"] == first["citations"]
        assert (second["origin"], second["versionNumber"]) == ("edit", 2)
        assert second["lineageId"] == first["lineageId"]
        assert second["modelLabel"] is None and second["provenance"]["model"] is None
        assert second["scope"] == first["scope"] and second["outline"] == first["outline"]
        # The section that was not touched keeps its check; the typed one is not re-checked.
        assert [s["checked"] for s in second["sections"]] == [True, False]
        assert again.status_code == 201 and again.json() == second
        assert stale.status_code == 409 and "latest" in stale.json()["detail"]
        assert client.get(route(base_id, first["id"]), headers=headers).json() == first

        # Checking again looks at the typed section only, and never changes the version.
        before = len(fake.requests)
        checked = client.post(route(base_id, second["id"], "/check"), headers=headers).json()
        calls = fake.requests[before:]
        assert [s["checked"] for s in checked["sections"]] == [True, True]
        assert checked["sections"][1]["issues"] == [
            {"claim": CLAIM, "verdict": "unsourced", "note": "No source is cited for this."}
        ]
        for field in ("content", "contentHash", "manifestHash", "citations", "versionNumber"):
            assert checked[field] == second[field]
        assert sum(is_auditing(r) for r in calls) == 1
        assert sum(is_support_checking(r) for r in calls) == 1
        # Nothing is left to check: no calls and no new findings.
        with check_reads(client) as reads:
            again_checked = client.post(
                route(base_id, second["id"], "/check"), headers=headers
            ).raise_for_status().json()
        assert len(reads) == 1
        assert again_checked == checked and len(fake.requests) == before + len(calls)
        with client.app.state.knowledge_service.sessions() as session:
            rows = session.scalar(
                select(func.count())
                .select_from(ArtifactCheck)
                .where(ArtifactCheck.artifact_id == second["id"])
            )
        assert rows == 2  # what the edit carried over, and what checking added


def test_a_typed_number_without_a_passage_is_flagged_when_checked(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with built_output(tmp_path, fake) as (client, base_id, first):
        headers = workspace(client)
        typed = first["content"].replace("CD19 marks B cells [1].", "CD19 marks B cells [9].")
        second = client.post(
            route(base_id, first["id"], "/edits"),
            headers=headers,
            json={"clientRequestId": "edit_request_0003", "content": typed},
        ).json()
        checked = client.post(route(base_id, second["id"], "/check"), headers=headers).json()
    assert second["content"].endswith("CD19 marks B cells [9].")
    [issue] = checked["sections"][1]["issues"]
    assert (issue["claim"], issue["verdict"]) == ("CD19 marks B cells.", "unsupported")
    assert issue["note"] == "Its number has no source in the list."


def test_check_route_preserves_model_error_detail(tmp_path: Path, monkeypatch) -> None:
    def fail_check(*args):
        raise ModelError("The Checker could not reach the model.")

    with app_for(tmp_path, FakeProvider()) as client:
        base_id, _ = library(client)
        monkeypatch.setattr(client.app.state.knowledge_service, "check_output", fail_check)
        response = client.post(route(base_id, "art_check", "/check"), headers=workspace(client))

    assert response.status_code == 502
    assert response.json() == {"detail": "The Checker could not reach the model."}


def test_a_version_from_the_old_builder_cannot_be_changed_but_can_be_rebuilt(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(
        agent_replies(
            "CD3D marks T cells [1].",
            {"action": "search_library", "query": "CD3D"},
            {"action": "answer"},
        )
    )
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        session_id = client.post(
            f"/api/knowledge-bases/{base_id}/sessions", headers=SIDECAR, json={}
        ).json()["id"]
        turn = ask_turn(client, session_id, {"content": "CD3D?"}, headers=SIDECAR)
        unit_id = client.post(
            f"/api/sessions/{session_id}/messages/{turn['assistantMessage']['id']}/proposal",
            headers=SIDECAR,
            json={"title": "The T-cell marker"},
        ).json()["knowledgeUnitId"]
        fake.replies = [output_replies(
            OUTLINE, lambda _index, prompt: f"## {heading(prompt)}\n\nCD3D marks T cells [1].",
        )]
        headers = workspace(client)
        legacy_id = insert_legacy_version(
            client.app.state.knowledge_service.sessions,
            workspace_id=headers["X-Gunther-Workspace-Id"],
            base_id=base_id,
            unit_id=unit_id,
            request_id="legacy_request_0001",
        )
        asked = len(fake.requests)

        edit = client.post(
            route(base_id, legacy_id, "/edits"),
            headers=headers,
            json={"clientRequestId": "edit_request_0004", "content": "# Typed"},
        )
        check = client.post(route(base_id, legacy_id, "/check"), headers=headers)
        # Nothing was asked of a model, and the old version is as it was.
        assert len(fake.requests) == asked
        legacy = client.get(route(base_id, legacy_id), headers=headers).json()

        # Building on it is allowed: the next version of the same output.
        rebuilt = built(build(client, base_id, body(supersedesArtifactId=legacy_id)))

    for refused in (edit, check):
        assert refused.status_code == 400 and "old builder" in refused.json()["detail"]
    assert legacy["origin"] == "legacy"
    assert legacy["outputType"] == "Field guide"
    assert (rebuilt["versionNumber"], rebuilt["origin"]) == (2, "rebuild")
    assert rebuilt["provenance"]["generator"] == "gunther.studio.v1"
    assert rebuilt["lineageId"] == legacy["lineageId"]
    assert rebuilt["supersedesArtifactId"] == legacy_id


def test_a_version_made_by_agents_is_sealed_and_tampering_is_caught(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with built_output(tmp_path, fake) as (client, base_id, first):
        headers = workspace(client)
        sessions = client.app.state.knowledge_service.sessions

        def set_field(field: str, value) -> object:
            with sessions() as session:
                row = session.get(Artifact, first["id"])
                before = getattr(row, field)
                setattr(row, field, value)
                session.commit()
            return before

        def status() -> tuple[int, int]:
            listed = client.get(f"/api/knowledge-bases/{base_id}/artifacts", headers=headers)
            return client.get(route(base_id, first["id"]), headers=headers).status_code, (
                listed.status_code
            )

        assert status() == (200, 200)
        for field, damaged in (
            ("content", "X" + first["content"][1:]),
            ("citations_json", "[]"),
            ("scope_json", '{"mode":"library"}'),
            ("brief", "changed"),
            ("outline_json", "[]"),
            ("outline_json", "["),
            ("kind", "slides"),
            ("style", "decision_brief"),
            ("origin", "edit"),
        ):
            original = set_field(field, damaged)
            assert status() == (409, 409), field
            if damaged == "[":
                response = client.get(route(base_id, first["id"]), headers=headers)
                assert "manifest contains invalid JSON" in response.json()["detail"]
            set_field(field, original)
            assert status() == (200, 200), field


def test_legacy_rebuild_locks_its_conversation_and_can_be_followed(
    tmp_path: Path,
) -> None:
    gate = threading.Event()

    def written(index: int, prompt: str) -> str:
        assert gate.wait(10)
        return REPORT[index]

    fake = FakeProvider(output_replies(OUTLINE, written))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        headers = workspace(client)
        runs = client.app.state.answer_runs
        key = "output-conv:talk"
        stream = f"/api/knowledge-bases/{base_id}/artifact-conversations/talk/stream"
        parent = saved_output(client, base_id, conversationId="talk")
        first, first_box = in_background(
            lambda: legacy_build(client, base_id, body(supersedesArtifactId=parent["id"]), headers)
        )
        wait_for(lambda: writing(runs.get(key)))
        busy = client.post(
            f"/api/knowledge-bases/{base_id}/outputs/build/stream",
            headers=headers, json=body(supersedesArtifactId=parent["id"]),
        )
        assert busy.status_code == 409

        def follow() -> list:
            with client.stream("GET", stream, headers=headers) as response:
                return read_events(response)

        again, again_box = in_background(follow)
        wait_for(lambda: len(runs.get(key)._watchers) == 2)
        gate.set()
        first.join(10)
        again.join(10)
        after = client.get(stream, headers=headers)
        assert len(versions(client)) == 2

    [followed] = again_box
    assert followed[0][0] == "resumed" and followed[-1][0] == "done"
    assert followed[-1][1]["id"] == first_box[0][-1][1]["id"]
    assert after.status_code == 204


def writing(run) -> bool:
    return run is not None and any(
        name == "stage" and data["id"] == "write" and data["state"] == "running"
        for name, data in run.events
    )


def test_stopping_a_build_saves_nothing(tmp_path: Path) -> None:
    gate = threading.Event()

    def written(index: int, prompt: str) -> str:
        assert gate.wait(10)
        return REPORT[index]

    fake = FakeProvider(output_replies(OUTLINE, written))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        headers = workspace(client)
        request = body(conversationId="talk")
        cid, plan_id, planned = plan_output(client, base_id, request, headers)
        proposal = built(planned)
        runs = client.app.state.answer_runs
        key = "output-conv:talk"
        first, first_box = in_background(
            lambda: approve_output(
                client, base_id, cid, plan_id, request["clientRequestId"], proposal, headers,
            )
        )
        wait_for(lambda: writing(runs.get(key)))
        stopped = client.post(
            f"/api/knowledge-bases/{base_id}/artifact-conversations/talk/stop", headers=headers,
        )
        assert stopped.status_code == 204
        gate.set()
        first.join(10)
        assert versions(client) == []
        # Once it has ended, another can start.
        wait_for(lambda: runs.get(key) is None)
        fake.replies = [output_replies(OUTLINE, REPORT)]
        retry = approve_output(
            client, base_id, cid, plan_id, "approve_after_stop", proposal, headers,
        )
        assert built(retry)["artifact"]["versionNumber"] == 1
    assert first_box[0][-1] == ("stopped", {"type": "stopped"})


def test_deleting_a_library_forever_removes_what_the_checker_found(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(OUTLINE, REPORT))
    with built_output(tmp_path, fake) as (client, base_id, first):
        second = built(
            build(client, base_id, body(supersedesArtifactId=first["id"]), workspace(client))
        )
        assert second["versionNumber"] == 2
        client.post(f"/api/knowledge-bases/{base_id}/trash", headers=SIDECAR)
        assert client.delete(f"/api/trash/library/{base_id}", headers=SIDECAR).status_code == 200
        with client.app.state.knowledge_service.sessions() as session:
            assert session.scalar(select(func.count()).select_from(Artifact)) == 0
            assert session.scalar(select(func.count()).select_from(ArtifactCheck)) == 0
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []


# The top bar's activity list -----------------------------------------------------------


def activity_of(client: TestClient) -> dict[str, dict]:
    return {item["id"]: item for item in client.app.state.activity.snapshot()["items"]}


@pytest.mark.parametrize(
    ("kind", "outline", "replies", "words"),
    [
        ("report", OUTLINE, REPORT, ("Writing a report", "Report ready")),
        ("slides", SLIDES_OUTLINE, SLIDES, ("Building slides", "Slides ready")),
    ],
)
def test_a_build_is_listed_while_it_works_and_when_it_is_done(
    tmp_path: Path, kind: str, outline: dict, replies: list[str], words: tuple[str, str]
) -> None:
    gate = threading.Event()
    calls = itertools.count()

    def written(index: int, prompt: str) -> str:
        if next(calls) == 1:
            assert gate.wait(10)
        return replies[index]

    fake = FakeProvider(output_replies(outline, written))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client, "Immune cells")
        key = "output-conv:talk"
        parent = saved_output(client, base_id, kind=kind, audience="student", conversationId="talk")
        request = body(kind=kind, audience="student", supersedesArtifactId=parent["id"])
        first, box = in_background(lambda: legacy_build(client, base_id, request))
        wait_for(lambda: activity_of(client).get(key, {}).get("detail", "").startswith("Writing"))
        item = activity_of(client)[key]
        assert (item["title"], item["state"], item["kind"]) == (words[0], "running", "output")
        assert item["about"] == "Immune cells" and item["canStop"] is True
        assert item["detail"] in ("Writing the report", "Writing the slides")
        assert item["open"] == {
            "view": "library", "libraryId": base_id, "tab": "outputs",
            "conversationId": "talk", "typeId": kind, "lineageId": parent["lineageId"],
            "artifactId": parent["id"],
        }
        assert item["progress"] is None
        gate.set()
        first.join(10)
        done = activity_of(client)[key]
    assert (done["state"], done["title"]) == ("done", words[1])
    assert done["endedAt"] and done["canStop"] is False
    saved = built(box[0])
    assert done["open"] == item["open"] | {
        "lineageId": saved["lineageId"], "artifactId": saved["id"],
    }


def test_a_failed_legacy_rebuild_says_why_in_the_list(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(
        OUTLINE, lambda _index, _prompt: api_error(openai.AuthenticationError, 401, "no"),
    ))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        parent = saved_output(client, base_id, conversationId="talk")
        events = legacy_build(client, base_id, body(supersedesArtifactId=parent["id"]))
        item = activity_of(client)["output-conv:talk"]
    assert events[-1][0] == "error"
    assert (item["state"], item["title"]) == ("failed", "Couldn't write the report")
    assert item["error"] == events[-1][1]["detail"]


def test_conversation_membership_and_instructions_survive_build_and_edit(tmp_path: Path) -> None:
    fake = FakeProvider(output_replies(ONE_SECTION, [REPORT[0]]))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        request = body(
            conversationId="conversation_first",
            conversationTitle="Lab briefing",
            brief="Make notes",
        )
        first = built(build(client, base_id, request))
        assert first["conversationId"] == "conversation_first"
        assert first["conversationTitle"] == "Lab briefing"
        assert first["clientRequestId"] == request["clientRequestId"]
        assert first["provenance"]["creationPlan"]["options"]["focus"] == "Make notes"
        listed = client.get(
            f"/api/knowledge-bases/{base_id}/artifacts", headers=workspace(client)
        ).json()
        assert listed[0]["conversationId"] == "conversation_first"
        assert listed[0]["request"] == first["provenance"]["request"]
        edited = client.post(
            route(base_id, first["id"], "/edits"),
            headers=workspace(client),
            json={
                "clientRequestId": "conversation_edit_001",
                "content": first["content"] + "\n\nExtra notes.",
            },
        ).json()
        assert edited["conversationId"] == "conversation_first"
        assert edited["provenance"]["conversationTitle"] == "Lab briefing"
        before = len(fake.requests)
        rejected = build(client, base_id, body(
            supersedesArtifactId=edited["id"], conversationId="conversation_other",
        ))
        assert rejected[-1][0] == "error"
        assert rejected[-1][1]["status"] == 404
        assert len(fake.requests) == before


def test_explicit_output_model_and_effort_are_used_and_invalid_models_do_not_run(
    tmp_path: Path,
) -> None:
    fake = FakeProvider(output_replies(ONE_SECTION, [REPORT[0]]))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        chosen = client.get("/api/models", headers=SIDECAR).json()["models"][-1]["ref"]
        model = client.app.state.knowledge_service.models.get(chosen)
        start = len(fake.requests)
        artifact = built(build(client, base_id, body(model=chosen, effort="max")))
        assert artifact["provenance"]["model"]["ref"] == chosen
        assert artifact["provenance"]["model"]["effort"] == "max"
        assert all(call["model"] == model.model_id for call in fake.requests[start:])
        start = len(fake.requests)
        rejected = build(client, base_id, body(model="missing/model"))
        assert rejected[-1][0] == "error"
        assert rejected[-1][1]["status"] == 422 and "available model" in rejected[-1][1]["detail"]
        assert len(fake.requests) == start


def test_an_image_the_material_did_not_keep_is_left_out_and_the_rest_stays_as_it_was():
    from gunther.outputs import drop_unknown_figures

    text = (
        "## Roofs cool fastest after two\n"
        "![Surface temperature](gunther-asset:kept_1)\n"
        "![Invented chart](gunther-asset:made_up)\n"
        "The gap peaks at two [1]"
    )
    shown, left_out = drop_unknown_figures(text, {"kept_1"})
    assert left_out == ["Invented chart"]
    assert shown == (
        "## Roofs cool fastest after two\n"
        "![Surface temperature](gunther-asset:kept_1)\n\n"
        "The gap peaks at two [1]"
    )
    assert drop_unknown_figures(text, {"kept_1", "made_up"}) == (text, [])
