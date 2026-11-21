import io
import wave
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from recording_fixtures import completed_recording
from sqlalchemy import func, select

from gunther import processing
from gunther.audio_decode import AudioUnreadable, audio_duration_ms
from gunther.config import Settings
from gunther.database import session_scope
from gunther.main import create_app
from gunther.models import ProcessingJob, RecordingSession, SourceIndexHead
from gunther.recording_metadata import source_recording_id


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
        processing_worker_enabled=False, ocr_provider="disabled",
        deepseek_api_key=None,
    )))


def saved_source(
    client: TestClient, data: bytes | None = None, *, legacy: bool = False, kind: str = "recording"
) -> tuple[str, str, str]:
    recording = completed_recording(
        client, data or audio_bytes(), title="Recording", content_type="audio/wav"
    ).json()
    recording_id = recording["id"]
    checkpointed = client.patch(f"/api/recordings/{recording_id}/checkpoint", json={
        "expectedRevision": 0, "durationSeconds": 1,
    }).raise_for_status().json()
    assert checkpointed["knowledgeBaseId"] is None
    assert checkpointed["transcriptSegments"] is None
    base = client.post("/api/knowledge-bases", json={
        "title": "Course", "question": "Course", "description": "Course",
    }).json()["id"]
    content = (
        f"# Recording\n\nDuration: 00:01 · Captured: today · Local recording: {recording_id}"
        "\n\n## Transcript\n\n[00:00] Original words."
    )
    if legacy:
        words = (
            "[00:00] Original words.\n\n"
            "These words mention Local recording: rec_000000000000000000000000 in a sentence.\n\n"
            + "More words. " * 210
        )
        content = f"{words.rstrip()}\n\nLocal recording: {recording_id}"
    source = client.post("/api/sources?deferAnalysis=true", json={
        "title": "Recording", "kind": kind, "content": content, "knowledgeBaseId": base,
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


@pytest.mark.parametrize("legacy", [False, True])
def test_duration_job_survives_restart_and_corrects_all_views_without_rewriting_evidence(
    tmp_path, monkeypatch, legacy
):
    probe = Mock(wraps=audio_duration_ms)
    monkeypatch.setattr(processing, "audio_duration_ms", probe)
    with client_for(tmp_path) as client:
        rid, sid, base = saved_source(client, legacy=legacy)
        probe.assert_not_called()
        original = client.get(f"/api/sources/{sid}").json()
        assert original["recordingId"] == rid
        assert original["recordingReferenceAtEnd"] is legacy
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
        assert source["recordingId"] == rid
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


@pytest.mark.parametrize("current_header", [False, True])
def test_source_keeps_the_audio_reference_above_the_heading(tmp_path, current_header):
    with client_for(tmp_path) as client:
        recording = completed_recording(
            client, audio_bytes(), title="Lecture", content_type="audio/wav"
        )
        rid = recording.json()["id"]
        decoy = "rec_" + "0" * 24
        head = f"# Lecture\n\nLocal recording: {decoy if current_header else rid}\n\n"
        if current_header:
            head += f"Duration: 00:01 · Captured: today · Local recording: {rid}\n\n"
        tail = f"[00:00] Words.\n\nLocal recording: {decoy}"
        content = f"{head}Preamble.\n\n## Transcript\n\n{tail}"
        saved = client.post("/api/sources?deferAnalysis=true", json={
            "title": "Lecture", "kind": "recording", "content": content,
        }).raise_for_status().json()["source"]
        assert saved["recordingId"] == rid
        measure(client)

        source = client.get(f"/api/sources/{saved['id']}").raise_for_status().json()
        assert source["recordingId"] == rid
        assert source["recordingDurationSeconds"] == 4
        assert not source["recordingReferenceAtEnd"]
        assert source["content"] == content


def test_unheaded_legacy_reference_must_be_the_last_nonempty_line():
    rid = "rec_" + "a" * 24
    content = f"Old capture\r\n\r\nLocal recording: {rid}"
    assert source_recording_id(content + "\r\n \r\n") == rid
    assert source_recording_id(content + "\r\nDuration: 00:35") is None


def test_transcript_only_recording_keeps_a_reference_like_last_line(tmp_path):
    with client_for(tmp_path) as client:
        recording = completed_recording(
            client, audio_bytes(), title="Other recording", content_type="audio/wav"
        ).json()
        content = (
            "# Lecture\n\nDuration: 00:12 · Captured: today\n\n## Transcript\n\n"
            f"[00:00] Words.\n\nLocal recording: {recording['id']}"
        )
        saved = client.post("/api/sources?deferAnalysis=true", json={
            "title": "Lecture", "kind": "recording", "content": content,
        }).raise_for_status().json()["source"]
        opened = client.get(f"/api/sources/{saved['id']}").raise_for_status().json()
        assert opened["recordingId"] is None
        assert not opened["recordingReferenceAtEnd"]
        assert opened["recordingDurationSeconds"] == 12
        assert not opened["recordingDurationPending"]
        assert opened["content"] == content


def test_audio_linked_note_does_not_wait_for_a_measurement_job(tmp_path):
    with client_for(tmp_path) as client:
        rid, sid, _ = saved_source(client, kind="note")
        service = client.app.state.knowledge_service
        service.index.backfill(10)
        with session_scope(service.sessions) as session:
            assert session.scalar(select(ProcessingJob.id).where(
                ProcessingJob.kind == "measure_recording"
            )) is None
        opened = client.get(f"/api/sources/{sid}").raise_for_status().json()
        assert opened["recordingId"] == rid
        assert opened["recordingDurationSeconds"] == 1
        assert not opened["recordingDurationPending"]


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
        assert source["recordingDurationSeconds"] == 1
        assert source["recordingDurationPending"]
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
