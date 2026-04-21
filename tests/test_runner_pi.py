"""Tests for runner pi interactions and setup."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fix_die_repeat.config import Paths
from fix_die_repeat.pi_bridge import PiBridge, PiBridgeError, PromptOverrides
from fix_die_repeat.runner import PiRunner
from fix_die_repeat.utils import get_git_revision_hash

# Sample timeout overrides for the settings-plumbing test. Deliberately distinct
# from the production defaults (120s / 3600s) so a failure clearly points at
# the forwarding logic rather than the defaults.
SAMPLE_IDLE_TIMEOUT_S = 42.0
SAMPLE_HARD_TIMEOUT_S = 900.0


class TestRunPi:
    """Tests for run_pi and run_pi_safe routed through the pi-bridge."""

    def _build_runner(self, tmp_path: Path) -> tuple[PiRunner, MagicMock]:
        """Construct a PiRunner with a mocked bridge for unit testing."""
        settings = MagicMock()
        paths = MagicMock()
        paths.project_root = tmp_path
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]

        bridge = MagicMock(spec=PiBridge)
        runner._bridge = bridge
        return runner, bridge

    def test_run_pi_writes_log(self, tmp_path: Path) -> None:
        """run_pi writes command + stdout/stderr to pi.log via the bridge path."""
        runner, bridge = self._build_runner(tmp_path)
        bridge.prompt.return_value = (0, "hello", "warn")

        returncode, stdout, stderr = runner.run_pi("-p", "hello")

        assert returncode == 0
        assert stdout == "hello"
        assert stderr == "warn"
        log_content = runner.paths.pi_log.read_text()
        assert "Command: pi -p hello" in log_content
        assert "STDOUT:\nhello" in log_content
        assert "STDERR:\nwarn" in log_content
        bridge.prompt.assert_called_once()

    def test_run_pi_logs_error_on_failure(self, tmp_path: Path) -> None:
        """run_pi logs an error when the bridge returns a non-zero exit code."""
        runner, bridge = self._build_runner(tmp_path)
        bridge.prompt.return_value = (1, "", "boom")

        runner.run_pi("-p", "boom")

        runner.logger.error.assert_any_call("pi exited with code %s", 1)  # type: ignore[attr-defined]

    def test_run_pi_translates_tools_flag(self, tmp_path: Path) -> None:
        """run_pi extracts --tools csv and forwards it as a per-prompt override."""
        runner, bridge = self._build_runner(tmp_path)
        bridge.prompt.return_value = (0, "", "")

        runner.run_pi("-p", "--tools", "read,grep,ls", "hello")

        bridge.prompt.assert_called_once()
        _args, kwargs = bridge.prompt.call_args
        overrides = kwargs["overrides"]
        assert isinstance(overrides, PromptOverrides)
        assert overrides.tools == ["read", "grep", "ls"]

    def test_run_pi_model_override_is_one_shot(self, tmp_path: Path) -> None:
        """--model translates to a per-prompt override, not a sticky set_model call."""
        runner, bridge = self._build_runner(tmp_path)
        bridge.prompt.return_value = (0, "", "")

        runner.run_pi("-p", "--model", "anthropic/claude-sonnet-4-5", "hello")

        bridge.set_model.assert_not_called()
        _args, kwargs = bridge.prompt.call_args
        overrides = kwargs["overrides"]
        assert isinstance(overrides, PromptOverrides)
        assert overrides.provider == "anthropic"
        assert overrides.model == "claude-sonnet-4-5"

    def test_run_pi_forwards_idle_and_hard_timeouts_from_settings(self, tmp_path: Path) -> None:
        """Settings' idle/hard timeouts are threaded through to PiBridge.prompt."""
        runner, bridge = self._build_runner(tmp_path)
        runner.settings.pi_prompt_idle_timeout_s = SAMPLE_IDLE_TIMEOUT_S
        runner.settings.pi_prompt_hard_timeout_s = SAMPLE_HARD_TIMEOUT_S
        bridge.prompt.return_value = (0, "", "")

        runner.run_pi("-p", "hello")

        _args, kwargs = bridge.prompt.call_args
        assert kwargs["idle_timeout_s"] == SAMPLE_IDLE_TIMEOUT_S
        assert kwargs["hard_timeout_s"] == SAMPLE_HARD_TIMEOUT_S

    def test_run_pi_forwards_progress_callback(self, tmp_path: Path) -> None:
        """run_pi supplies an on_event callback so bridge progress can be logged."""
        runner, bridge = self._build_runner(tmp_path)
        bridge.prompt.return_value = (0, "", "")

        runner.run_pi("-p", "hello")

        _args, kwargs = bridge.prompt.call_args
        assert callable(kwargs["on_event"])

    def test_log_bridge_event_logs_tool_execution_start_only(self, tmp_path: Path) -> None:
        """Progress log fires on tool_execution_start, not on deltas or end events."""
        runner, _bridge = self._build_runner(tmp_path)
        runner.logger = MagicMock()

        runner._log_bridge_event(
            {
                "type": "tool_execution_start",
                "toolName": "read",
                "args": {"filePath": "fix_die_repeat/runner.py"},
            }
        )
        runner._log_bridge_event({"type": "tool_execution_end", "toolName": "read"})
        runner._log_bridge_event({"type": "text_delta", "delta": "hi"})
        runner._log_bridge_event({"type": "thinking_delta", "delta": "pondering"})

        info_calls = runner.logger.info.call_args_list  # type: ignore[attr-defined]
        assert len(info_calls) == 1
        rendered = info_calls[0].args[0] % info_calls[0].args[1:]
        assert "pi: read" in rendered
        assert "fix_die_repeat/runner.py" in rendered

    def test_run_pi_embeds_at_file_contents(self, tmp_path: Path) -> None:
        """run_pi inlines @file contents so pi's CLI @-syntax is preserved."""
        runner, bridge = self._build_runner(tmp_path)
        runner.paths.project_root = tmp_path  # real path for _safe_relative
        attached = tmp_path / "note.md"
        attached.write_text("hello from the attached file")
        bridge.prompt.return_value = (0, "", "")

        runner.run_pi("-p", f"@{attached}", "review this")

        args, _kwargs = bridge.prompt.call_args
        message = args[0]
        assert "hello from the attached file" in message
        assert "Attached:" in message
        assert message.endswith("review this")

    def test_run_pi_embeds_non_utf8_attachment(self, tmp_path: Path) -> None:
        """Binary/non-UTF8 attachments become replacement chars, not a crash."""
        runner, bridge = self._build_runner(tmp_path)
        runner.paths.project_root = tmp_path
        attached = tmp_path / "binary.bin"
        attached.write_bytes(b"valid ascii \xff\xfe invalid utf8")
        bridge.prompt.return_value = (0, "", "")

        runner.run_pi("-p", f"@{attached}", "review this")

        args, _kwargs = bridge.prompt.call_args
        message = args[0]
        assert "valid ascii" in message
        assert "Attached:" in message
        assert message.endswith("review this")

    def test_run_pi_slash_command_is_skipped(self, tmp_path: Path) -> None:
        """Legacy pi slash-commands (e.g. /model-skip) no-op through the bridge."""
        runner, bridge = self._build_runner(tmp_path)

        returncode, stdout, stderr = runner.run_pi("-p", "/model-skip")

        assert returncode == 0
        assert stdout == ""
        assert stderr == ""
        bridge.prompt.assert_not_called()
        runner.logger.warning.assert_called()  # type: ignore[attr-defined]

    def test_run_pi_handles_bridge_error(self, tmp_path: Path) -> None:
        """run_pi returns a non-zero tuple when the bridge raises."""
        runner, bridge = self._build_runner(tmp_path)
        bridge.prompt.side_effect = PiBridgeError("kaboom")

        returncode, stdout, stderr = runner.run_pi("-p", "hello")

        assert returncode == 1
        assert stdout == ""
        assert "kaboom" in stderr

    def test_run_pi_without_bridge_fails_gracefully(self, tmp_path: Path) -> None:
        """run_pi returns (1, '', ...) when called without a live bridge."""
        runner, _bridge = self._build_runner(tmp_path)
        runner._bridge = None

        returncode, stdout, _stderr = runner.run_pi("-p", "hello")

        assert returncode == 1
        assert stdout == ""

    def test_run_pi_safe_capacity_error_warns(self, tmp_path: Path) -> None:
        """run_pi_safe logs a warning on 503 but no longer auto-skips the model."""
        settings = MagicMock()
        paths = MagicMock()
        paths.pi_log = tmp_path / "pi.log"
        paths.pi_log.write_text("503 No capacity")

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.run_pi = MagicMock(  # type: ignore[method-assign]
            side_effect=[(1, "", ""), (0, "", "")],
        )

        returncode, _stdout, _stderr = runner.run_pi_safe("-p", "fix")

        assert returncode == 0
        # Exactly two run_pi calls: original, then one retry (no /model-skip detour).
        expected_call_count = 2  # initial + one retry
        assert len(runner.run_pi.call_args_list) == expected_call_count
        assert runner.logger.warning.called

    def test_run_pi_safe_long_context_error(self, tmp_path: Path) -> None:
        """run_pi_safe still triggers emergency_compact on 429 long-context errors."""
        settings = MagicMock()
        paths = MagicMock()
        paths.pi_log = tmp_path / "pi.log"
        paths.pi_log.write_text("429 long context")

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.emergency_compact = MagicMock()  # type: ignore[method-assign]
        runner.run_pi = MagicMock(  # type: ignore[method-assign]
            side_effect=[(1, "", ""), (0, "", "")],
        )

        returncode, _stdout, _stderr = runner.run_pi_safe("-p", "fix")

        assert returncode == 0
        runner.emergency_compact.assert_called_once()


class TestParsePiArgvFailFast:
    """Malformed argv for value-taking flags must match legacy pi fail-fast."""

    def test_tools_without_value_fails(self, tmp_path: Path) -> None:
        """run_pi returns (1, '', ...) when --tools is at argv end with no value."""
        settings = MagicMock()
        paths = MagicMock()
        paths.project_root = tmp_path
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]
        bridge = MagicMock(spec=PiBridge)
        runner._bridge = bridge

        returncode, stdout, stderr = runner.run_pi("-p", "--tools")

        assert returncode == 1
        assert stdout == ""
        assert "--tools" in stderr
        bridge.prompt.assert_not_called()

    def test_model_without_value_fails(self, tmp_path: Path) -> None:
        """run_pi returns (1, '', ...) when --model is at argv end with no value."""
        settings = MagicMock()
        paths = MagicMock()
        paths.project_root = tmp_path
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]
        bridge = MagicMock(spec=PiBridge)
        runner._bridge = bridge

        returncode, stdout, stderr = runner.run_pi("-p", "--model")

        assert returncode == 1
        assert stdout == ""
        assert "--model" in stderr
        bridge.prompt.assert_not_called()


class TestApplyModelOverrideErrorHandling:
    """Invalid --model values must not crash the run."""

    def test_invalid_model_override_returns_error_tuple(self, tmp_path: Path) -> None:
        """A bare model id in --model must not propagate ValueError out of run_pi."""
        settings = MagicMock()
        paths = MagicMock()
        paths.project_root = tmp_path
        paths.pi_log = tmp_path / "pi.log"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner.before_pi_call = MagicMock()  # type: ignore[method-assign]
        bridge = MagicMock(spec=PiBridge)
        runner._bridge = bridge

        returncode, stdout, stderr = runner.run_pi("-p", "--model", "bare-model", "hello")

        assert returncode == 1
        assert stdout == ""
        assert "provider/model-id" in stderr
        bridge.prompt.assert_not_called()

    def test_start_bridge_translates_invalid_model_to_bridge_error(self, tmp_path: Path) -> None:
        """Settings with a bare model id must surface a typed bridge error, not ValueError.

        The legacy pi subprocess path would have returned a non-zero tuple at
        call time. The bridge validates the model up front in ``_start_bridge``;
        we want a clear typed error (PiBridgeError) there rather than an
        uncaught ValueError traceback.
        """
        settings = MagicMock()
        settings.model = "bare-model"  # missing provider/ prefix
        paths = MagicMock()
        paths.project_root = tmp_path
        paths.bridge_source_dir = tmp_path / "bridge-src"
        paths.bridge_runtime_dir = tmp_path / "bridge-runtime"

        runner = PiRunner.__new__(PiRunner)
        runner.settings = settings
        runner.paths = paths
        runner.logger = MagicMock()
        runner._bridge = None

        with (
            patch(
                "fix_die_repeat.runner.ensure_bridge_installed",
                return_value=tmp_path / "bridge.js",
            ),
            pytest.raises(PiBridgeError, match="provider/model-id"),
        ):
            runner._start_bridge()


class TestSplitSettingsModel:
    """Tests for _split_settings_model argument validation."""

    def test_none_returns_none_pair(self) -> None:
        """Unset model leaves pi's SDK to pick defaults."""
        assert PiRunner._split_settings_model(None) == (None, None)
        assert PiRunner._split_settings_model("") == (None, None)

    def test_provider_slash_model_splits(self) -> None:
        """Well-formed provider/model-id is split on the first slash."""
        assert PiRunner._split_settings_model("anthropic/claude-sonnet-4-5") == (
            "anthropic",
            "claude-sonnet-4-5",
        )

    def test_bare_model_id_raises(self) -> None:
        """Bare model ids must be rejected — bridge requires both fields."""
        with pytest.raises(ValueError, match="provider/model-id"):
            PiRunner._split_settings_model("claude-sonnet-4-5")

    def test_slash_with_missing_side_raises(self) -> None:
        """Leading/trailing slash is not a valid provider/model pair."""
        with pytest.raises(ValueError, match="provider/model-id"):
            PiRunner._split_settings_model("/claude-sonnet-4-5")
        with pytest.raises(ValueError, match="provider/model-id"):
            PiRunner._split_settings_model("anthropic/")


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

        def fake_run_pi(*_args: str) -> tuple[int, str, str]:
            test_file.write_text("MODEL TEST OK")
            return (0, "", "")

        runner.run_pi = MagicMock(side_effect=fake_run_pi)  # type: ignore[method-assign]

        with pytest.raises(SystemExit) as excinfo:
            runner.test_model()

        assert excinfo.value.code == 0
        assert not test_file.exists()
        # The test model must be forwarded as a --model override so the bridge
        # exercises the model under test, not the settings default.
        assert runner.run_pi.call_args.args[:3] == ("-p", "--model", "test-model")

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

        def fake_run_pi(*_args: str) -> tuple[int, str, str]:
            test_file.write_text("File.write('oops')")
            return (0, "", "")

        runner.run_pi = MagicMock(side_effect=fake_run_pi)  # type: ignore[method-assign]

        with pytest.raises(SystemExit) as excinfo:
            runner.test_model()

        assert excinfo.value.code == 1
        assert runner.logger.warning.called
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
