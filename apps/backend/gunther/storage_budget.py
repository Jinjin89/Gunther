from __future__ import annotations

import errno
import os
import shutil
import stat
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

DEFAULT_STORAGE_QUOTA_BYTES = 20 * 1024 * 1024 * 1024
DEFAULT_STORAGE_MIN_FREE_BYTES = 1024 * 1024 * 1024


class StorageBudgetError(Exception):
    """A managed-file write cannot safely consume more storage."""


class StorageQuotaExceededError(StorageBudgetError):
    pass


class StorageSpaceUnavailableError(StorageBudgetError):
    pass


@dataclass
class _ReservationState:
    device: int
    remaining: int = 0


def _normalized_roots(roots: Iterable[Path]) -> tuple[Path, ...]:
    # ``abspath`` normalizes ``..`` without following a configured symlink. A
    # managed root that is itself a link must remain visible to the fail-closed
    # scan below instead of silently becoming an outside directory.
    resolved = sorted(
        {Path(os.path.abspath(root)) for root in roots},
        key=lambda path: len(path.parts),
    )
    result: list[Path] = []
    for root in resolved:
        if any(root == parent or parent in root.parents for parent in result):
            continue
        result.append(root)
    return tuple(result)


def _assert_no_symlink_components(path: Path) -> None:
    for candidate in reversed((path, *path.parents)):
        try:
            candidate_status = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise StorageSpaceUnavailableError(
                "Managed storage path safety cannot be inspected."
            ) from error
        if stat.S_ISLNK(candidate_status.st_mode):
            raise StorageSpaceUnavailableError(
                f"Managed storage refuses symbolic links: {candidate}"
            )


def _existing_probe(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise StorageSpaceUnavailableError(
                "Managed storage is unavailable because its filesystem cannot be inspected."
            )
        candidate = parent
    return candidate


def _file_bytes(root: Path) -> int:
    """Count regular managed files without following links outside the data roots."""

    try:
        root_status = root.stat(follow_symlinks=False)
    except FileNotFoundError:
        return 0
    except OSError as error:
        raise StorageSpaceUnavailableError(
            "Managed storage usage cannot be inspected safely."
        ) from error
    if stat.S_ISLNK(root_status.st_mode):
        raise StorageSpaceUnavailableError(
            f"Managed storage refuses symbolic links: {root}"
        )
    if not stat.S_ISDIR(root_status.st_mode):
        raise StorageSpaceUnavailableError(
            f"Managed storage path is not a directory: {root}"
        )

    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        entry_status = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISLNK(entry_status.st_mode):
                        raise StorageSpaceUnavailableError(
                            f"Managed storage refuses symbolic links: {entry.path}"
                        )
                    if stat.S_ISDIR(entry_status.st_mode):
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(entry_status.st_mode):
                        total += entry_status.st_size
        except FileNotFoundError:
            continue
        except OSError as error:
            raise StorageSpaceUnavailableError(
                "Managed storage usage cannot be inspected safely."
            ) from error
    return total


class StorageReservation:
    """Capacity held for one write and released on every exit path."""

    def __init__(self, budget: StorageBudget, token: str, target: Path) -> None:
        self._budget = budget
        self._token = token
        self._target = target
        self._closed = False

    def prepare_write(self, byte_count: int) -> None:
        """Reserve enough capacity and re-check quota/free space before a write."""

        if self._closed:
            raise RuntimeError("Storage reservation is already closed")
        self._budget._prepare_write(self._token, self._target, byte_count)

    def consume(self, byte_count: int) -> None:
        """Mark reserved bytes as materialized on disk."""

        if self._closed:
            raise RuntimeError("Storage reservation is already closed")
        self._budget._consume(self._token, byte_count)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._budget._release(self._token)

    def __enter__(self) -> StorageReservation:
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, traceback: object) -> bool:
        del exc_type, traceback
        self.close()
        if isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}:
            raise StorageSpaceUnavailableError(
                "The managed storage filesystem ran out of writable space."
            ) from exc
        return False


class StorageBudget:
    """Serialize quota reservations across all managed file writers in this process.

    Existing files are measured for every reservation check. Bytes already written
    by an active request therefore come from the filesystem scan, while its not-yet-
    written bytes remain reserved here. This avoids both double counting and the
    classic concurrent preflight race.
    """

    def __init__(
        self,
        roots: Iterable[Path],
        *,
        quota_bytes: int = DEFAULT_STORAGE_QUOTA_BYTES,
        min_free_bytes: int = DEFAULT_STORAGE_MIN_FREE_BYTES,
    ) -> None:
        if quota_bytes <= 0:
            raise ValueError("storage quota must be greater than zero")
        if min_free_bytes < 0:
            raise ValueError("storage minimum free space cannot be negative")
        self.roots = _normalized_roots(roots)
        if not self.roots:
            raise ValueError("at least one managed storage root is required")
        self.quota_bytes = quota_bytes
        self.min_free_bytes = min_free_bytes
        self._lock = threading.Lock()
        self._reservations: dict[str, _ReservationState] = {}

    def assert_safe_path(self, path: Path) -> Path:
        """Return an absolute managed path only when no component is a symlink."""

        target_path = Path(os.path.abspath(path))
        if not any(target_path == root or root in target_path.parents for root in self.roots):
            raise ValueError("storage path is outside the managed roots")
        _assert_no_symlink_components(target_path)
        return target_path

    def reserve(self, target: Path, expected_bytes: int = 0) -> StorageReservation:
        """Hold capacity before receiving or writing bytes."""

        if expected_bytes < 0:
            raise ValueError("expected storage bytes cannot be negative")
        target_path = self.assert_safe_path(target)
        probe = _existing_probe(target_path)
        try:
            device = probe.stat().st_dev
        except OSError as error:
            raise StorageSpaceUnavailableError(
                "Managed storage filesystem cannot be inspected safely."
            ) from error
        token = uuid4().hex
        with self._lock:
            self._reservations[token] = _ReservationState(device=device)
            try:
                self._increase(token, target_path, expected_bytes)
            except Exception:
                self._reservations.pop(token, None)
                raise
        return StorageReservation(self, token, target_path)

    def managed_bytes(self) -> int:
        with self._lock:
            return self._managed_bytes_unlocked()

    def _managed_bytes_unlocked(self) -> int:
        total = 0
        for root in self.roots:
            _assert_no_symlink_components(root)
            total += _file_bytes(root)
        return total

    def _outstanding_unlocked(self) -> int:
        return sum(state.remaining for state in self._reservations.values())

    def _validate_unlocked(self, target: Path, device: int) -> None:
        _assert_no_symlink_components(target)
        used = self._managed_bytes_unlocked()
        outstanding = self._outstanding_unlocked()
        if used + outstanding > self.quota_bytes:
            raise StorageQuotaExceededError(
                "Managed storage quota exceeded. Free managed files or increase "
                "STORAGE_QUOTA_BYTES."
            )

        probe = _existing_probe(target)
        try:
            free_bytes = shutil.disk_usage(probe).free
        except OSError as error:
            raise StorageSpaceUnavailableError(
                "Managed storage free space cannot be inspected safely."
            ) from error
        device_outstanding = sum(
            state.remaining
            for state in self._reservations.values()
            if state.device == device
        )
        if free_bytes - device_outstanding < self.min_free_bytes:
            raise StorageSpaceUnavailableError(
                "Not enough disk space for this write while preserving "
                "STORAGE_MIN_FREE_BYTES."
            )

    def _increase(self, token: str, target: Path, byte_count: int) -> None:
        state = self._reservations[token]
        state.remaining += byte_count
        try:
            self._validate_unlocked(target, state.device)
        except Exception:
            state.remaining -= byte_count
            raise

    def _prepare_write(self, token: str, target: Path, byte_count: int) -> None:
        if byte_count < 0:
            raise ValueError("write size cannot be negative")
        with self._lock:
            state = self._reservations.get(token)
            if state is None:
                raise RuntimeError("Storage reservation is no longer active")
            additional = max(0, byte_count - state.remaining)
            self._increase(token, target, additional)
            if not additional:
                self._validate_unlocked(target, state.device)

    def _consume(self, token: str, byte_count: int) -> None:
        if byte_count < 0:
            raise ValueError("consumed storage bytes cannot be negative")
        with self._lock:
            state = self._reservations.get(token)
            if state is None:
                raise RuntimeError("Storage reservation is no longer active")
            if byte_count > state.remaining:
                raise RuntimeError("write consumed more bytes than were reserved")
            state.remaining -= byte_count

    def _release(self, token: str) -> None:
        with self._lock:
            self._reservations.pop(token, None)
