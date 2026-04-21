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
import math
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
    from collections.abc import Callable
    from types import TracebackType

# Idle timeout: a pi turn that emits no events for this long is considered hung.
# Pi normally emits tool_execution_start/end and text_delta every few seconds
# while working; 120s is comfortably above any realistic single-step gap.
DEFAULT_IDLE_TIMEOUT_S = 120.0
# Hard cap: bounds wall-clock even if pi keeps the idle timer alive with a
# pathological event storm. 60 min matches long-form review turns on big diffs.
DEFAULT_HARD_TIMEOUT_S = 3600.0
DEFAULT_INIT_TIMEOUT_S = 15.0
DEFAULT_SHUTDOWN_TIMEOUT_S = 5.0
_READER_JOIN_TIMEOUT_S = 2.0


def _coerce_positive_timeout(value: float | None, default: float) -> float:
    """Return ``value`` if positive and finite; otherwise fall back to ``default``.

    The Node bridge treats non-positive ``timeoutMs`` as "use 300_000ms default",
    so passing through a zero/negative/NaN value would let the two sides
    disagree about when to give up — the Python ``_await_event`` hard deadline
    would fire before the Node watchdog. Coerce at the boundary so the two
    sides share the same clock.
    """
    if value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if numeric <= 0.0 or math.isnan(numeric):
        return default
    return numeric


class PiBridgeError(RuntimeError):
    """Raised when the bridge subprocess can't be started or driven."""


@dataclass(frozen=True)
class _AwaitParams:
    """Grouped parameters for ``PiBridge._await_event``.

    Split into a dataclass so the underlying routine doesn't violate PLR0913
    (it needs both a timing model and a classification model for incoming
    events, which is intrinsically more than 5 unrelated arguments).
    """

    expected_types: frozenset[str]
    error_types: frozenset[str]
    idle_timeout_s: float
    context: str
    drain_intermediate: bool = False
    hard_timeout_s: float | None = None
    on_event: Callable[[dict[str, object]], None] | None = None


class _AwaitDeadline:
    """Idle + hard deadline bookkeeping for a single ``_await_event`` call.

    The idle deadline resets on every received event so long but active pi
    turns stay alive; the optional hard deadline caps total wall clock and
    only exists as a safety net for pathological event storms.
    """

    def __init__(self, idle_timeout_s: float, hard_timeout_s: float | None) -> None:
        """Start both deadlines from now."""
        now = time.monotonic()
        self._idle_timeout_s = idle_timeout_s
        self._hard_timeout_s = hard_timeout_s
        self._idle_deadline = now + idle_timeout_s
        self._hard_deadline = now + hard_timeout_s if hard_timeout_s is not None else None

    def reset_idle(self) -> None:
        """Move the idle deadline forward; hard deadline stays fixed."""
        self._idle_deadline = time.monotonic() + self._idle_timeout_s

    def next_wait(self, context: str) -> float:
        """Return how long to wait for the next event, raising if a deadline passed."""
        now = time.monotonic()
        if self._hard_deadline is not None and now >= self._hard_deadline:
            raise PiBridgeError(self._hard_message(context))
        wait = self._idle_deadline - now
        if self._hard_deadline is not None:
            wait = min(wait, self._hard_deadline - now)
        if wait <= 0:
            raise PiBridgeError(self._idle_message(context))
        return wait

    def classify_empty(self, context: str) -> PiBridgeError:
        """Pick the right error message when ``queue.get`` times out."""
        if self._hard_deadline is not None and time.monotonic() >= self._hard_deadline:
            return PiBridgeError(self._hard_message(context))
        return PiBridgeError(self._idle_message(context))

    def _idle_message(self, context: str) -> str:
        return f"pi-bridge {context} idle for more than {self._idle_timeout_s:.1f}s"

    def _hard_message(self, context: str) -> str:
        # hard_timeout_s is guaranteed non-None when this is called, but keep
        # the format defensive — tests that construct deadlines with None
        # shouldn't blow up on message rendering.
        hard = 0.0 if self._hard_timeout_s is None else self._hard_timeout_s
        return f"pi-bridge {context} exceeded hard timeout ({hard:.1f}s)"


@dataclass(frozen=True)
class PromptOverrides:
    """Per-prompt overrides of bridge init defaults.

    ``tools`` swaps the tool set for a single turn. ``provider`` + ``model``
    mirror the legacy ``pi -p --model X`` flag — they apply to that one
    prompt only and do not mutate the bridge's config for subsequent prompts.
    ``provider`` and ``model`` must be set together or both left unset.
    """

    tools: list[str] | None = None
    provider: str | None = None
    model: str | None = None


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
            _AwaitParams(
                expected_types=frozenset({"ready"}),
                error_types=frozenset({"error"}),
                idle_timeout_s=DEFAULT_INIT_TIMEOUT_S,
                context="init",
            )
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
        *,
        idle_timeout_s: float | None = None,
        hard_timeout_s: float | None = None,
        overrides: PromptOverrides | None = None,
        on_event: Callable[[dict[str, object]], None] | None = None,
    ) -> tuple[int, str, str]:
        """Run one agent turn with ``message``.

        Returns ``(returncode, stdout, stderr)`` where ``stdout`` is the agent's
        final assistant text, ``stderr`` is the bridge diagnostics for this
        turn, and ``returncode`` is 0 on ``agent_end``, 1 on ``error`` or timeout.

        Timeouts:
          * ``idle_timeout_s`` — fail if no event arrives for this long (hung pi).
          * ``hard_timeout_s`` — wall-clock cap on the whole turn; also forwarded
            to the bridge as ``timeoutMs`` so a genuinely runaway agent session
            is cleaned up at its source, not just in Python.

        Pass ``overrides`` to swap init defaults for this turn only (tool set,
        provider/model). Per-prompt overrides do not mutate the bridge's
        configured defaults for later prompts — use :meth:`set_model` for that.
        Pass ``on_event`` to observe intermediate events (tool_execution_*,
        text_delta, thinking_delta) as they arrive — useful for progress UI.
        """
        idle_timeout_s = _coerce_positive_timeout(idle_timeout_s, DEFAULT_IDLE_TIMEOUT_S)
        hard_timeout_s = _coerce_positive_timeout(hard_timeout_s, DEFAULT_HARD_TIMEOUT_S)
        self._reset_stderr_capture()
        command: dict[str, object] = {
            "type": "prompt",
            "message": message,
            "timeoutMs": int(hard_timeout_s * 1000),
        }
        if overrides is not None:
            if (overrides.provider is None) != (overrides.model is None):
                msg = (
                    "PromptOverrides requires provider and model to be set together "
                    f"or both left unset (got provider={overrides.provider!r}, "
                    f"model={overrides.model!r})."
                )
                raise PiBridgeError(msg)
            if overrides.tools is not None:
                command["tools"] = list(overrides.tools)
            if overrides.provider is not None:
                command["provider"] = overrides.provider
            if overrides.model is not None:
                command["modelId"] = overrides.model
        self._send(command)
        try:
            event = self._await_event(
                _AwaitParams(
                    expected_types=frozenset({"agent_end"}),
                    error_types=frozenset({"error"}),
                    idle_timeout_s=idle_timeout_s,
                    hard_timeout_s=hard_timeout_s + 10.0,
                    context="prompt",
                    drain_intermediate=True,
                    on_event=on_event,
                )
            )
        except PiBridgeError as err:
            return (1, "", self._drain_stderr() + f"\n{err}")

        final_text = str(event.get("finalText", ""))
        return (0, final_text, self._drain_stderr())

    def set_model(self, provider: str, model_id: str) -> None:
        """Swap the model used by subsequent prompts."""
        self._send({"type": "set_model", "provider": provider, "modelId": model_id})
        self._await_event(
            _AwaitParams(
                expected_types=frozenset({"ready"}),
                error_types=frozenset({"error"}),
                idle_timeout_s=DEFAULT_INIT_TIMEOUT_S,
                context="set_model",
            )
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
            _AwaitParams(
                expected_types=frozenset({"ready"}),
                error_types=frozenset({"error"}),
                idle_timeout_s=DEFAULT_INIT_TIMEOUT_S,
                context="compact",
            )
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

    def _await_event(self, params: _AwaitParams) -> dict[str, object]:
        """Block until an event of ``params.expected_types`` arrives.

        See :class:`_AwaitParams` for the full shape. The timing model lives
        in :class:`_AwaitDeadline`; this method is just the classifier
        dispatch around ``self._events.get``.
        """
        deadline = _AwaitDeadline(params.idle_timeout_s, params.hard_timeout_s)
        while True:
            try:
                event = self._events.get(timeout=deadline.next_wait(params.context))
            except queue.Empty as err:
                raise deadline.classify_empty(params.context) from err
            if event is None:
                exit_code = self._proc.poll() if self._proc else "?"
                msg = f"pi-bridge closed stdout during {params.context} (exit code {exit_code})"
                raise PiBridgeError(msg)
            deadline.reset_idle()
            event_type = str(event.get("type", ""))
            if event_type in params.expected_types:
                return event
            if event_type in params.error_types:
                reason = event.get("reason", "unknown")
                detail = event.get("detail")
                msg = f"pi-bridge {params.context} failed: {reason} ({detail})"
                raise PiBridgeError(msg)
            if params.drain_intermediate:
                self._handle_intermediate_event(event, event_type, params)
                continue
            self._logger.warning(
                "pi-bridge unexpected event during %s: %s", params.context, event_type
            )

    def _handle_intermediate_event(
        self,
        event: dict[str, object],
        event_type: str,
        params: _AwaitParams,
    ) -> None:
        """Trace an intermediate event and forward it to the optional observer."""
        self._logger.debug("pi-bridge event during %s: %s", params.context, event_type)
        if params.on_event is None:
            return
        try:
            params.on_event(event)
        except Exception:
            # A misbehaving observer must not abort the prompt.
            self._logger.exception("pi-bridge on_event callback raised during %s", params.context)

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
