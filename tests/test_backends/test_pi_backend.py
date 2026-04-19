"""Tests for fix_die_repeat.backends.pi.PiBackend."""

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from fix_die_repeat.backends import (
    Backend,
    BackendRequest,
    BackendResult,
    PiBackend,
)

EXPECTED_RETRY_COUNT = 2


def _make_backend(tmp_path: Path, on_long_context: object = None) -> PiBackend:
    """Construct a PiBackend with MagicMock settings/paths pointed at tmp_path."""
    settings = MagicMock()
    settings.pi_sequential_delay_seconds = 0  # Tests run without sleeping.
    paths = MagicMock()
    paths.project_root = tmp_path
    paths.pi_log = tmp_path / "pi.log"
    logger = MagicMock()
    kwargs = {"on_long_context": on_long_context} if on_long_context is not None else {}
    return PiBackend(settings, paths, logger, **kwargs)  # type: ignore[arg-type]


class TestBackendRequestArgv:
    """Tests for translating BackendRequest into pi-shaped argv."""

    def test_prompt_only(self, tmp_path: Path) -> None:
        """Prompt-only request emits just ``-p <prompt>``."""
        backend = _make_backend(tmp_path)
        request = BackendRequest(prompt="do the thing")
        assert backend.build_argv(request) == ["-p", "do the thing"]

    def test_with_tools(self, tmp_path: Path) -> None:
        """Tools become a single ``--tools csv`` pair."""
        backend = _make_backend(tmp_path)
        request = BackendRequest(prompt="x", tools=("read", "write", "edit"))
        assert backend.build_argv(request) == ["-p", "--tools", "read,write,edit", "x"]

    def test_with_model(self, tmp_path: Path) -> None:
        """Model becomes ``--model <name>``."""
        backend = _make_backend(tmp_path)
        request = BackendRequest(prompt="x", model="claude-sonnet-4-6")
        assert backend.build_argv(request) == [
            "-p",
            "--model",
            "claude-sonnet-4-6",
            "x",
        ]

    def test_with_attachments(self, tmp_path: Path) -> None:
        """Attachments become ``@<path>`` positional args."""
        backend = _make_backend(tmp_path)
        request = BackendRequest(
            prompt="x",
            attachments=(Path("/a.txt"), Path("/b.txt")),
        )
        assert backend.build_argv(request) == ["-p", "@/a.txt", "@/b.txt", "x"]

    def test_full_request_preserves_pi_argv_order(self, tmp_path: Path) -> None:
        """Order must match today's inline pi calls: -p, --tools, --model, @files, prompt."""
        backend = _make_backend(tmp_path)
        request = BackendRequest(
            prompt="fix it",
            tools=("read", "edit", "write"),
            attachments=(Path("/x"), Path("/y")),
            model="claude-opus-4-7",
        )
        assert backend.build_argv(request) == [
            "-p",
            "--tools",
            "read,edit,write",
            "--model",
            "claude-opus-4-7",
            "@/x",
            "@/y",
            "fix it",
        ]


class TestPiBackendInvoke:
    """Tests for PiBackend.invoke()."""

    def test_invokes_pi_with_built_argv(self, tmp_path: Path) -> None:
        """invoke() shells out to ``pi`` with the translated argv in project_root."""
        backend = _make_backend(tmp_path)
        request = BackendRequest(prompt="hi", tools=("read",))

        with patch("fix_die_repeat.backends.pi.run_command") as mock_run:
            mock_run.return_value = (0, "hello", "warn")
            result = backend.invoke(request)

        mock_run.assert_called_once_with(
            ["pi", "-p", "--tools", "read", "hi"],
            cwd=tmp_path,
        )
        assert result == BackendResult(returncode=0, stdout="hello", stderr="warn")

    def test_invoke_writes_pi_log(self, tmp_path: Path) -> None:
        """invoke() appends command, exit code, stdout, and stderr to pi.log."""
        backend = _make_backend(tmp_path)
        with patch("fix_die_repeat.backends.pi.run_command") as mock_run:
            mock_run.return_value = (0, "stdout-text", "stderr-text")
            backend.invoke(BackendRequest(prompt="hello"))

        log = (tmp_path / "pi.log").read_text()
        assert "Command: pi -p hello" in log
        assert "Exit code: 0" in log
        assert "STDOUT:\nstdout-text" in log
        assert "STDERR:\nstderr-text" in log

    def test_invoke_logs_error_on_nonzero(self, tmp_path: Path) -> None:
        """Non-zero pi exit is logged via logger.error."""
        backend = _make_backend(tmp_path)
        with patch("fix_die_repeat.backends.pi.run_command") as mock_run:
            mock_run.return_value = (7, "", "boom")
            backend.invoke(BackendRequest(prompt="x"))

        cast("MagicMock", backend.logger).error.assert_any_call("pi exited with code %s", 7)

    def test_invoke_applies_sequential_delay(self, tmp_path: Path) -> None:
        """Second and subsequent invocations sleep for pi_sequential_delay_seconds."""
        backend = _make_backend(tmp_path)
        backend.settings.pi_sequential_delay_seconds = 3
        with (
            patch("fix_die_repeat.backends.pi.run_command", return_value=(0, "", "")),
            patch("fix_die_repeat.backends.pi.time.sleep") as mock_sleep,
        ):
            backend.invoke(BackendRequest(prompt="one"))
            backend.invoke(BackendRequest(prompt="two"))
            backend.invoke(BackendRequest(prompt="three"))

        # First call: no delay; subsequent two: one sleep each.
        assert mock_sleep.call_args_list == [((3,), {}), ((3,), {})]


class TestPiBackendInvokeRaw:
    """Tests for PiBackend.invoke_raw() (legacy *args path used by PiRunner shims)."""

    def test_invoke_raw_passes_args_through(self, tmp_path: Path) -> None:
        """invoke_raw prepends ``pi`` and forwards args verbatim."""
        backend = _make_backend(tmp_path)
        with patch("fix_die_repeat.backends.pi.run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            result = backend.invoke_raw("-p", "--tools", "read", "hello")

        mock_run.assert_called_once_with(
            ["pi", "-p", "--tools", "read", "hello"],
            cwd=tmp_path,
        )
        assert result.returncode == 0


class TestPiBackendInvokeSafe:
    """Tests for PiBackend.invoke_safe() structured retry path."""

    def test_invoke_safe_happy_path_returns_first_result(self, tmp_path: Path) -> None:
        """When invoke succeeds, invoke_safe returns it without retry."""
        backend = _make_backend(tmp_path)
        backend.invoke = MagicMock(  # type: ignore[method-assign]
            return_value=BackendResult(0, "ok", ""),
        )

        result = backend.invoke_safe(BackendRequest(prompt="x"))

        assert result == BackendResult(0, "ok", "")
        backend.invoke.assert_called_once()

    def test_invoke_safe_capacity_error_skips_model_then_retries(
        self,
        tmp_path: Path,
    ) -> None:
        """A 503 in pi.log triggers ``/model-skip`` via invoke_raw, then retry."""
        backend = _make_backend(tmp_path)
        (tmp_path / "pi.log").write_text("503 No capacity")
        backend.invoke = MagicMock(  # type: ignore[method-assign]
            side_effect=[
                BackendResult(1, "", ""),  # first invocation fails
                BackendResult(0, "", ""),  # retry succeeds
            ],
        )
        backend.invoke_raw = MagicMock(  # type: ignore[method-assign]
            return_value=BackendResult(0, "", ""),
        )

        result = backend.invoke_safe(BackendRequest(prompt="fix"))

        assert result.returncode == 0
        backend.invoke_raw.assert_called_once_with("-p", "/model-skip")

    def test_invoke_safe_long_context_triggers_on_long_context_callback(
        self,
        tmp_path: Path,
    ) -> None:
        """A 429 + long context in pi.log fires the injected on_long_context callback."""
        on_long_context = MagicMock()
        backend = _make_backend(tmp_path, on_long_context=on_long_context)
        (tmp_path / "pi.log").write_text("429 long context")
        backend.invoke = MagicMock(  # type: ignore[method-assign]
            side_effect=[
                BackendResult(1, "", ""),
                BackendResult(0, "", ""),
            ],
        )

        result = backend.invoke_safe(BackendRequest(prompt="fix"))

        assert result.returncode == 0
        on_long_context.assert_called_once()

    def test_invoke_safe_generic_failure_retries_once(self, tmp_path: Path) -> None:
        """No 503/429 detected → retry once anyway, matching run_pi_safe today."""
        backend = _make_backend(tmp_path)
        (tmp_path / "pi.log").write_text("some other error")
        backend.invoke = MagicMock(  # type: ignore[method-assign]
            side_effect=[
                BackendResult(2, "", ""),
                BackendResult(0, "", ""),
            ],
        )

        result = backend.invoke_safe(BackendRequest(prompt="fix"))

        assert result.returncode == 0
        assert backend.invoke.call_count == EXPECTED_RETRY_COUNT


class TestPiBackendInvokeRawSafe:
    """Tests for PiBackend.invoke_raw_safe() — the legacy-argv safe path."""

    def test_invoke_raw_safe_retries_once_on_failure(self, tmp_path: Path) -> None:
        """invoke_raw_safe reuses the same retry semantics as invoke_safe."""
        backend = _make_backend(tmp_path)
        (tmp_path / "pi.log").write_text("generic failure")
        backend.invoke_raw = MagicMock(  # type: ignore[method-assign]
            side_effect=[
                BackendResult(1, "", ""),
                BackendResult(0, "", ""),
            ],
        )

        result = backend.invoke_raw_safe("-p", "hello")

        assert result.returncode == 0
        assert backend.invoke_raw.call_count == EXPECTED_RETRY_COUNT


class TestBackendProtocol:
    """Confirm PiBackend satisfies the structural Backend protocol."""

    def test_pi_backend_is_a_backend(self, tmp_path: Path) -> None:
        """PiBackend is assignable to the Backend protocol."""
        backend = _make_backend(tmp_path)
        assert isinstance(backend, Backend)
