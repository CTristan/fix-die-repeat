"""Prompt-improvement management for fix-die-repeat runner.

Drives the ``--improve-prompts`` mode: reads accumulated PR review
introspection data, copies (on-demand) the shipped prompt templates into
the user dotfolder, and asks pi to edit the user copies so future runs
pick up the improvements. Nothing inside the installed package is ever
mutated.
"""

import logging
import shutil
from importlib import resources
from pathlib import Path

import yaml

from fix_die_repeat.backends import Backend, BackendRequest
from fix_die_repeat.config import (
    Settings,
    get_introspection_archive_file_path,
    get_introspection_file_path,
    get_user_templates_dir,
)
from fix_die_repeat.prompts import clear_prompt_cache, render_prompt
from fix_die_repeat.runner_introspection import _FileLock

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

_IMPROVE_PROMPTS_TOOLS: tuple[str, ...] = ("read", "write", "edit")

# Non-zero exit code returned when pi succeeds but leaves the introspection
# or archive file unparseable, so the user notices the rollback.
_ROLLBACK_EXIT_CODE = 1


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

    @classmethod
    def has_pending_work(cls, logger: logging.Logger) -> bool:
        """Return True if ``<FDR_HOME>/introspection.yaml`` has pending entries.

        Exposed as a classmethod so callers (e.g. the CLI) can decide whether
        --improve-prompts needs to materialize per-repo state before invoking
        the manager — matching the ``create=False`` no-op contract.
        """
        introspection_file = get_introspection_file_path(create=False)
        return cls._file_has_pending_entries(introspection_file, logger)

    def run_improve_prompts(
        self,
        backend: Backend,
    ) -> int:
        """Run the prompt-improvement mode.

        Args:
            backend: Agent backend (e.g. ``PiBackend``).

        Returns:
            Exit code (0 on success, non-zero on failure).

        """
        # Avoid creating <FDR_HOME>/ for no-op runs; the file's presence implies
        # the parent already exists, and we exit early below when it doesn't.
        introspection_file = get_introspection_file_path(create=False)

        if not self._has_pending_entries(introspection_file):
            self.logger.info(
                "[ImprovePrompts] No pending introspection entries at %s; nothing to do.",
                introspection_file,
            )
            return 0

        templates_dir = get_user_templates_dir()
        templates_dir.mkdir(parents=True, exist_ok=True)
        template_paths = self._ensure_user_templates(templates_dir)

        archive_file = get_introspection_archive_file_path()
        prompt = render_prompt(
            "improve_prompts.j2",
            introspection_file_path=str(introspection_file),
            archive_file_path=str(archive_file),
            templates_dir=str(templates_dir),
            template_paths={name: str(path) for name, path in template_paths.items()},
        )

        self.logger.info(
            "[ImprovePrompts] Asking pi to review introspection data and update user templates...",
        )
        returncode = self._run_pi_with_rollback(
            introspection_file=introspection_file,
            archive_file=archive_file,
            backend=backend,
            prompt=prompt,
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

    def _run_pi_with_rollback(
        self,
        *,
        introspection_file: Path,
        archive_file: Path,
        backend: Backend,
        prompt: str,
    ) -> int:
        """Hold an exclusive lock on the introspection file for the pi call.

        Acquires ``_FileLock`` on ``introspection.yaml`` so a concurrent FDR
        process appending introspection entries serializes with us. Takes a
        backup of both the main and archive files before invoking pi, and
        rolls back if pi leaves either file unparseable.
        """
        # Open in r+ so we can hold an exclusive flock without truncating pi's
        # edits. pi rewrites the file in place via its own open(...) calls; our
        # handle exists only to anchor the flock.
        try:
            lock_handle = introspection_file.open("r+")
        except OSError:
            self.logger.exception(
                "[ImprovePrompts] Failed to open introspection file %s for locking",
                introspection_file,
            )
            return _ROLLBACK_EXIT_CODE

        with lock_handle, _FileLock(lock_handle):
            main_backup = self._create_backup(introspection_file)
            archive_existed = archive_file.exists()
            archive_backup = self._create_backup(archive_file) if archive_existed else None

            try:
                result = backend.invoke_safe(
                    BackendRequest(prompt=prompt, tools=_IMPROVE_PROMPTS_TOOLS),
                )
                returncode = result.returncode
                introspection_exists = introspection_file.exists()
                archive_exists = archive_file.exists()
                introspection_invalid = not introspection_exists or not self._is_valid_yaml(
                    introspection_file
                )
                archive_invalid = (archive_existed and not archive_exists) or (
                    archive_exists and not self._is_valid_yaml(archive_file)
                )
                if introspection_invalid or archive_invalid:
                    self.logger.error(
                        "[ImprovePrompts] pi left %s or %s missing or unparseable; rolling back.",
                        introspection_file,
                        archive_file,
                    )
                    self._restore_backup(introspection_file, main_backup)
                    self._restore_archive(
                        archive_file,
                        archive_backup,
                        archive_existed_before=archive_existed,
                    )
                    if returncode == 0:
                        returncode = _ROLLBACK_EXIT_CODE
                return returncode
            finally:
                if main_backup is not None and main_backup.exists():
                    main_backup.unlink()
                if archive_backup is not None and archive_backup.exists():
                    archive_backup.unlink()

    @staticmethod
    def _create_backup(source: Path) -> Path | None:
        """Copy ``source`` to a sibling ``.bak`` path and return it, or None if absent."""
        if not source.exists():
            return None
        backup = source.with_suffix(source.suffix + ".bak")
        shutil.copy2(source, backup)
        return backup

    def _is_valid_yaml(self, path: Path) -> bool:
        """Return True if ``path`` is absent, empty, or parses as YAML documents."""
        if not path.exists():
            return True
        try:
            content = path.read_text()
        except OSError as exc:
            self.logger.warning("[ImprovePrompts] Failed to read %s: %s", path, exc)
            return False
        if not content.strip():
            return True
        try:
            list(yaml.safe_load_all(content))
        except yaml.YAMLError as exc:
            self.logger.warning("[ImprovePrompts] %s is not valid YAML: %s", path, exc)
            return False
        return True

    @staticmethod
    def _restore_backup(target: Path, backup: Path | None) -> None:
        """Overwrite ``target`` with ``backup`` if ``backup`` exists."""
        if backup is not None and backup.exists():
            shutil.copy2(backup, target)

    def _restore_archive(
        self,
        archive_file: Path,
        archive_backup: Path | None,
        *,
        archive_existed_before: bool,
    ) -> None:
        """Restore the archive file to its pre-pi state.

        If the archive existed before pi ran, restore from its backup. If it
        didn't exist before, delete any file pi created so we don't leave a
        half-formed archive behind.
        """
        if archive_existed_before:
            self._restore_backup(archive_file, archive_backup)
        elif archive_file.exists():
            archive_file.unlink()

    def _has_pending_entries(self, introspection_file: Path) -> bool:
        """Return True if ``introspection_file`` has at least one ``status: pending`` document."""
        return self._file_has_pending_entries(introspection_file, self.logger)

    @staticmethod
    def _file_has_pending_entries(introspection_file: Path, logger: logging.Logger) -> bool:
        """Shared YAML-parsing core for ``has_pending_work`` / ``_has_pending_entries``."""
        if not introspection_file.exists():
            return False
        try:
            content = introspection_file.read_text()
        except OSError as exc:
            logger.warning(
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
            logger.warning(
                "[ImprovePrompts] %s is not valid YAML: %s",
                introspection_file,
                exc,
            )
            return False
        return any(isinstance(doc, dict) and doc.get("status") == "pending" for doc in documents)

    def _ensure_user_templates(self, templates_dir: Path) -> dict[str, Path]:
        """Ensure the editable templates exist under ``templates_dir``.

        For each template in ``EDITABLE_TEMPLATES``, copy the shipped
        package version into ``templates_dir`` if the user doesn't already
        have their own. Returns a mapping of template filename to the
        absolute user path (for all entries, whether freshly seeded or
        pre-existing).
        """
        package_templates = resources.files("fix_die_repeat").joinpath("templates")
        ensured: dict[str, Path] = {}
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
            ensured[name] = target
        return ensured
