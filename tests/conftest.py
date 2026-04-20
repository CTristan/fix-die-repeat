"""Shared pytest fixtures for fix-die-repeat tests."""

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

FAKE_TEMPLATE_CONTEXT: dict[str, str] = {
    "fdr_dir_path": "/fake/fdr/repos/proj-deadbeef",
    "review_history_path": "/fake/fdr/repos/proj-deadbeef/review.md",
    "review_current_path": "/fake/fdr/repos/proj-deadbeef/review_current.md",
    "build_history_path": "/fake/fdr/repos/proj-deadbeef/build_history.md",
    "checks_log_path": "/fake/fdr/repos/proj-deadbeef/checks.log",
    "checks_filtered_log_path": "/fake/fdr/repos/proj-deadbeef/checks_filtered.log",
    "diff_file_path": "/fake/fdr/repos/proj-deadbeef/changes.diff",
    "resolved_threads_path": "/fake/fdr/repos/proj-deadbeef/.resolved_threads",
    "config_file_path": "/fake/fdr/repos/proj-deadbeef/config",
}


@pytest.fixture(autouse=True)
def _isolated_fdr_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point FDR_HOME at a tmp dir so tests never touch the real ~/.fix-die-repeat."""
    fdr_home = tmp_path / "fdr_home"
    monkeypatch.setenv("FDR_HOME", str(fdr_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # Pre-commit injects GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE into the hook env.
    # Tests that `git init` a tmp dir or construct `Paths()` must not inherit that
    # context or git operates on the parent repo instead of the test's sandbox.
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    return fdr_home


@pytest.fixture(autouse=True)
def _silence_runner_side_effects() -> Iterator[None]:
    """Stub out audible and network side effects triggered by runner completion.

    Patches the names as imported by ``fix_die_repeat.runner`` so tests that
    go all the way through a completion path don't play sounds or hit ntfy.
    Tests in ``test_utils.py`` exercising the real functions import from
    ``fix_die_repeat.utils`` directly and are unaffected.
    """
    with (
        patch("fix_die_repeat.runner.play_completion_sound"),
        patch("fix_die_repeat.runner.send_ntfy_notification"),
    ):
        yield
