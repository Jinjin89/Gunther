"""Editorial changes, supplementary sources and retained figures keep their boundaries."""

import json

import pytest
from fake_models import FakeProvider, gateway, is_sentence_checking, skill_replies, skill_step
from sqlalchemy import select
from test_documents import FigureParser, capture_pdf, png
from test_output_skills import APPROACH, OUTLINE, REPORT, SEARCH
from test_outputs import SIDECAR, app_for, body, build, built, library, revise

from gunther.agent import Evidence
from gunther.asset_service import AssetTextExtraction
from gunther.database import session_scope
from gunther.llm import ModelError
from gunther.models import ArtifactAsset, ArtifactCheck, Asset
from gunther.outputs import Draft, OutputAgents, Planned, Pool, Spec, section_hash, split_document


@pytest.mark.parametrize(
    "rewrite",
    [
        "## CD19 is the B-cell marker\n\nCD19 marks B cells in every patient [2].",
        "## CD19 is the B-cell marker\n\nCD19 marks 99% of B cells [2].",
    ],
)
def test_editor_cannot_introduce_facts_even_when_the_citations_are_unchanged(tmp_path, rewrite):
    replies = skill_replies(
        APPROACH,
        OUTLINE,
        REPORT,
        gather=SEARCH,
        editor={"issues": [{"section": 3, "problem": "Improve its ending."}]},
        revision={"sections": [{"section": 3, "instruction": "Clarify the ending."}]},
        rewrite=lambda index, prompt: rewrite,
    )

    def reply(request):
        if skill_step(request) == "editor_fidelity":
            return json.dumps({"faithful": False})  # An absent reason must still reject it.
        return replies(request)

    with app_for(tmp_path, FakeProvider(reply), skills=True) as client:
        base_id, _ = library(client)
        version = built(build(client, base_id, body()))
    assert "every patient" not in version["content"] and "99%" not in version["content"]
    assert any(
        item["step"] == "editor_fidelity" for item in version["provenance"]["skill"]["skipped"]
    )


def test_supplements_are_opt_in_and_cannot_search_another_library(tmp_path):
    with app_for(tmp_path, FakeProvider("{}")) as client:
        base_id, ids = library(client)
        _, other = library(client, "Unrelated", {"Foreign": "CD19 is a marker of B cells."})
        service = client.app.state.knowledge_service
        with session_scope(service.sessions) as session:
            primary = [ids["Markers"]]
            restricted = service._output_search_tool(session, base_id, primary, False, "selection")
            assert all(item.payload[0].source_id in primary for item in restricted.run("marker"))
            extended = service._output_search_tool(session, base_id, primary, True, "selection")
            results = extended.run("marker")
            assert {item.payload[0].source_id for item in results} == set(ids.values())
            assert not set(other.values()) & {item.payload[0].source_id for item in results}
            assert results[0].payload[0].source_origin == "selected"
            assert (
                next(item for item in results if item.payload[0].source_id == ids["Receptors"])
                .payload[0]
                .source_origin
                == "library_supplement"
            )


def test_invented_chart_values_are_never_drawn_as_a_chart(tmp_path):
    outline = {
        "title": "Markers",
        "sections": [
            {"heading": "Markers", "layout": "title", "cards": []},
            {"heading": "Marker levels", "layout": "bar_chart", "cards": [1]},
            {"heading": "Choose markers", "layout": "takeaways", "cards": [1]},
        ],
    }
    pages = [
        "# Markers\n\nHow the markers compare",
        "## Marker levels\n\n| Marker | Level (%) |\n| --- | --- |\n| CD3D | 62 [1] |\n"
        "| CD19 | 41 [1] |",
        "## Choose markers\n\nCD3D marks T cells [1].",
    ]
    fake = FakeProvider(skill_replies({**APPROACH, "use": "talk"}, outline, pages, gather=SEARCH))
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(client)
        version = built(build(client, base_id, body(kind="slides")))
    # The deck is saved, but the slide whose values its source does not state is drawn as a
    # table with what the Checker found, never as a chart, and the version says why.
    chart = version["content"].split("\n---\n")[1].strip()
    assert chart.startswith("<!-- layout: compare -->")
    assert version["sections"][1]["issues"]
    skipped = version["provenance"]["skill"]["skipped"]
    assert any(item["label"] == "Slide 2 drawn as a table" for item in skipped)


@pytest.mark.parametrize("historical_check", [False, True])
def test_revising_another_slide_preserves_a_verified_chart(tmp_path, historical_check):
    outline = {
        "title": "Measured rates",
        "sections": [
            {"heading": "Measured rates", "layout": "title", "cards": []},
            {"heading": "Cohort rate", "layout": "bar_chart", "cards": [1]},
            {"heading": "Takeaways", "layout": "takeaways", "cards": [1]},
        ],
    }
    pages = [
        "# Measured rates\n\nTwo cohort rates",
        "## Cohort rate\n\n| Cohort | Rate (%) |\n| --- | --- |\n| CD3D | 62 [1] |\n"
        "| CD19 | 41 [1] |",
        "## Takeaways\n\nCD3D cohort rate was 62% [1].",
    ]
    fake = FakeProvider(
        skill_replies(
            {**APPROACH, "use": "talk"},
            outline,
            pages,
            gather=[{"action": "search_library", "query": "CD3D"}],
            rewrite=lambda index, prompt: "## Takeaways\n\nThe CD3D cohort measured 62% [1].",
        )
    )
    with app_for(tmp_path, fake, skills=True) as client:
        base_id, _ = library(
            client, notes={"Measured rate": "CD3D cohort rate was 62%. CD19 cohort rate was 41%."}
        )
        first = built(build(client, base_id, body(kind="slides")))
        if historical_check:
            with session_scope(client.app.state.knowledge_service.sessions) as session:
                for check in session.scalars(
                    select(ArtifactCheck).where(ArtifactCheck.artifact_id == first["id"])
                ):
                    records = json.loads(check.checks_json)
                    for record in records["sections"]:
                        record.pop("coverage", None)
                    check.checks_json = json.dumps(records)
        before = len(fake.requests)
        second = built(
            revise(client, base_id, first["id"], "Clarify the takeaway", sectionIndex=2)
        )

    old_sections = split_document(first["content"], "slides")[1]
    new_sections = split_document(second["content"], "slides")[1]
    assert new_sections[1] == old_sections[1]
    assert new_sections[2] != old_sections[2]
    chart_check = second["sections"][1]
    assert chart_check["checked"] and not chart_check["issues"]
    assert chart_check["coverage"]["status"] == "complete"
    assert chart_check["coverage"]["answerHash"] == section_hash(new_sections[1])
    # Reuse a complete check; older charts need a fresh check before they can be saved.
    assert sum(is_sentence_checking(r) for r in fake.requests[before:]) == (
        2 if historical_check else 1
    )


def test_output_with_omitted_claim_verdicts_is_saved_as_unchecked(tmp_path):
    replies = skill_replies(APPROACH, OUTLINE, REPORT, gather=SEARCH)

    def reply(request):
        return json.dumps({"sentences": []}) if is_sentence_checking(request) else replies(request)

    with app_for(tmp_path, FakeProvider(reply), skills=True) as client:
        base_id, _ = library(client)
        version = built(build(client, base_id, body()))
    assert all(not section["checked"] for section in version["sections"])
    assert any(
        issue["verdict"] == "unchecked"
        for section in version["sections"]
        for issue in section["issues"]
    )


@pytest.mark.parametrize("coverage", [None, {}, {"status": "partial"}])
def test_chart_requires_complete_verification_even_when_its_values_are_cited(coverage):
    fake = FakeProvider("{}")
    chosen = gateway(fake)
    pool = Pool(10)
    pool.adopt(Evidence("library", "Cohort", "Cohort rate was 62%."))
    chart = "## Cohort rate\n\n| Cohort | Rate (%) |\n| --- | --- |\n| A | 62 [1] |"

    with pytest.raises(ModelError, match="verification is incomplete"):
        OutputAgents(chosen).assemble(
            Spec("slides", "scientist"),
            "Cohort rate",
            "",
            [Draft(chart, [], coverage)],
            pool,
            [Planned("Cohort rate", layout="bar_chart")],
            chosen.models[0],
            "off",
        )
    assert not fake.requests


def test_output_retains_its_figure_when_the_original_source_is_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gunther.processing.extract_asset_content",
        lambda *args: AssetTextExtraction("CD3D marks T cells [1]."),
    )
    image_ids = []

    class MarkerFigureParser(FigureParser):
        def parse(self, path):
            document = super().parse(path)
            document.elements[1].text = "CD3D marks T cells [1]."
            return document

    def written(index, prompt):
        if index == 0:
            assert f"gunther-asset:{image_ids[0]}" in prompt
            return f"## Evidence\n\nEvidence [1].\n\n![Figure](gunther-asset:{image_ids[0]}) [1]."
        return f"## Evidence {index}\n\nEvidence [1]."

    fake = FakeProvider(
        skill_replies(
            APPROACH,
            OUTLINE,
            written,
            gather=[{"action": "search_library", "query": "CD3D"}],
        )
    )
    with app_for(tmp_path, fake, skills=True) as client:
        client.headers.update(SIDECAR)
        base_id, _ = library(client, notes={})
        saved = capture_pdf(client, base_id)
        worker = client.app.state.processing_worker
        worker.document_parser = MarkerFigureParser()
        assert worker.run_once()
        document = client.get(f"/api/sources/{saved}/document").json()
        image_ids.append(
            next(e for e in document["elements"] if e["kind"] == "figure")["payload"][
                "imageAssetId"
            ]
        )
        version = built(build(client, base_id, body()))
        service = client.app.state.knowledge_service
        with session_scope(service.sessions) as session:
            assert (
                session.scalar(
                    select(ArtifactAsset.asset_id).where(ArtifactAsset.artifact_id == version["id"])
                )
                == image_ids[0]
            )
        client.post(f"/api/sources/{saved}/trash")
        assert client.delete(f"/api/trash/source/{saved}").status_code == 200
        assert client.get(f"/api/assets/{image_ids[0]}").content == png()
        with session_scope(service.sessions) as session:
            assert session.get(Asset, image_ids[0]) is not None
        client.post(f"/api/knowledge-bases/{base_id}/trash")
        assert client.delete(f"/api/trash/library/{base_id}").status_code == 200
        with session_scope(service.sessions) as session:
            assert session.get(Asset, image_ids[0]) is None


def test_a_finding_in_the_speaker_notes_does_not_stop_a_verified_chart():
    from gunther.outputs import Issue, chart_unverified

    by_ref = {1: Evidence("library", "Cohort", "Cohort A rate was 62%; cohort B 41%.", ref=1)}
    chart = (
        "<!-- layout: bar_chart -->\n## Cohort A leads\n\n| Cohort | Rate (%) |\n| --- | --- |\n"
        "| A | 62 [1] |\n| B | 41 [1] |\n\nNote: Stress that A is higher [1]."
    )
    supported = {
        "status": "partial",
        "claims": [
            {"text": "A 62", "verdict": "supported", "refs": [1]},
            {"text": "B 41", "verdict": "supported", "refs": [1]},
            {"text": "Stress that A is higher", "verdict": "unchecked", "refs": [1]},
        ],
    }
    notes_issue = [Issue("Stress that A is higher", "unsupported", "Not stated.")]
    assert chart_unverified(chart, notes_issue, supported, by_ref, 1) == ([], None)
    # A finding about a value, or a value claim not found supported, keeps it a table.
    value_issue = [Issue("B 41", "unsupported", "Not stated.")]
    assert chart_unverified(chart, value_issue, supported, by_ref, 1)[1] is not None
    unchecked = {**supported, "claims": [{**supported["claims"][0], "verdict": "unchecked"}]}
    assert chart_unverified(chart, [], unchecked, by_ref, 1)[1] is not None
