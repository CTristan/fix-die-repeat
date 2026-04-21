"""Python client for the pi-bridge Node.js sidecar.

The bridge is spawned as a long-lived subprocess for the duration of a
:class:`~fix_die_repeat.runner.PiRunner` invocation. Communication is
newline-delimited JSON over stdin/stdout (diagnostics on stderr).

This module hides the JSONL protocol behind an API that returns
``(returncode, stdout, stderr)`` tuples, matching the shape the runner
and managers already consume from the old ``run_pi`` subprocess path.
"""

from __future__ import annotations

import contextlib
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    import logging
    from types import TracebackType

DEFAULT_PROMPT_TIMEOUT_MS = 300_000
DEFAULT_INIT_TIMEOUT_S = 15.0
DEFAULT_SHUTDOWN_TIMEOUT_S = 5.0
_READER_JOIN_TIMEOUT_S = 2.0


class PiBridgeError(RuntimeError):
    """Raised when the bridge subprocess can't be started or driven."""


@dataclass
class PiBridgeConfig:
    """Configuration for one bridge session.

    Mirrors the ``init`` command payload. When ``provider`` and ``model`` are
    both ``None``, the bridge lets pi's SDK pick the default from the user's
    pi settings (``~/.pi/``). ``provider`` and ``model`` must be set together
    or both left unset — the bridge rejects the asymmetric case.
    """

    provider: str | None = None
    model: str | None = None
    tools: tuple[str, ...] = ("read", "bash", "edit", "write")
    working_dir: Path = field(default_factory=Path.cwd)
    thinking: str = "medium"

    def to_init_command(self) -> dict[str, object]:
        """Serialize this config into the bridge's ``init`` JSON payload."""
        if (self.provider is None) != (self.model is None):
            msg = (
                "PiBridgeConfig requires provider and model to be set together "
                f"or both left unset (got provider={self.provider!r}, model={self.model!r})."
            )
            raise PiBridgeError(msg)
        cmd: dict[str, object] = {
            "type": "init",
            "tools": list(self.tools),
            "workingDir": str(self.working_dir),
            "thinking": self.thinking,
        }
        if self.provider is not None:
            cmd["provider"] = self.provider
        if self.model is not None:
            cmd["model"] = self.model
        return cmd


class PiBridge:
    """Long-lived wrapper around a ``node bridge.js`` subprocess.

    Typical usage::

        with PiBridge(config, bridge_script=path, logger=log) as bridge:
            rc, stdout, stderr = bridge.prompt("Review the diff...")
            if rc == 0:
                ...

    Re-entrancy is not supported — one prompt at a time.
    """

    def __init__(
        self,
        config: PiBridgeConfig,
        *,
        bridge_script: Path,
        logger: logging.Logger,
        node_executable: str | None = None,
    ) -> None:
        """Store config and prepare subprocess state; spawn happens in ``__enter__``."""
        self._config = config
        self._bridge_script = bridge_script
        self._logger = logger
        self._node = node_executable or shutil.which("node") or "node"
        self._proc: subprocess.Popen[str] | None = None
        self._events: queue.Queue[dict[str, object] | None] = queue.Queue()
        self._stderr_buf: list[str] = []
        self._stderr_lock = threading.Lock()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    # --- Context manager ---

    def __enter__(self) -> Self:
        """Spawn the bridge subprocess and wait for the ``ready`` event."""
        self._spawn()
        self._send(self._config.to_init_command())
        self._await_event(
            expected_types={"ready"},
            error_types={"error"},
            timeout_s=DEFAULT_INIT_TIMEOUT_S,
            context="init",
        )
        self._logger.debug(
            "pi-bridge initialized (provider=%s model=%s)",
            self._config.provider,
            self._config.model,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Gracefully stop the bridge subprocess, killing on timeout if needed."""
        self._shutdown()

    # --- Public command API ---

    def prompt(
        self,
        message: str,
        timeout_ms: int | None = None,
        tools: list[str] | None = None,
    ) -> tuple[int, str, str]:
        """Run one agent turn with ``message``.

        Returns ``(returncode, stdout, stderr)`` where ``stdout`` is the agent's
        final assistant text, ``stderr`` is the bridge diagnostics for this
        turn, and ``returncode`` is 0 on ``agent_end``, 1 on ``error`` or timeout.
        Pass ``tools`` to override the init-time tool set for this turn only.
        """
        timeout_ms = timeout_ms if timeout_ms is not None else DEFAULT_PROMPT_TIMEOUT_MS
        self._reset_stderr_capture()
        command: dict[str, object] = {"type": "prompt", "message": message, "timeoutMs": timeout_ms}
        if tools is not None:
            command["tools"] = list(tools)
        self._send(command)
        try:
            event = self._await_event(
                expected_types={"agent_end"},
                error_types={"error"},
                timeout_s=(timeout_ms / 1000) + 10.0,
                context="prompt",
                drain_intermediate=True,
            )
        except PiBridgeError as err:
            return (1, "", self._drain_stderr() + f"\n{err}")

        final_text = str(event.get("finalText", ""))
        return (0, final_text, self._drain_stderr())

    def set_model(self, provider: str, model_id: str) -> None:
        """Swap the model used by subsequent prompts."""
        self._send({"type": "set_model", "provider": provider, "modelId": model_id})
        self._await_event(
            expected_types={"ready"},
            error_types={"error"},
            timeout_s=DEFAULT_INIT_TIMEOUT_S,
            context="set_model",
        )
        self._config.provider = provider
        self._config.model = model_id

    def compact(self) -> None:
        """Request emergency compaction.

        With fresh-session-per-prompt semantics this is a no-op on the bridge
        side, but the command is preserved so ``PiRunner.emergency_compact``
        keeps a stable API to call.
        """
        self._send({"type": "compact"})
        self._await_event(
            expected_types={"ready"},
            error_types={"error"},
            timeout_s=DEFAULT_INIT_TIMEOUT_S,
            context="compact",
        )

    def abort(self) -> None:
        """Signal the bridge to cancel the current prompt, if any.

        Fire-and-forget — does not wait for a response.
        """
        if self._proc is None or self._proc.poll() is not None:
            return
        try:
            self._send({"type": "abort"})
        except PiBridgeError:
            self._logger.warning("pi-bridge abort failed (process may already be gone)")

    # --- Subprocess management ---

    def _spawn(self) -> None:
        """Spawn the bridge subprocess and start the stdio reader threads."""
        if not self._bridge_script.exists():
            msg = (
                f"pi-bridge script not found at {self._bridge_script}. "
                "Did ensure_bridge_installed() run?"
            )
            raise PiBridgeError(msg)
        try:
            self._proc = subprocess.Popen(  # noqa: S603 — node executable path is trusted
                [self._node, str(self._bridge_script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                text=True,
                cwd=str(self._config.working_dir),
            )
        except FileNotFoundError as err:
            msg = (
                f"Could not launch node at {self._node}. "
                "fix-die-repeat requires Node.js >=20 on PATH for the pi bridge."
            )
            raise PiBridgeError(msg) from err

        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _shutdown(self) -> None:
        """Send shutdown, wait, kill if needed, and join reader threads."""
        if self._proc is None:
            return
        with contextlib.suppress(PiBridgeError):
            self._send({"type": "shutdown"})
        try:
            self._proc.wait(timeout=DEFAULT_SHUTDOWN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._logger.warning(
                "pi-bridge did not exit within %.1fs; killing", DEFAULT_SHUTDOWN_TIMEOUT_S
            )
            self._proc.kill()
            try:
                self._proc.wait(timeout=DEFAULT_SHUTDOWN_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                self._logger.exception("pi-bridge still alive after kill; giving up")
        if self._stdout_thread and self._stdout_thread.is_alive():
            self._stdout_thread.join(timeout=_READER_JOIN_TIMEOUT_S)
        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=_READER_JOIN_TIMEOUT_S)
        self._proc = None

    # --- Reader threads ---

    def _read_stdout(self) -> None:
        """Reader thread entry point for parsing bridge stdout into events."""
        if self._proc is None or self._proc.stdout is None:
            return
        for line in self._proc.stdout:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                self._logger.warning("pi-bridge emitted non-JSON on stdout: %s", stripped[:200])
                continue
            if not isinstance(event, dict):
                self._logger.warning("pi-bridge emitted non-object event: %s", stripped[:200])
                continue
            self._events.put(event)
        # EOF sentinel
        self._events.put(None)

    def _read_stderr(self) -> None:
        """Reader thread entry point forwarding bridge stderr to the logger."""
        if self._proc is None or self._proc.stderr is None:
            return
        for line in self._proc.stderr:
            stripped = line.rstrip("\n")
            if not stripped:
                continue
            with self._stderr_lock:
                self._stderr_buf.append(stripped)
            self._logger.debug("pi-bridge stderr: %s", stripped)

    # --- Protocol helpers ---

    def _send(self, command: dict[str, object]) -> None:
        """Write one JSON command to the bridge's stdin."""
        if self._proc is None or self._proc.stdin is None or self._proc.poll() is not None:
            msg = "pi-bridge process is not running"
            raise PiBridgeError(msg)
        payload = json.dumps(command) + "\n"
        try:
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as err:
            msg = f"pi-bridge stdin closed unexpectedly: {err}"
            raise PiBridgeError(msg) from err

    def _await_event(
        self,
        *,
        expected_types: set[str],
        error_types: set[str],
        timeout_s: float,
        context: str,
        drain_intermediate: bool = False,
    ) -> dict[str, object]:
        """Block until an event of ``expected_types`` arrives.

        Raises :class:`PiBridgeError` on ``error_types``, timeout, or EOF.
        When ``drain_intermediate`` is True, non-matching events are logged
        and discarded (used for ``prompt`` where we don't consume deltas).
        """
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                msg = f"pi-bridge {context} timed out after {timeout_s:.1f}s"
                raise PiBridgeError(msg)
            try:
                event = self._events.get(timeout=remaining)
            except queue.Empty as err:
                msg = f"pi-bridge {context} timed out after {timeout_s:.1f}s"
                raise PiBridgeError(msg) from err
            if event is None:
                exit_code = self._proc.poll() if self._proc else "?"
                msg = f"pi-bridge closed stdout during {context} (exit code {exit_code})"
                raise PiBridgeError(msg)
            event_type = event.get("type")
            if event_type in expected_types:
                return event
            if event_type in error_types:
                reason = event.get("reason", "unknown")
                detail = event.get("detail")
                msg = f"pi-bridge {context} failed: {reason} ({detail})"
                raise PiBridgeError(msg)
            if drain_intermediate:
                # intermediate events (text_delta, tool_execution_*, thinking_delta, etc.)
                # could be surfaced for live logging; for Phase 0 we just trace them.
                self._logger.debug("pi-bridge event during %s: %s", context, event_type)
                continue
            self._logger.warning("pi-bridge unexpected event during %s: %s", context, event_type)

    # --- stderr capture around one prompt ---

    def _reset_stderr_capture(self) -> None:
        with self._stderr_lock:
            self._stderr_buf.clear()

    def _drain_stderr(self) -> str:
        with self._stderr_lock:
            out = "\n".join(self._stderr_buf)
            self._stderr_buf.clear()
        return out


def _python_version_tuple() -> tuple[int, ...]:
    """Exposed for testability; unused at runtime."""
    return sys.version_info[:3]
