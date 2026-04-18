"""Tests for the --improve-prompts mode manager."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fix_die_repeat.config import (
    Settings,
    get_introspection_file_path,
    get_user_templates_dir,
)
from fix_die_repeat.runner_improve_prompts import (
    EDITABLE_TEMPLATES,
    ImprovePromptsManager,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

# Non-zero pi exit code used to verify failure propagation.
PI_FAILURE_EXIT_CODE = 3


class _PiSpy:
    """Captures pi invocations so tests can assert on arguments."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> tuple[int, str, str]:
        self.calls.append(args)
        return (self.returncode, "", "")


def _manager(tmp_path: Path) -> ImprovePromptsManager:
    """Build a manager with a stub logger and no-op project root."""
    return ImprovePromptsManager(
        settings=Settings(),
        project_root=tmp_path,
        logger=logging.getLogger("test-improve-prompts"),
    )


def _write_introspection(content: str) -> Path:
    """Seed <FDR_HOME>/introspection.yaml with the given raw content."""
    path = get_introspection_file_path()
    path.write_text(content)
    return path


class TestPendingDetection:
    """run_improve_prompts should short-circuit unless there's pending work."""

    def test_missing_file_exits_zero_without_calling_pi(self, tmp_path: Path) -> None:
        """No introspection file means nothing to do."""
        spy = _PiSpy()
        manager = _manager(tmp_path)

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        assert spy.calls == []
        # And no user templates should be seeded yet
        assert not get_user_templates_dir().exists()

    def test_empty_file_exits_zero_without_calling_pi(self, tmp_path: Path) -> None:
        """An empty introspection file is treated the same as missing."""
        _write_introspection("")
        spy = _PiSpy()
        manager = _manager(tmp_path)

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        assert spy.calls == []

    def test_only_reviewed_entries_skip(self, tmp_path: Path) -> None:
        """If every entry is already reviewed, skip invoking pi."""
        _write_introspection(
            "date: '2025-01-01'\nstatus: reviewed\nthreads: []\n"
            "---\n"
            "date: '2025-02-01'\nstatus: reviewed\nthreads: []\n",
        )
        spy = _PiSpy()
        manager = _manager(tmp_path)

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        assert spy.calls == []

    def test_malformed_yaml_skips_gracefully(self, tmp_path: Path) -> None:
        """A malformed YAML file should not crash the mode."""
        _write_introspection(":\n  : bad-indent: :\n  -")
        spy = _PiSpy()
        manager = _manager(tmp_path)

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        assert spy.calls == []


class TestSeedingAndPiInvocation:
    """When there's pending work, seed templates and call pi."""

    def test_seeds_missing_user_templates(self, tmp_path: Path) -> None:
        """All four editable templates materialize in the user dir on first run."""
        _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\npr_url: https://example.com/pr/1\n"
            "project: demo\nthreads: []\n",
        )
        spy = _PiSpy()
        manager = _manager(tmp_path)

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        templates_dir = get_user_templates_dir()
        for name in EDITABLE_TEMPLATES:
            target = templates_dir / name
            assert target.exists(), f"{name} should be seeded"
            assert target.read_text(), f"{name} seeded file is empty"

    def test_does_not_overwrite_existing_user_templates(self, tmp_path: Path) -> None:
        """If the user already customized a template, seeding must not clobber it."""
        templates_dir = get_user_templates_dir()
        templates_dir.mkdir(parents=True, exist_ok=True)
        customized = templates_dir / "local_review.j2"
        customized.write_text("USER CUSTOMIZED {{ review_history_path }}")

        _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\npr_url: https://example.com/pr/1\n"
            "project: demo\nthreads: []\n",
        )
        spy = _PiSpy()
        manager = _manager(tmp_path)

        rc = manager.run_improve_prompts(spy)

        assert rc == 0
        # User version preserved
        assert customized.read_text() == "USER CUSTOMIZED {{ review_history_path }}"
        # Others still seeded
        for name in EDITABLE_TEMPLATES:
            if name == "local_review.j2":
                continue
            assert (templates_dir / name).exists()

    def test_invokes_pi_with_read_write_edit_tools(self, tmp_path: Path) -> None:
        """The pi call uses the expected tool allow-list and carries the rendered prompt."""
        _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\npr_url: https://example.com/pr/1\n"
            "project: demo\nthreads: []\n",
        )
        spy = _PiSpy()
        manager = _manager(tmp_path)

        manager.run_improve_prompts(spy)

        assert len(spy.calls) == 1
        args = spy.calls[0]
        assert args[0] == "-p"
        assert args[1] == "--tools"
        assert args[2] == "read,write,edit"
        prompt = args[3]
        # Rendered prompt should reference the introspection file and every editable template
        assert str(get_introspection_file_path()) in prompt
        for name in EDITABLE_TEMPLATES:
            assert name in prompt

    def test_propagates_pi_nonzero_exit(self, tmp_path: Path) -> None:
        """A non-zero pi exit is surfaced as the mode's exit code."""
        _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\npr_url: https://example.com/pr/1\n"
            "project: demo\nthreads: []\n",
        )
        spy = _PiSpy(returncode=PI_FAILURE_EXIT_CODE)
        manager = _manager(tmp_path)

        rc = manager.run_improve_prompts(spy)

        assert rc == PI_FAILURE_EXIT_CODE


class TestCacheClear:
    """After pi finishes, the Jinja cache should be cleared so edits are visible."""

    def test_clears_prompt_cache_after_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The manager calls clear_prompt_cache after invoking pi."""
        cleared: list[bool] = []

        def _spy_clear() -> None:
            cleared.append(True)

        monkeypatch.setattr(
            "fix_die_repeat.runner_improve_prompts.clear_prompt_cache",
            _spy_clear,
        )

        _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\npr_url: https://example.com/pr/1\n"
            "project: demo\nthreads: []\n",
        )
        spy: Callable[..., tuple[int, str, str]] = _PiSpy()
        manager = _manager(tmp_path)

        manager.run_improve_prompts(spy)

        assert cleared == [True]
