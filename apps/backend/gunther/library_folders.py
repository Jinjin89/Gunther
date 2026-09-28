"""Readable library folders: Gunther's on-disk contract (format 1).

Inside the library root Gunther writes::

    README.md
    Inbox/
      Sources/<date> <title>/      sources waiting for a library
      Notes/<title>.md             quick notes waiting in Inbox
    Libraries/<title>/
      library.json                 the library and where each of its sources is
      Sources/<date> <title>/      sources whose home is this library
        source.json                what it is, where it came from, your decisions
        content.md                 its text: the note, transcript or extracted text
        original.<ext>             the captured file, when there is one
        recording.<ext>            the audio, for a recording
    Trash/                         the same shapes, for what rests in Trash

A source filed in several libraries lives in the first; each other library
holds a relative symlink to it, which Finder shows as an alias. Originals are
hard links to the single copy under ``.gunther/``, so they take no extra space,
and that copy is made read-only so an editor cannot change it in place.

Gunther owns these folders. It rewrites them as things change and removes only
files it wrote and nobody has changed since (``.gunther/folders.json``), so
anything added by hand stays. Edits made here are not read back in format 1.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased, sessionmaker

from gunther.database import session_scope
from gunther.models import (
    Assertion,
    Asset,
    Entity,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    NotebookNote,
    RecordingSession,
    Source,
    WebSnapshot,
    utc_now,
)
from gunther.trash import recording_ids

logger = logging.getLogger(__name__)

FOLDERS_FORMAT = 1
TOP_LEVEL = ("Inbox", "Libraries", "Trash")
MANIFEST = PurePosixPath(".gunther/folders.json")
# Rows whose change can change what the folders should show.
WATCHED_MODELS = (
    Assertion,
    Asset,
    KnowledgeBaseRecord,
    KnowledgeBaseSource,
    NotebookNote,
    RecordingSession,
    Source,
    WebSnapshot,
)

README = """# Gunther library

This folder is written by Gunther. It shows your libraries as ordinary files so
you can browse, copy and back them up with any tool.

- `Inbox/` holds sources and quick notes that are not in a library yet.
- `Libraries/<name>/` is one library. `library.json` describes it; each source
  has its own folder under `Sources/` with `source.json` (what it is and where
  it came from), `content.md` (its text) and, when there is one, the original
  file or recording.
- A source filed in more than one library lives in the first; the others hold
  an alias to it.
- `Trash/` shows what is in Gunther's Trash until it is restored or deleted.
- `.gunther/` is Gunther's own store. Please leave it alone.

Gunther keeps these folders up to date. Edits made here are not read back into
Gunther, and files you add yourself are never removed. Format: gunther.library/1.
"""


@dataclass(frozen=True, slots=True)
class Entry:
    kind: Literal["file", "link", "symlink"]
    data: bytes = b""
    source: Path | None = None
    target: str = ""


@dataclass(slots=True)
class FolderStatus:
    enabled: bool
    root: Path | None
    problem: str | None = None
    last_synced_at: datetime | None = None
    last_error: str | None = None


def _iso(value: datetime | None) -> str | None:
    return f"{value.isoformat(timespec='seconds')}Z" if value else None


_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')


def safe_name(text: str, fallback: str = "Untitled", limit: int = 80) -> str:
    """A folder or file name that is valid everywhere and never hidden or relative."""

    name = _UNSAFE.sub(" ", text)
    name = re.sub(r"\s+", " ", name).strip().lstrip(".").strip()
    if len(name) > limit:
        name = name[:limit].rstrip()
    return name.rstrip(". ") or fallback


def _unique(name: str, taken: set[str]) -> str:
    """Add " 2", " 3"… until the name is free (case-insensitively, as macOS compares)."""

    candidate, number = name, 2
    while candidate.casefold() in taken:
        candidate = f"{name} {number}"
        number += 1
    taken.add(candidate.casefold())
    return candidate


def _json(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _extension(name: str | None) -> str:
    suffix = Path(name or "").suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ""


@dataclass
class _Plan:
    entries: dict[str, Entry] = field(default_factory=dict)

    def file(self, path: PurePosixPath, data: bytes) -> None:
        self.entries[str(path)] = Entry("file", data=data)

    def link(self, path: PurePosixPath, source: Path) -> None:
        self.entries[str(path)] = Entry("link", source=source)

    def symlink(self, path: PurePosixPath, target: str) -> None:
        self.entries[str(path)] = Entry("symlink", target=target)


class LibraryFolders:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        root: Path,
        *,
        assets_dir: Path,
        recordings_dir: Path,
        debounce_seconds: float = 0.8,
        refresh_seconds: float = 600.0,
    ) -> None:
        self.sessions = sessions
        self.root = root
        self.assets_dir = assets_dir
        self.recordings_dir = recordings_dir
        self.debounce_seconds = debounce_seconds
        self.refresh_seconds = refresh_seconds
        self.status = FolderStatus(enabled=True, root=root)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    # Running ---------------------------------------------------------------

    def request_sync(self) -> None:
        self._wake.set()

    def start(self) -> None:
        """Keep the folders current in the background: now, after changes, and periodically."""

        if self._thread is not None:
            return
        self._wake.set()
        self._thread = threading.Thread(
            target=self._run, name="gunther-library-folders", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait(self.refresh_seconds)
            if self._stopping.is_set():
                return
            # Let a burst of changes settle, then write once.
            self._stopping.wait(self.debounce_seconds)
            self._wake.clear()
            try:
                self.sync()
            except Exception:  # Reported in status; the next change or refresh retries.
                logger.warning("Library folders could not be updated", exc_info=True)

    # Syncing ---------------------------------------------------------------

    def sync(self) -> int:
        """Bring the folders in line with the database; return how many paths changed."""

        with self._lock:
            try:
                plan = self._plan()
                changed = self._apply(plan)
            except Exception as error:
                self.status.last_error = f"{type(error).__name__}: {error}"
                raise
            self.status.last_synced_at = utc_now()
            self.status.last_error = None
            return changed

    def _plan(self) -> _Plan:
        plan = _Plan()
        plan.file(PurePosixPath("README.md"), README.encode("utf-8"))
        with session_scope(self.sessions) as session:
            libraries = session.scalars(
                select(KnowledgeBaseRecord).order_by(
                    KnowledgeBaseRecord.created_at, KnowledgeBaseRecord.id
                )
            ).all()
            sources = session.scalars(select(Source).order_by(Source.created_at, Source.id)).all()
            memberships: dict[str, list[str]] = defaultdict(list)
            for source_id, library_id in session.execute(
                select(KnowledgeBaseSource.source_id, KnowledgeBaseSource.knowledge_base_id)
                .order_by(KnowledgeBaseSource.created_at, KnowledgeBaseSource.id)
            ):
                memberships[source_id].append(library_id)
            assets = {asset.id: asset for asset in session.scalars(select(Asset))}
            snapshots = {
                snapshot.source_id: snapshot for snapshot in session.scalars(select(WebSnapshot))
            }
            recordings = {
                recording.id: recording
                for recording in session.scalars(
                    select(RecordingSession).where(RecordingSession.status == "completed")
                )
            }
            claims: dict[str, list[dict[str, str]]] = defaultdict(list)
            subject, object_ = aliased(Entity), aliased(Entity)
            for claim_id, source_id, predicate, status, subject_label, object_label in (
                session.execute(
                    select(
                        Assertion.id,
                        Assertion.source_id,
                        Assertion.predicate,
                        Assertion.status,
                        subject.label,
                        object_.label,
                    )
                    .join(subject, subject.id == Assertion.subject_entity_id)
                    .join(object_, object_.id == Assertion.object_entity_id)
                    .order_by(Assertion.created_at, Assertion.id)
                )
            ):
                claims[source_id].append({
                    "id": claim_id,
                    "subject": subject_label,
                    "predicate": predicate,
                    "object": object_label,
                    "status": status,
                })
            notes = session.scalars(
                select(NotebookNote)
                .where(NotebookNote.status == "inbox")
                .order_by(NotebookNote.created_at, NotebookNote.id)
            ).all()

            # Where every library lives.
            taken: dict[str, set[str]] = defaultdict(set)
            library_folder: dict[str, PurePosixPath] = {}
            batch_library: dict[str, str] = {}
            for library in libraries:
                parent = "Trash" if library.trashed_at else "Libraries"
                library_folder[library.id] = PurePosixPath(parent) / _unique(
                    safe_name(library.title, "Library"), taken[parent]
                )
                if library.trashed_at and library.trash_batch_id:
                    batch_library[library.trash_batch_id] = library.id
            live_libraries = {lib.id for lib in libraries if lib.trashed_at is None}
            titles = {library.id: library.title for library in libraries}

            # Where every source lives, and where it is also shown.
            home: dict[str, PurePosixPath] = {}
            aliases: dict[str, list[PurePosixPath]] = defaultdict(list)
            listed: dict[str, list[dict[str, object]]] = defaultdict(list)
            for source in sources:
                name = safe_name(f"{source.created_at:%Y-%m-%d} {source.title}", "Source")
                if source.trashed_at:
                    owner = batch_library.get(source.trash_batch_id or "")
                    parent = (
                        library_folder[owner] / "Sources"
                        if owner
                        else PurePosixPath("Trash/Sources")
                    )
                    home[source.id] = parent / _unique(name, taken[str(parent)])
                    if owner:
                        listed[owner].append({"id": source.id, "title": source.title,
                                              "folder": f"Sources/{home[source.id].name}"})
                    continue
                shown_in = [lib for lib in memberships.get(source.id, []) if lib in live_libraries]
                if not shown_in:
                    parent = PurePosixPath("Inbox/Sources")
                    home[source.id] = parent / _unique(name, taken[str(parent)])
                    continue
                for index, library_id in enumerate(shown_in):
                    parent = library_folder[library_id] / "Sources"
                    path = parent / _unique(name, taken[str(parent)])
                    if index == 0:
                        home[source.id] = path
                    else:
                        aliases[source.id].append(path)
                    listed[library_id].append({"id": source.id, "title": source.title,
                                               "folder": f"Sources/{path.name}",
                                               "alias": index > 0})

            for library in libraries:
                folder = library_folder[library.id]
                plan.file(folder / "library.json", _json({
                    "format": "gunther.library/1",
                    "id": library.id,
                    "title": library.title,
                    "question": library.question,
                    "description": library.description,
                    "color": library.color,
                    "createdAt": _iso(library.created_at),
                    "trashedAt": _iso(library.trashed_at),
                    "sources": listed[library.id],
                }))

            for source in sources:
                folder = home[source.id]
                asset = assets.get(source.asset_id or "")
                original = None
                if asset is not None:
                    file_name = f"original{_extension(asset.original_name)}"
                    stored = self.assets_dir / asset.relative_path
                    if stored.is_file():
                        plan.link(folder / file_name, stored)
                    original = {
                        "file": file_name,
                        "name": asset.original_name,
                        "mediaType": asset.media_type,
                        "sizeBytes": asset.size_bytes,
                        "sha256": asset.content_hash,
                    }
                recording = None
                for recording_id in sorted(recording_ids(source.content)):
                    session_row = recordings.get(recording_id)
                    if session_row is None:
                        continue
                    file_name = f"recording{_extension(session_row.file_name)}"
                    stored = self.recordings_dir / session_row.file_name
                    if stored.is_file():
                        plan.link(folder / file_name, stored)
                    recording = {
                        "id": recording_id,
                        "file": file_name,
                        "durationSeconds": session_row.duration_seconds,
                    }
                    break
                snapshot = snapshots.get(source.id)
                membership_ids = memberships.get(source.id, [])
                plan.file(folder / "source.json", _json({
                    "format": "gunther.source/1",
                    "id": source.id,
                    "title": source.title,
                    "kind": source.kind,
                    "capturedAt": _iso(source.created_at),
                    "fingerprint": source.content_hash,
                    "libraries": [
                        {"id": library_id, "title": titles.get(library_id, library_id)}
                        for library_id in membership_ids
                    ],
                    "original": original,
                    "recording": recording,
                    "web": {
                        "url": snapshot.original_url,
                        "finalUrl": snapshot.final_url,
                        "capturedAt": _iso(snapshot.captured_at),
                    } if snapshot else None,
                    "claims": claims.get(source.id, []),
                    "trashedAt": _iso(source.trashed_at),
                }))
                plan.file(folder / "content.md", (source.content.rstrip() + "\n").encode("utf-8"))
                for alias in aliases.get(source.id, []):
                    plan.symlink(alias, os.path.relpath(folder, alias.parent))

            for note in notes:
                parent = PurePosixPath("Trash/Notes" if note.trashed_at else "Inbox/Notes")
                name = _unique(safe_name(note.title, "Untitled note"), taken[str(parent)])
                front = "\n".join((
                    "---",
                    "format: gunther.note/1",
                    f"id: {note.id}",
                    f"title: {json.dumps(note.title, ensure_ascii=False)}",
                    f"created: {_iso(note.created_at)}",
                    f"updated: {_iso(note.updated_at)}",
                    "---",
                ))
                plan.file(parent / f"{name}.md", f"{front}\n\n{note.content.rstrip()}\n".encode())
        return plan

    # Writing ---------------------------------------------------------------

    def _manifest_path(self) -> Path:
        return self.root / MANIFEST

    def _load_manifest(self) -> dict[str, dict[str, object]]:
        try:
            data = json.loads(self._manifest_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        entries = data.get("entries", {}) if isinstance(data, dict) else {}
        return {
            key: value
            for key, value in entries.items()
            if isinstance(value, dict) and self._inside(key)
        }

    @staticmethod
    def _inside(relative: str) -> bool:
        path = PurePosixPath(relative)
        return (
            not path.is_absolute()
            and ".." not in path.parts
            and bool(path.parts)
            and path.parts[0] in (*TOP_LEVEL, "README.md")
        )

    def _no_symlink_between(self, path: Path) -> bool:
        """True when no folder between the root and ``path`` is a symlink."""

        current = self.root
        for part in path.relative_to(self.root).parts[:-1]:
            current = current / part
            if current.is_symlink():
                return False
        return True

    @staticmethod
    def _record(path: Path, entry: Entry) -> dict[str, object]:
        info = path.lstat()
        if entry.kind == "symlink":
            return {"kind": "symlink", "target": entry.target}
        record: dict[str, object] = {
            "kind": entry.kind,
            "size": info.st_size,
            "mtime": info.st_mtime_ns,
            "inode": info.st_ino,
        }
        if entry.kind == "file":
            record["sha256"] = hashlib.sha256(entry.data).hexdigest()
        return record

    @staticmethod
    def _unchanged(path: Path, record: dict[str, object] | None) -> bool:
        """Whether ``path`` is still exactly what Gunther last wrote there."""

        if record is None:
            return False
        try:
            info = path.lstat()
        except OSError:
            return False
        if record.get("kind") == "symlink":
            return stat.S_ISLNK(info.st_mode) and os.readlink(path) == record.get("target")
        return (
            stat.S_ISREG(info.st_mode)
            and info.st_size == record.get("size")
            and info.st_mtime_ns == record.get("mtime")
            and info.st_ino == record.get("inode")
        )

    def _current(self, path: Path, entry: Entry, record: dict[str, object] | None) -> bool:
        if not self._unchanged(path, record) or record.get("kind") != entry.kind:
            return False
        if entry.kind == "file":
            return record.get("sha256") == hashlib.sha256(entry.data).hexdigest()
        if entry.kind == "symlink":
            return record.get("target") == entry.target
        try:
            original = entry.source.stat() if entry.source else None
        except OSError:
            return False
        # A hard link shares its original's inode; a fallback copy matches by size.
        return original is not None and (
            original.st_ino == path.lstat().st_ino or original.st_size == path.lstat().st_size
        )

    @staticmethod
    def _already(path: Path, entry: Entry) -> bool:
        """Whether ``path`` already holds exactly what ``entry`` describes."""

        try:
            info = path.lstat()
            if entry.kind == "symlink":
                return stat.S_ISLNK(info.st_mode) and os.readlink(path) == entry.target
            if not stat.S_ISREG(info.st_mode):
                return False
            if entry.kind == "link":
                return entry.source is not None and entry.source.stat().st_ino == info.st_ino
            return info.st_size == len(entry.data) and path.read_bytes() == entry.data
        except OSError:
            return False

    def _write(self, path: Path, entry: Entry) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.gunther-tmp")
        if temporary.is_symlink() or temporary.exists():
            temporary.unlink()
        if entry.kind == "file":
            temporary.write_bytes(entry.data)
        elif entry.kind == "symlink":
            os.symlink(entry.target, temporary)
        else:
            assert entry.source is not None
            # Originals never change in place: read-only guards them through the link.
            mode = entry.source.stat().st_mode
            if mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                os.chmod(entry.source, stat.S_IMODE(mode) & ~0o222)
            try:
                os.link(entry.source, temporary)
            except OSError:
                # A volume without hard links (exFAT): fall back to a copy.
                shutil.copy2(entry.source, temporary)
        os.replace(temporary, path)

    def _apply(self, plan: _Plan) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in TOP_LEVEL:
            (self.root / name).mkdir(exist_ok=True)
        previous = self._load_manifest()
        changed = 0

        # First take away what should no longer be here, so a stale alias is gone
        # before a real folder is written in its place.
        emptied: set[Path] = set()
        for relative, record in previous.items():
            entry = plan.entries.get(relative)
            if entry is not None and entry.kind == record.get("kind"):
                continue
            path = self.root / relative
            if self._unchanged(path, record) and self._no_symlink_between(path):
                path.unlink()
                changed += 1
                emptied.add(path.parent)

        manifest: dict[str, dict[str, object]] = {}
        for relative, entry in plan.entries.items():
            path = self.root / relative
            if not self._no_symlink_between(path):
                logger.warning("Skipped %s: a folder above it is a symlink", relative)
                continue
            record = previous.get(relative)
            if record is not None and self._current(path, entry, record):
                manifest[relative] = record
                continue
            if path.is_symlink() or path.exists():
                if self._already(path, entry):
                    # Right already (say the manifest was lost): adopt it as written.
                    manifest[relative] = self._record(path, entry)
                    continue
                ours = record is not None and self._unchanged(path, record)
                if path.is_dir() and not path.is_symlink() or (not ours and entry.kind != "file"):
                    logger.warning("Left %s alone: it was not written by Gunther", relative)
                    continue
            try:
                self._write(path, entry)
            except OSError:
                logger.warning("Could not write %s", relative, exc_info=True)
                continue
            manifest[relative] = self._record(path, entry)
            changed += 1

        for folder in sorted(emptied, key=lambda item: len(item.parts), reverse=True):
            current = folder
            while current != self.root and current.name not in TOP_LEVEL:
                try:
                    current.rmdir()
                except OSError:
                    break  # still holds something, perhaps a file someone added
                current = current.parent

        manifest_path = self._manifest_path()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest_path.with_name(f".{manifest_path.name}.tmp")
        temporary.write_text(
            json.dumps({"format": FOLDERS_FORMAT, "entries": manifest}, indent=1),
            encoding="utf-8",
        )
        os.replace(temporary, manifest_path)
        return changed
