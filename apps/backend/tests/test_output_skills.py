"""Saved legacy output fixtures and regressions for the current readers and reviser."""

import json
from pathlib import Path

from fake_models import FakeProvider, gateway, is_writing, output_replies
from test_outputs import (
    app_for,
    body,
    build,
    built,
    library,
    revise,
    saved_output,
    versions,
    workspace,
)

from gunther.agent import Evidence
from gunther.output_document import assemble_output
from gunther.outputs import Draft, Planned, Pool, Spec, split_document
from gunther.schemas import ConversationCitationOut
from gunther.slide_scene import (
    SceneEnvelope,
    SceneGroup,
    SceneLeaf,
    decode_scene,
    graphics_hash,
    scene_marker,
    source_hash,
    with_scene,
)

APPROACH = {
    "question": "How are T and B cells told apart?",
    "answer": "By their surface markers: CD3D for T cells, CD19 for B cells.",
    "purpose": "Scientists choosing markers for a panel.",
    "structure": "what",
    "structure_reason": "It describes how things are.",
    "cards": [
        {"claim": "CD3D marks T cells", "refs": [1], "type": "data"},
        {"claim": "CD19 marks B cells", "refs": [2], "type": "data"},
    ],
}
OUTLINE = {
    "title": "Telling immune cells apart",
    "sections": [
        {
            "heading": "Surface markers tell T and B cells apart",
            "goal": "The answer",
            "cards": [1, 2],
        },
        {"heading": "CD3D is the T-cell marker", "goal": "T cells", "cards": [1]},
        {"heading": "CD19 is the B-cell marker", "goal": "B cells", "cards": [2]},
    ],
}
REPORT = [
    "## Surface markers tell T and B cells apart\n\n"
    "CD3D marks T cells [1]; CD19 marks B cells [2].",
    "## CD3D is the T-cell marker\n\nCD3D is a marker of T cells [1].",
    "## CD19 is the B-cell marker\n\nCD19 is a marker of B cells [2].",
]


SLIDES_APPROACH = {**APPROACH, "use": "talk", "pages": 6}
SLIDES_OUTLINE = {
    "title": "Markers",
    "sections": [
        {"heading": "Markers", "layout": "compare", "cards": []},
        {
            "heading": "CD3D and CD19 set T and B cells apart",
            "layout": "compare",
            "cards": [1, 2],
            "link": "introduces the distinction named in the title",
        },
        {
            "heading": "Use both in every panel",
            "layout": "text",
            "cards": [1],
            "link": "applies both markers after the comparison",
        },
    ],
}
SLIDES = [
    "# Markers\n\nTwo markers tell the cells apart",
    "## CD3D and CD19 set T and B cells apart\n\n| | T | B |\n| --- | --- | --- |\n"
    "| Marker | CD3D [1] | CD19 [2] |\n\nNote: Start with the table [1].",
    "## Use both in every panel\n\n- CD3D for T cells [1]\n\nNote: Close here [1].",
]


def saved_skill_output(client, base_id, *, kind="report", written=None, **fields):
    """A fixture for the saved Skills format used by readers and part operations."""
    if kind == "slides":
        return saved_output(
            client, base_id, SLIDES_OUTLINE, written or SLIDES,
            kind=kind, approach=SLIDES_APPROACH, **fields,
        )
    return saved_output(client, base_id, OUTLINE, written or REPORT, approach=APPROACH, **fields)


def test_assemble_citation_renumbering_keeps_the_scene_source_fingerprint() -> None:
    fake = FakeProvider()
    model = gateway(fake).models[0]
    pool = Pool(10)
    evidence = []
    for number in range(1, 4):
        citation = ConversationCitationOut(
            id=f"cit_{number}",
            source_id=f"src_{number}",
            source_title=f"Source {number}",
            quote=f"Evidence passage {number}.",
            locator="note",
            status="provisional",
            confidence=0.0,
            source_revision_id=f"rev_{number}",
            block_id=f"blk_{number}",
        )
        evidence.append(
            Evidence(
                "library",
                f"Source {number}",
                f"Evidence passage {number}.",
                ref=number,
                payload=(citation, None),
            )
        )
    pool.restore(evidence)
    body = (
        "<!-- layout: composed -->\n## The sample reports a 72% measure\n\n"
        "| Group | Rate (%) |\n| --- | --- |\n| A | 72% [3] |\n| B | 68% [3] |\n\n"
        "The measurement is from the reported sample [3]."
    )
    root = SceneGroup(
        kind="row",
        weights=[2, 1],
        children=[
            SceneLeaf(kind="leaf", blockId="b1", view="bar_chart"),
            SceneLeaf(kind="leaf", blockId="b2", view="text"),
        ],
    )
    draft = Draft(
        with_scene(
            body,
            SceneEnvelope(
                version=2,
                sourceHash=source_hash(body),
                graphicsHash=graphics_hash(body),
                graphics={"b1": "bar_chart"},
                root=root,
            ),
        ),
        {},
    )

    built = assemble_output(
        Spec("slides", "scientist"),
        "Markers",
        "",
        [draft],
        pool,
        [Planned("The sample reports a 72% measure", id="p7", layout="composed")],
        model,
        "off",
    )

    final_page = split_document(built.content, "slides")[1][0]
    assert "[1]" in final_page and "[3]" not in final_page
    final_scene = decode_scene(scene_marker(final_page)[1])
    assert final_scene is not None
    assert built.outline == [{
        "id": "p7", "heading": "The sample reports a 72% measure", "goal": "", "layout": "composed",
    }]
    assert final_scene.root == root
    assert final_scene.source_hash == source_hash(final_page)
    assert final_scene.graphics == {"b1": "bar_chart"}
    assert final_scene.graphics_hash == graphics_hash(final_page)


def test_studio_revises_a_saved_skills_version_and_keeps_its_approach(tmp_path: Path) -> None:
    changed = "## CD3D is the T-cell marker\n\nCD3D marks T cells [1]."
    fake = FakeProvider(output_replies(OUTLINE, [REPORT[0], changed, REPORT[2]]))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        supplement = {"title": "Trial update", "url": "https://news.test/a", "role": "update",
                      "why": "A newer trial."}
        first = saved_output(
            client, base_id, OUTLINE, REPORT, approach={**APPROACH, "supplements": [supplement]},
        )
        assert first["provenance"]["approach"]["supplements"][0]["url"] == supplement["url"]
        fake.requests.clear()
        second = built(revise(client, base_id, first["id"], "Shorter", sectionIndex=1))
        edited = client.post(
            f"/api/knowledge-bases/{base_id}/artifacts/{second['id']}/edits",
            headers=workspace(client),
            json={"clientRequestId": "edit_request_0001", "content": second["content"] + "\n"},
        ).json()

    rewriting = next(r for r in fake.requests if is_writing(r))
    assert "Rewrite section 2" in rewriting["messages"][0]["content"]
    assert "Shorter" in rewriting["messages"][0]["content"] and changed in second["content"]
    for version in (second, edited):
        assert version["provenance"]["skill"]["name"] == "studio"
        assert version["provenance"]["approach"] == first["provenance"]["approach"]
    assert len(versions(client)) == 3


def test_studio_can_revise_an_older_slide_layout_and_keeps_the_titles_accent(
    tmp_path: Path,
) -> None:
    def rewrite(index: int, prompt: str) -> str:
        if index == 0:
            return "# Markers\n\nA new message"
        return (
            "<!-- layout: points -->\n## CD3D and CD19 set T and B cells apart\n\n"
            "- CD3D marks T cells [1]\n"
            "- CD19 marks B cells [2]\n\nNote: Say it as a list [1]."
        )

    fake = FakeProvider(output_replies(SLIDES_OUTLINE, rewrite))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        first = saved_skill_output(client, base_id, kind="slides")
        # The person changed this slide's layout, and picked an accent for the title slide, by hand.
        typed = first["content"].replace("<!-- layout: compare -->", "<!-- layout: cards -->")
        typed = typed.replace("layout: title", "layout: title; accent: blue", 1)
        assert typed.count("layout: cards") == 1 and "accent: blue" in typed
        second = client.post(
            f"/api/knowledge-bases/{base_id}/artifacts/{first['id']}/edits",
            headers=workspace(client),
            json={"clientRequestId": "edit_request_0006", "content": typed},
        ).json()
        fake.requests.clear()
        third = built(revise(client, base_id, second["id"], "Say it as a list", sectionIndex=1))
        fourth = built(revise(client, base_id, third["id"], "A new message", sectionIndex=0))

    old = split_document(second["content"], "slides")[1]
    middle = split_document(third["content"], "slides")[1]
    last = split_document(fourth["content"], "slides")[1]
    assert middle[1].startswith("<!-- layout: text -->\n## CD3D and CD19 set T and B cells apart")
    assert (middle[0], middle[2]) == (old[0], old[2])
    assert last[0] == "<!-- layout: title; accent: blue; design: v1 -->\n# Markers\n\nA new message"
    assert (last[1], last[2]) == (middle[1], middle[2])
    asked = [r["messages"][0]["content"] for r in fake.requests if is_writing(r)]
    assert "layout: cards" in asked[0] and "accent: blue" in asked[1]
    assert [row["ref"] for row in fourth["citations"]] == [1, 2]
    assert fourth["provenance"]["approach"] == first["provenance"]["approach"]
    assert not any("world-class presentation designer" in r["messages"][0]["content"]
                   for r in fake.requests)
    assert [item.get("layout") for item in fourth["outline"]] == ["title", "text", "takeaways"]


def test_studio_builds_with_retired_output_settings_on_disk(tmp_path: Path) -> None:
    (tmp_path / "service-settings.json").write_text(json.dumps({
        "developer": {"traces": True, "output_skills": False, "output_method": "skills"},
    }))
    with app_for(tmp_path, FakeProvider(output_replies(OUTLINE, REPORT))) as client:
        base_id, _ = library(client)
        assert client.get("/api/settings/developer", headers=workspace(client)).json() == {
            "traces": True,
        }
        version = built(build(client, base_id, body()))
    assert version["provenance"]["generator"] == "gunther.studio.v1"
    assert version["provenance"]["skill"]["name"] == "studio"
    assert version["style"] == "auto"
