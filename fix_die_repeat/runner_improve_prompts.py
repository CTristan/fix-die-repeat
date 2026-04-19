"""Prompt-improvement management for fix-die-repeat runner.

Drives the ``--improve-prompts`` mode: reads accumulated PR review
introspection data, copies (on-demand) the shipped prompt templates into
the user dotfolder, and asks pi to edit the user copies so future runs
pick up the improvements. Nothing inside the installed package is ever
mutated.
"""

import logging
import shutil
from collections.abc import Callable
from importlib import resources
from pathlib import Path

import yaml

from fix_die_repeat.config import (
    Settings,
    get_introspection_archive_file_path,
    get_introspection_file_path,
    get_user_templates_dir,
)
from fix_die_repeat.prompts import clear_prompt_cache, render_prompt

# Templates exposed to pi for editing. Kept intentionally narrow: the
# four top-level language-agnostic prompts that steer every run, plus the
# shared ``partials/`` fragments those prompts compose. Per-language
# partials under ``lang_checks/`` are deliberately excluded.
EDITABLE_TEMPLATES = (
    "fix_checks.j2",
    "local_review.j2",
    "resolve_review_issues.j2",
    "pr_threads_header.j2",
    "partials/_review_readonly_task.j2",
    "partials/_issue_classification.j2",
    "partials/_critical_checklist.j2",
    "partials/_language_checks.j2",
    "partials/_review_reporting_rules.j2",
    "partials/_review_output_contract.j2",
)


class ImprovePromptsManager:
    """Orchestrates the ``--improve-prompts`` one-shot mode."""

    def __init__(
        self,
        settings: Settings,
        logger: logging.Logger,
    ) -> None:
        """Initialize the manager.

        Args:
            settings: Configuration settings.
            logger: Logger instance for output.

        """
        self.settings = settings
        self.logger = logger

    def run_improve_prompts(
        self,
        run_pi_callback: Callable[..., tuple[int, str, str]],
    ) -> int:
        """Run the prompt-improvement mode.

        Args:
            run_pi_callback: Function to invoke pi (from ``PiRunner``).

        Returns:
            Exit code (0 on success, non-zero on failure).

        """
        introspection_file = get_introspection_file_path()

        if not self._has_pending_entries(introspection_file):
            self.logger.info(
                "[ImprovePrompts] No pending introspection entries at %s; nothing to do.",
                introspection_file,
            )
            return 0

        templates_dir = get_user_templates_dir()
        templates_dir.mkdir(parents=True, exist_ok=True)
        template_paths = self._seed_user_templates(templates_dir)

        prompt = render_prompt(
            "improve_prompts.j2",
            introspection_file_path=str(introspection_file),
            archive_file_path=str(get_introspection_archive_file_path()),
            templates_dir=str(templates_dir),
            template_paths={name: str(path) for name, path in template_paths.items()},
        )

        self.logger.info(
            "[ImprovePrompts] Asking pi to review introspection data and update user templates...",
        )
        returncode, _stdout, _stderr = run_pi_callback(
            "-p",
            "--tools",
            "read,write,edit",
            prompt,
        )

        # Refresh the Jinja environment so the edits land on the next render,
        # even if the same process goes on to use templates (tests do this).
        clear_prompt_cache()

        if returncode != 0:
            self.logger.warning(
                "[ImprovePrompts] pi exited with code %s; user templates may be partially updated.",
                returncode,
            )
            return returncode

        self.logger.info(
            "[ImprovePrompts] Done. User templates at %s now take precedence.",
            templates_dir,
        )
        return 0

    def _has_pending_entries(self, introspection_file: Path) -> bool:
        """Return True if ``introspection_file`` has at least one ``status: pending`` document."""
        if not introspection_file.exists():
            return False
        try:
            content = introspection_file.read_text()
        except OSError as exc:
            self.logger.warning(
                "[ImprovePrompts] Failed to read %s: %s",
                introspection_file,
                exc,
            )
            return False
        if not content.strip():
            return False
        try:
            documents = list(yaml.safe_load_all(content))
        except yaml.YAMLError as exc:
            self.logger.warning(
                "[ImprovePrompts] %s is not valid YAML: %s",
                introspection_file,
                exc,
            )
            return False
        return any(isinstance(doc, dict) and doc.get("status") == "pending" for doc in documents)

    def _seed_user_templates(self, templates_dir: Path) -> dict[str, Path]:
        """Ensure the editable templates exist under ``templates_dir``.

        For each template in ``EDITABLE_TEMPLATES``, copy the shipped
        package version into ``templates_dir`` if the user doesn't already
        have their own. Returns a mapping of template filename to the
        absolute user path.
        """
        package_templates = resources.files("fix_die_repeat").joinpath("templates")
        seeded: dict[str, Path] = {}
        for name in EDITABLE_TEMPLATES:
            target = templates_dir / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                # Traversable.joinpath('a/b') works for filesystem packages but
                # is not portable to zip/wheel resources; chain the components
                # so this keeps working after packaging.
                source = package_templates
                for part in name.split("/"):
                    source = source.joinpath(part)
                with resources.as_file(source) as source_path:
                    shutil.copyfile(source_path, target)
                self.logger.info(
                    "[ImprovePrompts] Seeded %s from shipped defaults.",
                    target,
                )
            seeded[name] = target
        return seeded
