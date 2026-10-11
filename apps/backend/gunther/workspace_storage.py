"""Keep a desktop workspace together; migrate and recover without replacing its identity."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from gunther import vector_index


class WorkspaceStorageError(RuntimeError):
    pass


def _regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise WorkspaceStorageError(f"Workspace storage is not a regular file: {path}")


def _connect(path: Path) -> sqlite3.Connection:
    _regular(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    vector_index.load_extension(connection)
    return connection


def database_identity(path: Path) -> str | None:
    with closing(_connect(path)) as connection:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise WorkspaceStorageError(
                f"The workspace database failed its integrity check: {path}"
            )
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        if "workspace_identity" not in tables:
            return None  # A database from before workspace identities existed.
        identities = connection.execute("SELECT workspace_id FROM workspace_identity").fetchall()
        if len(identities) != 1:
            raise WorkspaceStorageError(f"The workspace database has an invalid identity: {path}")
        return identities[0][0]


def snapshot_database(source: Path, target: Path) -> None:
    """Publish a checked SQLite snapshot, including committed WAL transactions."""
    if target.exists():
        raise WorkspaceStorageError(f"Refusing to replace an existing database: {target}")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.parent.is_symlink() or not target.parent.is_dir():
        raise WorkspaceStorageError(f"Invalid database directory: {target.parent}")
    staging = target.with_name(f".{target.name}.incoming")
    if staging.exists():
        _regular(staging)
        staging.unlink()
    try:
        original = _connect(source)
        copied = sqlite3.connect(staging)
        try:
            original.backup(copied)
        finally:
            copied.close()
            original.close()
        database_identity(staging)
        os.chmod(staging, 0o600)
        with staging.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)


def _copy_missing_tree(source: Path, target: Path, *, check_existing: bool = True) -> None:
    """Copy legacy persistent files, preserving every existing target and old copy."""
    if not source.exists():
        return
    if source.is_symlink():
        raise WorkspaceStorageError(f"Refusing a symlink in legacy workspace storage: {source}")
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.is_symlink() or not target.is_dir():
            raise WorkspaceStorageError(f"Invalid workspace directory: {target}")
        for child in source.iterdir():
            _copy_missing_tree(child, target / child.name, check_existing=check_existing)
    else:
        _regular(source)
        if not target.exists():
            # Exclusive creation also makes interrupted migration safe to retry.
            staging = target.with_name(f".{target.name}.migration")
            if staging.exists() or staging.is_symlink():
                _regular(staging)
                staging.unlink()
            with source.open("rb") as original, staging.open("wb") as copied:
                shutil.copyfileobj(original, copied)
                copied.flush()
                os.fsync(copied.fileno())
            os.chmod(staging, 0o600)
            os.replace(staging, target)
        elif target.is_symlink() or not target.is_file():
            raise WorkspaceStorageError(f"Invalid workspace file: {target}")
        elif check_existing:
            with source.open("rb") as original, target.open("rb") as existing:
                if (
                    hashlib.file_digest(original, "sha256").digest()
                    != hashlib.file_digest(existing, "sha256").digest()
                ):
                    raise WorkspaceStorageError(
                        "Legacy storage conflicts with an existing file; "
                        f"both copies remain: {target}"
                    )


def prepare_workspace_storage(
    data_dir: Path, config_dir: Path, library_root: Path, legacy_dir: Path | None = None
) -> None:
    """Called with the desktop workspace lock held, before SQLite can create a file."""
    if legacy_dir is not None and legacy_dir != data_dir and legacy_dir.is_dir():
        from gunther.desktop_server import _acquire_instance_lock

        with _acquire_instance_lock(legacy_dir):
            _prepare_workspace_storage(data_dir, config_dir, library_root, legacy_dir)
    else:
        _prepare_workspace_storage(data_dir, config_dir, library_root, legacy_dir)


def _prepare_workspace_storage(
    data_dir: Path, config_dir: Path, library_root: Path, legacy_dir: Path | None
) -> None:
    if data_dir != library_root / ".gunther":
        raise WorkspaceStorageError("Desktop data must live inside the chosen library folder")
    identity_file = data_dir / "root.json"
    expected = None
    if identity_file.exists():
        _regular(identity_file)
        identity = json.loads(identity_file.read_text(encoding="utf-8"))
        expected = identity.get("workspaceId")
        if not isinstance(expected, str) or not expected:
            raise WorkspaceStorageError("The library folder has an unreadable workspace identity")

    database = data_dir / "gunther.sqlite"
    migration_marker = data_dir / "storage-migration.json"
    if not database.exists():
        candidates: list[Path] = []
        backups = data_dir / "backups"
        if backups.is_dir() and not backups.is_symlink():
            candidates.extend(sorted(backups.glob("*.sqlite"), reverse=True))
        # Workspace snapshots include work saved since migration. The retained
        # legacy database is only an initial migration source, never a recovery
        # source after that migration completed.
        if (
            legacy_dir is not None
            and not migration_marker.exists()
            and (legacy_dir / "gunther.sqlite").exists()
        ):
            candidates.append(legacy_dir / "gunther.sqlite")
        for candidate in candidates:
            try:
                found = database_identity(candidate)
            except (WorkspaceStorageError, sqlite3.Error):
                continue
            if expected is not None and found != expected:
                continue
            snapshot_database(candidate, database)
            break
        if not database.exists() and (
            expected is not None
            or any(
                (library_root / name).is_dir() and any((library_root / name).iterdir())
                for name in ("Inbox", "Libraries", "Trash")
            )
        ):
            raise WorkspaceStorageError(
                f"Your library files are still in {library_root}, but their database is missing. "
                "Restore a database backup for this library. Your files have been left in place."
            )
        if not database.exists() and candidates:
            raise WorkspaceStorageError("The existing database could not be migrated or recovered")

    if database.exists():
        actual = database_identity(database)
        if expected is not None and actual != expected:
            raise WorkspaceStorageError(
                "The database and library folder do not belong to the same workspace. "
                "Restore the matching database backup; your existing files have been left in place."
            )

    if (
        legacy_dir is not None
        and legacy_dir != data_dir
        and database.exists()
        and not (migration_marker.exists())
    ):
        legacy_database = legacy_dir / "gunther.sqlite"
        if legacy_database.exists() and database_identity(legacy_database) == database_identity(
            database
        ):
            for name in ("assets", "recordings", "mobile-gateway-pki", "speech"):
                _copy_missing_tree(legacy_dir / name, data_dir / name)
            for name in ("service-settings.json", ".env"):
                _copy_missing_tree(legacy_dir / name, config_dir / name, check_existing=False)
            from gunther.library_root import _write_json

            _write_json(migration_marker, {"legacyDirectory": str(legacy_dir)})

    if database.exists():
        from gunther.migrations import LATEST_SCHEMA_VERSION, MIGRATION_TABLE

        with closing(_connect(database)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
            version = (
                connection.execute(
                    f"SELECT COALESCE(MAX(version), 0) FROM {MIGRATION_TABLE}"
                ).fetchone()[0]
                if MIGRATION_TABLE in tables
                else 0
            )
        if version < LATEST_SCHEMA_VERSION:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            snapshot_database(database, data_dir / "backups" / f"{stamp}-v{version}.sqlite")


def backup_workspace_database(data_dir: Path, *, interval_seconds: float = 900) -> None:
    """Keep bounded database snapshots beside the originals, at startup and after changes.

    These recover a missing database while its original files remain. A full external
    backup is still needed to recover a lost disk or deleted workspace folder.
    """
    backups = data_dir / "backups"
    previous = sorted(backups.glob("*-auto.sqlite")) if backups.is_dir() else []
    if previous and time.time() - previous[-1].stat().st_mtime < interval_seconds:
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_database(data_dir / "gunther.sqlite", backups / f"{stamp}-auto.sqlite")
    # Never prune upgrade snapshots, user backups or originals.
    for old in sorted(backups.glob("*-auto.sqlite"))[:-12]:
        _regular(old)
        old.unlink()
