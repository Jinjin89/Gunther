from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gunther.config import Settings
from gunther.database import session_scope
from gunther.extraction import LocalExtractor
from gunther.main import create_app
from gunther.models import Assertion, Fragment, ProcessingJob, Source, utc_now
from gunther.service import source_fingerprint


def client_for(path: Path) -> TestClient:
    return TestClient(create_app(Settings(
        database_url=f"sqlite+pysqlite:///{path / 'db.sqlite'}",
        assets_dir=path / "assets", recordings_dir=path / "recordings",
        processing_worker_enabled=False, ocr_provider="disabled", seed_demo=False,
        deepseek_api_key=None, stt_provider="compatible",
    )))


def capture(client: TestClient, kind: str = "recording") -> dict:
    response = client.post("/api/sources?deferAnalysis=true", json={
        "title": "Saved capture", "kind": kind, "content": "Exercise improves memory.",
    })
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("kind", ["recording", "note"])
def test_capture_is_readable_without_a_model_and_analysis_survives_restart(tmp_path, kind):
    with client_for(tmp_path) as client:
        service = client.app.state.knowledge_service
        extract = Mock(side_effect=RuntimeError("The model is unavailable"))
        service.extractor.extract = extract
        saved = capture(client, kind)
        source_id = saved["source"]["id"]
        assert saved["created"] == {"entities": 0, "assertions": 0}
        extract.assert_not_called()
        assert client.get(f"/api/sources/{source_id}").json()["content"] == (
            "Exercise improves memory."
        )
        structure = client.get(f"/api/sources/{source_id}/structure").json()
        assert structure["processing"]["state"] == "ready"
        assert any("Exercise improves memory." in row["content"] for row in structure["blocks"])
        assert capture(client, kind)["duplicate"]
        with session_scope(service.sessions) as session:
            jobs = list(session.scalars(select(ProcessingJob).where(
                ProcessingJob.kind == "extract_assertions",
            )))
            assert len(jobs) == 1 and jobs[0].state == "queued"

    with client_for(tmp_path) as client:
        service = client.app.state.knowledge_service
        worker = client.app.state.processing_worker
        assert worker.run_once(ai=True)
        assert not worker.run_once(ai=True)
        with session_scope(service.sessions) as session:
            assert session.scalar(select(func.count()).select_from(Assertion)) == 1
            assert session.scalar(select(Fragment.content)) == "Exercise improves memory."
            job = session.scalar(select(ProcessingJob).where(
                ProcessingJob.kind == "extract_assertions",
            ))
            assert job.state == "completed"


def test_model_failure_leaves_the_saved_source_readable_and_retries_without_duplicates(tmp_path):
    with client_for(tmp_path) as client:
        service = client.app.state.knowledge_service
        saved = capture(client)
        result = LocalExtractor().extract("Saved capture", "Exercise improves memory.")
        service.extractor.extract = Mock(side_effect=[RuntimeError("offline"), result])
        worker = client.app.state.processing_worker
        assert worker.run_once(ai=True)
        assert client.get(f"/api/sources/{saved['source']['id']}").status_code == 200
        with session_scope(service.sessions) as session:
            job = session.scalar(select(ProcessingJob).where(
                ProcessingJob.kind == "extract_assertions",
            ))
            assert job.state == "queued" and job.attempts == 1
            job.available_at = utc_now()
            assert session.scalar(select(func.count()).select_from(Assertion)) == 0
        assert worker.run_once(ai=True)
        assert not worker.run_once(ai=True)
        with session_scope(service.sessions) as session:
            assert session.scalar(select(func.count()).select_from(Assertion)) == 1


@pytest.mark.parametrize("interruption", ["cancelled", "changed", "trashed"])
def test_late_analysis_cannot_publish_after_cancellation_or_source_changes(tmp_path, interruption):
    with client_for(tmp_path) as client:
        service = client.app.state.knowledge_service
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
            return LocalExtractor().extract(title, content)

        service.extractor.extract = interrupted_extract
        assert client.app.state.processing_worker.run_once(ai=True)
        with session_scope(service.sessions) as session:
            assert session.scalar(select(func.count()).select_from(Assertion)) == 0
