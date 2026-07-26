"""Tests for sequencer locking and state persistence."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from fix_die_repeat.sequencer_state import SequencerLock, StateError, read_state, write_state


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


def test_write_state_wraps_directory_creation_failure(tmp_path: Path) -> None:
    """State directory failures preserve the module error contract."""
    path = tmp_path / "state" / "state.json"

    with (
        patch.object(Path, "mkdir", side_effect=OSError("denied")),
        pytest.raises(StateError, match="Cannot write sequencer state"),
    ):
        write_state(path, {"state_schema_version": 1})


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


def test_read_state_uses_utf8_encoding(tmp_path: Path) -> None:
    """State decoding matches the writer's explicit UTF-8 encoding."""
    state = {"state_schema_version": 1, "message": "résumé"}
    path = tmp_path / "state.json"

    with patch.object(Path, "read_text", return_value=json.dumps(state)) as read_text:
        assert read_state(path) == state

    read_text.assert_called_once_with(encoding="utf-8")
