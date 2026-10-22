from __future__ import annotations

from dataclasses import replace

import pytest
from fake_models import FakeProvider, gateway, model
from PIL import Image
from pydantic import ValidationError

from gunther.llm import ModelError
from gunther.outputs import BuiltOutput, _marked, section_hash, split_document, with_layout
from gunther.run_budget import BudgetedGateway, RunBudget
from gunther.schemas import ConversationCitationOut, SkillRunOut
from gunther.slide_render import RenderError, SlideRenderer, built_hash
from gunther.slide_review import (
    VisualRepairs,
    apply_repairs,
    invalidate_reviews,
    review_deck,
)
from gunther.slide_scene import SceneEnvelope, graphics_hash, source_hash, with_scene


def deck(pages=2, vision=True):
    text = ["<!-- layout: title; theme: editorial; design: v1 -->\n# Pilot [1]"]
    text += [
        f"<!-- layout: text -->\n## Scope {i} [1]\n\n40 hours fell to 16 hours. [1]"
        "\n\nQuality was not measured. [1]\n\nNote: Eight weeks only. [1]"
        for i in range(1, pages)
    ]
    citation = ConversationCitationOut(
        id="c1",
        source_id="s1",
        source_title="Record",
        locator="p. 1",
        quote="40 to 16 hours; quality not measured.",
        status="verified",
        confidence=1,
        ref=1,
    )
    return BuiltOutput(
        "Pilot",
        "\n\n---\n\n".join(text),
        [
            {
                "id": f"p{i + 1}",
                "heading": "Pilot" if i == 0 else f"Scope {i}",
                "layout": "title" if i == 0 else "text",
            }
            for i in range(pages)
        ],
        [citation],
        [
            {
                "hash": section_hash(t),
                "issues": [],
                "coverage": {"answerHash": section_hash(t), "claims": []},
            }
            for t in text
        ],
        model(vision=vision),
        "low",
        pool_refs=(7,),
        skill={"name": "slides", "version": 11},
    )


def root(view="text"):
    return {
        "kind": "row",
        "children": [
            {"kind": "leaf", "blockId": "b1", "view": view, "size": "large"},
            {"kind": "leaf", "blockId": "b2", "view": "text", "size": "medium"},
        ],
        "weights": [2, 1],
    }


def finding(identity="p2", route="layout"):
    return {
        "slideId": identity,
        "blockId": "b1",
        "severity": "error",
        "problem": "The main text is too small.",
        "correction": "Give it more room.",
        "route": route,
    }


class Renderer:
    def __init__(self, tmp_path, *, failure=False, stale=False):
        self.path = tmp_path / "image.png"
        Image.new("RGB", (16, 9), "white").save(self.path)
        self.candidates = []
        self.failure, self.stale = failure, stale

    def capture(self, candidate, use="talk"):
        self.candidates.append(candidate)
        if self.failure:
            raise RenderError("Browser unavailable")
        return {
            "version": 1,
            "id": f"capture{len(self.candidates)}",
            "status": "captured",
            "candidateHash": "stale" if self.stale else built_hash(candidate),
            "assetHashes": {"figure": "immutable"},
            "rendererHash": "renderer-v1",
            "contact": "contact.png",
            "pdfCheck": {"status": "checked"},
            "slides": [
                {
                    "id": p["id"],
                    "index": i,
                    "blocks": [{"id": "b1"}, {"id": "b2"}],
                    "image": f"{i}.png",
                    "images": [],
                    "overflow": False,
                    "smallText": [],
                    "resolvedFonts": ["AvenirNext-Regular"],
                }
                for i, p in enumerate(candidate.outline)
            ],
        }

    def file(self, *args):
        return self.path

    def current(self, snapshot):
        return True


def run(candidate, renderer, fake):
    return review_deck(
        candidate,
        renderer,
        gateway(fake, candidate.model),
        candidate.model,
        candidate.effort,
        brief="A cautious account of the pilot",
        use="talk",
    )


def test_layout_repair_keeps_exact_words_notes_citations_and_check_coverage():
    before = deck()
    result = apply_repairs(before, VisualRepairs(pages=[{"slideId": "p2", "root": root()}]), {"p2"})
    old = split_document(before.content, "slides")[1][1]
    new = split_document(result.content, "slides")[1][1]
    assert _marked(old).body == _marked(new).body
    assert result.citations == before.citations and result.pool_refs == (7,)
    assert result.checks[1]["hash"] == section_hash(new)
    assert result.checks[1]["coverage"]["answerHash"] == section_hash(new)
    assert result.checks[1]["coverage"]["claims"] == before.checks[1]["coverage"]["claims"]


@pytest.mark.parametrize(
    "change", ["unknown", "duplicate", "missing", "graphic", "locked", "title"]
)
def test_invalid_repair_cannot_mutate_the_candidate(change):
    before = deck()
    repair = {"slideId": "p2", "root": root()}
    if change == "unknown":
        repair["slideId"] = "other"
    if change == "duplicate":
        repair["root"]["children"][1]["blockId"] = "b1"
    if change == "missing":
        repair["root"]["children"][1]["blockId"] = "b3"
    if change == "graphic":
        repair["root"] = root("flow")
    if change == "locked":
        before.outline[1]["designLocked"] = True
    if change == "title":
        repair["slideId"] = "p1"
    content = before.content
    with pytest.raises(ModelError):
        apply_repairs(before, VisualRepairs(pages=[repair]), {"p1", "p2"})
    assert before.content == content


def test_model_cannot_return_words_or_grant_a_graphic_license():
    with pytest.raises(ValidationError):
        VisualRepairs(pages=[{"slideId": "p2", "text": "omit the caveat"}])
    with pytest.raises(ValidationError):
        VisualRepairs(pages=[{"slideId": "p2", "graphics": {"b1": "flow"}, "root": root()}])


def test_existing_graphic_envelope_is_retained_exactly():
    before = deck()
    parts = split_document(before.content, "slides")[1]
    scene = SceneEnvelope(
        version=2,
        sourceHash=source_hash(parts[1]),
        graphicsHash=graphics_hash(parts[1]),
        root=root(),
    )
    parts[1] = with_scene(with_layout(parts[1], "composed"), scene)
    before = replace(before, content="\n\n---\n\n".join(parts))
    result = apply_repairs(before, VisualRepairs(pages=[{"slideId": "p2", "root": root()}]), {"p2"})
    final = _marked(split_document(result.content, "slides")[1][1]).scene
    assert final.version == 2 and final.source_hash == scene.source_hash
    assert final.graphics_hash == scene.graphics_hash and final.graphics == scene.graphics


def test_clean_images_do_not_invoke_repair(tmp_path):
    candidate, renderer = deck(), Renderer(tmp_path)
    fake = FakeProvider('{"findings":[]}')
    result = run(candidate, renderer, fake)
    assert result.content == candidate.content and len(renderer.candidates) == 1
    assert result.skill["visualReview"]["status"] == "clean"
    assert len(fake.requests) == 1
    images = fake.requests[0]["messages"][-1]["content"]
    assert sum(item["type"] == "image_url" for item in images) == 3


def test_repair_is_followed_by_image_review_of_changed_pages(tmp_path):
    import json

    candidate, renderer = deck(), Renderer(tmp_path)
    fake = FakeProvider(
        json.dumps({"findings": [finding()]}),
        json.dumps({"pages": [{"slideId": "p2", "root": root()}]}),
        '{"findings":[]}',
    )
    result = run(candidate, renderer, fake)
    assert result.skill["visualReview"]["status"] == "repaired"
    assert result.skill["visualReview"]["repairCycles"] == 1
    assert result.skill["visualReview"]["repairedSlides"] == ["p2"]
    assert result.skill["renderCheck"]["candidateHash"] == built_hash(result)
    assert len(fake.requests) == 3 and len(renderer.candidates) == 2
    assert result.citations == candidate.citations


@pytest.mark.parametrize("route", ["write", "plan"])
def test_content_and_structure_findings_route_to_checked_tools(tmp_path, route):
    import json

    candidate = deck()
    result = run(
        candidate,
        Renderer(tmp_path),
        FakeProvider(json.dumps({"findings": [finding(route=route)]})),
    )
    assert result.content == candidate.content
    assert result.skill["visualReview"]["status"] == "needs_review"
    assert result.skill["visualReview"]["findings"][0]["route"] == route
    assert result.skill["visualReview"]["repairCycles"] == 0


def test_nonvision_model_gets_geometry_without_hidden_provider_calls(tmp_path):
    candidate, fake = deck(vision=False), FakeProvider()
    result = run(candidate, Renderer(tmp_path), fake)
    assert not fake.requests and result.content == candidate.content
    assert result.skill["renderCheck"]["status"] == "captured"
    assert result.skill["visualReview"]["imageReview"] == "unavailable"
    assert result.skill["visualReview"]["status"] == "needs_review"


@pytest.mark.parametrize("failure,stale", [(True, False), (False, True)])
def test_failed_or_stale_capture_preserves_previous_deck(tmp_path, failure, stale):
    candidate, fake = deck(), FakeProvider()
    result = run(candidate, Renderer(tmp_path, failure=failure, stale=stale), fake)
    assert result.content == candidate.content and not fake.requests
    assert result.skill["visualReview"]["status"] != "clean"


def test_failed_recheck_rolls_back_all_layout_changes(tmp_path):
    import json

    candidate = deck()
    fake = FakeProvider(
        json.dumps({"findings": [finding()]}),
        json.dumps({"pages": [{"slideId": "p2", "root": root()}]}),
        ModelError("Provider unavailable"),
    )
    result = run(candidate, Renderer(tmp_path), fake)
    assert result.content == candidate.content
    assert result.skill["renderCheck"]["candidateHash"] == built_hash(candidate)
    assert result.skill["visualReview"]["status"] == "needs_review"


def test_nested_request_budgets_count_json_retries():
    candidate = deck()
    fake = FakeProvider("not json", "still not json")
    outer, inner = RunBudget(0, 0, 2, 2), RunBudget(0, 0, 2, 2)
    bounded = BudgetedGateway(BudgetedGateway(gateway(fake, candidate.model), inner), outer)
    from gunther.slide_review import VisualReview

    with pytest.raises(ModelError):
        bounded.complete_json(candidate.model, VisualReview, system="review", prompt="test")
    assert outer.model_calls == inner.model_calls == len(fake.requests) == 2


def test_visual_budget_stops_the_ninth_physical_request_and_keeps_previous_candidate(tmp_path):
    import json

    candidate = deck(pages=14)
    replies = [json.dumps({"findings": [finding()]})]
    replies += ['{"findings":[]}'] * 4
    replies += ["invalid json", json.dumps({"pages": [{"slideId": "p2", "root": root()}]})]
    replies += ["invalid json", '{"findings":[]}']
    fake = FakeProvider(*replies)
    result = run(candidate, Renderer(tmp_path), fake)
    assert len(fake.requests) == result.skill["visualReview"]["calls"] == 8
    assert result.content == candidate.content
    assert result.skill["visualReview"]["status"] == "needs_review"
    assert result.skill["visualReview"]["repairCycles"] == 1
    assert all(
        sum(part["type"] == "image_url" for part in request["messages"][-1]["content"]) <= 4
        for request in fake.requests
        if isinstance(request["messages"][-1]["content"], list)
    )


@pytest.mark.parametrize("change", ["wording", "style", "outline", "citations"])
def test_freshness_uses_content_outline_and_source_bindings(change):
    candidate = deck()
    citations = [item.model_dump(mode="json") for item in candidate.citations]
    skill = {
        "renderCheck": {"candidateHash": built_hash(candidate), "status": "captured"},
        "visualReview": {"candidateHash": built_hash(candidate), "status": "clean"},
    }
    content, outline = candidate.content, [dict(p) for p in candidate.outline]
    if change == "wording":
        content += "\nNote: Changed"
    if change == "style":
        content = content.replace("editorial", "keynote")
    if change == "outline":
        outline.reverse()
    if change == "citations":
        citations[0]["source_id"] = "another-source"
    result = invalidate_reviews(skill, content, outline, citations)
    assert result["visualReview"]["status"] == result["renderCheck"]["status"] == "stale"


def test_cached_image_integrity_and_renderer_asset_freshness(tmp_path):
    import json

    root = tmp_path / "host"
    root.mkdir()
    (root / "manifest.json").write_text('{"rendererHash":"v1"}')
    asset = tmp_path / "asset.png"
    asset.write_bytes(b"original")
    renderer = SlideRenderer(tmp_path / "cache", lambda _: (asset, "image/png", "asset"), host=root)
    import hashlib

    snapshot = {"rendererHash": "v1", "assetHashes": {"a": hashlib.sha256(b"original").hexdigest()}}
    assert renderer.current(snapshot)
    asset.write_bytes(b"changed")
    assert not renderer.current(snapshot)
    identity = "a" * 32
    folder = renderer.cache / identity
    folder.mkdir(parents=True)
    (folder / "contact.png").write_bytes(b"original")
    (folder / "snapshot.json").write_text(
        json.dumps({"files": {"contact.png": hashlib.sha256(b"original").hexdigest()}})
    )
    assert renderer.file(identity, "contact.png").is_file()
    (folder / "contact.png").write_bytes(b"tampered")
    with pytest.raises(LookupError):
        renderer.file(identity, "contact.png")
    with pytest.raises(LookupError):
        renderer.file(identity, "../source.pdf")


def test_old_skill_records_round_trip_without_fabricated_reviews():
    saved = {"name": "slides", "version": 9, "skipped": []}
    assert SkillRunOut.model_validate(saved).model_dump(by_alias=True) == saved


def test_browser_disconnect_is_a_render_failure():
    import time

    from websockets.exceptions import ConnectionClosedError

    from gunther.slide_render import _CDP

    class Socket:
        def send(self, payload):
            pass

        def recv(self, **kwargs):
            raise ConnectionClosedError(None, None)

    with pytest.raises(RenderError, match="disconnected"):
        _CDP(Socket(), time.monotonic() + 1).call("Page.captureScreenshot")


def test_missing_cached_images_do_not_fail_the_build(tmp_path):
    candidate = deck()
    renderer = Renderer(tmp_path)
    renderer.path.unlink()
    result = run(candidate, renderer, FakeProvider())
    assert result.content == candidate.content
    assert result.skill["visualReview"]["status"] == "needs_review"


def test_failed_image_request_never_claims_images_were_reviewed(tmp_path):
    result = run(deck(), Renderer(tmp_path), FakeProvider(ModelError("Provider unavailable")))
    assert result.skill["visualReview"]["status"] == "needs_review"
    assert result.skill["visualReview"]["imageReview"] == "unavailable"


def test_one_cycle_changes_at_most_four_pages_and_reports_the_rest(tmp_path):
    import json

    candidate = deck(pages=7)
    replies = [
        json.dumps({"findings": [finding("p2"), finding("p3")]}),
        json.dumps({"findings": [finding("p4"), finding("p5"), finding("p6")]}),
        json.dumps({"findings": [finding("p7")]}),
        json.dumps({"pages": [{"slideId": f"p{i}", "root": root()} for i in range(2, 6)]}),
        '{"findings":[]}',
        '{"findings":[]}',
    ]
    fake = FakeProvider(*replies)
    result = run(candidate, Renderer(tmp_path), fake)
    assert result.skill["visualReview"]["repairedSlides"] == ["p2", "p3", "p4", "p5"]
    assert {f["slideId"] for f in result.skill["visualReview"]["findings"]} == {"p6", "p7"}
    assert result.skill["visualReview"]["status"] == "needs_review"
    assert result.skill["visualReview"]["repairCycles"] == 1
    assert result.skill["visualReview"]["calls"] == 6


def test_a_locked_design_is_reviewed_but_never_automatically_changed(tmp_path):
    import json

    candidate = deck()
    candidate.outline[1]["designLocked"] = True
    fake = FakeProvider(json.dumps({"findings": [finding()]}))
    result = run(candidate, Renderer(tmp_path), fake)
    assert result.content == candidate.content and len(fake.requests) == 1
    assert result.skill["visualReview"]["repairCycles"] == 0


def test_pdf_glyph_comparison_keeps_raw_text_and_detects_a_real_change():
    from gunther.slide_render import pdf_glyph_text

    raw = "討論意⻅：12 units [1]"
    expected = "討論意见：12 units [1]"
    assert pdf_glyph_text(raw) == pdf_glyph_text(expected)
    assert raw == "討論意⻅：12 units [1]"
    assert pdf_glyph_text(raw) != pdf_glyph_text("討論意见：21 units [2]")


@pytest.mark.parametrize("licensed", [False, True])
def test_workspace_keeps_repaired_scene_fresh_with_original_pool_citations(licensed):
    from test_artifact_steps import passage, steps_for

    from gunther.artifact_workspace import ItemRec, PartRec, digest_part_inputs
    from gunther.outputs import Pool

    pool = Pool(20)
    pool.adopt(passage("unused", "Unassigned context."))
    pool.adopt(passage("scope", "Collect, check. Quality was not measured."))
    steps = steps_for(FakeProvider(), "slides", pool=pool)
    title = "<!-- layout: title; design: v1 -->\n# Scope [2]"
    body = "<!-- layout: text -->\n## The checked scope [2]\n\n"
    body += "1. Collect. [2]\n2. Check. [2]" if licensed else "Measured scope. [2]"
    body += "\n\nQuality was not measured. [2]\n\nNote: Keep the qualification. [2]"
    tree = root("flow" if licensed else "text")
    if licensed:
        body = with_scene(
            with_layout(body, "composed"),
            SceneEnvelope(
                version=2,
                sourceHash=source_hash(body),
                graphicsHash=graphics_hash(body),
                graphics={"b1": "flow"},
                root=tree,
            ),
        )
    steps.work.outline = [
        ItemRec(id="p1", heading="Scope", layout="title"),
        ItemRec(id="p2", heading="The checked scope", layout="composed" if licensed else "text"),
    ]
    for item, text in zip(steps.work.outline, [title, body], strict=True):
        steps.work.parts[item.id] = PartRec(
            text=text,
            inputs=digest_part_inputs(steps.brief, steps.work, item),
            checked=section_hash(text),
            issues=[],
        )
    changed_tree = {**tree, "weights": [3, 2]}
    steps.agents.review_rendered = lambda built, spec, events: apply_repairs(
        built, VisualRepairs(pages=[{"slideId": "p2", "root": changed_tree}]), {"p2"}
    )
    saved, mapping = steps.assemble()
    current = steps.work.parts["p2"]
    assert mapping == [2] and "[2]" in _marked(current.text).body
    assert _marked(current.text).body == _marked(body).body
    assert current.checked == section_hash(current.text)
    assert current.inputs == digest_part_inputs(steps.brief, steps.work, steps.work.outline[1])
    final_scene = _marked(split_document(saved.content, "slides")[1][1]).scene
    workspace_scene = _marked(current.text).scene
    assert workspace_scene.root.weights == final_scene.root.weights == [3, 2]
    if licensed:
        assert workspace_scene.graphics == final_scene.graphics == {"b1": "flow"}
        assert workspace_scene.graphics_hash == graphics_hash(current.text)
        assert final_scene.graphics_hash == graphics_hash(
            split_document(saved.content, "slides")[1][1]
        )
