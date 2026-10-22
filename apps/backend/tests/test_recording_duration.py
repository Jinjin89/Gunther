import io
import wave
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gunther import processing
from gunther.audio_decode import AudioUnreadable, audio_duration_ms
from gunther.config import Settings
from gunther.database import session_scope
from gunther.main import create_app
from gunther.models import ProcessingJob, RecordingSession, SourceIndexHead


def audio_bytes(seconds: int = 4, channels: int = 2) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * seconds * 16_000 * channels)
    return output.getvalue()


def client_for(path: Path) -> TestClient:
    return TestClient(create_app(Settings(
        database_url=f"sqlite+pysqlite:///{path / 'db.sqlite'}",
        assets_dir=path / "assets", recordings_dir=path / "recordings",
        processing_worker_enabled=False, ocr_provider="disabled", seed_demo=False,
        deepseek_api_key=None,
    )))


def saved_source(client: TestClient, data: bytes | None = None) -> tuple[str, str, str]:
    recording = client.post(
        "/api/recordings?title=Recording", content=data or audio_bytes(),
        headers={"Content-Type": "audio/wav"},
    ).json()
    recording_id = recording["id"]
    client.patch(f"/api/recordings/{recording_id}/checkpoint", json={
        "expectedRevision": 0, "durationSeconds": 1,
    }).raise_for_status()
    base = client.post("/api/knowledge-bases", json={
        "title": "Course", "question": "Course", "description": "Course",
    }).json()["id"]
    content = (
        f"# Recording\n\nDuration: 00:01 · Captured: today · Local recording: {recording_id}"
        "\n\n## Transcript\n\n[00:00] Original words."
    )
    source = client.post("/api/sources?deferAnalysis=true", json={
        "title": "Recording", "kind": "recording", "content": content, "knowledgeBaseId": base,
    })
    source.raise_for_status()
    return recording_id, source.json()["source"]["id"], base


def measure(client: TestClient) -> None:
    worker = client.app.state.processing_worker
    service = client.app.state.knowledge_service
    with session_scope(service.sessions) as session:
        job_id = session.scalar(select(ProcessingJob.id).where(
            ProcessingJob.kind == "measure_recording"
        ))
    # Other reading jobs can precede the measurement; none waits on a model.
    while worker.run_once(ai=False):
        pass
    with session_scope(service.sessions) as session:
        assert session.get(ProcessingJob, job_id).state == "completed"


def test_duration_counts_samples_per_channel_without_writing_a_copy(tmp_path):
    path = tmp_path / "stereo.wav"
    original = audio_bytes()
    path.write_bytes(original)
    assert audio_duration_ms(path) == 4_000
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_duration_job_survives_restart_and_corrects_all_views_without_rewriting_evidence(
    tmp_path, monkeypatch
):
    probe = Mock(wraps=audio_duration_ms)
    monkeypatch.setattr(processing, "audio_duration_ms", probe)
    with client_for(tmp_path) as client:
        rid, sid, base = saved_source(client)
        probe.assert_not_called()
        original = client.get(f"/api/sources/{sid}").json()
        assert original["recordingDurationSeconds"] == 1
        assert original["recordingDurationPending"]
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            revision = session.get(SourceIndexHead, sid).revision_id
            # Simulate a source created by a version before measurement jobs existed.
            job = session.scalar(select(ProcessingJob).where(
                ProcessingJob.kind == "measure_recording"
            ))
            session.delete(job)

    with client_for(tmp_path) as client:
        service = client.app.state.knowledge_service
        service.index.backfill(10)
        measure(client)
        assert client.get(f"/api/recordings/{rid}/metadata").json()["durationSeconds"] == 4
        source = client.get(f"/api/sources/{sid}").json()
        assert source["content"] == original["content"]
        assert source["recordingDurationSeconds"] == 4
        assert not source["recordingDurationPending"]
        row = client.get(f"/api/knowledge-bases/{base}/contents").json()["sources"][0]
        assert row["durationSeconds"] == 4 and not row["durationPending"]
        assert client.get(f"/api/recordings/{rid}").content == audio_bytes()
        # A late checkpoint cannot replace measured duration with an old timer estimate.
        client.patch(f"/api/recordings/{rid}/checkpoint", json={
            "expectedRevision": 1, "durationSeconds": 2,
        }).raise_for_status()
        assert client.get(f"/api/recordings/{rid}/metadata").json()["durationSeconds"] == 4
        service.index.backfill(10)
        with session_scope(service.sessions) as session:
            recording = session.get(RecordingSession, rid)
            assert recording.duration_seconds == 2 and recording.audio_duration_ms == 4_000
            assert session.get(SourceIndexHead, sid).revision_id == revision
            assert session.scalar(select(func.count()).select_from(ProcessingJob).where(
                ProcessingJob.kind == "measure_recording"
            )) == 1


def test_unreadable_audio_preserves_the_estimate_and_reports_failure(tmp_path):
    with client_for(tmp_path) as client:
        rid, sid, _ = saved_source(client, b"not playable audio")
        worker = client.app.state.processing_worker
        while worker.run_once(ai=False):
            pass
        with session_scope(worker.sessions) as session:
            job = session.scalar(select(ProcessingJob).where(
                ProcessingJob.kind == "measure_recording"
            ))
            assert job.state == "queued" and job.error
            assert session.get(RecordingSession, rid).audio_duration_ms is None
        assert client.get(f"/api/sources/{sid}").json()["recordingDurationSeconds"] == 1


def test_an_unset_legacy_timer_keeps_the_source_estimate_until_audio_is_measured(tmp_path):
    with client_for(tmp_path) as client:
        rid, sid, base = saved_source(client)
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            session.get(RecordingSession, rid).duration_seconds = 0
        source = client.get(f"/api/sources/{sid}").json()
        assert source["recordingDurationSeconds"] is None
        row = client.get(f"/api/knowledge-bases/{base}/contents").json()["sources"][0]
        assert row["durationSeconds"] == 1 and row["durationPending"]
        measure(client)
        assert client.get(f"/api/sources/{sid}").json()["recordingDurationSeconds"] == 4


def test_cancelled_duration_job_cannot_publish_a_late_measurement(tmp_path, monkeypatch):
    with client_for(tmp_path) as client:
        rid, _, _ = saved_source(client)
        worker = client.app.state.processing_worker

        def cancelled(path, **options):
            measured = audio_duration_ms(path, **options)
            with session_scope(worker.sessions) as session:
                job = session.scalar(select(ProcessingJob).where(
                    ProcessingJob.kind == "measure_recording"
                ))
                job.state = "cancelled"
            return measured

        monkeypatch.setattr(processing, "audio_duration_ms", cancelled)
        while worker.run_once(ai=False):
            pass
        with session_scope(worker.sessions) as session:
            assert session.get(RecordingSession, rid).audio_duration_ms is None


def test_invalid_audio_has_no_invented_duration(tmp_path):
    path = tmp_path / "broken.webm"
    path.write_bytes(b"invalid")
    with pytest.raises(AudioUnreadable):
        audio_duration_ms(path)
