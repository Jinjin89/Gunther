"""Editorial changes, supplementary sources and retained figures keep their boundaries."""

import json
import re

import pytest
from fake_models import (
    FakeProvider,
    gateway,
    is_auditing,
    is_sentence_checking,
    is_support_checking,
    model,
    output_replies,
)
from sqlalchemy import select
from test_artifact_steps import drafted
from test_documents import FigureParser, capture_pdf, png
from test_output_skills import APPROACH, OUTLINE, REPORT
from test_outputs import (
    SIDECAR,
    app_for,
    body,
    build,
    built,
    library,
    revise,
    route,
    saved_output,
    workspace,
)

from gunther.agent import AskAgent, Evidence, Tool
from gunther.artifact_read import latest_check_records
from gunther.artifact_workspace import ConversationBrief, Workspace, import_work, sync
from gunther.asset_service import AssetTextExtraction
from gunther.checks import coverage_of, read_verdicts
from gunther.database import session_scope
from gunther.llm import ModelError
from gunther.models import Artifact, ArtifactAsset, ArtifactCheck, Asset
from gunther.output_checks import check_saved_part
from gunther.output_document import assemble_output
from gunther.outputs import Draft, OutputLimits, Planned, Pool, Spec, section_hash, split_document
from gunther.schemas import ArtifactOut, ConversationCitationOut
from gunther.studio.pipeline import StudioAgents


def test_check_persists_an_unavailable_audit_and_retries_only_unchecked_claims(tmp_path):
    first = "CD3D marks T cells [1]."
    unchecked = "CD19 also marks B cells [1]."
    original = "CD19 marks B cells [1]."
    outline = {"title": "Markers", "sections": [{"heading": "Marker comparison"}]}
    report = [f"## Marker comparison\n\n{first}\n\n{original}"]
    normal_reply = output_replies(outline, report)

    def incomplete_reply(request, audit_fails=True):
        if audit_fails and is_auditing(request):
            raise ModelError("The audit was unavailable")
        reply = normal_reply(request)
        if not is_sentence_checking(request):
            return reply
        prompt = request["messages"][-1]["content"]
        skipped = next(
            int(n) for n, text in re.findall(r"^\((\d+)\) (.+)$", prompt, re.M)
            if text == unchecked
        )
        return json.dumps({
            "sentences": [item for item in json.loads(reply)["sentences"] if item["n"] != skipped]
        })

    fake = FakeProvider(incomplete_reply)
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        headers = workspace(client)
        initial = saved_output(client, base, outline, report)
        edited = client.post(route(base, initial["id"], "/edits"), headers=headers, json={
            "clientRequestId": "partial_check_edit_0001",
            "content": initial["content"].replace(original, unchecked),
        }).raise_for_status().json()
        version = route(base, edited["id"])
        partial = client.post(f"{version}/check", headers=headers).raise_for_status().json()
        [section] = partial["sections"]
        assert not section["checked"]
        coverage = section["coverage"]
        assert coverage["status"] == "unavailable"
        assert coverage["audit"] == {"status": "unavailable", "claims": None}
        assert coverage["support"]["status"] == "complete"
        assert (coverage["checked"], coverage["unchecked"]) == (2, 1)
        assert [c["text"] for c in coverage["claims"] if c["verdict"] == "unchecked"] == [
            unchecked
        ]
        assert client.get(version, headers=headers).raise_for_status().json() == partial
        service = client.app.state.knowledge_service
        state, brief = Workspace(), ConversationBrief()
        with service.sessions() as session:
            prior = latest_check_records(session, session.get(Artifact, edited["id"]))
            work = import_work(ArtifactOut.model_validate(partial), prior, state, brief)

        fake.replies[:] = [lambda request: incomplete_reply(request, audit_fails=False)]
        progressed = client.post(f"{version}/check", headers=headers).raise_for_status().json()
        with service.sessions() as session:
            records = latest_check_records(session, session.get(Artifact, edited["id"]))
        [part] = work.parts.values()
        assert part.coverage["audit"]["status"] == "unavailable"
        assert sync(work, ArtifactOut.model_validate(progressed), records, state, brief)
        assert part.coverage["checked"] == 2 and part.coverage["status"] == "partial"
        assert part.coverage["audit"] == {"status": "complete", "claims": []}

        fake.replies[:] = [normal_reply]
        start = len(fake.requests)
        result = client.post(f"{version}/check", headers=headers).raise_for_status().json()
        calls = fake.requests[start:]
        assert not any(is_auditing(request) for request in calls)
        assert not any(is_support_checking(request) for request in calls)
        [prompt] = [r["messages"][-1]["content"] for r in calls if is_sentence_checking(r)]
        assert unchecked in prompt and first not in prompt and "Marker comparison" not in prompt
        [checked] = result["sections"]
        assert checked["checked"] and checked["issues"] == []
        assert checked["coverage"]["status"] == "complete"
        assert checked["coverage"]["audit"] == {"status": "complete", "claims": []}
        assert checked["coverage"]["checked"] == 3
        for key in ("content", "contentHash", "citations", "versionNumber"):
            assert result[key] == edited[key]
        start = len(fake.requests)
        assert client.post(f"{version}/check", headers=headers).raise_for_status().json() == result
        assert len(fake.requests) == start


def test_retry_keeps_studios_cover_claims_and_leaves_audience_metadata_out():
    subtitle = "CD3D marks T cells [1]."
    cover = f"<!-- layout: title -->\n# Marker comparison\n\n{subtitle}\n\nTeam · 2025"
    evidence = {1: Evidence("library", "Markers", "CD3D marks T cells.")}
    normal_reply = output_replies(OUTLINE, REPORT)

    def first_reply(request):
        if is_sentence_checking(request):
            return '{"sentences":[{"n":1,"verdict":"supported"}]}'
        return normal_reply(request)

    fake = FakeProvider(first_reply, normal_reply)
    models = gateway(fake)
    state = StudioAgents(models)._claims(model(), cover, evidence)
    digest = section_hash(cover)
    coverage = coverage_of(state, digest)
    assert coverage["considered"] == 2 and coverage["status"] == "partial"

    checked = check_saved_part(
        AskAgent(models), model(), cover, evidence, state, limit=OutputLimits().sentences,
    )
    prompt = fake.requests[-1]["messages"][-1]["content"]
    assert re.findall(r"^\(\d+\) (.*)$", prompt, re.M) == [subtitle]
    coverage = coverage_of(checked, digest)
    assert coverage["status"] == "complete" and coverage["considered"] == 2


def test_retry_and_sync_replace_historical_partial_claims_and_their_unchecked_issue(tmp_path):
    heading = "Marker comparison"
    first = "CD3D marks T cells [1]."
    second = "CD19 marks B cells [1]."
    section = f"## {heading}\n\n{first}\n\n{second}"
    outline = {"title": "Markers", "sections": [{"heading": heading}]}
    unchecked = {
        "claim": "CD19 marks B cells.", "verdict": "unchecked", "note": "No verdict returned",
    }
    independent = {"claim": "CD3D marks T cells.", "verdict": "unverified", "note": "From memory."}
    fake = FakeProvider(output_replies(outline, [section]))
    with app_for(tmp_path, fake) as client:
        base, _ = library(client)
        headers = workspace(client)
        saved = saved_output(client, base, outline, [section])
        url = route(base, saved["id"])
        service = client.app.state.knowledge_service
        with session_scope(service.sessions) as session:
            row = session.scalar(
                select(ArtifactCheck).where(ArtifactCheck.artifact_id == saved["id"])
            )
            row.checks_json = json.dumps({"sections": [{
                "hash": section_hash(section), "issues": [unchecked, independent], "coverage": {
                    "status": "partial", "considered": 3, "checked": 2, "unchecked": 1,
                    "claims": [
                        {"text": f"## {heading} [1]", "refs": [1], "verdict": "supported"},
                        {"text": first, "refs": [1], "verdict": "supported"},
                        {"text": second, "refs": [1], "verdict": "unchecked"},
                    ],
                },
            }]})
        old = ArtifactOut.model_validate(client.get(url, headers=headers).raise_for_status().json())
        state, brief = Workspace(), ConversationBrief()
        with service.sessions() as session:
            historical = latest_check_records(session, session.get(Artifact, saved["id"]))
            work = import_work(old, historical, state, brief)
        [part] = work.parts.values()
        assert part.coverage["status"] == "partial" and part.coverage["considered"] == 3
        assert unchecked in part.issues and independent in part.issues

        updated = ArtifactOut.model_validate(
            client.post(f"{url}/check", headers=headers).raise_for_status().json()
        )
        [checked] = updated.sections
        assert checked.checked and checked.coverage["considered"] == 3
        assert [issue.model_dump() for issue in checked.issues] == [independent]
        with service.sessions() as session:
            records = latest_check_records(session, session.get(Artifact, saved["id"]))
        assert sync(work, updated, records, state, brief)
        assert part.coverage == checked.coverage
        assert part.issues == [independent]
        assert not sync(work, old, historical, state, brief)


@pytest.mark.parametrize(
    "rewrite",
    [
        "## CD19 is the B-cell marker\n\nCD19 marks B cells in every patient [2].",
        "## CD19 is the B-cell marker\n\nCD19 marks 99% of B cells [2].",
    ],
)
def test_studio_checks_changed_claims_even_when_the_citation_is_unchanged(rewrite):
    replies = output_replies(OUTLINE, REPORT, rewrite=lambda *_: rewrite)

    def reply(request):
        if is_sentence_checking(request):
            return json.dumps({"sentences": [{"n": 1, "verdict": "not"}]})
        return replies(request)

    steps = drafted(FakeProvider(reply))
    assert steps.write(["p3"], "Clarify the ending.") == ["p3"]
    changed = steps.work.parts["p3"]
    assert changed.text == rewrite
    assert any(issue["verdict"] == "unsupported" for issue in changed.issues)


def test_retained_output_search_stays_in_its_source_scope(tmp_path):
    with app_for(tmp_path, FakeProvider("{}")) as client:
        base_id, ids = library(client)
        _, other = library(client, "Unrelated", {"Foreign": "CD19 is a marker of B cells."})
        service = client.app.state.knowledge_service
        with session_scope(service.sessions) as session:
            primary = [ids["Markers"]]
            tool = service._output_search_tool(session, primary, "selection")
            results = tool.run("marker")
            assert {item.payload[0].source_id for item in results} == set(primary)
            assert not set(other.values()) & {item.payload[0].source_id for item in results}
            assert all(item.payload[0].source_origin == "selected" for item in results)


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
        "<!-- layout: chart -->\n## Marker levels\n\n| Marker | Level (%) |\n| --- | --- |\n"
        "| CD3D | 62 [1] |\n| CD19 | 41 [1] |",
        "## Choose markers\n\nCD3D marks T cells [1].",
    ]
    def unsupported(request):
        prompt = request["messages"][-1]["content"]
        return [int(n) for n in re.findall(r"^\((\d+)\) \| CD", prompt, re.M)]

    fake = FakeProvider(output_replies(outline, pages, unsupported=unsupported))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client)
        version = built(build(client, base_id, body(kind="slides")))
    # The deck is saved, but the slide whose values its source does not state is drawn as a
    # table with what the Checker found, never as a chart, and the version says why.
    chart = version["content"].split("\n---\n")[1].strip()
    assert chart.startswith("<!-- layout: table -->")
    assert version["sections"][1]["issues"]
    skipped = version["provenance"]["skill"]["skipped"]
    assert any(item["label"] == "Part 2 drawn as a table" for item in skipped)


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
    rewritten = (
        "<!-- layout: points -->\n## Takeaways\n\n- The CD3D cohort measured 62% [1].\n"
        "Note: Lead with the measured cohort rate."
    )
    fake = FakeProvider(output_replies(outline, [*pages[:2], rewritten]))
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(
            client, notes={"Measured rate": "CD3D cohort rate was 62%. CD19 cohort rate was 41%."}
        )
        first = saved_output(client, base_id, outline, pages, kind="slides",
                             approach={**APPROACH, "use": "talk"})
        if historical_check:
            with session_scope(client.app.state.knowledge_service.sessions) as session:
                for check in session.scalars(
                    select(ArtifactCheck).where(ArtifactCheck.artifact_id == first["id"])
                ):
                    records = json.loads(check.checks_json)
                    for record in records["sections"]:
                        record.pop("verdicts")
                        record["issues"] = []
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
    replies = output_replies(OUTLINE, REPORT)

    def reply(request):
        return json.dumps({"sentences": []}) if is_sentence_checking(request) else replies(request)

    with app_for(tmp_path, FakeProvider(reply)) as client:
        base_id, _ = library(client)
        version = built(build(client, base_id, body()))
    assert all(not section["checked"] for section in version["sections"])
    assert all(section["coverage"]["unchecked"] > 0 for section in version["sections"])


@pytest.mark.parametrize("checker_fails", [False, True])
def test_report_keeps_authored_citations_without_borrowing_for_uncited_figures(
    tmp_path, checker_fails
):
    outline = {
        "title": "Response measurements",
        "sections": [
            {"heading": "Summary"},
            {"heading": "The measurements bound the comparison"},
        ],
    }
    measured = "The response took 37.5 ms."
    other_unit = "The sample completion rate was 37.5%."
    cited = "The response took 37.5 ms [1]."
    written = [
        f"## Summary\n\n{cited}",
        f"## The measurements bound the comparison\n\n{measured}\n\n{other_unit}",
    ]
    replies = output_replies(outline, written)

    def reply(request):
        if checker_fails and is_sentence_checking(request):
            raise ModelError("Checker unavailable")
        return replies(request)

    fake = FakeProvider(reply)
    with app_for(tmp_path, fake) as client:
        base_id, _ = library(client, title="Response", notes={"Timing": measured})
        version = built(build(client, base_id, body()))

    assert any(is_sentence_checking(request) for request in fake.requests)
    assert version["sections"][0]["checked"] is not checker_fails
    _, (summary, comparison) = split_document(version["content"], "report")
    assert cited in summary.splitlines()
    assert measured in comparison.splitlines()
    assert other_unit in comparison.splitlines()


def test_revision_appends_used_sources_and_keeps_parent_numbers(tmp_path, monkeypatch):
    revised = "## Results\n\nThe trial includes a follow-up [4]."
    with app_for(tmp_path, FakeProvider(output_replies(OUTLINE, [revised]))) as client:
        base_id, _ = library(client)
        first = saved_output(client, base_id)
        source = client.post("/api/sources", headers=SIDECAR, json={
            "title": "Trial", "kind": "note", "knowledgeBaseId": base_id,
            "content": "A trial was run. The trial includes a follow-up.",
        }).json()["source"]
        passages = [
            Evidence("library", "Trial", quote, payload=(ConversationCitationOut(
                id=f"cit_trial_{n}", source_id=source["id"], source_title="Trial", quote=quote,
                locator="Note", status="provisional", confidence=1.0,
            ), None))
            for n, quote in enumerate(("A trial was run.", "The trial includes a follow-up."))
        ]
        tool = Tool("search_library", "Trial", lambda _: passages, "the library")
        service = client.app.state.knowledge_service
        monkeypatch.setattr(service, "_output_search_tool", lambda *args: tool)
        second = built(revise(client, base_id, first["id"], "Include follow-up", sectionIndex=0))

    assert second["citations"][:2] == first["citations"]
    assert [(c["ref"], c["quote"]) for c in second["citations"][2:]] == [
        (3, "The trial includes a follow-up.")
    ]
    assert "The trial includes a follow-up [3]." in second["content"]
    assert split_document(second["content"], "report")[1][1:] == (
        split_document(first["content"], "report")[1][1:]
    )
    assert any(claim["refs"] == [3] for claim in second["sections"][0]["coverage"]["claims"])
    assert second["sections"][1] == first["sections"][1]


@pytest.mark.parametrize("coverage", [None, {}, {"status": "partial"}])
def test_chart_requires_complete_verification_even_when_its_values_are_cited(coverage):
    fake = FakeProvider("{}")
    chosen = gateway(fake)
    pool = Pool(10)
    pool.adopt(Evidence("library", "Cohort", "Cohort rate was 62%."))
    chart = "## Cohort rate\n\n| Cohort | Rate (%) |\n| --- | --- |\n| A | 62 [1] |"
    state = read_verdicts({"coverage": coverage}, chart) if coverage is not None else None

    with pytest.raises(ModelError, match="verification is incomplete"):
        assemble_output(
            Spec("slides", "scientist"),
            "Cohort rate",
            "",
            [Draft(chart, state)],
            pool,
            [Planned("Cohort rate", layout="bar_chart")],
            chosen.models[0],
            "off",
        )
    assert not fake.requests


def test_output_retains_its_figure_when_the_original_source_is_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gunther.asset_service._pdf_text",
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

    fake = FakeProvider(output_replies(OUTLINE, written))
    with app_for(tmp_path, fake) as client:
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
