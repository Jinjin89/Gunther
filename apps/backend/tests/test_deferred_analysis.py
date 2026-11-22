import json
from pathlib import Path
from unittest.mock import Mock

import openai
import pytest
from fake_models import FakeProvider, api_error, gateway, store_saved_claims
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gunther.config import Settings
from gunther.database import session_scope
from gunther.extraction import ModelExtractor, analysis_job_key
from gunther.knowledge_index import enqueue
from gunther.main import create_app
from gunther.models import Assertion, Fragment, ProcessingJob, Revision, Source, utc_now
from gunther.source_identity import source_fingerprint


def client_for(path: Path) -> TestClient:
    return TestClient(create_app(Settings(
        database_url=f"sqlite+pysqlite:///{path / 'db.sqlite'}",
        assets_dir=path / "assets", recordings_dir=path / "recordings",
        processing_worker_enabled=False, ocr_provider="disabled",
        deepseek_api_key=None, stt_provider="compatible",
    )))


def capture(client: TestClient, kind: str = "recording") -> dict:
    response = client.post("/api/sources?deferAnalysis=true", json={
        "title": "Saved capture", "kind": kind, "content": "Exercise improves memory.",
    })
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize(("content", "quote"), [
    ("Exercise -> improves -> memory", "Exercise -> improves -> memory"),
    (
        "> | Method | Result |\n> | --- | --- |\n> Exercise -> improves -> memory",
        "Exercise -> improves -> memory",
    ),
])
def test_analysis_receives_capture_markup_verbatim(tmp_path: Path, content: str, quote: str):
    with client_for(tmp_path) as client:
        fake = FakeProvider(model_reply(quote))
        client.app.state.knowledge_service.extractor = ModelExtractor(gateway(fake))
        receipt = client.post("/api/sources?deferAnalysis=true", json={
            "title": "Practice notes", "kind": "note", "content": content,
        }).raise_for_status().json()
        assert receipt["created"] == {"entities": 0, "assertions": 0}
        assert not fake.requests
        url = f"/api/sources/{receipt['source']['id']}"
        captured = client.get(url).raise_for_status().json()
        assert captured["content"] == content and captured["analysis"]["state"] == "queued"
        assert captured["assertions"] == []
        assert client.app.state.processing_worker.run_once(ai=True)
        [request] = fake.requests
        assert request["messages"][-1]["content"].endswith(content)
        finished = client.get(url).raise_for_status().json()
        [claim] = finished["assertions"]
        assert finished["analysis"]["state"] == "completed"
        assert claim["evidence"][0]["quote"] == quote


def model_reply(quote: str = "Exercise improves memory.", *, predicate: str = "improves") -> str:
    return json.dumps({"assertions": [{
        "subject_label": "Exercise", "subject_type": "Activity", "predicate": predicate,
        "object_label": "memory", "object_type": "Concept", "confidence": 0.9,
        "evidence_quote": quote, "qualifiers": [],
    }]})


@pytest.mark.parametrize(("query", "actor", "status"), [
    ("", "user", "provisional"),
    ("?deferAnalysis=true", "extractor:local", "disputed"),
], ids=["regular", "deferred"])
def test_saved_entered_assertions_survive_analysis_failure_and_replacement(
    tmp_path, query, actor, status,
):
    entered = "CD3D -> marker_of -> T cell"
    prose = "Exercise improves memory."
    content = f"{entered}\n\n{prose}"
    reply = json.loads(model_reply())
    reply["assertions"].append({
        "subject_label": "cd3d", "subject_type": "Gene", "predicate": "marker of",
        "object_label": "T cell", "object_type": "CellType", "confidence": 0.9,
        "evidence_quote": entered, "qualifiers": [],
    })
    fake = FakeProvider(api_error(openai.InternalServerError, 500, "offline"), json.dumps(reply))
    with client_for(tmp_path) as client:
        service = client.app.state.knowledge_service
        service.extractor = ModelExtractor(gateway(fake))
        receipt = client.post(f"/api/sources{query}", json={
            "title": "Mixed capture", "kind": "note", "content": content,
        }).raise_for_status().json()
        assert receipt["created"]["assertions"] == 0 and not fake.requests
        source_id = receipt["source"]["id"]
        url = f"/api/sources/{source_id}"
        store_saved_claims(client, source_id, ("CD3D", "marker_of", "T cell", entered))
        with session_scope(service.sessions) as session:
            creation = session.scalar(select(Revision).join(
                Assertion, Assertion.id == Revision.target_id,
            ).where(Assertion.source_id == source_id))
            creation.actor = actor
        saved = client.get(url).raise_for_status().json()
        assert saved["analysis"]["state"] == "queued"
        [explicit] = saved["assertions"]
        assert explicit["evidence"][0]["quote"] == entered
        client.patch(f"/api/assertions/{explicit['id']}/status", json={
            "status": status,
        }).raise_for_status()

        # A previous published model result can coexist with a pending retry.
        prior = ModelExtractor(gateway(FakeProvider(model_reply(predicate="supports"))))
        with session_scope(service.sessions) as session:
            source = session.get(Source, source_id)
            service.store_source_extraction(session, source, prior.extract(source.title, content))

        worker = client.app.state.processing_worker
        assert worker.run_once(ai=True)
        failed = client.get(url).raise_for_status().json()
        assert failed["analysis"]["state"] == "failed"
        assert {claim["predicate"] for claim in failed["assertions"]} == {"marker_of", "supports"}

        retry = client.post(f"{url}/analysis/retry").raise_for_status().json()
        assert retry["jobId"] == saved["analysis"]["jobId"] and retry["state"] == "queued"
        assert worker.run_once(ai=True)
        assert not worker.run_once(ai=True)
        finished = client.get(url).raise_for_status().json()
        assert finished["analysis"]["state"] == "completed"
        assert len(finished["assertions"]) == 2
        by_predicate = {claim["predicate"]: claim for claim in finished["assertions"]}
        assert by_predicate["marker_of"]["id"] == explicit["id"]
        assert by_predicate["marker_of"]["status"] == status
        assert by_predicate["marker_of"]["evidence"] == explicit["evidence"]
        assert by_predicate["improves"]["evidence"][0]["quote"] == prose
        assert len(fake.requests) == 2
        assert all(
            request["messages"][-1]["content"].endswith(content) for request in fake.requests
        )


def test_analysis_retry_keeps_reviewed_and_matching_model_assertions(tmp_path):
    verified = json.loads(model_reply())["assertions"][0]
    disputed = {**verified, "predicate": "inhibits", "evidence_quote": "One study found a decline."}
    matching_draft = {
        **verified, "predicate": "aids", "evidence_quote": "Exercise also aided recall.",
    }
    stale_draft = {**verified, "predicate": "supports"}
    fresh_quote = {**verified, "evidence_quote": "Memory improved after the exercise break."}
    content = "\n".join(claim["evidence_quote"] for claim in (
        verified, disputed, matching_draft, fresh_quote,
    ))
    before_retry = json.dumps({"assertions": [verified, disputed, matching_draft, stale_draft]})
    retry_reply = json.dumps({"assertions": [verified, matching_draft, fresh_quote]})

    with client_for(tmp_path) as client:
        service = client.app.state.knowledge_service
        service.extractor = ModelExtractor(gateway(FakeProvider(
            api_error(openai.InternalServerError, 500, "offline"), retry_reply,
        )))
        receipt = client.post("/api/sources", json={
            "title": "Model claims", "kind": "note", "content": content,
        }).raise_for_status().json()
        source_id = receipt["source"]["id"]
        url = f"/api/sources/{source_id}"
        prior = ModelExtractor(gateway(FakeProvider(before_retry)))
        with session_scope(service.sessions) as session:
            source = session.get(Source, source_id)
            service.store_source_extraction(session, source, prior.extract(source.title, content))

        worker = client.app.state.processing_worker
        assert worker.run_once(ai=True)
        failed = client.get(url).raise_for_status().json()
        assert failed["analysis"]["state"] == "failed"
        previous = {claim["predicate"]: claim for claim in failed["assertions"]}
        reviewed = []
        for predicate, status in [("improves", "verified"), ("inhibits", "disputed")]:
            status_url = f"/api/assertions/{previous[predicate]['id']}/status"
            response = client.patch(status_url, json={"status": status})
            reviewed.append(response.raise_for_status().json())

        assert client.post(f"{url}/analysis/retry").raise_for_status().json()["state"] == "queued"
        assert worker.run_once(ai=True)
        finished = client.get(url).raise_for_status().json()
        assert finished["analysis"]["state"] == "completed"
        current = {claim["id"]: claim for claim in finished["assertions"]}
        for saved in [*reviewed, previous["aids"]]:
            assert current.pop(saved["id"]) == saved
        [fresh] = current.values()
        assert fresh["predicate"] == "improves" and fresh["status"] == "provisional"
        assert fresh["evidence"][0]["quote"] == fresh_quote["evidence_quote"]


def test_old_queued_capture_waits_for_analysis_and_keeps_its_text(tmp_path):
    statement = "CD3D -> marker_of -> T cell"
    with client_for(tmp_path) as client:
        service = client.app.state.knowledge_service
        with session_scope(service.sessions) as session:
            source = Source(id="src_saved", title="Saved capture", kind="note", content=statement,
                            content_hash=source_fingerprint("Saved capture", "note", statement))
            session.add(source)
            session.flush()
            service.index.add_source(session, source)
            enqueue(session, source.id, "extract_assertions", analysis_job_key(
                source.id, source.content_hash,
            ))
        assert client.app.state.processing_worker.run_once(ai=True)
        saved = client.get("/api/sources/src_saved").raise_for_status().json()
        assert saved["analysis"]["state"] == "needs_model"
        assert saved["content"] == statement and saved["assertions"] == []


@pytest.mark.parametrize("kind", ["recording", "note"])
def test_capture_is_readable_without_analysis_and_retry_survives_restart(tmp_path, kind):
    with client_for(tmp_path) as client:
        saved = capture(client, kind)
        source_id = saved["source"]["id"]
        assert saved["created"] == {"entities": 0, "assertions": 0}
        detail = client.get(f"/api/sources/{source_id}").raise_for_status().json()
        assert detail["content"] == "Exercise improves memory."
        assert detail["analysis"]["state"] == "needs_model"
        job_id = detail["analysis"]["jobId"]
        structure = client.get(f"/api/sources/{source_id}/structure").json()
        assert structure["processing"]["state"] == "ready"
        assert any("Exercise improves memory." in row["content"] for row in structure["blocks"])
        search = client.get("/api/search?q=memory").raise_for_status().json()
        assert any(result["id"] == source_id for result in search)
        assert capture(client, kind)["duplicate"]
        assert not client.app.state.processing_worker.run_once(ai=True)

    with client_for(tmp_path) as client:
        url = f"/api/sources/{source_id}"
        state = client.get(url).json()["analysis"]
        assert state["state"] == "needs_model" and state["jobId"] == job_id
        service = client.app.state.knowledge_service
        service.extractor = ModelExtractor(gateway(FakeProvider(model_reply())))
        retry = client.post(f"{url}/analysis/retry")
        assert retry.status_code == 202 and retry.json()["state"] == "queued"
        assert retry.json()["jobId"] == job_id
        worker = client.app.state.processing_worker
        assert worker.run_once(ai=True)
        assert client.get(url).json()["analysis"]["state"] == "completed"
        assert client.post(f"{url}/analysis/retry").json()["state"] == "completed"
        assert not worker.run_once(ai=True)
        with session_scope(service.sessions) as session:
            assert session.scalar(select(func.count()).select_from(Assertion)) == 1
            assert session.scalar(select(Fragment.content)) == "Exercise improves memory."


@pytest.mark.parametrize("invalid_evidence", [False, True])
def test_model_failure_leaves_the_saved_source_readable_and_retries_without_duplicates(
    tmp_path, invalid_evidence,
):
    with client_for(tmp_path) as client:
        service = client.app.state.knowledge_service
        failure = model_reply("Invented quote.") if invalid_evidence else api_error(
            openai.InternalServerError, 500, "offline"
        )
        fake = FakeProvider(failure, model_reply())
        service.extractor = ModelExtractor(gateway(fake))
        saved = capture(client)
        url = f"/api/sources/{saved['source']['id']}"
        assert not fake.requests
        worker = client.app.state.processing_worker
        assert worker.run_once(ai=True)
        failed = client.get(url).raise_for_status().json()
        assert failed["content"] == "Exercise improves memory."
        assert failed["analysis"]["state"] == "failed"
        assert failed["assertions"] == []
        assert not worker.run_once(ai=True)
        assert client.post(f"{url}/analysis/retry").json()["state"] == "queued"
        assert worker.run_once(ai=True)
        assert not worker.run_once(ai=True)
        finished = client.get(url).raise_for_status().json()
        assert finished["analysis"]["state"] == "completed"
        [claim] = finished["assertions"]
        assert claim["evidence"][0]["quote"] == "Exercise improves memory."


@pytest.mark.parametrize("interruption", ["cancelled", "changed", "trashed"])
def test_late_analysis_cannot_publish_after_cancellation_or_source_changes(tmp_path, interruption):
    with client_for(tmp_path) as client:
        service = client.app.state.knowledge_service
        extractor = ModelExtractor(gateway(FakeProvider(model_reply())))
        service.extractor = extractor
        source_id = capture(client)["source"]["id"]

        def interrupted_extract(title, content):
            with session_scope(service.sessions) as session:
                source = session.get(Source, source_id)
                if interruption == "changed":
                    source.content = "The user replaced the transcript."
                    source.content_hash = source_fingerprint(
                        source.title, source.kind, source.content
                    )
                elif interruption == "trashed":
                    source.trashed_at = utc_now()
                else:
                    job = session.scalar(select(ProcessingJob).where(
                        ProcessingJob.kind == "extract_assertions",
                    ))
                    job.state = "cancelled"
            return extractor.extract(title, content)

        service.extractor = Mock(extract=interrupted_extract)
        assert client.app.state.processing_worker.run_once(ai=True)
        with session_scope(service.sessions) as session:
            assert session.scalar(select(func.count()).select_from(Assertion)) == 0
            assert session.scalar(select(ProcessingJob.state).where(
                ProcessingJob.kind == "extract_assertions",
            )) == "cancelled"
