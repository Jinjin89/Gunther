import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from gunther.library_folders import LibraryFolders
from gunther.schemas import ApiModel

router = APIRouter()


class RevealOut(ApiModel):
    opened: bool


class StorageStatusOut(ApiModel):
    """Where this workspace keeps its libraries on disk."""

    library_root: str | None
    folders_enabled: bool
    # Why a configured library root is not in use, in words a person can act on.
    problem: str | None = None
    last_synced_at: str | None = None
    last_error: str | None = None


def open_folder(path: Path) -> None:
    """Show a folder in the system file manager. The path is never user input."""

    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]  # Windows only
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _folders(request: Request) -> LibraryFolders | None:
    return request.app.state.library_folders


@router.get("/storage", response_model=StorageStatusOut)
def storage_status(request: Request) -> StorageStatusOut:
    folders = _folders(request)
    configured = request.app.state.settings.library_root
    if folders is None:
        return StorageStatusOut(
            library_root=str(configured) if configured else None,
            folders_enabled=False,
            problem=request.app.state.storage_problem,
        )
    status = folders.status
    return StorageStatusOut(
        library_root=str(folders.root),
        folders_enabled=True,
        last_synced_at=(
            f"{status.last_synced_at.isoformat(timespec='seconds')}Z"
            if status.last_synced_at
            else None
        ),
        last_error=status.last_error,
    )


@router.post("/storage/reveal", response_model=RevealOut)
def reveal_library_root(request: Request) -> RevealOut:
    folders = _folders(request)
    if folders is None:
        raise HTTPException(409, "No library folder is in use")
    try:
        open_folder(folders.root)
    except OSError as error:
        raise HTTPException(503, "No file manager could open the library folder") from error
    return RevealOut(opened=True)
