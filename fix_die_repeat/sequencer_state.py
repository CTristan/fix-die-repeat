"""Sequencer state paths, locking, and atomic persistence."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Self

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from fix_die_repeat.sequencer_workflow import ID_PATTERN

if TYPE_CHECKING:
    import types

    from fix_die_repeat.sequencer_git import RepositoryInfo

STATE_SCHEMA_VERSION = 1
PROTOCOL_VERSION = 1
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_RETRY_INTERVAL_SECONDS = 0.05


class StateError(RuntimeError):
    """Persisted sequencer state is missing, corrupt, or incompatible."""


@dataclass(frozen=True)
class RunPaths:
    """Filesystem paths owned by one sequencer run."""

    directory: Path
    state: Path
    lock: Path
    artifacts: Path


class SequencerLock:
    """Cross-platform operating-system lock for one sequencer run."""

    def __init__(self, path: Path) -> None:
        """Open the dedicated lock file without treating its contents as state."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a+", encoding="utf-8")
        self._ensure_lock_region()

    def _ensure_lock_region(self) -> None:
        if sys.platform != "win32":
            return
        self._handle.seek(0, os.SEEK_END)
        if self._handle.tell() == 0:
            self._handle.write("\0")
            self._handle.flush()

    def __enter__(self) -> Self:
        """Acquire an exclusive lock until context exit."""
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while not self._try_acquire():
            if time.monotonic() >= deadline:
                self._handle.close()
                msg = (
                    "Cannot acquire sequencer transition lock "
                    f"within {LOCK_TIMEOUT_SECONDS:g} seconds"
                )
                raise StateError(msg)
            time.sleep(LOCK_RETRY_INTERVAL_SECONDS)
        return self

    def _try_acquire(self) -> bool:
        """Try once without letting lock contention block the process."""
        if sys.platform == "win32":
            self._handle.seek(0)
            try:
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False
            return True
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        """Release the lock and close its file handle."""
        del exc_type, exc_value, traceback
        try:
            if sys.platform == "win32":
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()


def run_paths(home: Path, repository: RepositoryInfo, run_id: str) -> RunPaths:
    """Return stable state paths for one repository-scoped run ID."""
    if not ID_PATTERN.fullmatch(run_id):
        msg = f"Invalid run ID: {run_id!r}"
        raise StateError(msg)
    digest = hashlib.sha256(run_id.encode()).hexdigest()[:8]
    directory = (
        home.expanduser().resolve(strict=False)
        / "sequencer"
        / "repositories"
        / repository.key
        / "runs"
        / f"{run_id}-{digest}"
    )
    return RunPaths(
        directory=directory,
        state=directory / "state.json",
        lock=directory / "transition.lock",
        artifacts=directory / "artifacts",
    )


def read_state(path: Path) -> dict[str, Any]:
    """Read and minimally validate one authoritative state file."""
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, ValueError) as exc:
        msg = f"Cannot read sequencer state {path}: {exc}"
        raise StateError(msg) from exc
    if not isinstance(value, dict):
        msg = f"Sequencer state {path} is not an object"
        raise StateError(msg)
    if value.get("state_schema_version") != STATE_SCHEMA_VERSION:
        msg = f"Sequencer state {path} uses an unsupported schema"
        raise StateError(msg)
    return value


def _sync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json(handle: IO[str], state: dict[str, Any]) -> None:
    json.dump(state, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())


def write_state(path: Path, state: dict[str, Any]) -> None:
    """Atomically replace one state record and synchronize its directory."""
    temporary_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".state-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            _write_json(handle, state)
        temporary_path.replace(path)
        temporary_path = None
        _sync_directory(path.parent)
    except OSError as exc:
        msg = f"Cannot write sequencer state {path}: {exc}"
        raise StateError(msg) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
