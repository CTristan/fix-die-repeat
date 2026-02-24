"""Tests for runner pi interactions and setup."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fix_die_repeat.config import Paths
from fix_die_repeat.runner import PiRunner
from fix_die_repeat.utils import get_git_revision_hash


class TestRunPi:
    """Tests for run_pi and run_pi_safe."""

    def test_run_pi_writes_log(self, tmp_path: Path) -> None:
        """Test run_pi writes stdout/stderr to pi.log."""
        settings = MagicMock()
        paths = MagicMock()
        paths.project_root = tmp_path
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (0, "hello", "warn")
            returncode, stdout, stderr = runner.run_pi("-p", "hello")

        assert returncode == 0
        assert stdout == "hello"
        assert stderr == "warn"
        log_content = paths.pi_log.read_text()
        assert "Command: pi -p hello" in log_content
        assert "STDOUT:\nhello" in log_content
        assert "STDERR:\nwarn" in log_content

    def test_run_pi_logs_error_on_failure(self, tmp_path: Path) -> None:
        """Test run_pi logs error when pi fails."""
        settings = MagicMock()
        paths = MagicMock()
        paths.project_root = tmp_path
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        with patch("fix_die_repeat.runner.run_command") as mock_run:
            mock_run.return_value = (1, "", "boom")
            runner.run_pi("-p", "boom")

        runner.logger.error.assert_any_call("pi exited with code %s", 1)

    def test_run_pi_safe_capacity_error(self, tmp_path: Path) -> None:
        """Test run_pi_safe triggers model skip on 503 errors."""
        settings = MagicMock()
        paths = MagicMock()
        paths.pi_log = tmp_path / "pi.log"
        paths.pi_log.write_text("503 No capacity")

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.run_pi = MagicMock(  # type: ignore[method-assign]
            side_effect=[(1, "", ""), (0, "", ""), (0, "", "")],
        )

        returncode, _stdout, _stderr = runner.run_pi_safe("-p", "fix")

        assert returncode == 0
        assert runner.run_pi.call_args_list[1].args == ("-p", "/model-skip")

    def test_run_pi_safe_long_context_error(self, tmp_path: Path) -> None:
        """Test run_pi_safe triggers emergency compaction on 429 errors."""
        settings = MagicMock()
        paths = MagicMock()
        paths.pi_log = tmp_path / "pi.log"
        paths.pi_log.write_text("429 long context")

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner._emergency_compact = MagicMock()  # type: ignore[method-assign]
        runner.run_pi = MagicMock(  # type: ignore[method-assign]
            side_effect=[(1, "", ""), (0, "", "")],
        )

        returncode, _stdout, _stderr = runner.run_pi_safe("-p", "fix")

        assert returncode == 0
        runner._emergency_compact.assert_called_once()


class TestModelAndSetup:
    """Tests for test_model and setup logic."""

    def test_test_model_success(self, tmp_path: Path) -> None:
        """Test test_model exits successfully when model writes output."""
        settings = MagicMock()
        settings.test_model = "test-model"
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        test_file = tmp_path / ".model_test_result.txt"

        def fake_run_command(*_args: str, **_kwargs: object) -> tuple[int, str, str]:
            test_file.write_text("MODEL TEST OK")
            return (0, "", "")

        with (
            patch("fix_die_repeat.runner.run_command", side_effect=fake_run_command),
            pytest.raises(SystemExit) as excinfo,
        ):
            runner.test_model()

        assert excinfo.value.code == 0
        assert not test_file.exists()

    def test_test_model_pseudocode_failure(self, tmp_path: Path) -> None:
        """Test test_model warns on pseudo-code and exits with failure."""
        settings = MagicMock()
        settings.test_model = "test-model"
        paths = MagicMock()
        paths.fdr_dir = tmp_path
        paths.project_root = tmp_path

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        test_file = tmp_path / ".model_test_result.txt"

        def fake_run_command(*_args: str, **_kwargs: object) -> tuple[int, str, str]:
            test_file.write_text("File.write('oops')")
            return (0, "", "")

        with (
            patch("fix_die_repeat.runner.run_command", side_effect=fake_run_command),
            pytest.raises(SystemExit) as excinfo,
        ):
            runner.test_model()

        assert excinfo.value.code == 1
        assert runner.logger.warning.called
        assert not test_file.exists()

    def test_setup_run_archives_artifacts(self, tmp_path: Path) -> None:
        """Test _setup_run archives existing artifacts and writes logs."""
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
            runner._setup_run()

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
