"""Tests for PiRunner setup paths that exercise pi through the Backend abstraction.

The per-call pi invocation behavior (argv, log writing, retry) lives in
:mod:`tests.test_backends.test_pi_backend`. This module covers the wider
``test_model`` / ``setup_run`` / ``check_oscillation`` PiRunner flows that
sit above the backend.
"""

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from fix_die_repeat.backends import BackendResult
from fix_die_repeat.config import Paths
from fix_die_repeat.runner import PiRunner
from fix_die_repeat.utils import get_git_revision_hash


def _make_runner_with_stub_backend() -> tuple[PiRunner, MagicMock]:
    """Build a PiRunner (via ``__new__``) with a MagicMock backend attached."""
    runner = PiRunner.__new__(PiRunner)
    runner.logger = MagicMock()
    backend = MagicMock()
    runner.backend = backend  # type: ignore[assignment]
    return runner, backend


class TestModelAndSetup:
    """Tests for test_model and setup logic."""

    def test_test_model_success(self, tmp_path: Path) -> None:
        """Test test_model exits successfully when model writes output."""
        settings = MagicMock()
        settings.test_model = "test-model"
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path

        runner, backend = _make_runner_with_stub_backend()
        runner.settings = settings
        runner.paths = paths

        test_file = tmp_path / ".model_test_result.txt"

        def fake_invoke(_request: object) -> BackendResult:
            test_file.write_text("MODEL TEST OK")
            return BackendResult(0, "", "")

        backend.invoke.side_effect = fake_invoke

        with pytest.raises(SystemExit) as excinfo:
            runner.test_model()

        assert excinfo.value.code == 0
        assert not test_file.exists()
        # Backend received a structured request with the test model.
        request = backend.invoke.call_args.args[0]
        assert request.model == "test-model"
        assert "MODEL TEST OK" in request.prompt

    def test_test_model_pseudocode_failure(self, tmp_path: Path) -> None:
        """Test test_model warns on pseudo-code and exits with failure."""
        settings = MagicMock()
        settings.test_model = "test-model"
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path

        runner, backend = _make_runner_with_stub_backend()
        runner.settings = settings
        runner.paths = paths

        test_file = tmp_path / ".model_test_result.txt"

        def fake_invoke(_request: object) -> BackendResult:
            test_file.write_text("File.write('oops')")
            return BackendResult(0, "", "")

        backend.invoke.side_effect = fake_invoke

        with pytest.raises(SystemExit) as excinfo:
            runner.test_model()

        assert excinfo.value.code == 1
        assert cast("MagicMock", runner.logger).warning.called
        assert not test_file.exists()

    def test_setup_run_archives_artifacts(self, tmp_path: Path) -> None:
        """Test setup_run archives existing artifacts and writes logs."""
        settings = MagicMock()
        settings.archive_artifacts = True
        settings.test_model = None
        settings.compact_artifacts = True

        paths = Paths(project_root=tmp_path)
        paths.ensure_fdr_dir()
        existing_file = paths.fdr_dir / "old.log"
        existing_file.write_text("data")

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.session_log = paths.fdr_dir / "session.log"
        runner.test_model = MagicMock()  # type: ignore[method-assign]
        runner.check_and_compact_artifacts = MagicMock()  # type: ignore[method-assign]

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, "abc123\n", "")
            runner.setup_run()

        archive_dirs = list((paths.fdr_dir / "archive").glob("*"))
        assert archive_dirs, "Expected archive directory to be created"
        archived_files = list(archive_dirs[0].glob("old.log"))
        assert archived_files, "Expected old.log to be archived"
        assert paths.pi_log.exists()
        assert paths.checks_hash_file.exists()
        assert paths.start_sha_file.read_text() == "abc123"
        runner.test_model.assert_called_once()
        runner.check_and_compact_artifacts.assert_called_once()

    def test_check_oscillation_detects_repeat(self, tmp_path: Path) -> None:
        """Test check_oscillation returns warning for repeated hash."""
        settings = MagicMock()
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.checks_log = tmp_path / "checks.log"
        paths.checks_hash_file = tmp_path / "checks_hashes"
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.iteration = 2
        runner.logger = MagicMock()

        paths.checks_log.write_text("same output")
        current_hash = get_git_revision_hash(paths.checks_log)
        paths.checks_hash_file.write_text(f"{current_hash}:1\n")

        warning = runner.check_oscillation()

        assert warning is not None
        assert "iteration 1" in warning
