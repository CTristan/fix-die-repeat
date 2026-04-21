"""Unit tests for the PiBridge Python client.

These tests mock ``subprocess.Popen`` so no Node.js or real bridge is spawned.
The ``FakePopen`` helper scripts stdout/stderr with pre-built JSONL event
streams and records whatever Python writes to stdin.
"""

from __future__ import annotations

import io
import json
import logging
import subprocess
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from fix_die_repeat.pi_bridge import (
    PiBridge,
    PiBridgeConfig,
    PiBridgeError,
    _AwaitParams,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path


class FakePopen:
    """Stand-in for ``subprocess.Popen`` driven by the tests."""

    def __init__(self, stdout_lines: Iterable[str], stderr_lines: Iterable[str] = ()) -> None:
        """Prepare scripted stdout/stderr streams and a writable stdin buffer."""
        self.stdin = io.StringIO()
        self.stdout = _BlockingIterable(list(stdout_lines))
        self.stderr = _BlockingIterable(list(stderr_lines))
        self.returncode: int | None = None
        self.killed = False
        self.waited_timeouts: list[float] = []

    def poll(self) -> int | None:
        """Mimic ``subprocess.Popen.poll`` for test-mock consumers."""
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """Record the wait timeout and mark the process finished."""
        if timeout is not None:
            self.waited_timeouts.append(timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        """Record that kill was called and set a sentinel exit code."""
        self.killed = True
        self.returncode = -9


class _BlockingIterable:
    """Iterable that yields prepared lines, then blocks on an Event for EOF.

    Mirrors how a real ``Popen.stdout`` blocks after the subprocess exits:
    iteration returns each buffered line, then waits for the process to close
    stdout (signaled here via ``close()``) before raising StopIteration.
    """

    def __init__(self, lines: list[str]) -> None:
        """Store the scripted lines and create a closed-flag threading event."""
        self._lines = lines
        self._closed = threading.Event()

    def __iter__(self) -> Iterator[str]:
        """Yield scripted lines, then block until ``close()`` fires."""
        yield from self._lines
        self._closed.wait(timeout=2.0)

    def close(self) -> None:
        """Unblock iteration so the reader thread sees EOF."""
        self._closed.set()


def _event(obj: object) -> str:
    return json.dumps(obj) + "\n"


# Sample timeout values used in plumbing tests. Picked to be obviously not the
# production defaults (120s idle / 3600s hard) so assertion failures are easy
# to read: if the test breaks, it's clear that the plumbing didn't forward
# these specific numbers.
SAMPLE_IDLE_S = 60.0
SAMPLE_HARD_S = 1800.0
SAMPLE_HARD_MS = int(SAMPLE_HARD_S * 1000)


def _logger() -> logging.Logger:
    lg = logging.getLogger("test-pi-bridge")
    lg.setLevel(logging.DEBUG)
    return lg


class TestPiBridgeConfigInitPayload:
    """Invariants for ``PiBridgeConfig.to_init_command``."""

    def test_both_unset_emits_no_provider_or_model(self, tmp_path: Path) -> None:
        """Pi's SDK picks defaults when neither field is sent."""
        payload = PiBridgeConfig(working_dir=tmp_path).to_init_command()
        assert "provider" not in payload
        assert "model" not in payload

    def test_both_set_emits_both_fields(self, tmp_path: Path) -> None:
        """Matched provider/model pair is forwarded verbatim."""
        payload = PiBridgeConfig(
            provider="anthropic",
            model="claude-sonnet-4-5",
            working_dir=tmp_path,
        ).to_init_command()
        assert payload["provider"] == "anthropic"
        assert payload["model"] == "claude-sonnet-4-5"

    def test_provider_only_raises(self, tmp_path: Path) -> None:
        """Asymmetric config is caught at serialize time, not at bridge init."""
        config = PiBridgeConfig(provider="anthropic", model=None, working_dir=tmp_path)
        with pytest.raises(PiBridgeError, match="together"):
            config.to_init_command()

    def test_model_only_raises(self, tmp_path: Path) -> None:
        """Asymmetric config is caught at serialize time, not at bridge init."""
        config = PiBridgeConfig(provider=None, model="claude-sonnet-4-5", working_dir=tmp_path)
        with pytest.raises(PiBridgeError, match="together"):
            config.to_init_command()


class TestPiBridgeLifecycle:
    """Context-manager lifecycle tests."""

    def test_enter_waits_for_ready(self, tmp_path: Path) -> None:
        """Bridge __enter__ sends init and receives ready before returning."""
        bridge_script = tmp_path / "bridge.js"
        bridge_script.write_text("// fake\n")
        fake = FakePopen([_event({"type": "ready"})])

        with patch("fix_die_repeat.pi_bridge.subprocess.Popen", return_value=fake):
            bridge = PiBridge(
                PiBridgeConfig(working_dir=tmp_path),
                bridge_script=bridge_script,
                logger=_logger(),
            )
            with bridge:
                pass

        # init command was framed to stdin
        stdin_content = fake.stdin.getvalue()
        assert '"type": "init"' in stdin_content
        # shutdown command was sent on __exit__
        assert '"type": "shutdown"' in stdin_content

    def test_enter_raises_when_script_missing(self, tmp_path: Path) -> None:
        """Bridge __enter__ raises PiBridgeError when bridge.js is missing."""
        bridge = PiBridge(
            PiBridgeConfig(working_dir=tmp_path),
            bridge_script=tmp_path / "missing.js",
            logger=_logger(),
        )
        with pytest.raises(PiBridgeError, match="not found"), bridge:
            pass

    def test_exit_kills_unresponsive_bridge(self, tmp_path: Path) -> None:
        """Bridge.__exit__ falls back to kill() when wait() times out."""
        bridge_script = tmp_path / "bridge.js"
        bridge_script.write_text("// fake\n")
        fake = FakePopen([_event({"type": "ready"})])

        def never_exits(timeout: float | None = None) -> int:
            """Mock ``wait`` that always raises, simulating a hung subprocess."""
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0.0)

        with patch("fix_die_repeat.pi_bridge.subprocess.Popen", return_value=fake):
            bridge = PiBridge(
                PiBridgeConfig(working_dir=tmp_path),
                bridge_script=bridge_script,
                logger=_logger(),
            )
            bridge.__enter__()
            fake.wait = never_exits  # type: ignore[assignment]
            fake.stdout.close()
            fake.stderr.close()
            bridge.__exit__(None, None, None)

        assert fake.killed


def _prepare_bridge(tmp_path: Path, events: list[dict[str, object]]) -> tuple[Path, FakePopen]:
    bridge_script = tmp_path / "bridge.js"
    bridge_script.write_text("// fake\n")
    fake = FakePopen([_event({"type": "ready"})] + [_event(e) for e in events])
    return bridge_script, fake


class TestPiBridgePrompt:
    """Prompt command behavior."""

    def test_prompt_returns_final_text_on_agent_end(self, tmp_path: Path) -> None:
        script, fake = _prepare_bridge(
            tmp_path,
            [
                {"type": "text_delta", "delta": "hi"},
                {"type": "agent_end", "finalText": "all done"},
            ],
        )
        with patch("fix_die_repeat.pi_bridge.subprocess.Popen", return_value=fake):
            with PiBridge(
                PiBridgeConfig(working_dir=tmp_path),
                bridge_script=script,
                logger=_logger(),
            ) as bridge:
                rc, out, _err = bridge.prompt("hello")
                fake.stdout.close()
                fake.stderr.close()

        assert rc == 0
        assert out == "all done"

    def test_prompt_returns_error_tuple_on_error_event(self, tmp_path: Path) -> None:
        script, fake = _prepare_bridge(
            tmp_path,
            [{"type": "error", "reason": "boom", "detail": "something bad"}],
        )
        with patch("fix_die_repeat.pi_bridge.subprocess.Popen", return_value=fake):
            with PiBridge(
                PiBridgeConfig(working_dir=tmp_path),
                bridge_script=script,
                logger=_logger(),
            ) as bridge:
                rc, out, err = bridge.prompt("hello")
                fake.stdout.close()
                fake.stderr.close()

        assert rc == 1
        assert out == ""
        assert "boom" in err

    def test_prompt_forwards_tools_override(self, tmp_path: Path) -> None:
        script, fake = _prepare_bridge(
            tmp_path,
            [{"type": "agent_end", "finalText": ""}],
        )
        with patch("fix_die_repeat.pi_bridge.subprocess.Popen", return_value=fake):
            with PiBridge(
                PiBridgeConfig(working_dir=tmp_path),
                bridge_script=script,
                logger=_logger(),
            ) as bridge:
                bridge.prompt("hello", tools=["read", "grep"])
                fake.stdout.close()
                fake.stderr.close()

        lines = [json.loads(ln) for ln in fake.stdin.getvalue().strip().splitlines()]
        prompt_cmd = next(cmd for cmd in lines if cmd.get("type") == "prompt")
        assert prompt_cmd["tools"] == ["read", "grep"]


class TestPiBridgeControlCommands:
    """set_model / compact / abort command framing."""

    def test_set_model_sends_command_and_updates_config(self, tmp_path: Path) -> None:
        script, fake = _prepare_bridge(tmp_path, [{"type": "ready"}])
        with patch("fix_die_repeat.pi_bridge.subprocess.Popen", return_value=fake):
            with PiBridge(
                PiBridgeConfig(working_dir=tmp_path),
                bridge_script=script,
                logger=_logger(),
            ) as bridge:
                bridge.set_model("anthropic", "claude-sonnet-4-5")
                fake.stdout.close()
                fake.stderr.close()

        lines = [json.loads(ln) for ln in fake.stdin.getvalue().strip().splitlines()]
        set_model_cmd = next(cmd for cmd in lines if cmd.get("type") == "set_model")
        assert set_model_cmd["provider"] == "anthropic"
        assert set_model_cmd["modelId"] == "claude-sonnet-4-5"

    def test_compact_sends_command(self, tmp_path: Path) -> None:
        script, fake = _prepare_bridge(tmp_path, [{"type": "ready"}])
        with patch("fix_die_repeat.pi_bridge.subprocess.Popen", return_value=fake):
            with PiBridge(
                PiBridgeConfig(working_dir=tmp_path),
                bridge_script=script,
                logger=_logger(),
            ) as bridge:
                bridge.compact()
                fake.stdout.close()
                fake.stderr.close()

        lines = [json.loads(ln) for ln in fake.stdin.getvalue().strip().splitlines()]
        assert any(cmd.get("type") == "compact" for cmd in lines)

    def test_abort_is_fire_and_forget(self, tmp_path: Path) -> None:
        script, fake = _prepare_bridge(tmp_path, [])
        with patch("fix_die_repeat.pi_bridge.subprocess.Popen", return_value=fake):
            with PiBridge(
                PiBridgeConfig(working_dir=tmp_path),
                bridge_script=script,
                logger=_logger(),
            ) as bridge:
                bridge.abort()
                fake.stdout.close()
                fake.stderr.close()

        lines = [json.loads(ln) for ln in fake.stdin.getvalue().strip().splitlines()]
        assert any(cmd.get("type") == "abort" for cmd in lines)


class TestPiBridgeIdleTimeout:
    """Idle-timeout semantics and hard-cap safety net.

    The old wall-clock ``timeout_s`` killed long-running prompts (large
    contextual reviews) that were actively emitting tool-execution events. The
    bridge now resets the deadline on every received event and relies on a
    separate ``hard_timeout_s`` safety net to bound runaway event storms.
    """

    def _make_bridge(self, tmp_path: Path) -> PiBridge:
        """Build a PiBridge without spawning a subprocess.

        ``_await_event`` only reads from ``self._events`` and touches
        ``self._proc`` on EOF, so tests that push events directly onto the
        queue don't need the full lifecycle.
        """
        bridge_script = tmp_path / "bridge.js"
        bridge_script.write_text("// fake\n")
        return PiBridge(
            PiBridgeConfig(working_dir=tmp_path),
            bridge_script=bridge_script,
            logger=_logger(),
        )

    def test_idle_timeout_fires_when_no_events(self, tmp_path: Path) -> None:
        """_await_event raises if no event arrives within idle_timeout_s."""
        bridge = self._make_bridge(tmp_path)
        start = time.monotonic()
        with pytest.raises(PiBridgeError, match=r"idle|timed out"):
            bridge._await_event(
                _AwaitParams(
                    expected_types=frozenset({"agent_end"}),
                    error_types=frozenset({"error"}),
                    idle_timeout_s=0.15,
                    context="prompt",
                    drain_intermediate=True,
                )
            )
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"idle timeout fired too late: {elapsed:.2f}s"

    def test_continuous_events_keep_idle_timer_alive(self, tmp_path: Path) -> None:
        """Events received within idle_timeout_s reset the deadline.

        Regression test: the old 300s wall-clock timeout killed contextual
        reviews that were actively emitting tool-execution events every few
        seconds. This test runs longer than idle_timeout_s but no gap between
        events ever exceeds it, so the prompt must succeed.
        """
        bridge = self._make_bridge(tmp_path)

        def pusher() -> None:
            """Emit 20 intermediate events spaced 0.05s apart, then agent_end."""
            for _ in range(20):
                time.sleep(0.05)
                bridge._events.put({"type": "tool_execution_start", "toolName": "read"})
            bridge._events.put({"type": "agent_end", "finalText": "done"})

        t = threading.Thread(target=pusher, daemon=True)
        t.start()

        event = bridge._await_event(
            _AwaitParams(
                expected_types=frozenset({"agent_end"}),
                error_types=frozenset({"error"}),
                idle_timeout_s=0.2,
                context="prompt",
                drain_intermediate=True,
            )
        )
        t.join(timeout=3.0)

        assert event["type"] == "agent_end"
        assert event.get("finalText") == "done"

    def test_hard_timeout_fires_despite_continuous_events(self, tmp_path: Path) -> None:
        """hard_timeout_s bounds total wall-clock even when events stream.

        Safety net against a pathological bridge that keeps the idle timer
        alive forever by spamming events but never emits ``agent_end``.
        """
        bridge = self._make_bridge(tmp_path)
        stop = threading.Event()

        def pusher() -> None:
            while not stop.is_set():
                bridge._events.put({"type": "text_delta", "delta": "."})
                time.sleep(0.02)

        t = threading.Thread(target=pusher, daemon=True)
        t.start()
        try:
            with pytest.raises(PiBridgeError, match=r"hard|timed out"):
                bridge._await_event(
                    _AwaitParams(
                        expected_types=frozenset({"agent_end"}),
                        error_types=frozenset({"error"}),
                        idle_timeout_s=5.0,
                        hard_timeout_s=0.3,
                        context="prompt",
                        drain_intermediate=True,
                    )
                )
        finally:
            stop.set()
            t.join(timeout=2.0)

    def test_on_event_callback_invoked_for_each_intermediate(self, tmp_path: Path) -> None:
        """``on_event`` fires for every drained intermediate, not for terminal events."""
        bridge = self._make_bridge(tmp_path)
        seen: list[str] = []

        bridge._events.put({"type": "tool_execution_start", "toolName": "read"})
        bridge._events.put({"type": "tool_execution_end", "toolName": "read"})
        bridge._events.put({"type": "agent_end", "finalText": ""})

        bridge._await_event(
            _AwaitParams(
                expected_types=frozenset({"agent_end"}),
                error_types=frozenset({"error"}),
                idle_timeout_s=1.0,
                context="prompt",
                drain_intermediate=True,
                on_event=lambda ev: seen.append(str(ev.get("type", ""))),
            )
        )

        assert seen == ["tool_execution_start", "tool_execution_end"]


class TestPiBridgePromptTimeoutPlumbing:
    """Prompt-level timeout parameters travel to the right places."""

    def test_prompt_sends_hard_timeout_as_bridge_timeout_ms(self, tmp_path: Path) -> None:
        """``hard_timeout_s * 1000`` becomes ``timeoutMs`` in the bridge command.

        The bridge-side timeout is the absolute cap on the agent's turn — it
        needs to outlive the Python idle timer so that a genuinely hung pi
        session gets cleaned up at the source, not just in Python.
        """
        script, fake = _prepare_bridge(
            tmp_path,
            [{"type": "agent_end", "finalText": ""}],
        )
        with patch("fix_die_repeat.pi_bridge.subprocess.Popen", return_value=fake):
            with PiBridge(
                PiBridgeConfig(working_dir=tmp_path),
                bridge_script=script,
                logger=_logger(),
            ) as bridge:
                bridge.prompt(
                    "hello",
                    idle_timeout_s=SAMPLE_IDLE_S,
                    hard_timeout_s=SAMPLE_HARD_S,
                )
                fake.stdout.close()
                fake.stderr.close()

        lines = [json.loads(ln) for ln in fake.stdin.getvalue().strip().splitlines()]
        prompt_cmd = next(cmd for cmd in lines if cmd.get("type") == "prompt")
        assert prompt_cmd["timeoutMs"] == SAMPLE_HARD_MS
