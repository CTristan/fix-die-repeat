"""Tests for sequencer locking and state persistence."""

from pathlib import Path
from unittest.mock import patch

import pytest

from fix_die_repeat.sequencer_state import SequencerLock, StateError, write_state


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
