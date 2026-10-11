import json
import sqlite3
from pathlib import Path

import pytest

from gunther.desktop_server import _acquire_instance_lock, _desktop_settings
from gunther.workspace_storage import (
    WorkspaceStorageError,
    backup_workspace_database,
    database_identity,
    prepare_workspace_storage,
)


def layout(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "home/Gunther"
    data = root / ".gunther"
    config = tmp_path / "home/.config/gunther"
    old = tmp_path / "Application Support"
    for path in (data, config, old):
        path.mkdir(parents=True)
    return root, data, config, old


def old_database(path: Path, workspace: str = "wsp_old") -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE workspace_identity(workspace_id TEXT)")
    connection.execute("INSERT INTO workspace_identity VALUES (?)", (workspace,))
    connection.execute("CREATE TABLE saved_work(kind TEXT, content TEXT)")
    connection.executemany(
        "INSERT INTO saved_work VALUES (?, ?)",
        [
            ("conversation", "Keep this answer"),
            ("report", "Keep this output"),
        ],
    )
    connection.commit()
    return connection


def test_migrates_committed_wal_and_files_preserving_identity_and_old_copy(tmp_path: Path) -> None:
    root, data, config, old = layout(tmp_path)
    (data / "root.json").write_text(json.dumps({"workspaceId": "wsp_old"}))
    connection = old_database(old / "gunther.sqlite")
    (old / "assets").mkdir()
    (old / "assets/document.pdf").write_bytes(b"original bytes")
    (old / "service-settings.json").write_text('{"model":"previous"}')
    try:
        prepare_workspace_storage(data, config, root, old)
        assert database_identity(data / "gunther.sqlite") == "wsp_old"
        with sqlite3.connect(data / "gunther.sqlite") as migrated:
            assert migrated.execute("SELECT * FROM saved_work").fetchall() == [
                ("conversation", "Keep this answer"),
                ("report", "Keep this output"),
            ]
        assert (data / "assets/document.pdf").read_bytes() == b"original bytes"
        assert (old / "assets/document.pdf").read_bytes() == b"original bytes"
        assert (old / "gunther.sqlite").exists()
        assert (config / "service-settings.json").read_text() == '{"model":"previous"}'
        (config / "service-settings.json").write_text('{"model":"new choice"}')
        prepare_workspace_storage(data, config, root, old)
        assert (config / "service-settings.json").read_text() == '{"model":"new choice"}'
    finally:
        connection.close()


def test_missing_database_recovers_only_a_matching_valid_backup(tmp_path: Path) -> None:
    root, data, config, old = layout(tmp_path)
    (data / "root.json").write_text(json.dumps({"workspaceId": "wsp_old"}))
    backups = data / "backups"
    backups.mkdir()
    old_database(backups / "20260101-v1.sqlite").close()
    old_database(backups / "20260102-v1.sqlite", "wsp_other").close()
    (backups / "20260103-v1.sqlite").write_bytes(b"broken database")
    prepare_workspace_storage(data, config, root, old)
    assert database_identity(data / "gunther.sqlite") == "wsp_old"


@pytest.mark.parametrize("migration_complete", [False, True])
def test_recovery_prefers_workspace_backups_over_the_retained_legacy_database(
    tmp_path: Path, migration_complete: bool
) -> None:
    root, data, config, old = layout(tmp_path)
    (data / "root.json").write_text('{"workspaceId":"wsp_old"}')
    old_database(old / "gunther.sqlite").close()
    if migration_complete:
        (data / "storage-migration.json").write_text("{}")
    backups = data / "backups"
    backups.mkdir()
    old_database(backups / "20260101-auto.sqlite").close()
    newest = old_database(backups / "20260102-auto.sqlite")
    newest.execute("INSERT INTO saved_work VALUES ('conversation', 'New work after migration')")
    newest.commit()
    newest.close()

    prepare_workspace_storage(data, config, root, old)

    with sqlite3.connect(data / "gunther.sqlite") as restored:
        assert restored.execute(
            "SELECT content FROM saved_work WHERE content = 'New work after migration'"
        ).fetchall() == [("New work after migration",)]
    with sqlite3.connect(old / "gunther.sqlite") as legacy:
        assert legacy.execute("SELECT COUNT(*) FROM saved_work").fetchone()[0] == 2


def test_completed_migration_cannot_restore_the_stale_legacy_database(tmp_path: Path) -> None:
    root, data, config, old = layout(tmp_path)
    (data / "root.json").write_text('{"workspaceId":"wsp_old"}')
    (data / "storage-migration.json").write_text("{}")
    old_database(old / "gunther.sqlite").close()

    with pytest.raises(WorkspaceStorageError, match="database is missing"):
        prepare_workspace_storage(data, config, root, old)

    assert not (data / "gunther.sqlite").exists()
    assert database_identity(old / "gunther.sqlite") == "wsp_old"


def test_lost_database_leaves_library_and_identity_untouched(tmp_path: Path) -> None:
    root, data, config, old = layout(tmp_path)
    identity = '{"workspaceId":"wsp_old"}'
    (data / "root.json").write_text(identity)
    (root / "Libraries").mkdir()
    (root / "Libraries/Keep.md").write_text("Old work")
    old_database(old / "gunther.sqlite", "wsp_other").close()
    with pytest.raises(WorkspaceStorageError, match="database is missing"):
        prepare_workspace_storage(data, config, root, old)
    assert not (data / "gunther.sqlite").exists()
    assert (data / "root.json").read_text() == identity
    assert (root / "Libraries/Keep.md").read_text() == "Old work"


def test_a_first_install_is_allowed_but_migration_from_a_running_app_is_refused(
    tmp_path: Path,
) -> None:
    root, data, config, old = layout(tmp_path)
    prepare_workspace_storage(data, config, root)
    assert not (data / "gunther.sqlite").exists()  # create_app owns initial creation.
    old_database(old / "gunther.sqlite").close()
    with _acquire_instance_lock(old), pytest.raises(RuntimeError, match="already"):
        prepare_workspace_storage(data, config, root, old)
    assert not (data / "gunther.sqlite").exists()


def test_native_settings_and_database_have_separate_locations(tmp_path: Path) -> None:
    root, data, config, _old = layout(tmp_path)
    settings = _desktop_settings(data, "token", config_dir=config, library_root=root)
    assert settings.database_url == f"sqlite+pysqlite:///{data / 'gunther.sqlite'}"
    assert settings.service_settings_file == config / "service-settings.json"
    assert settings.assets_dir == data / "assets"
    assert settings.recordings_dir == data / "recordings"
    assert settings.library_root == root


def test_automatic_snapshots_are_bounded_and_survive_settings_loss(tmp_path: Path) -> None:
    root, data, config, _old = layout(tmp_path)
    old_database(data / "gunther.sqlite").close()
    (data / "root.json").write_text('{"workspaceId":"wsp_old"}')
    backup_workspace_database(data)
    backup_workspace_database(data)
    assert len(list((data / "backups").glob("*-auto.sqlite"))) == 1
    for _index in range(13):
        backup_workspace_database(data, interval_seconds=0)
    assert len(list((data / "backups").glob("*-auto.sqlite"))) == 12
    (data / "gunther.sqlite").unlink()
    config.rmdir()
    config.mkdir()
    prepare_workspace_storage(data, config, root)
    assert database_identity(data / "gunther.sqlite") == "wsp_old"
