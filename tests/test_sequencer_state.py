"""Tests for sequencer locking and state persistence."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from fix_die_repeat import sequencer_state
from fix_die_repeat.sequencer_git import RepositoryInfo
from fix_die_repeat.sequencer_state import (
    SequencerLock,
    StateError,
    read_state,
    run_paths,
    write_state,
)


def test_lock_times_out_with_a_state_error(tmp_path: Path) -> None:
    """A held transition lock fails instead of blocking forever."""
    lock = SequencerLock(tmp_path / "transition.lock")

    with (
        patch.object(lock, "_try_acquire", return_value=False),
        patch(
            "fix_die_repeat.sequencer_state.time.monotonic",
            side_effect=[0.0, 0.0, 11.0],
        ),
        patch("fix_die_repeat.sequencer_state.time.sleep"),
        pytest.raises(StateError, match="within 10 seconds"),
    ):
        lock.__enter__()


def test_lock_enforces_mutual_exclusion_and_releases(tmp_path: Path) -> None:
    """One lock excludes peers until its context exits."""
    path = tmp_path / "transition.lock"
    first = SequencerLock(path)
    second = SequencerLock(path)
    with first:
        assert not second._try_acquire()
        second._handle.close()

    third = SequencerLock(path)
    with third:
        pass


def test_write_state_wraps_directory_creation_failure(tmp_path: Path) -> None:
    """State directory failures preserve the module error contract."""
    path = tmp_path / "state" / "state.json"

    with (
        patch.object(Path, "mkdir", side_effect=OSError("denied")),
        pytest.raises(StateError, match="Cannot write sequencer state"),
    ):
        write_state(path, {"state_schema_version": 1})


def test_state_round_trip_uses_repository_run_layout(tmp_path: Path) -> None:
    """State writes round-trip atomically under the repository-scoped run path."""
    repository = RepositoryInfo(root=tmp_path, common_dir=tmp_path / ".git", key="repo-key")
    paths = run_paths(tmp_path / "home", repository, "run-1")
    state = {"state_schema_version": 1, "message": "ready"}

    write_state(paths.state, state)

    assert paths.directory.parent.name == "runs"
    assert paths.directory.parent.parent.name == "repo-key"
    assert paths.state == paths.directory / "state.json"
    assert paths.lock == paths.directory / "transition.lock"
    assert paths.artifacts == paths.directory / "artifacts"
    assert read_state(paths.state) == state
    assert list(paths.directory.glob(".state-*.tmp")) == []


def test_lock_closes_handle_when_unlock_fails(tmp_path: Path) -> None:
    """Unlock errors cannot leak the lock-file handle."""
    lock = SequencerLock(tmp_path / "transition.lock")
    lock.__enter__()
    target = (
        "fix_die_repeat.sequencer_state.msvcrt.locking"
        if sys.platform == "win32"
        else "fix_die_repeat.sequencer_state.fcntl.flock"
    )

    with (
        patch(target, side_effect=OSError("unlock failed")),
        pytest.raises(OSError, match="unlock failed"),
    ):
        lock.__exit__(None, None, None)

    assert lock._handle.closed


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock behavior")
def test_lock_wraps_non_contention_os_error(tmp_path: Path) -> None:
    """Unexpected flock errors fail immediately and close the handle."""
    lock = SequencerLock(tmp_path / "transition.lock")

    with (
        patch("fix_die_repeat.sequencer_state.fcntl.flock", side_effect=OSError("denied")),
        pytest.raises(StateError, match="Cannot lock sequencer transition file"),
    ):
        lock.__enter__()

    assert lock._handle.closed


def test_read_state_uses_utf8_encoding(tmp_path: Path) -> None:
    """State decoding matches the writer's explicit UTF-8 encoding."""
    state = {"state_schema_version": 1, "message": "résumé"}
    path = tmp_path / "state.json"

    with patch.object(Path, "read_text", return_value=json.dumps(state)) as read_text:
        assert read_state(path) == state

    read_text.assert_called_once_with(encoding="utf-8")


def test_directory_sync_is_best_effort(tmp_path: Path) -> None:
    """Directory fsync failures do not invalidate an atomic state replacement."""
    with (
        patch("fix_die_repeat.sequencer_state.os.open", return_value=7),
        patch("fix_die_repeat.sequencer_state.os.fsync", side_effect=OSError("unsupported")),
        patch("fix_die_repeat.sequencer_state.os.close") as close,
    ):
        sequencer_state._sync_directory(tmp_path)

    close.assert_called_once_with(7)


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"state_schema_version": 999}',
    ],
)
def test_read_state_rejects_invalid_payloads(tmp_path: Path, payload: str) -> None:
    """State must remain an object with the supported schema version."""
    path = tmp_path / "state.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(StateError):
        read_state(path)


def test_read_state_wraps_invalid_utf8(tmp_path: Path) -> None:
    """State decoding errors use the module error contract."""
    path = tmp_path / "state.json"
    path.write_bytes(b"\xff")

    with pytest.raises(StateError, match="Cannot read sequencer state"):
        read_state(path)


@pytest.mark.parametrize("run_id", ["", "../escape", "with space"])
def test_run_paths_rejects_invalid_run_ids(tmp_path: Path, run_id: str) -> None:
    """Run IDs cannot become uncontrolled path components."""
    repository = RepositoryInfo(root=tmp_path, common_dir=tmp_path / ".git", key="repo")

    with pytest.raises(StateError, match="Invalid run ID"):
        run_paths(tmp_path / "home", repository, run_id)
