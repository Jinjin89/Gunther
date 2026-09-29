from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import gunther.asset_service as asset_module
import gunther.recording_service as recording_module
import gunther.storage_budget as budget_module
from gunther.asset_service import AssetService
from gunther.config import Settings
from gunther.database import session_scope
from gunther.main import create_app
from gunther.models import Asset, RecordingChunk, RecordingSession
from gunther.recording_service import RecordingService
from gunther.storage_budget import (
    StorageBudget,
    StorageQuotaExceededError,
    StorageSpaceUnavailableError,
)


def _settings(tmp_path: Path, *, quota: int) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        assets_dir=tmp_path / "assets",
        recordings_dir=tmp_path / "recordings",
        storage_quota_bytes=quota,
        storage_min_free_bytes=0,
        seed_demo=False,
        deepseek_api_key=None,
    )


def _managed_files(tmp_path: Path) -> list[Path]:
    return [
        path
        for directory in (tmp_path / "assets", tmp_path / "recordings")
        if directory.exists()
        for path in directory.rglob("*")
        if path.is_file()
    ]


def test_storage_settings_have_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_QUOTA_BYTES", "12345")
    monkeypatch.setenv("STORAGE_MIN_FREE_BYTES", "678")

    settings = Settings(_env_file=None)

    assert settings.storage_quota_bytes == 12_345
    assert settings.storage_min_free_bytes == 678


def test_active_concurrent_reservation_cannot_overcommit_quota(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    budget = StorageBudget((root,), quota_bytes=10, min_free_bytes=0)
    first_ready = threading.Event()
    release_first = threading.Event()
    outcome: list[type[BaseException] | str] = []

    def hold_first_reservation() -> None:
        with budget.reserve(root, expected_bytes=6):
            first_ready.set()
            assert release_first.wait(timeout=5)

    def try_second_reservation() -> None:
        assert first_ready.wait(timeout=5)
        try:
            with budget.reserve(root, expected_bytes=5):
                outcome.append("unexpected success")
        except StorageQuotaExceededError as error:
            outcome.append(type(error))
        finally:
            release_first.set()

    holder = threading.Thread(target=hold_first_reservation)
    contender = threading.Thread(target=try_second_reservation)
    holder.start()
    contender.start()
    holder.join(timeout=5)
    contender.join(timeout=5)

    assert not holder.is_alive()
    assert not contender.is_alive()
    assert outcome == [StorageQuotaExceededError]
    # Failed and completed requests release their capacity.
    with budget.reserve(root, expected_bytes=10):
        pass


def test_disk_free_floor_accounts_for_outstanding_reservations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    monkeypatch.setattr(
        budget_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=100, used=90, free=10),
    )
    budget = StorageBudget((root,), quota_bytes=100, min_free_bytes=8)

    with pytest.raises(StorageSpaceUnavailableError, match="STORAGE_MIN_FREE_BYTES"):
        budget.reserve(root, expected_bytes=3)


def test_managed_root_and_nested_symlinks_fail_closed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-root"
    try:
        linked_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This filesystem does not permit symlinks")

    root_budget = StorageBudget((linked_root,), quota_bytes=100, min_free_bytes=0)
    with pytest.raises(StorageSpaceUnavailableError, match="symbolic link"):
        root_budget.reserve(linked_root, expected_bytes=1)

    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / ".incoming").symlink_to(outside, target_is_directory=True)
    nested_budget = StorageBudget((managed,), quota_bytes=100, min_free_bytes=0)
    with pytest.raises(StorageSpaceUnavailableError, match="symbolic link"):
        nested_budget.reserve(managed, expected_bytes=1)


def test_asset_quota_rejection_leaves_no_file_or_database_row(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, quota=5))
    with TestClient(app) as client:
        response = client.post(
            "/api/captures/assets",
            params={"title": "Too large for storage", "fileName": "quota.bin"},
            content=b"123456",
            headers={"Content-Type": "application/octet-stream"},
        )

        assert response.status_code == 507
        assert "quota" in response.json()["detail"].lower()
        assert client.get("/api/sources").json() == []

    assert _managed_files(tmp_path) == []
    with session_scope(app.state.knowledge_service.sessions) as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 0


def test_low_disk_rejection_is_507_and_does_not_create_recording(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path, quota=100).model_copy(
        update={"storage_min_free_bytes": 8}
    )
    app = create_app(settings)
    monkeypatch.setattr(
        budget_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=100, used=90, free=10),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/recordings?title=No%20space",
            content=b"123",
            headers={"Content-Type": "audio/webm"},
        )

        assert response.status_code == 507
        assert "STORAGE_MIN_FREE_BYTES" in response.json()["detail"]
        assert client.get("/api/recordings").json() == []

    assert _managed_files(tmp_path) == []


def test_unknown_length_recording_stream_is_rejected_mid_receive_and_released(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path, quota=5))

    def body():
        yield b"123"
        yield b"456"

    with TestClient(app) as client:
        response = client.post(
            "/api/recordings?title=Streaming",
            content=body(),
            headers={"Content-Type": "audio/webm"},
        )

        assert response.status_code == 507
        assert client.get("/api/recordings").json() == []

        # The rejected stream must not leak its reservation into the next request.
        accepted = client.post(
            "/api/recordings?title=Fits",
            content=b"12345",
            headers={"Content-Type": "audio/webm"},
        )
        assert accepted.status_code == 201

    files = _managed_files(tmp_path)
    assert len(files) == 1
    assert files[0].read_bytes() == b"12345"


def test_chunk_quota_failure_keeps_file_and_integrity_ledger_aligned(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, quota=5))
    first_chunk = b"1234"
    rejected_chunk = b"56"
    with TestClient(app) as client:
        started = client.post(
            "/api/recordings/sessions?title=Budgeted",
            headers={"Content-Type": "audio/webm"},
        ).json()
        recording_id = started["id"]
        first = client.put(
            f"/api/recordings/{recording_id}/chunks?sequence=0",
            content=first_chunk,
            headers={"X-Chunk-SHA256": hashlib.sha256(first_chunk).hexdigest()},
        )
        rejected = client.put(
            f"/api/recordings/{recording_id}/chunks?sequence=1",
            content=rejected_chunk,
            headers={"X-Chunk-SHA256": hashlib.sha256(rejected_chunk).hexdigest()},
        )

        assert first.status_code == 200
        assert rejected.status_code == 507
        metadata = client.get(f"/api/recordings/{recording_id}/metadata").json()
        assert metadata["sizeBytes"] == len(first_chunk)
        assert metadata["nextExpectedSequence"] == 1

    part_path = _managed_files(tmp_path)[0]
    assert part_path.read_bytes() == first_chunk
    with session_scope(app.state.knowledge_service.sessions) as session:
        recording = session.get(RecordingSession, recording_id)
        assert recording is not None
        assert recording.byte_size == len(first_chunk)
        assert session.scalar(select(func.count()).select_from(RecordingChunk)) == 1


def test_recording_part_symlink_is_rejected_before_recovery_can_mutate_target(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path, quota=100))
    outside = tmp_path / "outside-audio"
    outside.write_bytes(b"must-not-be-truncated")
    with TestClient(app) as client:
        started = client.post(
            "/api/recordings/sessions?title=Symlink",
            headers={"Content-Type": "audio/webm"},
        ).json()
        part_path = app.state.settings.recordings_dir / f"{started['fileName']}.part"
        part_path.unlink()
        try:
            part_path.symlink_to(outside)
        except OSError:
            pytest.skip("This filesystem does not permit symlinks")

        chunk = b"new-audio"
        response = client.put(
            f"/api/recordings/{started['id']}/chunks?sequence=0",
            content=chunk,
            headers={"X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest()},
        )

        assert response.status_code == 507
        assert "symbolic link" in response.json()["detail"]

    assert outside.read_bytes() == b"must-not-be-truncated"
    with session_scope(app.state.knowledge_service.sessions) as session:
        recording = session.get(RecordingSession, started["id"])
        assert recording is not None
        assert recording.byte_size == 0
        assert recording.next_sequence == 0
        assert session.scalar(select(func.count()).select_from(RecordingChunk)) == 0


def test_asset_database_failure_removes_published_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path, quota=100))
    service = AssetService(
        app.state.knowledge_service.sessions,
        app.state.settings.assets_dir,
        app.state.knowledge_service,
        storage_budget=app.state.storage_budget,
    )
    incoming = app.state.settings.assets_dir / ".incoming"
    incoming.mkdir(parents=True)
    temporary = incoming / "received.part"
    data = b"rollback-asset"
    temporary.write_bytes(data)
    real_session_scope = asset_module.session_scope
    call_count = 0

    @contextmanager
    def fail_second_transaction(factory):
        nonlocal call_count
        call_count += 1
        with real_session_scope(factory) as session:
            yield session
            if call_count == 2:
                raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(asset_module, "session_scope", fail_second_transaction)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        service._preserve(
            temporary,
            hashlib.sha256(data).hexdigest(),
            len(data),
            "rollback.bin",
            "application/octet-stream",
        )

    assert _managed_files(tmp_path) == []
    with real_session_scope(app.state.knowledge_service.sessions) as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 0


def test_chunk_database_failure_truncates_bytes_and_rolls_back_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(_settings(tmp_path, quota=100))
    service = RecordingService(
        app.state.knowledge_service.sessions,
        app.state.settings.recordings_dir,
        app.state.storage_budget,
    )
    started = service.start("Rollback", "audio/webm")
    real_session_scope = recording_module.session_scope

    @contextmanager
    def fail_transaction(factory):
        with real_session_scope(factory) as session:
            yield session
            raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(recording_module, "session_scope", fail_transaction)
    data = b"uncommitted"
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        service.append(
            started.id,
            data,
            sequence=0,
            checksum=hashlib.sha256(data).hexdigest(),
        )

    part_path = app.state.settings.recordings_dir / f"{started.file_name}.part"
    assert part_path.read_bytes() == b""
    with real_session_scope(app.state.knowledge_service.sessions) as session:
        recording = session.get(RecordingSession, started.id)
        assert recording is not None
        assert recording.byte_size == 0
        assert recording.next_sequence == 0
        assert session.scalar(select(func.count()).select_from(RecordingChunk)) == 0
