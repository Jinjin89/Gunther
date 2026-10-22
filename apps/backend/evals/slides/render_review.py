"""Reproducible before/after capture and bounded live visual review, using synthetic sources."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from gunther import slide_review
from gunther.config import PROJECT_ROOT, Settings
from gunther.model_registry import build_gateway, effective
from gunther.outputs import BuiltOutput, _marked, split_document
from gunther.schemas import ConversationCitationOut
from gunther.service_settings import ServiceSettingsStore
from gunther.skillbook import skill_for
from gunther.slide_render import SlideRenderer, built_hash
from gunther.slide_review import geometry_findings, review_deck
from gunther.slide_scene import SceneEnvelope, source_hash, with_scene


def configured():
    base = Settings()
    store = ServiceSettingsStore(
        base.service_settings_file or PROJECT_ROOT / "data/service-settings.json"
    )
    settings = store.apply(base)
    gateway = build_gateway(effective(*store.models(), settings), settings)
    selected = gateway.for_role("outputs") or gateway.for_role("ask")
    if not selected:
        raise SystemExit("No configured Outputs model")
    return gateway, *selected


def make_deck(packet, theme, model, effort, dense=False):
    slides = packet["slides"]
    if dense:
        body = "## The pilot measured time; quality remains unknown [1][2]\n\n"
        body += packet["sources"][0]["text"] + " [1]\n\n"
        body += packet["sources"][1]["text"] + " [2]"
        scene = SceneEnvelope(
            version=1,
            sourceHash=source_hash(body),
            root={
                "kind": "row",
                "weights": [1, 4],
                "gap": "wide",
                "padding": "medium",
                "surface": "none",
                "children": [
                    {"kind": "leaf", "blockId": "b1", "view": "text", "size": "medium"},
                    {"kind": "leaf", "blockId": "b2", "view": "text", "size": "small"},
                ],
            },
        )
        slides = [packet["slides"][0], {"layout": "composed", "body": with_scene(body, scene)}]
    if slides[0]["layout"] != "title":
        slides = [
            {
                "layout": "title",
                "body": "# " + slides[0]["body"].splitlines()[0].lstrip("# "),
            },
            *slides,
        ]
    content = "\n\n---\n\n".join(
        f"<!-- layout: {slide['layout']}"
        + (f"; theme: {theme}; design: v1" if i == 0 else "")
        + " -->\n"
        + slide["body"]
        for i, slide in enumerate(slides)
    )
    # Fixed source-backed fixtures isolate visual evaluation; they are not a full writing exam.
    citations = [
        ConversationCitationOut(
            id=s["id"],
            ref=i + 1,
            source_id=s["id"],
            source_title=s["title"],
            locator=s["locator"],
            quote=s["text"],
            status="verified",
            confidence=1,
        )
        for i, s in enumerate(packet["sources"])
    ]
    sections = split_document(content, "slides")[1]
    outline = [
        {
            "id": f"{packet['id']}-p{i + 1}",
            "heading": _marked(t).body.splitlines()[0].lstrip("# "),
            "goal": "Explain the synthetic source and its scope",
            "layout": slide["layout"],
        }
        for i, (slide, t) in enumerate(zip(slides, sections, strict=True))
    ]
    return BuiltOutput(
        packet["title"],
        content,
        outline,
        citations,
        [],
        model,
        effort,
        skill={"name": "slides", "version": 11},
        pool_refs=tuple(range(1, len(citations) + 1)),
    )


def dump_deck(built, use):
    return {
        "title": built.title,
        "content": built.content,
        "outline": built.outline,
        "citations": [c.model_dump(mode="json") for c in built.citations],
        "use": use,
    }


def fidelity(before, after):
    old, new = (
        split_document(before.content, "slides")[1],
        split_document(after.content, "slides")[1],
    )
    return {
        "wordingNotesAndCitations": [_marked(t).body for t in old]
        == [_marked(t).body for t in new],
        "citationIdentities": before.citations == after.citations,
        "poolMapping": before.pool_refs == after.pool_refs,
        "pageIdentityAndOrder": [p["id"] for p in before.outline]
        == [p["id"] for p in after.outline],
        "theme": _marked(old[0]).theme == _marked(new[0]).theme,
        "designVersion": _marked(old[0]).design_version == _marked(new[0]).design_version,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", default="")
    parser.add_argument("--geometry-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    gateway, model, effort = configured()
    skill = skill_for("slides")
    instructions = skill.instructions(skill.step("visual_review"))
    protocol_hash = hashlib.sha256(
        Path(slide_review.__file__).read_bytes() + instructions.encode()
    ).hexdigest()
    renderer_hash = json.loads(
        (Path(__file__).parents[3] / "desktop/render-dist/manifest.json").read_text()
    )["rendererHash"]
    print(
        json.dumps({"model": model.display, "effort": effort, "vision": model.vision}), flush=True
    )
    args.output.mkdir(parents=True, exist_ok=True)
    renderer = SlideRenderer(
        args.output / "captures",
        lambda identity: (
            (
                Path(__file__).parents[3] / "desktop/evals/slides/source-figure.svg",
                "image/svg+xml",
                "source-figure.svg",
            )
            if identity == "eval-collection"
            else (_ for _ in ()).throw(LookupError())
        ),
    )
    packets = json.loads(Path(__file__).with_name("packets.json").read_text())
    cases = [
        (f"{p['id']}-{use}", p, ["briefing", "editorial", "keynote"][i % 3], use, False)
        for i, p in enumerate(packets)
        for use in ("talk", "read")
    ]
    cases += [
        (f"dense-{theme}", packets[0], theme, "talk", True)
        for theme in ("briefing", "editorial", "keynote")
    ]
    for identity, packet, theme, use, dense in cases:
        if args.only and identity not in args.only.split(","):
            continue
        record_file = args.output / f"{identity}.json"
        before = make_deck(packet, theme, model, effort, dense)
        if record_file.is_file() and not args.force:
            saved = json.loads(record_file.read_text())
            if (
                saved.get("inputHash") == built_hash(before)
                and saved.get("reviewProtocol") == protocol_hash
                and saved.get("modelRef") == model.ref
                and saved.get("beforeCapture", {}).get("rendererHash") == renderer_hash
            ):
                print(identity + ": reuse saved result", flush=True)
                continue
        print(identity + ": capture before", flush=True)
        capture = renderer.capture(before, use)
        if args.geometry_only:
            after = before
            result = {"status": "geometry-only", "findings": geometry_findings(capture), "calls": 0}
            after_capture = capture
        else:
            after = review_deck(
                before,
                renderer,
                gateway,
                model,
                "medium",
                brief=(
                    packet["brief"] + "\nVisual comparison scope: this fixed "
                    f"{len(before.outline)}-slide subset is complete for this review. "
                    "The full requested outline is evaluated separately. Assess the "
                    "rendered subset, not the missing pages of the full planned deck."
                ),
                use=use,
                instructions=instructions,
                emit=lambda event, identity=identity: print(
                    identity + ": " + event.get("detail", event.get("state", "")), flush=True
                ),
            )
            result = after.skill["visualReview"]
            after_capture = after.skill["renderCheck"]
        same = fidelity(before, after)
        assert all(same.values()), same
        data = {
            "id": identity,
            "synthetic": True,
            "model": model.display,
            "modelRef": model.ref,
            "effort": "medium",
            "reviewProtocol": protocol_hash,
            "vision": model.vision,
            "theme": theme,
            "use": use,
            "inputHash": built_hash(before),
            "sourceHash": hashlib.sha256(
                json.dumps(packet["sources"], sort_keys=True).encode()
            ).hexdigest(),
            "before": dump_deck(before, use),
            "after": dump_deck(after, use),
            "beforeCapture": capture,
            "afterCapture": after_capture,
            "review": result,
            "fidelity": same,
        }
        record_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(identity + ": " + result["status"] + f" · {result['calls']} requests", flush=True)


if __name__ == "__main__":
    main()
