"""Tests for the post-run summary emitted by ``--improve-prompts``.

Kept in a sibling file of ``test_runner_improve_prompts.py`` purely to keep
the summary-specific cases grouped; pytest collects both files the same way.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fix_die_repeat.config import (
    Settings,
    get_introspection_file_path,
    get_user_templates_dir,
)
from fix_die_repeat.runner_improve_prompts import ImprovePromptsManager

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _PiSpy:
    """Captures pi invocations so tests can assert on arguments."""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> tuple[int, str, str]:
        self.calls.append(args)
        return (self.returncode, "", "")


def _manager() -> ImprovePromptsManager:
    """Build a manager with a stub logger."""
    return ImprovePromptsManager(
        settings=Settings(),
        logger=logging.getLogger("test-improve-prompts-summary"),
    )


def _write_introspection(content: str) -> Path:
    """Seed <FDR_HOME>/introspection.yaml with the given raw content."""
    path = get_introspection_file_path()
    path.write_text(content)
    return path


_PENDING_ENTRY = (
    "date: '2026-04-01'\nstatus: pending\npr_number: 1\n"
    "pr_url: https://example.com/pr/1\nproject: demo\nthreads: []\n"
)


class TestSummary:
    """A successful run should print a summary of what pi changed."""

    def test_summary_lists_each_modified_template(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When pi edits templates, the summary names every changed file."""
        _write_introspection(_PENDING_ENTRY)
        templates_dir = get_user_templates_dir()

        def editing_pi(*_args: str) -> tuple[int, str, str]:
            (templates_dir / "local_review.j2").write_text("edited by pi\n")
            (templates_dir / "partials/_critical_checklist.j2").write_text("edited partial\n")
            return (0, "", "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(editing_pi)

        assert rc == 0
        summary_index = next(
            (i for i, r in enumerate(caplog.records) if "Summary" in r.message),
            None,
        )
        assert summary_index is not None, "expected a [ImprovePrompts] Summary log record"
        post_summary = " ".join(r.message for r in caplog.records[summary_index:])
        assert "local_review.j2" in post_summary
        assert "_critical_checklist.j2" in post_summary

    def test_summary_reports_noop_explicitly(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A run where pi edits zero templates must say so, not go silent."""
        _write_introspection(_PENDING_ENTRY)
        spy = _PiSpy()
        manager = _manager()

        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(spy)

        assert rc == 0
        messages = " ".join(r.message for r in caplog.records).lower()
        assert "summary" in messages
        assert "no" in messages
        assert any(keyword in messages for keyword in ("edit", "change", "modif"))

    def test_summary_reports_introspection_entries_consumed(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The summary reports how many pending entries transitioned away."""
        path = _write_introspection(
            "date: '2026-04-01'\nstatus: pending\npr_number: 1\n"
            "pr_url: https://example.com/pr/1\nproject: demo\nthreads: []\n"
            "---\n"
            "date: '2026-04-02'\nstatus: pending\npr_number: 2\n"
            "pr_url: https://example.com/pr/2\nproject: demo\nthreads: []\n",
        )

        def consuming_pi(*_args: str) -> tuple[int, str, str]:
            path.write_text(
                "date: '2026-04-01'\nstatus: reviewed\npr_number: 1\n"
                "pr_url: https://example.com/pr/1\nproject: demo\nthreads: []\n"
                "---\n"
                "date: '2026-04-02'\nstatus: reviewed\npr_number: 2\n"
                "pr_url: https://example.com/pr/2\nproject: demo\nthreads: []\n",
            )
            return (0, "", "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(consuming_pi)

        assert rc == 0
        summary_messages = [r.message for r in caplog.records if "Summary" in r.message]
        assert summary_messages, "expected at least one [ImprovePrompts] Summary log record"
        joined = " ".join(summary_messages).lower()
        # Count "2" must appear with "entries" context, not just anywhere in dates/paths.
        assert "2 introspection" in joined or "2 entries" in joined or "2 pending" in joined

    def test_summary_shows_line_delta_per_template(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The per-template line should include a diff-derived hint (line delta)."""
        _write_introspection(_PENDING_ENTRY)
        templates_dir = get_user_templates_dir()

        def extending_pi(*_args: str) -> tuple[int, str, str]:
            target = templates_dir / "fix_checks.j2"
            base = target.read_text()
            if not base.endswith("\n"):
                base += "\n"
            target.write_text(base + "line a\nline b\nline c\n")
            return (0, "", "")

        manager = _manager()
        with caplog.at_level(logging.INFO, logger="test-improve-prompts-summary"):
            rc = manager.run_improve_prompts(extending_pi)

        assert rc == 0
        summary_index = next(
            (i for i, r in enumerate(caplog.records) if "Summary" in r.message),
            None,
        )
        assert summary_index is not None, "expected a [ImprovePrompts] Summary log record"
        post_summary = " ".join(r.message for r in caplog.records[summary_index:])
        assert "fix_checks.j2" in post_summary
        assert "+3" in post_summary or "3 line" in post_summary
