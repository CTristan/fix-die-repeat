"""Tests for CLI module."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from fix_die_repeat import config, runner
from fix_die_repeat.cli import main

# Constants for CLI test values
TEST_MAX_ITERS = 5
KEYBOARD_INTERRUPT_EXIT_CODE = 130


class TestCliMain:
    """Tests for CLI main command."""

    def test_cli_help(self) -> None:
        """Test that CLI help works."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Automated check, review, and fix loop" in result.output
        assert "--check-cmd" in result.output
        assert "--max-iters" in result.output
        assert "--model" in result.output
        assert "--pr-review" in result.output
        assert "--test-model" in result.output

    def test_cli_version(self) -> None:
        """Test that CLI version works."""
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0

    def test_cli_missing_test_model_integration(self) -> None:
        """Test CLI behavior with test-model when no pi is available.

        This is an integration test that simulates the case where pi is not installed.
        The CLI should handle this gracefully or exit with a clear error.
        """
        runner = CliRunner()
        # We can't fully test the pi integration without mocking subprocess calls
        # but we can test that the CLI accepts the flag
        result = runner.invoke(main, ["--test-model", "test-model", "--help"])
        assert result.exit_code == 0
        assert "--test-model" in result.output

    def test_cli_invalid_check_cmd_integration(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test CLI with an invalid check command."""
        # Monkeypatch Paths to use tmp_path
        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)

        runner = CliRunner()

        # Test with a check command that doesn't exist
        result = runner.invoke(
            main,
            ["--check-cmd", "nonexistent-command-xyz-123"],
            catch_exceptions=False,
        )

        # The CLI will try to run the command, which will fail
        # The exact exit code depends on the system
        # This test verifies the CLI accepts the option and attempts to run
        assert "nonexistent-command-xyz-123" in result.output or result.exit_code != 0

    def test_cli_with_max_iters(self) -> None:
        """Test CLI with max-iters option."""
        runner = CliRunner()
        # We can't fully run the CLI without pi, but we can test option parsing
        result = runner.invoke(main, ["--help"])
        assert "--max-iters" in result.output
        assert "Maximum loop iterations" in result.output

    def test_cli_with_model(self) -> None:
        """Test CLI with model option."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--model" in result.output
        assert "Override model selection" in result.output

    def test_cli_with_pr_review(self) -> None:
        """Test CLI with pr-review flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--pr-review" in result.output
        assert "Enable PR review mode" in result.output

    def test_cli_with_archive_artifacts(self) -> None:
        """Test CLI with archive-artifacts flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--archive-artifacts" in result.output
        assert "Archive existing artifacts" in result.output

    def test_cli_with_no_compact(self) -> None:
        """Test CLI with no-compact flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--no-compact" in result.output
        assert "Skip automatic compaction" in result.output

    def test_cli_with_debug(self) -> None:
        """Test CLI with debug flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "--debug" in result.output
        assert "Enable debug mode" in result.output

    def test_cli_env_vars_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that environment variables work correctly."""
        # Set environment variables
        monkeypatch.setenv("FDR_CHECK_CMD", "make test")
        monkeypatch.setenv("FDR_MAX_ITERS", "5")
        monkeypatch.setenv("FDR_MODEL", "test-model")
        monkeypatch.setenv("FDR_PR_REVIEW", "1")
        monkeypatch.setenv("FDR_DEBUG", "1")

        # Create settings - should pick up env vars
        settings = config.Settings()
        assert settings.check_cmd == "make test"
        assert settings.max_iters == TEST_MAX_ITERS
        assert settings.model == "test-model"
        assert settings.pr_review
        assert settings.debug

    def test_cli_options_documented(self) -> None:
        """Test that CLI options are properly documented."""
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "check-cmd" in result.output
        assert "max-iters" in result.output
        assert "model" in result.output
        assert "pr-review" in result.output
        assert "test-model" in result.output


class TestCliOptions:
    """Tests for CLI option handling."""

    def test_check_cmd_short_option(self) -> None:
        """Test short option -c for check-cmd."""
        runner = CliRunner()
        result = runner.invoke(main, ["-c", "make test", "--help"])
        assert result.exit_code == 0

    def test_check_cmd_long_option(self) -> None:
        """Test long option --check-cmd."""
        runner = CliRunner()
        result = runner.invoke(main, ["--check-cmd", "make test", "--help"])
        assert result.exit_code == 0

    def test_max_iters_short_option(self) -> None:
        """Test short option -n for max-iters."""
        runner = CliRunner()
        result = runner.invoke(main, ["-n", "5", "--help"])
        assert result.exit_code == 0

    def test_max_iters_long_option(self) -> None:
        """Test long option --max-iters."""
        runner = CliRunner()
        result = runner.invoke(main, ["--max-iters", "5", "--help"])
        assert result.exit_code == 0

    def test_model_short_option(self) -> None:
        """Test short option -m for model."""
        runner = CliRunner()
        result = runner.invoke(main, ["-m", "test-model", "--help"])
        assert result.exit_code == 0

    def test_model_long_option(self) -> None:
        """Test long option --model."""
        runner = CliRunner()
        result = runner.invoke(main, ["--model", "test-model", "--help"])
        assert result.exit_code == 0

    def test_debug_short_option(self) -> None:
        """Test short option -d for debug."""
        runner = CliRunner()
        result = runner.invoke(main, ["-d", "--help"])
        assert result.exit_code == 0

    def test_debug_long_option(self) -> None:
        """Test long option --debug."""
        runner = CliRunner()
        result = runner.invoke(main, ["--debug", "--help"])
        assert result.exit_code == 0

    def test_multiple_options(self) -> None:
        """Test that multiple options can be combined."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "-c",
                "pytest",
                "-n",
                "5",
                "-m",
                "test-model",
                "--pr-review",
                "--debug",
                "--help",
            ],
        )
        assert result.exit_code == 0


class TestCliExceptions:
    """Tests for CLI exception handling."""

    def test_keyboard_interrupt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test CLI handles KeyboardInterrupt gracefully."""
        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)

        # Monkeypatch PiRunner.run to raise KeyboardInterrupt
        def interrupting_run(_self: object) -> int:
            raise KeyboardInterrupt

        monkeypatch.setattr(runner.PiRunner, "run", interrupting_run)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, catch_exceptions=False)

        # Should exit with code 130 (standard for KeyboardInterrupt)
        assert result.exit_code == KEYBOARD_INTERRUPT_EXIT_CODE
        assert "Interrupted" in result.output

    def test_generic_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test CLI handles generic exceptions."""
        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)

        # Monkeypatch PiRunner.run to raise a generic exception
        def failing_run(_self: object) -> int:
            raise RuntimeError

        monkeypatch.setattr(runner.PiRunner, "run", failing_run)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, catch_exceptions=False)

        # Should exit with code 1 for generic errors
        assert result.exit_code == 1
        assert "Unexpected error" in result.output

    def test_debug_mode_shows_traceback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that debug mode shows traceback for exceptions."""
        original_paths = config.Paths

        def mock_paths(_project_root: Path | None = None) -> config.Paths:
            return original_paths(project_root=tmp_path)

        monkeypatch.setattr(config, "Paths", mock_paths)

        # Monkeypatch PiRunner.run to raise a generic exception
        def failing_run(_self: object) -> int:
            raise RuntimeError

        monkeypatch.setattr(runner.PiRunner, "run", failing_run)

        cli_runner = CliRunner()
        result = cli_runner.invoke(main, ["--debug"], catch_exceptions=False)

        # Should show traceback in debug mode
        assert result.exit_code == 1
        assert "RuntimeError" in result.output
