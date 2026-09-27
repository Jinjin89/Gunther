import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gunther.config import Settings
from gunther.main import create_app
from gunther.migrations import LATEST_SCHEMA_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKUP_SCRIPT = PROJECT_ROOT / "scripts" / "backup_gunther.py"


def _create_data_directory(root: Path) -> tuple[Path, sqlite3.Connection]:
    data_directory = root / "data"
    (data_directory / "assets" / "sha256" / "ab").mkdir(parents=True)
    (data_directory / "recordings").mkdir(parents=True)
    (data_directory / "assets" / "sha256" / "ab" / "source.pdf").write_bytes(
        b"immutable-source-bytes"
    )
    (data_directory / "recordings" / "rec_test.webm.part").write_bytes(
        b"recoverable-recording-bytes"
    )

    connection = sqlite3.connect(data_directory / "gunther.sqlite")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE gunther_schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE knowledge_bases (id TEXT PRIMARY KEY, title TEXT NOT NULL);
        CREATE TABLE sources (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            knowledge_base_id TEXT REFERENCES knowledge_bases(id)
        );
        """
    )
    connection.executemany(
        "INSERT INTO gunther_schema_migrations (version, name, applied_at) "
        "VALUES (?, ?, ?)",
        [
            (1, "baseline_current_schema", "2026-01-01T00:00:00Z"),
            (2, "durable_recording_lifecycle", "2026-01-02T00:00:00Z"),
            (3, "immutable_source_assets", "2026-01-03T00:00:00Z"),
            (4, "recording_recovery_checkpoints", "2026-01-04T00:00:00Z"),
        ],
    )
    connection.execute(
        "INSERT INTO knowledge_bases (id, title) VALUES ('biology', 'Biology')"
    )
    connection.commit()
    # Keep a committed row in the active WAL while this connection remains open.
    connection.execute(
        "INSERT INTO sources (id, title, knowledge_base_id) "
        "VALUES ('src_wal', 'Committed in WAL', 'biology')"
    )
    connection.commit()
    return data_directory, connection


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BACKUP_SCRIPT), *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _fingerprint(path: Path) -> tuple[int, int, str]:
    data = path.read_bytes()
    return path.stat().st_mtime_ns, len(data), hashlib.sha256(data).hexdigest()


def _real_settings(data_directory: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{data_directory / 'gunther.sqlite'}",
        assets_dir=data_directory / "assets",
        recordings_dir=data_directory / "recordings",
        seed_demo=False,
        deepseek_api_key=None,
        openai_api_key=None,
        stt_provider="openai",
    )


def _database_integrity(database: Path) -> tuple[list[str], list[tuple[object, ...]]]:
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
        foreign_key_errors = list(connection.execute("PRAGMA foreign_key_check"))
        return quick_check, foreign_key_errors
    finally:
        connection.close()


def _reseal_manifest(backup: Path) -> None:
    """Update only the outer manifest, simulating a self-consistent tampered backup."""

    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    total_size = 0
    for record in manifest["files"]:
        payload = backup / record["path"]
        data = payload.read_bytes()
        record["size"] = len(data)
        record["sha256"] = hashlib.sha256(data).hexdigest()
        total_size += len(data)
    manifest["totalSize"] = total_size
    manifest_data = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    manifest_path.write_bytes(manifest_data)
    (backup / "manifest.sha256").write_text(
        f"{hashlib.sha256(manifest_data).hexdigest()}  manifest.json\n",
        encoding="ascii",
    )


def test_backup_uses_sqlite_snapshot_copies_files_and_publishes_atomically(
    tmp_path: Path,
) -> None:
    data_directory, source_connection = _create_data_directory(tmp_path)
    output_directory = tmp_path / "backups"
    try:
        created = _run(
            "backup",
            "--data-dir",
            str(data_directory),
            "--output-dir",
            str(output_directory),
        )
        assert created.returncode == 0, created.stderr
        result = json.loads(created.stdout)
        backup = Path(result["backup"])
        assert result["status"] == "ok"
        assert result["created"] is True
        assert backup.parent == output_directory
        assert backup.is_dir()
        assert not list(output_directory.glob(".*.tmp"))

        manifest = json.loads((backup / "manifest.json").read_text())
        assert manifest["format"] == "gunther-local-backup"
        assert manifest["formatVersion"] == 1
        assert manifest["database"]["guntherSchemaVersion"] == 4
        assert manifest["database"]["quickCheck"] == "ok"
        records = {record["path"]: record for record in manifest["files"]}
        assert set(records) == {
            "gunther.sqlite",
            "assets/sha256/ab/source.pdf",
            "recordings/rec_test.webm.part",
        }
        for relative, record in records.items():
            copied = backup / relative
            assert copied.stat().st_size == record["size"]
            assert hashlib.sha256(copied.read_bytes()).hexdigest() == record["sha256"]
            if os.name != "nt":
                assert copied.stat().st_mode & 0o077 == 0

        snapshot = sqlite3.connect(
            f"file:{backup / 'gunther.sqlite'}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            assert snapshot.execute(
                "SELECT title FROM sources WHERE id = 'src_wal'"
            ).fetchone() == ("Committed in WAL",)
        finally:
            snapshot.close()
        assert not (backup / "gunther.sqlite-wal").exists()
        assert not (backup / "gunther.sqlite-shm").exists()
    finally:
        source_connection.close()


def test_verify_is_read_only_and_detects_tampering(tmp_path: Path) -> None:
    data_directory, source_connection = _create_data_directory(tmp_path)
    output_directory = tmp_path / "backups"
    try:
        created = _run(
            "backup",
            "--data-dir",
            str(data_directory),
            "--output-dir",
            str(output_directory),
        )
        assert created.returncode == 0, created.stderr
        backup = Path(json.loads(created.stdout)["backup"])
        before = {
            path.relative_to(backup).as_posix(): _fingerprint(path)
            for path in backup.rglob("*")
            if path.is_file()
        }

        verified = _run("verify", str(backup))
        assert verified.returncode == 0, verified.stderr
        assert json.loads(verified.stdout)["schemaVersion"] == 4
        after = {
            path.relative_to(backup).as_posix(): _fingerprint(path)
            for path in backup.rglob("*")
            if path.is_file()
        }
        assert after == before

        asset = backup / "assets" / "sha256" / "ab" / "source.pdf"
        asset.write_bytes(asset.read_bytes() + b"tampered")
        rejected = _run("verify", str(backup))
        assert rejected.returncode == 2
        assert "differs from manifest" in rejected.stderr
    finally:
        source_connection.close()


def test_restore_is_verified_atomic_and_never_replaces_existing_data(
    tmp_path: Path,
) -> None:
    data_directory, source_connection = _create_data_directory(tmp_path)
    try:
        created = _run(
            "backup",
            "--data-dir",
            str(data_directory),
            "--output-dir",
            str(tmp_path / "backups"),
        )
        assert created.returncode == 0, created.stderr
        backup = Path(json.loads(created.stdout)["backup"])
        restored_data = tmp_path / "restored-data"

        restored = _run(
            "restore",
            str(backup),
            "--data-dir",
            str(restored_data),
        )
        assert restored.returncode == 0, restored.stderr
        result = json.loads(restored.stdout)
        assert result["status"] == "ok"
        assert result["restored"] is True
        assert result["dataDirectory"] == str(restored_data)
        assert (restored_data / "gunther.sqlite").is_file()
        assert not (restored_data / "manifest.json").exists()
        assert not list(tmp_path.glob(".restored-data-restore-*.tmp"))
        if os.name != "nt":
            assert restored_data.stat().st_mode & 0o077 == 0
            assert (restored_data / "gunther.sqlite").stat().st_mode & 0o077 == 0

        sentinel = restored_data / "do-not-replace.txt"
        sentinel.write_text("existing data remains untouched", encoding="utf-8")
        refused = _run(
            "restore",
            str(backup),
            "--data-dir",
            str(restored_data),
        )
        assert refused.returncode == 2
        assert "already exists" in refused.stderr
        assert sentinel.read_text(encoding="utf-8") == "existing data remains untouched"
        assert not list(tmp_path.glob(".restored-data-restore-*.tmp"))
    finally:
        source_connection.close()


def test_busy_database_or_unsafe_source_never_publishes_partial_backup(
    tmp_path: Path,
) -> None:
    data_directory, source_connection = _create_data_directory(tmp_path)
    output_directory = tmp_path / "backups"
    try:
        source_connection.execute("BEGIN IMMEDIATE")
        busy = _run(
            "backup",
            "--data-dir",
            str(data_directory),
            "--output-dir",
            str(output_directory),
            "--lock-timeout",
            "0.05",
        )
        assert busy.returncode == 2
        assert "busy" in busy.stderr.lower()
        assert not list(output_directory.iterdir())
        source_connection.rollback()

        symlink = data_directory / "assets" / "unsafe-link"
        try:
            symlink.symlink_to(tmp_path / "outside-secret")
        except OSError:
            pytest.skip("This filesystem does not permit symlinks")
        unsafe = _run(
            "backup",
            "--data-dir",
            str(data_directory),
            "--output-dir",
            str(output_directory),
        )
        assert unsafe.returncode == 2
        assert "symlink" in unsafe.stderr.lower()
        assert not list(output_directory.iterdir())
    finally:
        source_connection.close()


def test_nonempty_asset_and_completed_recording_survive_full_restore(
    tmp_path: Path,
) -> None:
    """Prove DB rows and immutable bytes survive backup into a fresh workspace."""

    original_data = tmp_path / "original-data"
    restored_data = tmp_path / "restored-data"
    original_data.mkdir(mode=0o700)
    original_settings = _real_settings(original_data)
    asset_bytes = (
        b"FASTA alignment evidence -> supports -> conserved domain\n"
        b"This is the immutable source original used by the restore drill.\n"
    )
    recording_chunks = (
        b"gunther-recording-chunk-zero\x00\x01",
        b"gunther-recording-chunk-one\x02\x03",
    )
    recording_bytes = b"".join(recording_chunks)

    with TestClient(create_app(original_settings)) as client:
        knowledge_base_response = client.post(
            "/api/knowledge-bases",
            json={
                "title": "Restore Biology",
                "question": "Which evidence survives a disaster restore?",
                "description": "A real knowledge base for the non-empty restore test.",
            },
        )
        assert knowledge_base_response.status_code == 201
        knowledge_base_id = knowledge_base_response.json()["id"]

        capture_response = client.post(
            "/api/captures/assets",
            params={
                "title": "Conserved domain evidence",
                "fileName": "domain-evidence.txt",
                "kind": "paper",
                "knowledgeBaseId": knowledge_base_id,
            },
            headers={"Content-Type": "text/plain"},
            content=asset_bytes,
        )
        assert capture_response.status_code == 201
        capture = capture_response.json()
        source_id = capture["importResult"]["source"]["id"]
        asset_id = capture["asset"]["id"]
        asset_hash = hashlib.sha256(asset_bytes).hexdigest()
        assert capture["asset"]["contentHash"] == asset_hash

        knowledge_session = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/sessions",
            json={"title": "Restore evidence review"},
        ).json()
        turn = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages",
            json={"content": "What evidence supports a conserved domain?"},
        ).json()
        assert turn["assistantMessage"]["citations"]
        proposal = client.post(
            f"/api/sessions/{knowledge_session['id']}/messages/"
            f"{turn['assistantMessage']['id']}/proposal",
            json={"title": "Conserved-domain evidence"},
        ).json()
        accepted = client.patch(
            f"/api/proposals/{proposal['id']}",
            json={"status": "accepted", "reason": "Verified before backup"},
        ).json()
        unit_id = accepted["knowledgeUnitId"]
        workspace_id = client.get("/api/workspace/bootstrap").json()["workspaceId"]
        artifact_response = client.post(
            f"/api/knowledge-bases/{knowledge_base_id}/artifacts",
            headers={"X-Gunther-Workspace-Id": workspace_id},
            json={
                "clientRequestId": "restore_artifact_request_0001",
                "format": "decision_brief",
                "audience": "collaborator",
                "acceptedUnitIds": [unit_id],
            },
        )
        assert artifact_response.status_code == 201
        artifact = artifact_response.json()
        artifact_id = artifact["id"]

        started_response = client.post(
            "/api/recordings/sessions",
            params={"title": "Restorable lecture"},
            headers={"Content-Type": "audio/webm"},
        )
        assert started_response.status_code == 201
        started = started_response.json()
        recording_id = started["id"]
        recording_file_name = started["fileName"]
        for sequence, chunk in enumerate(recording_chunks):
            appended = client.put(
                f"/api/recordings/{recording_id}/chunks",
                params={"sequence": sequence},
                headers={
                    "Content-Type": "audio/webm",
                    "X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest(),
                },
                content=chunk,
            )
            assert appended.status_code == 200

        checkpointed = client.patch(
            f"/api/recordings/{recording_id}/checkpoint",
            json={
                "expectedRevision": 0,
                "transcript": "[00:00] Disaster recovery must preserve original evidence.",
                "durationSeconds": 42,
                "moments": [{"seconds": 12, "label": "Recovery invariant"}],
                "recordingContext": "lecture",
                "knowledgeBaseId": knowledge_base_id,
            },
        )
        assert checkpointed.status_code == 200
        completed = client.post(f"/api/recordings/{recording_id}/complete")
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"

    original_database = original_data / "gunther.sqlite"
    original_connection = sqlite3.connect(original_database)
    try:
        original_connection.row_factory = sqlite3.Row
        asset_row = original_connection.execute(
            "SELECT a.relative_path, a.content_hash, a.size_bytes "
            "FROM assets AS a JOIN sources AS s ON s.asset_id = a.id "
            "WHERE s.id = ? AND a.id = ?",
            (source_id, asset_id),
        ).fetchone()
        assert asset_row is not None
        asset_relative_path = str(asset_row["relative_path"])
        assert asset_row["content_hash"] == asset_hash
        assert asset_row["size_bytes"] == len(asset_bytes)
    finally:
        original_connection.close()

    original_asset = original_data / "assets" / asset_relative_path
    original_recording = original_data / "recordings" / recording_file_name
    original_payload_fingerprints = {
        "database": _fingerprint(original_database),
        "asset": _fingerprint(original_asset),
        "recording": _fingerprint(original_recording),
    }

    created = _run(
        "backup",
        "--data-dir",
        str(original_data),
        "--output-dir",
        str(tmp_path / "backups"),
    )
    assert created.returncode == 0, created.stderr
    backup_result = json.loads(created.stdout)
    backup = Path(backup_result["backup"])
    assert backup_result["created"] is True
    assert backup_result["fileCount"] == 3
    assert backup_result["formatVersion"] == 1
    assert backup_result["schemaVersion"] == LATEST_SCHEMA_VERSION
    assert backup_result["status"] == "ok"

    verified = _run("verify", str(backup))
    assert verified.returncode == 0, verified.stderr
    verify_result = json.loads(verified.stdout)
    assert verify_result["status"] == "ok"
    assert verify_result["schemaVersion"] == LATEST_SCHEMA_VERSION
    assert verify_result["fileCount"] == 3

    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    manifest_records = {record["path"]: record for record in manifest["files"]}
    assert set(manifest_records) == {
        "gunther.sqlite",
        f"assets/{asset_relative_path}",
        f"recordings/{recording_file_name}",
    }
    assert manifest_records[f"assets/{asset_relative_path}"] == {
        "path": f"assets/{asset_relative_path}",
        "sha256": asset_hash,
        "size": len(asset_bytes),
    }
    assert manifest_records[f"recordings/{recording_file_name}"] == {
        "path": f"recordings/{recording_file_name}",
        "sha256": hashlib.sha256(recording_bytes).hexdigest(),
        "size": len(recording_bytes),
    }

    restored = _run(
        "restore",
        str(backup),
        "--data-dir",
        str(restored_data),
    )
    assert restored.returncode == 0, restored.stderr
    restore_result = json.loads(restored.stdout)
    assert restore_result["status"] == "ok"
    assert restore_result["restored"] is True
    assert restore_result["schemaVersion"] == LATEST_SCHEMA_VERSION
    assert restore_result["fileCount"] == 3
    assert restored_data.is_relative_to(tmp_path)
    assert not (restored_data / "manifest.json").exists()
    assert not (restored_data / "manifest.sha256").exists()

    quick_check, foreign_key_errors = _database_integrity(
        restored_data / "gunther.sqlite"
    )
    assert quick_check == ["ok"]
    assert foreign_key_errors == []

    restored_connection = sqlite3.connect(restored_data / "gunther.sqlite")
    try:
        restored_connection.row_factory = sqlite3.Row
        assert restored_connection.execute(
            "SELECT MAX(version) FROM gunther_schema_migrations"
        ).fetchone()[0] == LATEST_SCHEMA_VERSION
        restored_asset_row = restored_connection.execute(
            "SELECT s.asset_id, a.relative_path, a.content_hash, a.size_bytes "
            "FROM sources AS s JOIN assets AS a ON a.id = s.asset_id WHERE s.id = ?",
            (source_id,),
        ).fetchone()
        assert restored_asset_row is not None
        assert dict(restored_asset_row) == {
            "asset_id": asset_id,
            "relative_path": asset_relative_path,
            "content_hash": asset_hash,
            "size_bytes": len(asset_bytes),
        }
        restored_recording_row = restored_connection.execute(
            "SELECT status, file_name, byte_size, next_sequence, transcript, "
            "duration_seconds, knowledge_base_id FROM recording_sessions WHERE id = ?",
            (recording_id,),
        ).fetchone()
        assert restored_recording_row is not None
        assert dict(restored_recording_row) == {
            "status": "completed",
            "file_name": recording_file_name,
            "byte_size": len(recording_bytes),
            "next_sequence": len(recording_chunks),
            "transcript": "[00:00] Disaster recovery must preserve original evidence.",
            "duration_seconds": 42,
            "knowledge_base_id": knowledge_base_id,
        }
        restored_chunks = restored_connection.execute(
            "SELECT sequence, checksum, size_bytes FROM recording_chunks "
            "WHERE recording_id = ? ORDER BY sequence",
            (recording_id,),
        ).fetchall()
        assert [tuple(row) for row in restored_chunks] == [
            (sequence, hashlib.sha256(chunk).hexdigest(), len(chunk))
            for sequence, chunk in enumerate(recording_chunks)
        ]
        restored_artifact_row = restored_connection.execute(
            "SELECT knowledge_base_id, workspace_id, version_number, content_hash, "
            "manifest_hash FROM artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        assert restored_artifact_row is not None
        assert dict(restored_artifact_row) == {
            "knowledge_base_id": knowledge_base_id,
            "workspace_id": workspace_id,
            "version_number": 1,
            "content_hash": artifact["contentHash"],
            "manifest_hash": artifact["manifestHash"],
        }
    finally:
        restored_connection.close()

    restored_asset = restored_data / "assets" / asset_relative_path
    restored_recording = restored_data / "recordings" / recording_file_name
    assert restored_asset.read_bytes() == asset_bytes
    assert restored_recording.read_bytes() == recording_bytes
    assert hashlib.sha256(restored_asset.read_bytes()).hexdigest() == asset_hash
    assert hashlib.sha256(restored_recording.read_bytes()).hexdigest() == hashlib.sha256(
        recording_bytes
    ).hexdigest()

    with TestClient(create_app(_real_settings(restored_data))) as restored_client:
        source = restored_client.get(f"/api/sources/{source_id}")
        assert source.status_code == 200
        assert source.json()["asset"]["id"] == asset_id
        assert source.json()["asset"]["contentHash"] == asset_hash
        downloaded_asset = restored_client.get(source.json()["asset"]["downloadUrl"])
        assert downloaded_asset.status_code == 200
        assert downloaded_asset.content == asset_bytes

        library_sources = restored_client.get(
            f"/api/knowledge-bases/{knowledge_base_id}/sources"
        )
        assert library_sources.status_code == 200
        assert [item["id"] for item in library_sources.json()] == [source_id]

        recording_metadata = restored_client.get(
            f"/api/recordings/{recording_id}/metadata"
        )
        assert recording_metadata.status_code == 200
        assert recording_metadata.json()["status"] == "completed"
        assert recording_metadata.json()["sizeBytes"] == len(recording_bytes)
        assert recording_metadata.json()["knowledgeBaseId"] == knowledge_base_id
        assert recording_metadata.json()["transcript"].startswith("[00:00]")
        downloaded_recording = restored_client.get(f"/api/recordings/{recording_id}")
        assert downloaded_recording.status_code == 200
        assert downloaded_recording.content == recording_bytes

        restored_artifact = restored_client.get(
            f"/api/knowledge-bases/{knowledge_base_id}/artifacts/{artifact_id}",
            headers={"X-Gunther-Workspace-Id": workspace_id},
        )
        assert restored_artifact.status_code == 200
        assert restored_artifact.json() == artifact

    quick_check, foreign_key_errors = _database_integrity(
        restored_data / "gunther.sqlite"
    )
    assert quick_check == ["ok"]
    assert foreign_key_errors == []
    assert original_payload_fingerprints == {
        "database": _fingerprint(original_database),
        "asset": _fingerprint(original_asset),
        "recording": _fingerprint(original_recording),
    }

    rebackup = _run(
        "backup",
        "--data-dir",
        str(restored_data),
        "--output-dir",
        str(tmp_path / "restored-backups"),
    )
    assert rebackup.returncode == 0, rebackup.stderr
    rebackup_path = Path(json.loads(rebackup.stdout)["backup"])
    reverified = _run("verify", str(rebackup_path))
    assert reverified.returncode == 0, reverified.stderr
    assert json.loads(reverified.stdout)["fileCount"] == 3

    original_asset_bytes = restored_asset.read_bytes()
    restored_asset.write_bytes(b"X" + original_asset_bytes[1:])
    rejected_asset_backup = _run(
        "backup",
        "--data-dir",
        str(restored_data),
        "--output-dir",
        str(tmp_path / "rejected-asset-backups"),
    )
    assert rejected_asset_backup.returncode == 2
    assert "SQLite integrity record" in rejected_asset_backup.stderr
    restored_asset.write_bytes(original_asset_bytes)

    original_recording_bytes = restored_recording.read_bytes()
    restored_recording.write_bytes(b"X" + original_recording_bytes[1:])
    rejected_recording_backup = _run(
        "backup",
        "--data-dir",
        str(restored_data),
        "--output-dir",
        str(tmp_path / "rejected-recording-backups"),
    )
    assert rejected_recording_backup.returncode == 2
    assert "chunk ledger" in rejected_recording_backup.stderr
    restored_recording.write_bytes(original_recording_bytes)

    tampered_artifact_backup = tmp_path / "tampered-artifact-backup"
    shutil.copytree(rebackup_path, tampered_artifact_backup)
    tampered_database = tampered_artifact_backup / "gunther.sqlite"
    tampered_connection = sqlite3.connect(tampered_database)
    try:
        content = str(
            tampered_connection.execute(
                "SELECT content FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()[0]
        )
        tampered_connection.execute(
            "UPDATE artifacts SET content = ? WHERE id = ?",
            ("X" + content[1:], artifact_id),
        )
        tampered_connection.commit()
    finally:
        tampered_connection.close()
    _reseal_manifest(tampered_artifact_backup)
    rejected_artifact_verify = _run("verify", str(tampered_artifact_backup))
    assert rejected_artifact_verify.returncode == 2
    assert "immutable provenance" in rejected_artifact_verify.stderr

    tampered_binding_backup = tmp_path / "tampered-binding-backup"
    shutil.copytree(rebackup_path, tampered_binding_backup)
    tampered_database = tampered_binding_backup / "gunther.sqlite"
    tampered_connection = sqlite3.connect(tampered_database)
    try:
        tampered_connection.execute(
            "UPDATE artifact_unit_bindings SET content_hash = ? WHERE artifact_id = ?",
            ("0" * 64, artifact_id),
        )
        tampered_connection.commit()
    finally:
        tampered_connection.close()
    _reseal_manifest(tampered_binding_backup)
    rejected_binding_verify = _run("verify", str(tampered_binding_backup))
    assert rejected_binding_verify.returncode == 2
    assert "binding differs" in rejected_binding_verify.stderr
