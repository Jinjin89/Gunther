import hashlib
import os
from pathlib import Path

from fastapi.testclient import TestClient

from gunther.config import Settings
from gunther.main import create_app


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        recordings_dir=tmp_path / "recordings",
        seed_demo=False,
        deepseek_api_key=None,
    )


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _append(
    client: TestClient,
    recording_id: str,
    sequence: int,
    data: bytes,
):
    return client.put(
        f"/api/recordings/{recording_id}/chunks?sequence={sequence}",
        content=data,
        headers={
            "Content-Type": "audio/webm",
            "X-Chunk-SHA256": _checksum(data),
        },
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

        wrong_checksum = client.put(
            f"/api/recordings/{recording_id}/chunks?sequence=1",
            content=b"second-audio-chunk",
            headers={"X-Chunk-SHA256": "0" * 64},
        )
        assert wrong_checksum.status_code == 422

        second_data = b"second-audio-chunk"
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


def test_completed_recording_corruption_fails_closed_without_deleting_bytes(
    tmp_path: Path,
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
    final_path.write_bytes(corrupted)

    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/recordings/{recording_id}").status_code == 404
        metadata = client.get(f"/api/recordings/{recording_id}/metadata")
        assert metadata.status_code == 200
        assert metadata.json()["status"] == "failed"
        assert metadata.json()["recovery"]["audioAvailable"] is True
        listed = next(
            item for item in client.get("/api/recordings").json() if item["id"] == recording_id
        )
        assert listed["status"] == "failed"
        assert client.post(f"/api/recordings/{recording_id}/complete").status_code == 409
        assert final_path.read_bytes() == corrupted


def test_direct_upload_is_a_completed_recording_session(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        saved = client.post(
            "/api/recordings?title=Imported%20recording",
            content=b"complete-audio",
            headers={"Content-Type": "audio/ogg"},
        )
        assert saved.status_code == 201
        recording = saved.json()
        assert recording["status"] == "completed"
        assert recording["nextExpectedSequence"] == 1
        assert recording["completedAt"] is not None
        assert client.get(f"/api/recordings/{recording['id']}/metadata").json() == recording
        assert any(item["id"] == recording["id"] for item in client.get("/api/recordings").json())


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
