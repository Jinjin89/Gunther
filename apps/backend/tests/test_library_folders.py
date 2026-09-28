import errno
import json
import os
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gunther.library_root as library_root_module
from gunther.config import Settings
from gunther.database import session_scope
from gunther.library_folders import safe_name
from gunther.library_root import LibraryRootConflict, move_originals, prepare_library_root
from gunther.main import create_app
from gunther.models import Asset, RecordingSession, Source

RECORDING_ID = "rec_" + "b" * 24


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_originals_move_into_the_root_once_and_interrupted_uploads_stay(tmp_path: Path) -> None:
    old_assets, old_recordings = tmp_path / "data/assets", tmp_path / "data/recordings"
    write(old_assets / "ab/abc123", b"pdf bytes")
    write(old_assets / ".incoming/half", b"partial upload")
    write(old_recordings / f"{RECORDING_ID}.webm", b"audio")
    write(old_recordings / "rec_live.webm.part", b"still recording")

    root = prepare_library_root(
        tmp_path / "Gunther",
        "wsp_1",
        previous_assets_dir=old_assets,
        previous_recordings_dir=old_recordings,
    )

    assert (root.assets_dir / "ab/abc123").read_bytes() == b"pdf bytes"
    assert (root.recordings_dir / f"{RECORDING_ID}.webm").read_bytes() == b"audio"
    assert (root.recordings_dir / "rec_live.webm.part").read_bytes() == b"still recording"
    assert not (old_assets / "ab").exists()
    assert (old_assets / ".incoming/half").exists(), "interrupted uploads stay behind"
    assert not old_recordings.exists()
    identity = json.loads((root.internal / "root.json").read_text())
    assert identity["workspaceId"] == "wsp_1"
    assert identity["movedFrom"] == [str(old_assets), str(old_recordings)]

    old_recordings.mkdir()
    again = prepare_library_root(
        tmp_path / "Gunther", "wsp_1", previous_recordings_dir=old_recordings
    )
    assert again == root
    assert not old_recordings.exists(), "an empty old folder is tidied away"


def test_a_move_across_volumes_is_verified_and_a_different_file_is_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous, target = tmp_path / "old", tmp_path / "new"
    write(previous / "aa/one", b"first")
    write(previous / "aa/two", b"second")
    write(target / "aa/two", b"different")

    def cross_device(_source: object, _destination: object) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(library_root_module.os, "rename", cross_device)
    assert move_originals(previous, target) == 1
    assert (target / "aa/one").read_bytes() == b"first"
    assert not (previous / "aa/one").exists()
    assert (previous / "aa/two").read_bytes() == b"second", "a conflicting file stays put"
    assert (target / "aa/two").read_bytes() == b"different"


def test_a_library_root_belongs_to_one_workspace(tmp_path: Path) -> None:
    prepare_library_root(tmp_path / "Gunther", "wsp_1")
    with pytest.raises(LibraryRootConflict, match="another Gunther workspace"):
        prepare_library_root(tmp_path / "Gunther", "wsp_2")


def test_names_are_safe_everywhere() -> None:
    assert safe_name("a/b\\c:d*e?f\"g<h>i|j") == "a b c d e f g h i j"
    assert safe_name("..hidden") == "hidden"
    assert safe_name("   ") == "Untitled"
    assert len(safe_name("x" * 300)) == 80


def make_client(tmp_path: Path, **overrides: object) -> TestClient:
    settings = {
        "database_url": f"sqlite+pysqlite:///{tmp_path / 'gunther.sqlite'}",
        "library_root": tmp_path / "Gunther",
        "previous_assets_dir": tmp_path / "data/assets",
        "previous_recordings_dir": tmp_path / "data/recordings",
        "seed_demo": False,
        "deepseek_api_key": None,
        "processing_worker_enabled": False,
        **overrides,
    }
    return TestClient(create_app(Settings(**settings)))


def add_source(client: TestClient, title: str, content: str, base_id: str | None = None) -> str:
    payload = {"title": title, "kind": "note", "content": content}
    if base_id:
        payload["knowledgeBaseId"] = base_id
    return client.post("/api/sources", json=payload).json()["source"]["id"]


def create_base(client: TestClient, title: str) -> str:
    return client.post(
        "/api/knowledge-bases",
        json={"title": title, "question": f"What is {title}?", "description": title},
    ).json()["id"]


def folder_of(library_dir: Path, source_id: str) -> Path:
    """A source's folder, as the library's own library.json describes it."""

    library = json.loads((library_dir / "library.json").read_text())
    folder = next(item["folder"] for item in library["sources"] if item["id"] == source_id)
    return library_dir / folder


def only_dir(path: Path) -> Path:
    [child] = [item for item in path.iterdir() if not item.name.startswith(".")]
    return child


def test_libraries_become_readable_folders_that_follow_every_change(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        folders = client.app.state.library_folders
        folders.stop()  # sync explicitly, so every assertion sees a finished write
        root = tmp_path / "Gunther"
        settings = client.app.state.settings
        assert settings.assets_dir == root / ".gunther/assets"

        cells, genes = create_base(client, "Cells"), create_base(client, "Genes/Proteins")
        marker = add_source(client, "Marker list", "CD3E marks T cells.", cells)
        shared = add_source(client, "Shared review", "Reviews both.", cells)
        client.post(f"/api/sources/{shared}/file", json={"knowledgeBaseId": genes})
        loose = add_source(client, "Loose capture", "Waiting in Inbox.")
        client.post("/api/notes", json={"title": "Idea: doublets?", "content": "Check doublets."})
        lecture = add_source(
            client,
            "Lecture 3",
            f"Duration: 10:00 · Captured: Today · Local recording: {RECORDING_ID}\nClusters.",
            cells,
        )
        audio = write(settings.recordings_dir / f"{RECORDING_ID}.webm", b"audio")
        stored = write(settings.assets_dir / "cd/cdef", b"%PDF paper")
        with session_scope(client.app.state.knowledge_service.sessions) as session:
            session.add(
                RecordingSession(
                    id=RECORDING_ID, title="Lecture 3", status="completed",
                    file_name=audio.name, knowledge_base_id=cells,
                )
            )
            session.add(
                Asset(
                    id="ast_paper", content_hash="c" * 64, original_name="Paper.PDF",
                    media_type="application/pdf", size_bytes=10, relative_path="cd/cdef",
                )
            )
            session.get(Source, marker).asset_id = "ast_paper"
        folders.sync()

        assert (root / "README.md").read_text().startswith("# Gunther library")
        library = json.loads((root / "Libraries/Cells/library.json").read_text())
        assert library["format"] == "gunther.library/1"
        assert {item["id"] for item in library["sources"]} == {marker, shared, lecture}
        marker_folder = folder_of(root / "Libraries/Cells", marker)
        described = json.loads((marker_folder / "source.json").read_text())
        assert described["format"] == "gunther.source/1"
        assert described["original"]["file"] == "original.pdf"
        assert (marker_folder / "content.md").read_text() == "CD3E marks T cells.\n"
        assert (marker_folder / "original.pdf").stat().st_ino == stored.stat().st_ino
        assert not stored.stat().st_mode & stat.S_IWUSR, "originals are read-only"

        lecture_folder = folder_of(root / "Libraries/Cells", lecture)
        assert (lecture_folder / "recording.webm").stat().st_ino == audio.stat().st_ino

        alias = only_dir(root / "Libraries/Genes Proteins/Sources")
        assert alias.is_symlink()
        assert json.loads((alias / "source.json").read_text())["id"] == shared
        assert os.readlink(alias).startswith("../../Cells/Sources/")

        inbox_source = only_dir(root / "Inbox/Sources")
        assert inbox_source.name.endswith("Loose capture")
        note = (root / "Inbox/Notes/Idea doublets.md").read_text()
        assert note.startswith("---\nformat: gunther.note/1\n") and "Check doublets." in note

        # Someone's own file inside Gunther's folder is never removed.
        write(inbox_source / "my-notes.txt", b"mine")
        client.post(f"/api/sources/{loose}/trash")
        client.post(f"/api/knowledge-bases/{genes}/trash")
        client.patch(f"/api/knowledge-bases/{cells}", json={"title": "Cell types"})
        folders.sync()

        assert (inbox_source / "my-notes.txt").read_bytes() == b"mine"
        assert not (inbox_source / "source.json").exists()
        assert only_dir(root / "Trash/Sources").name.endswith("Loose capture")
        assert json.loads((root / "Trash/Genes Proteins/library.json").read_text())["trashedAt"]
        assert not (root / "Libraries/Genes Proteins").exists()
        assert not (root / "Libraries/Cells").exists()
        assert (root / "Libraries/Cell types/library.json").exists()

        client.delete(f"/api/trash/source/{loose}")
        folders.sync()
        assert not (root / "Trash/Sources").exists()


def test_storage_status_and_opening_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr("gunther.storage_api.open_folder", opened.append)
    with make_client(tmp_path) as client:
        client.app.state.library_folders.stop()
        status = client.get("/api/storage").json()
        assert status["libraryRoot"] == str(tmp_path / "Gunther")
        assert status["foldersEnabled"] is True
        assert client.post("/api/storage/reveal").json() == {"opened": True}
        assert opened == [tmp_path / "Gunther"]

    (tmp_path / "plain").mkdir()
    with make_client(tmp_path / "plain", library_root=None) as client:
        assert client.get("/api/storage").json() == {
            "libraryRoot": None,
            "foldersEnabled": False,
            "problem": None,
            "lastSyncedAt": None,
            "lastError": None,
        }
        assert client.post("/api/storage/reveal").status_code == 409


def test_a_root_claimed_by_another_workspace_leaves_originals_in_place(tmp_path: Path) -> None:
    prepare_library_root(tmp_path / "Gunther", "wsp_someone_else")
    write(tmp_path / "data/assets/ab/kept", b"stays")
    with make_client(tmp_path) as client:
        status = client.get("/api/storage").json()
        assert status["foldersEnabled"] is False
        assert "another Gunther workspace" in status["problem"]
        assert client.app.state.settings.assets_dir == tmp_path / "data/assets"
    assert (tmp_path / "data/assets/ab/kept").read_bytes() == b"stays"


def test_writes_ask_the_folders_to_catch_up(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        folders = client.app.state.library_folders
        folders.stop()
        folders._wake.clear()
        client.get("/api/inbox")
        assert not folders._wake.is_set(), "reading changes nothing"
        create_base(client, "Chemistry")
        assert folders._wake.is_set()


def test_the_desktop_app_keeps_libraries_in_home_unless_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gunther.desktop_server as desktop_server

    home_root = tmp_path / "home" / "Gunther"
    monkeypatch.setattr(desktop_server, "DEFAULT_LIBRARY_ROOT", home_root)
    data_dir = tmp_path / "Application Support"
    data_dir.mkdir()

    settings = desktop_server._desktop_settings(data_dir, "token")
    assert settings.library_root == home_root.resolve()
    assert settings.assets_dir == home_root.resolve() / ".gunther/assets"
    assert settings.previous_assets_dir == data_dir / "assets"
    assert settings.previous_recordings_dir == data_dir / "recordings"

    (data_dir / ".env").write_text(f"LIBRARY_ROOT={tmp_path / 'External'}\n")
    configured = desktop_server._desktop_settings(data_dir, "token")
    assert configured.library_root == (tmp_path / "External").resolve()

    (data_dir / ".env").write_text(f"LIBRARY_ROOT={data_dir / 'Libraries'}\n")
    refused = desktop_server._desktop_settings(data_dir, "token")
    assert refused.library_root == home_root.resolve(), "never inside the private data folder"
