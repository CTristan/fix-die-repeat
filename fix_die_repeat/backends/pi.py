"""PiBackend — the [pi](https://github.com/mariozechner/pi) CLI adapter."""

import logging
import shlex
import time
from collections.abc import Callable

from fix_die_repeat.backends.base import BackendRequest, BackendResult
from fix_die_repeat.config import Paths, Settings
from fix_die_repeat.utils import run_command


def _noop() -> None:
    pass


class PiBackend:
    """Drives the `pi` CLI for every fix/review/introspection invocation.

    Owns sequential-call delay bookkeeping, pi.log writing, and the pi-specific
    retry semantics (503 → `/model-skip`, 429 long-context → on_long_context).
    """

    def __init__(
        self,
        settings: Settings,
        paths: Paths,
        logger: logging.Logger,
        on_long_context: Callable[[], None] = _noop,
    ) -> None:
        """Construct a PiBackend.

        Args:
            settings: Runtime settings (reads ``pi_sequential_delay_seconds``).
            paths: Path container for ``pi_log`` and ``project_root``.
            logger: Session logger; errors and retry events are emitted here.
            on_long_context: Callback invoked on the 429 long-context path
                (typically ``ArtifactManager.emergency_compact``).

        """
        self.settings = settings
        self.paths = paths
        self.logger = logger
        self.on_long_context = on_long_context
        self._invocation_count = 0

    def build_argv(self, request: BackendRequest) -> list[str]:
        """Translate a BackendRequest into pi-shaped argv.

        Order matches every inline pi call in the pre-abstraction codebase:
        `-p`, `--tools csv`, `--model m`, `@files`, prompt-last.
        """
        argv: list[str] = ["-p"]
        if request.tools:
            argv += ["--tools", ",".join(request.tools)]
        if request.model:
            argv += ["--model", request.model]
        for attachment in request.attachments:
            argv.append(f"@{attachment}")
        argv.append(request.prompt)
        return argv

    def invoke(self, request: BackendRequest) -> BackendResult:
        """Run pi once for the given request, no retry."""
        return self._run(["pi", *self.build_argv(request)])

    def invoke_safe(self, request: BackendRequest) -> BackendResult:
        """Run pi with a single retry, handling capacity and long-context errors."""
        return self._retry(lambda: self.invoke(request))

    def _invoke_raw(self, *args: str) -> BackendResult:
        """Pi-internal escape hatch: run pi with a pre-built argv list.

        Used only by the ``/model-skip`` retry path inside :meth:`_retry`.
        External callers must use :meth:`invoke` / :meth:`invoke_safe` with
        :class:`BackendRequest`.
        """
        return self._run(["pi", *args])

    def _retry(self, call: Callable[[], BackendResult]) -> BackendResult:
        result = call()
        if result.returncode == 0:
            return result

        # Scan only the failing call's own output. pi.log is append-only across
        # invocations within a run, so reading it would let stale 503/429 lines
        # from prior unrelated calls misroute the retry decision.
        failing_output = f"{result.stdout}\n{result.stderr}"

        if "503" in failing_output or "No capacity" in failing_output:
            self.logger.info(
                "Detected model capacity error (503). Skipping current model...",
            )
            self._invoke_raw("-p", "/model-skip")

        lowered = failing_output.lower()
        if "429" in lowered and "long context" in lowered:
            self.logger.info(
                "Detected long context rate limit (429). Forcing emergency compaction...",
            )
            self.on_long_context()
            self.logger.info("Emergency compaction complete. Retrying...")

        self.logger.info("pi failed (exit %s). Retrying once...", result.returncode)
        return call()

    def _before_invoke(self) -> None:
        if self._invocation_count > 0:
            time.sleep(self.settings.pi_sequential_delay_seconds)
        self._invocation_count += 1

    def _run(self, cmd_args: list[str]) -> BackendResult:
        self._before_invoke()
        returncode, stdout, stderr = run_command(cmd_args, cwd=self.paths.project_root)

        if self.paths.pi_log:
            with self.paths.pi_log.open("a", encoding="utf-8") as f:
                f.write(f"Command: {shlex.join(cmd_args)}\n")
                f.write(f"Exit code: {returncode}\n")
                if stdout:
                    f.write(f"STDOUT:\n{stdout}\n")
                if stderr:
                    f.write(f"STDERR:\n{stderr}\n")
                f.write("\n")

        if returncode != 0:
            self.logger.error("pi exited with code %s", returncode)
            if self.paths.pi_log:
                self.logger.error("pi output logged to: %s", self.paths.pi_log)

        return BackendResult(returncode=returncode, stdout=stdout, stderr=stderr)


__all__ = ["PiBackend"]
