"""Tests for prompt template rendering."""

from pathlib import Path

import pytest
from jinja2 import UndefinedError

from fix_die_repeat.prompts import render_prompt


class TestRenderPrompt:
    """Tests for render_prompt."""

    def test_fix_checks_template_with_optional_sections(self) -> None:
        """Render fix_checks prompt with optional context included."""
        prompt = render_prompt(
            "fix_checks.j2",
            check_cmd="pytest",
            oscillation_warning="WARNING: oscillating",
            include_review_history=True,
            include_build_history=True,
            context_mode="pull",
            large_context_list="- app.py\n- tests/test_app.py",
            large_file_warning="CRITICAL WARNING: large file",
        )

        assert "`pytest`" in prompt
        assert "WARNING: oscillating" in prompt
        assert ".fix-die-repeat/review.md" in prompt
        assert ".fix-die-repeat/build_history.md" in prompt
        assert "- app.py" in prompt
        assert "CRITICAL WARNING: large file" in prompt
        assert "structured data or external tool output" in prompt
        assert "atomic/locked writes" in prompt

    def test_fix_checks_template_without_optional_sections(self) -> None:
        """Render fix_checks prompt without optional sections."""
        prompt = render_prompt(
            "fix_checks.j2",
            check_cmd="./scripts/ci.sh",
            oscillation_warning="",
            include_review_history=False,
            include_build_history=False,
            context_mode="push",
            large_context_list="",
            large_file_warning="",
        )

        assert "`./scripts/ci.sh`" in prompt
        assert ".fix-die-repeat/review.md" not in prompt
        assert ".fix-die-repeat/build_history.md" not in prompt
        assert "I have also attached the currently changed files for context." in prompt

    def test_missing_template_context_raises(self) -> None:
        """Raise when required template variables are missing."""
        with pytest.raises(UndefinedError):
            render_prompt("local_review.j2")

    def test_local_review_with_agents_file(self) -> None:
        """Render local_review prompt when AGENTS.md exists."""
        prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix="",
            has_agents_file=True,
        )

        assert "Project policy: no violations of AGENTS.md" in prompt
        assert (
            "If you find any policy violations from AGENTS.md, classify them as [CRITICAL]."
            in prompt
        )

    def test_local_review_without_agents_file(self) -> None:
        """Render local_review prompt when AGENTS.md does not exist."""
        prompt = render_prompt(
            "local_review.j2",
            review_prompt_prefix="",
            has_agents_file=False,
        )

        assert "Project policy: no violations of AGENTS.md" not in prompt
        assert "If you find any policy violations from AGENTS.md" not in prompt
        assert "No test configuration changes without explicit approval" in prompt
        assert "external tool output or parsed JSON/YAML" in prompt
        assert (
            "Data serialization: structured outputs (JSON/YAML/etc.) use safe serializers" in prompt
        )
        assert (
            "docs/prompts/config instructions and examples match actual behavior "
            "and required fields" in prompt
        )
        assert "avoid terminating the process from library/orchestration code" in prompt
        assert "propagate internal failure codes to process exit status" in prompt
        assert "tests assert observable behavior" in prompt
        assert "user-facing logs/errors clearly explain limits, skips, or partial results" in prompt

    def test_introspect_pr_review_template(self, tmp_path: Path) -> None:
        """Render introspect_pr_review template with all required variables."""
        output_path = tmp_path / "result.yaml"
        prompt = render_prompt(
            "introspect_pr_review.j2",
            run_date="2026-02-26",
            project_name="test-project",
            pr_number=123,
            pr_url="https://github.com/owner/repo/pull/123",
            output_path=str(output_path),
        )

        assert "2026-02-26" in prompt
        assert "test-project" in prompt
        assert "123" in prompt
        assert "https://github.com/owner/repo/pull/123" in prompt
        assert str(output_path) in prompt
        assert "YAML document" in prompt
        assert "GraphQL thread ID" in prompt
        assert "security, error-handling, performance" in prompt
        assert "Use the 'write' tool" in prompt
