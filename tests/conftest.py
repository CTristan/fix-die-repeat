"""Shared pytest fixtures for fix-die-repeat tests."""

from pathlib import Path

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
    return fdr_home
