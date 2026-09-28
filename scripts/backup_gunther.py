"""Create, verify, and safely restore self-contained local Gunther backups.

A backup or restore is assembled in a private temporary directory and
published with one atomic rename only after its database, files, hashes, and
manifest-derived integrity records have all verified successfully.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

BACKUP_FORMAT = "gunther-local-backup"
BACKUP_FORMAT_VERSION = 1
DATABASE_NAME = "gunther.sqlite"
MANIFEST_NAME = "manifest.json"
MANIFEST_CHECKSUM_NAME = "manifest.sha256"
COPY_BUFFER_SIZE = 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BackupError(RuntimeError):
    """A backup could not be proven complete and was not published."""


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(COPY_BUFFER_SIZE):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _walk_regular_files(root: Path) -> Iterator[tuple[Path, Path]]:
    """Yield regular files without ever following a source symlink."""

    if not root.exists():
        return
    root_status = root.lstat()
    if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
        raise BackupError(f"Expected a real directory, not a symlink or special file: {root}")

    pending = [(root, Path())]
    while pending:
        directory, relative_directory = pending.pop()
        with os.scandir(directory) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            relative = relative_directory / entry.name
            status = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(status.st_mode):
                raise BackupError(f"Backup source contains a symlink: {root / relative}")
            if stat.S_ISDIR(status.st_mode):
                pending.append((root / relative, relative))
                continue
            if not stat.S_ISREG(status.st_mode):
                raise BackupError(f"Backup source contains a special file: {root / relative}")
            yield root / relative, relative


def _copy_stable_file(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for _attempt in range(3):
        digest = hashlib.sha256()
        copied_size = 0
        try:
            with source.open("rb") as source_handle:
                before = os.fstat(source_handle.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise BackupError(f"Backup source is not a regular file: {source}")
                with destination.open("wb") as destination_handle:
                    os.chmod(destination, 0o600)
                    while chunk := source_handle.read(COPY_BUFFER_SIZE):
                        destination_handle.write(chunk)
                        digest.update(chunk)
                        copied_size += len(chunk)
                    destination_handle.flush()
                    os.fsync(destination_handle.fileno())
                after = os.fstat(source_handle.fileno())
        except FileNotFoundError as error:
            raise BackupError(f"A source file disappeared during backup: {source}") from error

        unchanged = (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size == copied_size
            and before.st_mtime_ns == after.st_mtime_ns
        )
        if unchanged:
            return {"size": copied_size, "sha256": digest.hexdigest()}
        destination.unlink(missing_ok=True)
    raise BackupError(f"A source file kept changing during backup; stop Gunther first: {source}")


def _copy_data_tree(source: Path, destination: Path, prefix: str) -> list[dict[str, Any]]:
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    records: list[dict[str, Any]] = []
    for source_file, relative in _walk_regular_files(source):
        destination_file = destination / relative
        copied = _copy_stable_file(source_file, destination_file)
        records.append(
            {
                "path": (PurePosixPath(prefix) / PurePosixPath(relative.as_posix())).as_posix(),
                **copied,
            }
        )
    return records


def _read_database_metadata(database: Path) -> dict[str, Any]:
    uri = f"{database.resolve().as_uri()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as error:
        raise BackupError(f"Could not open the backup database read-only: {error}") from error
    try:
        connection.execute("PRAGMA query_only=ON")
        quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
        if quick_check != ["ok"]:
            raise BackupError(f"SQLite quick_check failed: {quick_check}")
        foreign_key_errors = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
        if foreign_key_errors:
            raise BackupError(
                f"SQLite foreign_key_check found {len(foreign_key_errors)} violation(s)"
            )
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        migration_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='gunther_schema_migrations'"
        ).fetchone()
        migrations: list[dict[str, Any]] = []
        if migration_table:
            migrations = [
                {
                    "version": int(version),
                    "name": str(name),
                    "appliedAt": str(applied_at),
                }
                for version, name, applied_at in connection.execute(
                    "SELECT version, name, applied_at FROM gunther_schema_migrations "
                    "ORDER BY version"
                )
            ]
        return {
            "sqliteVersion": sqlite3.sqlite_version,
            "userVersion": user_version,
            "guntherSchemaVersion": migrations[-1]["version"] if migrations else 0,
            "migrations": migrations,
            "quickCheck": "ok",
            "foreignKeyViolations": 0,
        }
    except sqlite3.Error as error:
        raise BackupError(f"Backup database verification failed: {error}") from error
    finally:
        connection.close()


def _verify_database_payload_integrity(
    database: Path,
    files: dict[str, Path],
) -> None:
    """Cross-check managed bytes and immutable rows instead of trusting the manifest."""

    uri = f"{database.resolve().as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        if "assets" in tables:
            for asset in connection.execute(
                "SELECT id, relative_path, content_hash, size_bytes FROM assets"
            ):
                relative_path = _validate_manifest_path(
                    f"assets/{asset['relative_path']!s}"
                )
                path = files.get(relative_path)
                if path is None:
                    raise BackupError(f"Asset {asset['id']} is missing from the backup")
                size, checksum = _sha256_file(path)
                if size != int(asset["size_bytes"]) or checksum != asset["content_hash"]:
                    raise BackupError(
                        f"Asset {asset['id']} differs from its SQLite integrity record"
                    )

        if {"recording_sessions", "recording_chunks"}.issubset(tables):
            for recording in connection.execute(
                "SELECT id, status, file_name, byte_size, next_sequence "
                "FROM recording_sessions"
            ):
                status = str(recording["status"])
                if status not in {"capturing", "completed", "failed"}:
                    continue
                file_name = str(recording["file_name"])
                if Path(file_name).name != file_name:
                    raise BackupError(
                        f"Recording {recording['id']} contains an unsafe file name"
                    )
                if status == "failed":
                    candidates = [
                        relative
                        for relative in (
                            f"recordings/{file_name}",
                            f"recordings/{file_name}.part",
                        )
                        if relative in files
                    ]
                    if not candidates:
                        continue
                    if len(candidates) != 1:
                        raise BackupError(
                            f"Recording {recording['id']} has ambiguous failed-session bytes"
                        )
                    relative_path = candidates[0]
                else:
                    relative_path = (
                        f"recordings/{file_name}.part"
                        if status == "capturing"
                        else f"recordings/{file_name}"
                    )
                path = files.get(relative_path)
                if path is None:
                    raise BackupError(
                        f"Recording {recording['id']} is missing from the backup"
                    )
                chunks = connection.execute(
                    "SELECT sequence, checksum, size_bytes FROM recording_chunks "
                    "WHERE recording_id = ? ORDER BY sequence",
                    (recording["id"],),
                ).fetchall()
                if (
                    len(chunks) != int(recording["next_sequence"])
                    or any(int(chunk["sequence"]) != index for index, chunk in enumerate(chunks))
                    or sum(int(chunk["size_bytes"]) for chunk in chunks)
                    != int(recording["byte_size"])
                    or path.stat().st_size != int(recording["byte_size"])
                ):
                    raise BackupError(
                        f"Recording {recording['id']} ledger shape is inconsistent"
                    )
                with path.open("rb") as source:
                    for chunk in chunks:
                        remaining = int(chunk["size_bytes"])
                        digest = hashlib.sha256()
                        while remaining:
                            block = source.read(min(COPY_BUFFER_SIZE, remaining))
                            if not block:
                                raise BackupError(
                                    f"Recording {recording['id']} ended inside a chunk"
                                )
                            digest.update(block)
                            remaining -= len(block)
                        if digest.hexdigest() != chunk["checksum"]:
                            raise BackupError(
                                f"Recording {recording['id']} differs from its chunk ledger"
                            )
                    if source.read(1):
                        raise BackupError(
                            f"Recording {recording['id']} contains uncommitted trailing bytes"
                        )

        if {"artifacts", "artifact_unit_bindings"}.issubset(tables):
            for artifact in connection.execute("SELECT * FROM artifacts"):
                try:
                    accepted_unit_ids = json.loads(artifact["accepted_unit_ids_json"])
                    snapshots = json.loads(artifact["revision_snapshot_json"])
                    provenance = json.loads(artifact["provenance_json"])
                except (TypeError, json.JSONDecodeError) as error:
                    raise BackupError(
                        f"Artifact {artifact['id']} contains invalid JSON"
                    ) from error
                if not isinstance(accepted_unit_ids, list) or not isinstance(snapshots, list):
                    raise BackupError(f"Artifact {artifact['id']} snapshot is invalid")
                content_hash = hashlib.sha256(
                    str(artifact["content"]).encode("utf-8")
                ).hexdigest()
                snapshot_unit_ids: list[object] = []
                revision_ids: list[object] = []
                snapshot_by_position: list[tuple[object, object, str]] = []
                for snapshot in snapshots:
                    if not isinstance(snapshot, dict):
                        raise BackupError(f"Artifact {artifact['id']} snapshot is invalid")
                    snapshot_hash = hashlib.sha256(
                        str(snapshot.get("content", "")).encode("utf-8")
                    ).hexdigest()
                    if snapshot_hash != snapshot.get("content_hash"):
                        raise BackupError(
                            f"Artifact {artifact['id']} snapshot content hash differs"
                        )
                    snapshot_unit_ids.append(snapshot.get("unit_id"))
                    revision_ids.append(snapshot.get("revision_id"))
                    snapshot_by_position.append(
                        (snapshot.get("unit_id"), snapshot.get("revision_id"), snapshot_hash)
                    )
                if (
                    content_hash != artifact["content_hash"]
                    or snapshot_unit_ids != accepted_unit_ids
                    or not isinstance(provenance, dict)
                    or provenance.get("workspace_id") != artifact["workspace_id"]
                    or provenance.get("knowledge_base_id") != artifact["knowledge_base_id"]
                    or provenance.get("accepted_only") is not True
                    or provenance.get("accepted_unit_ids") != accepted_unit_ids
                    or provenance.get("revision_ids") != revision_ids
                ):
                    raise BackupError(
                        f"Artifact {artifact['id']} differs from its immutable provenance"
                    )
                bindings = connection.execute(
                    "SELECT position, unit_id, revision_id, content_hash "
                    "FROM artifact_unit_bindings WHERE artifact_id = ? ORDER BY position",
                    (artifact["id"],),
                ).fetchall()
                if len(bindings) != len(snapshot_by_position):
                    raise BackupError(f"Artifact {artifact['id']} binding count differs")
                for position, (binding, expected) in enumerate(
                    zip(bindings, snapshot_by_position, strict=True)
                ):
                    unit_id, revision_id, snapshot_hash = expected
                    if (
                        int(binding["position"]) != position
                        or binding["unit_id"] != unit_id
                        or binding["revision_id"] != revision_id
                        or binding["content_hash"] != snapshot_hash
                    ):
                        raise BackupError(
                            f"Artifact {artifact['id']} binding differs from its snapshot"
                        )
                    revision = connection.execute(
                        "SELECT unit_id, revision_number, content "
                        "FROM knowledge_unit_revisions WHERE id = ?",
                        (revision_id,),
                    ).fetchone()
                    snapshot = snapshots[position]
                    if (
                        revision is None
                        or revision["unit_id"] != unit_id
                        or int(revision["revision_number"])
                        != int(snapshot.get("revision_number", -1))
                        or hashlib.sha256(
                            str(revision["content"]).encode("utf-8")
                        ).hexdigest()
                        != snapshot_hash
                    ):
                        raise BackupError(
                            f"Artifact {artifact['id']} pinned revision differs"
                        )
                manifest_hash = hashlib.sha256(
                    json.dumps(
                        {
                            "format": artifact["format"],
                            "audience": artifact["audience"],
                            "title": artifact["title"],
                            "contentHash": artifact["content_hash"],
                            "acceptedUnitIds": accepted_unit_ids,
                            "revisionSnapshot": snapshots,
                            "provenance": provenance,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if manifest_hash != artifact["manifest_hash"]:
                    raise BackupError(
                        f"Artifact {artifact['id']} manifest hash differs"
                    )
    except (TypeError, ValueError, OverflowError) as error:
        raise BackupError(
            f"Managed payload contains invalid integrity metadata: {error}"
        ) from error
    except sqlite3.Error as error:
        raise BackupError(f"Managed payload verification failed: {error}") from error
    finally:
        connection.close()


def _backup_database(
    source_connection: sqlite3.Connection,
    destination: Path,
) -> dict[str, Any]:
    try:
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection, pages=256, sleep=0.05)
        finally:
            destination_connection.close()
    except sqlite3.Error as error:
        raise BackupError(f"SQLite backup API failed: {error}") from error
    os.chmod(destination, 0o600)
    _fsync_file(destination)
    size, checksum = _sha256_file(destination)
    return {"path": DATABASE_NAME, "size": size, "sha256": checksum}


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _write_manifest(backup_directory: Path, manifest: dict[str, Any]) -> None:
    manifest_path = backup_directory / MANIFEST_NAME
    manifest_data = _manifest_bytes(manifest)
    with manifest_path.open("xb") as manifest_file:
        os.chmod(manifest_path, 0o600)
        manifest_file.write(manifest_data)
        manifest_file.flush()
        os.fsync(manifest_file.fileno())
    checksum = hashlib.sha256(manifest_data).hexdigest()
    checksum_path = backup_directory / MANIFEST_CHECKSUM_NAME
    with checksum_path.open("x", encoding="ascii", newline="\n") as checksum_file:
        os.chmod(checksum_path, 0o600)
        checksum_file.write(f"{checksum}  {MANIFEST_NAME}\n")
        checksum_file.flush()
        os.fsync(checksum_file.fileno())


def _validate_manifest_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise BackupError("Manifest contains an invalid file path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise BackupError(f"Manifest contains an unsafe file path: {value!r}")
    allowed = value == DATABASE_NAME or value.startswith(("assets/", "recordings/"))
    if not allowed:
        raise BackupError(f"Manifest contains an unexpected file path: {value!r}")
    return value


def verify_backup(backup_directory: Path) -> dict[str, Any]:
    """Verify a backup without creating or changing anything inside it."""

    requested_directory = backup_directory.expanduser()
    if requested_directory.is_symlink():
        raise BackupError(f"Backup directory must not be a symlink: {requested_directory}")
    backup_directory = requested_directory.resolve()
    if not backup_directory.is_dir():
        raise BackupError(f"Backup directory does not exist or is unsafe: {backup_directory}")
    manifest_path = backup_directory / MANIFEST_NAME
    checksum_path = backup_directory / MANIFEST_CHECKSUM_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise BackupError(f"Missing safe {MANIFEST_NAME}")
    if not checksum_path.is_file() or checksum_path.is_symlink():
        raise BackupError(f"Missing safe {MANIFEST_CHECKSUM_NAME}")

    manifest_data = manifest_path.read_bytes()
    checksum_text = checksum_path.read_text(encoding="ascii")
    expected_manifest_checksum = hashlib.sha256(manifest_data).hexdigest()
    if checksum_text != f"{expected_manifest_checksum}  {MANIFEST_NAME}\n":
        raise BackupError("Manifest checksum does not match")
    try:
        manifest = json.loads(manifest_data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackupError(f"Manifest is not valid UTF-8 JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise BackupError("Manifest root must be an object")
    if manifest.get("format") != BACKUP_FORMAT:
        raise BackupError("This is not a Gunther local backup")
    if manifest.get("formatVersion") != BACKUP_FORMAT_VERSION:
        raise BackupError(f"Unsupported backup format version: {manifest.get('formatVersion')!r}")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise BackupError("Manifest files must be a list")

    expected: dict[str, dict[str, Any]] = {}
    for record in files:
        if not isinstance(record, dict):
            raise BackupError("Manifest contains a malformed file record")
        relative = _validate_manifest_path(record.get("path"))
        if relative in expected:
            raise BackupError(f"Manifest contains a duplicate file path: {relative}")
        if not isinstance(record.get("size"), int) or record["size"] < 0:
            raise BackupError(f"Manifest contains an invalid size for {relative}")
        checksum = record.get("sha256")
        if (
            not isinstance(checksum, str)
            or len(checksum) != 64
            or any(character not in "0123456789abcdef" for character in checksum)
        ):
            raise BackupError(f"Manifest contains an invalid SHA-256 for {relative}")
        expected[relative] = record

    actual: dict[str, Path] = {}
    for path, relative_path in _walk_regular_files(backup_directory):
        relative = relative_path.as_posix()
        if relative in {MANIFEST_NAME, MANIFEST_CHECKSUM_NAME}:
            continue
        actual[relative] = path
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise BackupError(f"Backup file set differs from manifest; missing={missing}, extra={extra}")

    total_size = 0
    for relative, record in expected.items():
        size, checksum = _sha256_file(actual[relative])
        if size != record["size"]:
            raise BackupError(f"File size differs from manifest: {relative}")
        if checksum != record["sha256"]:
            raise BackupError(f"File SHA-256 differs from manifest: {relative}")
        total_size += size
    if manifest.get("fileCount") != len(expected) or manifest.get("totalSize") != total_size:
        raise BackupError("Manifest totals do not match its file records")
    if DATABASE_NAME not in actual:
        raise BackupError(f"Backup is missing {DATABASE_NAME}")

    database_metadata = _read_database_metadata(actual[DATABASE_NAME])
    _verify_database_payload_integrity(actual[DATABASE_NAME], actual)
    recorded_database = manifest.get("database")
    if not isinstance(recorded_database, dict):
        raise BackupError("Manifest database metadata must be an object")
    stable_database_fields = {
        "userVersion",
        "guntherSchemaVersion",
        "migrations",
        "quickCheck",
        "foreignKeyViolations",
    }
    if any(
        recorded_database.get(field) != database_metadata.get(field)
        for field in stable_database_fields
    ):
        raise BackupError("Database schema metadata differs from the manifest")
    return {
        "status": "ok",
        "backup": str(backup_directory),
        "formatVersion": BACKUP_FORMAT_VERSION,
        "schemaVersion": database_metadata["guntherSchemaVersion"],
        "fileCount": len(expected),
        "totalSize": total_size,
    }


def _originals_directories(
    data_directory: Path, library_root: Path | None
) -> tuple[Path, Path]:
    """Where assets/ and recordings/ are: the data directory, or a library root's store."""

    if library_root is None:
        return data_directory / "assets", data_directory / "recordings"
    store = library_root.expanduser().resolve() / ".gunther"
    if not (store / "root.json").is_file() or store.is_symlink():
        raise BackupError(f"{library_root} is not a Gunther library root")
    return store / "assets", store / "recordings"


def create_backup(
    data_directory: Path,
    output_directory: Path,
    *,
    lock_timeout: float = 2.0,
    library_root: Path | None = None,
) -> Path:
    requested_data_directory = data_directory.expanduser()
    requested_output_directory = output_directory.expanduser()
    if requested_data_directory.is_symlink():
        raise BackupError("Gunther data directory must not be a symlink")
    if requested_output_directory.is_symlink():
        raise BackupError("Backup output directory must not be a symlink")
    data_directory = requested_data_directory.resolve()
    output_directory = requested_output_directory.resolve()
    database = data_directory / DATABASE_NAME
    if lock_timeout <= 0:
        raise BackupError("Lock timeout must be greater than zero")
    if not data_directory.is_dir():
        raise BackupError(f"Data directory does not exist or is unsafe: {data_directory}")
    if not database.is_file() or database.is_symlink():
        raise BackupError(f"Expected a regular SQLite database at {database}")
    if output_directory == data_directory or output_directory.is_relative_to(data_directory):
        raise BackupError("Backup output must be outside the active Gunther data directory")
    assets_directory, recordings_directory = _originals_directories(data_directory, library_root)
    if library_root is not None and output_directory.is_relative_to(
        library_root.expanduser().resolve()
    ):
        raise BackupError("Backup output must be outside the library root")
    output_directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_name = f"gunther-backup-{timestamp}-{uuid4().hex[:8]}"
    final_directory = output_directory / backup_name
    temporary_directory = Path(
        tempfile.mkdtemp(prefix=f".{backup_name}-", suffix=".tmp", dir=output_directory)
    )
    os.chmod(temporary_directory, 0o700)
    source_connection: sqlite3.Connection | None = None
    exclusive_held = False
    try:
        source_connection = sqlite3.connect(database, timeout=lock_timeout)
        source_connection.execute(f"PRAGMA busy_timeout={max(1, int(lock_timeout * 1000))}")
        try:
            source_connection.execute("BEGIN EXCLUSIVE")
            source_connection.rollback()
        except sqlite3.OperationalError as error:
            raise BackupError("Gunther data is busy; close the app before creating a backup") from error

        data_version_before = int(source_connection.execute("PRAGMA data_version").fetchone()[0])
        database_record = _backup_database(
            source_connection,
            temporary_directory / DATABASE_NAME,
        )
        file_records = [database_record]
        file_records.extend(
            _copy_data_tree(
                assets_directory,
                temporary_directory / "assets",
                "assets",
            )
        )
        file_records.extend(
            _copy_data_tree(
                recordings_directory,
                temporary_directory / "recordings",
                "recordings",
            )
        )

        try:
            source_connection.execute("BEGIN EXCLUSIVE")
            exclusive_held = True
        except sqlite3.OperationalError as error:
            raise BackupError(
                "Gunther data became busy during backup; no backup was published"
            ) from error
        data_version_after = int(source_connection.execute("PRAGMA data_version").fetchone()[0])
        if data_version_after != data_version_before:
            raise BackupError("Gunther data changed during backup; no backup was published")

        file_records.sort(key=lambda record: str(record["path"]))
        database_metadata = _read_database_metadata(temporary_directory / DATABASE_NAME)
        manifest = {
            "format": BACKUP_FORMAT,
            "formatVersion": BACKUP_FORMAT_VERSION,
            "backupId": backup_name,
            "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "database": database_metadata,
            "fileCount": len(file_records),
            "totalSize": sum(int(record["size"]) for record in file_records),
            "files": file_records,
        }
        _write_manifest(temporary_directory, manifest)
        _fsync_directory(temporary_directory / "assets")
        _fsync_directory(temporary_directory / "recordings")
        _fsync_directory(temporary_directory)
        verify_backup(temporary_directory)
        if final_directory.exists():
            raise BackupError(f"Refusing to replace an existing backup: {final_directory}")
        os.replace(temporary_directory, final_directory)
        _fsync_directory(output_directory)
        return final_directory
    except sqlite3.Error as error:
        raise BackupError(f"SQLite backup failed: {error}") from error
    finally:
        if source_connection is not None:
            if exclusive_held:
                source_connection.rollback()
            source_connection.close()
        if temporary_directory.exists():
            shutil.rmtree(temporary_directory)


def restore_backup(backup_directory: Path, data_directory: Path) -> dict[str, Any]:
    """Restore a verified backup into a new data directory without overwriting.

    Restoring into an absent directory makes the operation recoverable: an
    existing Gunther data directory is never deleted, renamed, or replaced by
    this command. Operators can launch Gunther against the returned path and
    only retire the old directory after application-level checks succeed.
    """

    verified = verify_backup(backup_directory)
    requested_backup = backup_directory.expanduser()
    requested_data_directory = data_directory.expanduser()
    if requested_data_directory.is_symlink():
        raise BackupError("Restore destination must not be a symlink")
    if requested_data_directory.exists():
        raise BackupError(
            "Restore destination already exists; choose a new directory so no data is overwritten"
        )
    requested_parent = requested_data_directory.parent
    if requested_parent.is_symlink():
        raise BackupError("Restore destination parent must not be a symlink")
    requested_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent = requested_parent.resolve()
    destination = parent / requested_data_directory.name
    backup = requested_backup.resolve()
    if destination == backup or destination.is_relative_to(backup):
        raise BackupError("Restore destination must be outside the backup directory")

    manifest = json.loads((backup / MANIFEST_NAME).read_bytes())
    records = manifest["files"]
    temporary_directory = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}-restore-",
            suffix=".tmp",
            dir=parent,
        )
    )
    os.chmod(temporary_directory, 0o700)
    try:
        restored_files: dict[str, Path] = {}
        for record in records:
            relative = _validate_manifest_path(record["path"])
            source = backup / PurePosixPath(relative)
            target = temporary_directory / PurePosixPath(relative)
            copied = _copy_stable_file(source, target)
            if copied["size"] != record["size"] or copied["sha256"] != record["sha256"]:
                raise BackupError(f"Restored file differs from the verified backup: {relative}")
            restored_files[relative] = target

        for directory_name in ("assets", "recordings"):
            directory = temporary_directory / directory_name
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)

        database = restored_files.get(DATABASE_NAME)
        if database is None:
            raise BackupError(f"Backup is missing {DATABASE_NAME}")
        database_metadata = _read_database_metadata(database)
        _verify_database_payload_integrity(database, restored_files)
        if database_metadata["guntherSchemaVersion"] != verified["schemaVersion"]:
            raise BackupError("Restored database schema differs from the verified backup")

        _fsync_directory(temporary_directory / "assets")
        _fsync_directory(temporary_directory / "recordings")
        _fsync_directory(temporary_directory)
        if destination.exists():
            raise BackupError("Restore destination appeared during restore; no data was replaced")
        os.replace(temporary_directory, destination)
        _fsync_directory(parent)
        return {
            "status": "ok",
            "restored": True,
            "backup": str(backup),
            "dataDirectory": str(destination),
            "schemaVersion": verified["schemaVersion"],
            "fileCount": verified["fileCount"],
            "totalSize": verified["totalSize"],
        }
    finally:
        if temporary_directory.exists():
            shutil.rmtree(temporary_directory)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, verify, or safely restore a local Gunther backup."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup", help="Create an atomic, verified backup")
    backup.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("GUNTHER_DATA_DIR", PROJECT_ROOT / "data")),
        help="Directory containing gunther.sqlite, assets/, and recordings/",
    )
    backup.add_argument(
        "--library-root",
        type=Path,
        default=Path(os.environ["LIBRARY_ROOT"]) if os.environ.get("LIBRARY_ROOT") else None,
        help="Library root whose .gunther/ holds the originals (default: LIBRARY_ROOT)",
    )
    backup.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "backups",
        help="Parent directory that will receive the completed backup",
    )
    backup.add_argument(
        "--lock-timeout",
        type=float,
        default=2.0,
        help=argparse.SUPPRESS,
    )
    verify = subparsers.add_parser("verify", help="Read and verify a completed backup")
    verify.add_argument("backup", type=Path, help="Completed backup directory")
    restore = subparsers.add_parser(
        "restore",
        help="Restore a verified backup into a new, absent data directory",
    )
    restore.add_argument("backup", type=Path, help="Completed backup directory")
    restore.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="New destination directory; it must not already exist",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "backup":
            result = create_backup(
                arguments.data_dir,
                arguments.output_dir,
                lock_timeout=arguments.lock_timeout,
                library_root=arguments.library_root,
            )
            payload = verify_backup(result)
            payload["created"] = True
        elif arguments.command == "verify":
            payload = verify_backup(arguments.backup)
        else:
            payload = restore_backup(arguments.backup, arguments.data_dir)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except BackupError as error:
        print(f"Backup error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
