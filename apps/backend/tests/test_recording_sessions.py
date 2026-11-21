import hashlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from recording_fixtures import append_recording_chunk as _append
from recording_fixtures import completed_recording

from gunther.config import Settings
from gunther.database import session_scope
from gunther.main import create_app
from gunther.models import RecordingChunk, RecordingSession, utc_now


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        recordings_dir=tmp_path / "recordings",
        deepseek_api_key=None,
    )


def test_chunk_protocol_is_idempotent_ordered_and_final(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        started = client.post(
            "/api/recordings/sessions?title=Reliable%20lecture",
            headers={"Content-Type": "audio/webm"},
        )
        assert started.status_code == 201
        recording = started.json()
        recording_id = recording["id"]
        assert recording["status"] == "capturing"
        assert recording["sizeBytes"] == 0
        assert recording["nextExpectedSequence"] == 0

        first_data = b"first-audio-chunk"
        first = _append(client, recording_id, 0, first_data)
        assert first.status_code == 200
        assert first.json()["sizeBytes"] == len(first_data)
        assert first.json()["nextExpectedSequence"] == 1

        duplicate = _append(client, recording_id, 0, first_data)
        assert duplicate.status_code == 200
        assert duplicate.json()["sizeBytes"] == len(first_data)
        assert duplicate.json()["nextExpectedSequence"] == 1

        conflicting_data = b"different-first-chunk"
        conflict = _append(client, recording_id, 0, conflicting_data)
        assert conflict.status_code == 409

        out_of_order = _append(client, recording_id, 2, b"third-before-second")
        assert out_of_order.status_code == 409
        assert "expected sequence 1" in out_of_order.json()["detail"]

        second_data = b"second-audio-chunk"
        chunks_url = f"/api/recordings/{recording_id}/chunks"
        correct_checksum = {"X-Chunk-SHA256": hashlib.sha256(second_data).hexdigest()}
        for params, headers in (
            ({}, correct_checksum),
            ({"sequence": 1}, {}),
            ({"sequence": 1}, {"X-Chunk-SHA256": "0" * 64}),
        ):
            rejected = client.put(chunks_url, params=params, content=second_data, headers=headers)
            assert rejected.status_code == 422

        second = _append(client, recording_id, 1, second_data)
        assert second.status_code == 200
        assert second.json()["nextExpectedSequence"] == 2

        completed = client.post(f"/api/recordings/{recording_id}/complete")
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        assert completed.json()["completedAt"] is not None

        repeated_complete = client.post(f"/api/recordings/{recording_id}/complete")
        assert repeated_complete.status_code == 200
        assert repeated_complete.json()["completedAt"] == completed.json()["completedAt"]

        append_after_complete = _append(client, recording_id, 2, b"too-late")
        assert append_after_complete.status_code == 409

        downloaded = client.get(f"/api/recordings/{recording_id}")
        assert downloaded.status_code == 200
        assert downloaded.content == first_data + second_data


def test_recording_metadata_and_list_survive_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first_chunk = b"committed-before-restart"

    with TestClient(create_app(settings)) as client:
        started = client.post(
            "/api/recordings/sessions?title=Restartable",
            headers={"Content-Type": "audio/mpeg"},
        ).json()
        recording_id = started["id"]
        assert _append(client, recording_id, 0, first_chunk).status_code == 200
        part_path = settings.recordings_dir / f"{started['fileName']}.part"
        with part_path.open("ab") as interrupted_write:
            interrupted_write.write(b"uncommitted-tail")
            interrupted_write.flush()
            os.fsync(interrupted_write.fileno())

    with TestClient(create_app(settings)) as client:
        metadata = client.get(f"/api/recordings/{recording_id}/metadata")
        assert metadata.status_code == 200
        assert metadata.json()["status"] == "capturing"
        assert metadata.json()["sizeBytes"] == len(first_chunk)
        assert metadata.json()["nextExpectedSequence"] == 1
        assert part_path.stat().st_size == len(first_chunk)

        duplicate = _append(client, recording_id, 0, first_chunk)
        assert duplicate.status_code == 200
        assert duplicate.json()["nextExpectedSequence"] == 1

        sessions = client.get("/api/recordings")
        assert sessions.status_code == 200
        listed = next(item for item in sessions.json() if item["id"] == recording_id)
        assert listed["title"] == "Restartable"
        assert listed["contentType"] == "audio/mpeg"

        second_chunk = b"committed-after-restart"
        assert _append(client, recording_id, 1, second_chunk).status_code == 200
        completed = client.post(f"/api/recordings/{recording_id}/complete")
        assert completed.status_code == 200
        assert not part_path.exists()
        assert (settings.recordings_dir / started["fileName"]).is_file()

    with TestClient(create_app(settings)) as client:
        completed_metadata = client.get(f"/api/recordings/{recording_id}/metadata")
        assert completed_metadata.json()["status"] == "completed"
        downloaded = client.get(f"/api/recordings/{recording_id}")
        assert downloaded.content == first_chunk + second_chunk


def test_interrupted_finalize_returns_to_capturing_state(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    audio = b"audio-before-interrupted-finalize"

    with TestClient(create_app(settings)) as client:
        started = client.post(
            "/api/recordings/sessions?title=Interrupted%20finalize",
            headers={"Content-Type": "audio/webm"},
        ).json()
        recording_id = started["id"]
        assert _append(client, recording_id, 0, audio).status_code == 200

    part_path = settings.recordings_dir / f"{started['fileName']}.part"
    final_path = settings.recordings_dir / started["fileName"]
    os.replace(part_path, final_path)

    with TestClient(create_app(settings)) as client:
        metadata = client.get(f"/api/recordings/{recording_id}/metadata")
        assert metadata.status_code == 200
        assert metadata.json()["status"] == "capturing"
        assert part_path.is_file()
        assert not final_path.exists()
        assert client.post(f"/api/recordings/{recording_id}/complete").status_code == 200
        assert final_path.is_file()


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_completed_recording_damage_fails_closed_without_deleting_bytes(
    tmp_path: Path, damage: str,
) -> None:
    settings = _settings(tmp_path)
    audio = b"durable-completed-audio"

    with TestClient(create_app(settings)) as client:
        started = client.post(
            "/api/recordings/sessions?title=Corruption%20check",
            headers={"Content-Type": "audio/webm"},
        ).json()
        recording_id = started["id"]
        assert _append(client, recording_id, 0, audio).status_code == 200
        assert client.post(f"/api/recordings/{recording_id}/complete").status_code == 200

    final_path = settings.recordings_dir / started["fileName"]
    corrupted = b"X" + audio[1:]
    assert len(corrupted) == len(audio)
    if damage == "missing":
        final_path.unlink()
    else:
        final_path.write_bytes(corrupted)

    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/recordings/{recording_id}").status_code == 404
        metadata = client.get(f"/api/recordings/{recording_id}/metadata")
        assert metadata.status_code == 200
        assert metadata.json()["status"] == "failed"
        listed = next(
            item for item in client.get("/api/recordings").json() if item["id"] == recording_id
        )
        assert listed["status"] == "failed"
        assert client.post(f"/api/recordings/{recording_id}/complete").status_code == 409
        if damage == "missing":
            assert not final_path.exists()
        else:
            assert metadata.json()["recovery"]["audioAvailable"] is True
            assert final_path.read_bytes() == corrupted


def test_imported_audio_is_a_completed_recording_session(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.post(
            "/api/recordings", params={"title": "Retired"}, content=b"audio"
        ).status_code == 405
        assert client.get("/api/recordings").json() == []
        saved = completed_recording(
            client, b"complete-audio", title="Imported recording", content_type="audio/ogg"
        )
        assert saved.status_code == 200
        recording = saved.json()
        assert recording["status"] == "completed"
        assert recording["nextExpectedSequence"] == 1
        assert recording["completedAt"] is not None
        assert client.get(f"/api/recordings/{recording['id']}/metadata").json() == recording
        assert any(item["id"] == recording["id"] for item in client.get("/api/recordings").json())


@pytest.mark.parametrize("has_ledger", [False, True])
def test_old_recording_audio_still_opens(tmp_path: Path, has_ledger: bool) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    recording_id = "rec_0123456789abcdef01234567"
    file_name = f"{recording_id}.Old-lecture.ogg"
    audio = b"previously-saved-audio"
    with TestClient(app) as client:
        settings.recordings_dir.mkdir(exist_ok=True)
        (settings.recordings_dir / file_name).write_bytes(audio)
        if has_ledger:
            with session_scope(app.state.knowledge_service.sessions) as session:
                session.add(RecordingSession(
                    id=recording_id,
                    title="Old lecture",
                    status="completed",
                    content_type="audio/ogg",
                    file_name=file_name,
                    byte_size=len(audio),
                    next_sequence=1,
                    completed_at=utc_now(),
                    chunks=[RecordingChunk(
                        id="rch_legacy", sequence=0, size_bytes=len(audio),
                        checksum=hashlib.sha256(audio).hexdigest(),
                    )],
                ))

        metadata = client.get(f"/api/recordings/{recording_id}/metadata")
        assert metadata.status_code == (200 if has_ledger else 404)
        if has_ledger:
            assert metadata.json()["status"] == "completed"
            assert metadata.json()["nextExpectedSequence"] == 1
        audio_response = client.get(f"/api/recordings/{recording_id}")
        assert audio_response.status_code == 200
        assert audio_response.headers["content-type"] == "audio/ogg"
        assert audio_response.content == audio


def test_recording_checkpoint_is_idempotent_versioned_and_recoverable(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    initial_checkpoint = {
        "expectedRevision": 0,
        "transcript": "[00:00] Welcome to bioinformatics.",
        "durationSeconds": 15,
        "moments": [{"seconds": 12, "label": "Important definition"}],
        "recordingContext": "lecture",
        "knowledgeBaseId": "bioinformatics",
    }

    with TestClient(create_app(settings)) as client:
        started = client.post(
            "/api/recordings/sessions?title=Checkpointed%20lecture",
            headers={"Content-Type": "audio/webm"},
        ).json()
        recording_id = started["id"]
        assert started["checkpointRevision"] == 0
        assert started["transcript"] == ""
        assert started["recovery"] == {
            "canResume": True,
            "audioAvailable": False,
            "nextExpectedSequence": 0,
            "checkpointRevision": 0,
            "checkpointedAt": None,
        }
        assert _append(client, recording_id, 0, b"audio-progress").status_code == 200

        checkpointed = client.patch(
            f"/api/recordings/{recording_id}/checkpoint",
            json=initial_checkpoint,
        )
        assert checkpointed.status_code == 200
        state = checkpointed.json()
        assert state["checkpointRevision"] == 1
        assert state["transcript"] == initial_checkpoint["transcript"]
        assert state["durationSeconds"] == 15
        assert state["moments"] == initial_checkpoint["moments"]
        assert state["recordingContext"] == "lecture"
        assert state["knowledgeBaseId"] == "bioinformatics"
        assert state["checkpointedAt"] is not None
        assert state["recovery"]["audioAvailable"] is True

        retried = client.patch(
            f"/api/recordings/{recording_id}/checkpoint",
            json=initial_checkpoint,
        )
        assert retried.status_code == 200
        assert retried.json()["checkpointRevision"] == 1
        assert retried.json()["checkpointedAt"] == state["checkpointedAt"]

        stale = client.patch(
            f"/api/recordings/{recording_id}/checkpoint",
            json={**initial_checkpoint, "transcript": "stale competing text"},
        )
        assert stale.status_code == 409
        assert state["transcriptSegments"] is None

        advanced = client.patch(
            f"/api/recordings/{recording_id}/checkpoint",
            json={
                **initial_checkpoint,
                "expectedRevision": 1,
                "transcript": "[00:00] Welcome.\n[00:15] Alignment begins.",
                "durationSeconds": 30,
            },
        )
        assert advanced.status_code == 200
        assert advanced.json()["checkpointRevision"] == 2
        assert client.post(f"/api/recordings/{recording_id}/complete").status_code == 200

        post_complete_edit = client.patch(
            f"/api/recordings/{recording_id}/checkpoint",
            json={
                **initial_checkpoint,
                "expectedRevision": 2,
                "transcript": "Edited after capture finished.",
                "durationSeconds": 30,
            },
        )
        assert post_complete_edit.status_code == 200
        assert post_complete_edit.json()["status"] == "completed"
        assert post_complete_edit.json()["checkpointRevision"] == 3

    with TestClient(create_app(settings)) as client:
        recovered = client.get(f"/api/recordings/{recording_id}/metadata")
        assert recovered.status_code == 200
        state = recovered.json()
        assert state["transcript"] == "Edited after capture finished."
        assert state["durationSeconds"] == 30
        assert state["moments"] == initial_checkpoint["moments"]
        assert state["recordingContext"] == "lecture"
        assert state["knowledgeBaseId"] == "bioinformatics"
        assert state["checkpointRevision"] == 3
        assert state["recovery"]["canResume"] is False


def test_checkpoint_stores_and_returns_the_transcript_segments(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    segments = {
        "version": 1,
        "sentences": [{"id": "s-0", "startMs": 0, "endMs": 900, "text": "你好。"}],
        "paragraphs": [{"id": "p-0", "sentenceIds": ["s-0"], "frozen": True}],
    }
    body = {
        "expectedRevision": 0,
        "transcript": "[00:00] 你好。",
        "transcriptSegments": segments,
        "durationSeconds": 3,
    }
    with TestClient(create_app(settings)) as client:
        recording_id = client.post(
            "/api/recordings/sessions?title=Paragraphs", headers={"Content-Type": "audio/webm"}
        ).json()["id"]
        saved = client.patch(f"/api/recordings/{recording_id}/checkpoint", json=body)
        assert saved.status_code == 200
        assert saved.json()["transcriptSegments"] == segments
        # The same checkpoint again changes nothing; different segments make a new revision.
        assert (
            client.patch(f"/api/recordings/{recording_id}/checkpoint", json=body).json()[
                "checkpointRevision"
            ]
            == 1
        )
        changed = {**segments, "paragraphs": []}
        again = client.patch(
            f"/api/recordings/{recording_id}/checkpoint",
            json={**body, "expectedRevision": 1, "transcriptSegments": changed},
        )
        assert again.json()["checkpointRevision"] == 2
        assert (
            client.get(f"/api/recordings/{recording_id}/metadata").json()["transcriptSegments"]
            == changed
        )
        too_big = {"version": 1, "pad": "x" * 2_100_000}
        rejected = client.patch(
            f"/api/recordings/{recording_id}/checkpoint",
            json={**body, "expectedRevision": 2, "transcriptSegments": too_big},
        )
        assert rejected.status_code == 422
