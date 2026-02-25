"""Tests for prompt template rendering."""

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
            threshold=0.8,
        )

        assert "`pytest`" in prompt
        assert "WARNING: oscillating" in prompt
        assert ".fix-die-repeat/review.md" in prompt
        assert ".fix-die-repeat/build_history.md" in prompt
        assert "- app.py" in prompt
        assert "CRITICAL WARNING: large file" in prompt
        assert "below 0.8" in prompt

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
            threshold=0.7,
        )

        assert "`./scripts/ci.sh`" in prompt
        assert ".fix-die-repeat/review.md" not in prompt
        assert ".fix-die-repeat/build_history.md" not in prompt
        assert "I have also attached the currently changed files for context." in prompt
        assert "below 0.7" in prompt

    def test_missing_template_context_raises(self) -> None:
        """Raise when required template variables are missing."""
        with pytest.raises(UndefinedError):
            render_prompt("local_review.j2")
