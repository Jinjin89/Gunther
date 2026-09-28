"""The library root: where Gunther keeps originals and one readable folder per library.

Layout (format 1)::

    <root>/
      README.md                   what this folder is, for people and tools
      Inbox/ Libraries/ Trash/    readable folders Gunther writes (see library_folders)
      .gunther/
        root.json                 which workspace owns this root
        assets/                   originals, stored once by fingerprint
        recordings/               recording audio

The database stays in the application's private data directory: SQLite must
not live in a folder that iCloud or Dropbox might sync. A root belongs to one
workspace; a second workspace pointed at it is refused rather than mixed in.

Originals kept before a root was configured move into it once, at startup and
before any service opens them, one file at a time: a rename on the same volume,
otherwise a copy that is verified byte for byte before the old file goes. A file
that already exists in the root with different content is left where it was.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import shutil
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from gunther.models import utc_now

logger = logging.getLogger(__name__)

ROOT_FORMAT = "gunther.library-root/1"
INTERNAL_DIRECTORY = ".gunther"


class LibraryRootConflict(RuntimeError):
    """The configured root cannot be used by this workspace as it stands."""


@dataclass(frozen=True, slots=True)
class LibraryRoot:
    path: Path
    assets_dir: Path
    recordings_dir: Path

    @property
    def internal(self) -> Path:
        return self.path / INTERNAL_DIRECTORY


def resolve_root(root: Path) -> Path:
    """An absolute path without symlinks, which managed storage requires."""

    return root.expanduser().resolve()


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise LibraryRootConflict(f"{path} is not a real directory")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _move_file(source: Path, destination: Path) -> None:
    """Move one original without ever leaving it in neither place."""

    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.rename(source, destination)
        return
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
    staging = destination.with_name(f".{destination.name}.incoming")
    shutil.copy2(source, staging)
    with staging.open("rb") as handle:
        os.fsync(handle.fileno())
    if staging.stat().st_size != source.stat().st_size or _sha256(staging) != _sha256(source):
        staging.unlink(missing_ok=True)
        raise LibraryRootConflict(f"Copy of {source.name} did not verify")
    os.replace(staging, destination)
    source.unlink()


def move_originals(previous: Path, target: Path) -> int:
    """Move every original from ``previous`` into ``target``; return how many moved."""

    previous = previous.expanduser().absolute()
    if not previous.is_dir() or previous.is_symlink():
        return 0
    if previous.resolve() == target.resolve():
        return 0
    moved = 0
    for directory, directory_names, file_names in os.walk(previous, topdown=True):
        # Dot directories hold interrupted uploads; they stay with the old folder.
        directory_names[:] = [name for name in directory_names if not name.startswith(".")]
        for name in file_names:
            source = Path(directory) / name
            if name.startswith(".") or source.is_symlink() or not source.is_file():
                continue
            destination = target / source.relative_to(previous)
            if destination.exists():
                # Already here (an earlier move stopped before tidying up), or a
                # different file with the same name, which is left for a person.
                if destination.stat().st_size == source.stat().st_size and _sha256(
                    destination
                ) == _sha256(source):
                    source.unlink()
                else:
                    logger.warning("Left %s in place: the library root has a different file", name)
                continue
            _move_file(source, destination)
            moved += 1
    for directory, _directories, _files in sorted(os.walk(previous), reverse=True):
        # A folder that still holds something deliberately left stays.
        with suppress(OSError):
            Path(directory).rmdir()
    return moved


def prepare_library_root(
    root: Path,
    workspace_id: str,
    *,
    previous_assets_dir: Path | None = None,
    previous_recordings_dir: Path | None = None,
) -> LibraryRoot:
    """Claim ``root`` for this workspace and move earlier originals into it."""

    root = resolve_root(root)
    internal = root / INTERNAL_DIRECTORY
    _private_directory(internal)
    identity_path = internal / "root.json"
    if identity_path.exists():
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LibraryRootConflict(f"{identity_path} is unreadable") from error
        if identity.get("workspaceId") != workspace_id:
            raise LibraryRootConflict(
                f"{root} belongs to another Gunther workspace. Choose another LIBRARY_ROOT."
            )
    else:
        identity = {
            "format": ROOT_FORMAT,
            "workspaceId": workspace_id,
            "createdAt": f"{utc_now().isoformat(timespec='seconds')}Z",
        }
        _write_json(identity_path, identity)

    library_root = LibraryRoot(
        path=root,
        assets_dir=internal / "assets",
        recordings_dir=internal / "recordings",
    )
    for previous, target in (
        (previous_assets_dir, library_root.assets_dir),
        (previous_recordings_dir, library_root.recordings_dir),
    ):
        _private_directory(target)
        if previous is not None and move_originals(previous, target):
            identity.setdefault("movedFrom", [])
            if str(previous) not in identity["movedFrom"]:
                identity["movedFrom"].append(str(previous))
            _write_json(identity_path, identity)
            logger.info("Moved originals from %s into %s", previous, target)
    return library_root
