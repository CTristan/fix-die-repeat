"""Command-line interface for fix-die-repeat."""

import logging
import traceback

import click
from rich.console import Console

from fix_die_repeat.config import (
    CliOptions,
    Paths,
    get_introspection_file_path,
    get_settings,
)
from fix_die_repeat.detection import (
    get_system_config_path,
    resolve_check_cmd,
    validate_check_cmd_or_exit,
)
from fix_die_repeat.runner import PiRunner
from fix_die_repeat.runner_improve_prompts import ImprovePromptsManager
from fix_die_repeat.utils import is_running_in_dev_mode

console = Console()


_MAIN_HELP = (
    "Automated check, review, and fix loop using pi.\n"
    "\n"
    "\b\n"
    "Environment variables:\n"
    "  FDR_CHECK_CMD, FDR_MAX_ITERS, FDR_MODEL, FDR_MAX_PR_THREADS,\n"
    "  FDR_ARCHIVE_ARTIFACTS, FDR_COMPACT_ARTIFACTS,\n"
    "  FDR_PR_REVIEW, FDR_PR_REVIEW_INTROSPECT,\n"
    "  FDR_CONTEXTUAL_REVIEW, FDR_FULL_CODEBASE_REVIEW,\n"
    "  FDR_PR_THREADS_INTROSPECT_ONLY, FDR_IMPROVE_PROMPTS,\n"
    "  FDR_TEST_MODEL, FDR_DEBUG, FDR_LANGUAGES,\n"
    "  FDR_HOME (base directory for state; defaults to ~/.fix-die-repeat),\n"
    "  FDR_NTFY_ENABLED (default: 1),\n"
    "  FDR_NTFY_URL (default: http://localhost:2586)\n"
    "\n"
    "\b\n"
    "Examples:\n"
    "  # Run with default settings\n"
    "  fix-die-repeat\n"
    "\b\n"
    "  # Use a custom check command\n"
    '  fix-die-repeat -c "make test"\n'
    "\b\n"
    "  # Test a model before running\n"
    "  fix-die-repeat --test-model anthropic/claude-sonnet-4-5\n"
    "\b\n"
    "  # Enable PR review mode\n"
    "  fix-die-repeat --pr-review\n"
    "\b\n"
    "  # PR review mode with prompt introspection\n"
    "  fix-die-repeat --pr-review-introspect\n"
    "\b\n"
    "  # Smart contextual review (uncommitted > branch > full codebase)\n"
    "  fix-die-repeat --contextual-review\n"
    "\b\n"
    "  # Audit the entire codebase (report-only, no fixes attempted)\n"
    "  fix-die-repeat --full-codebase-review\n"
    "\b\n"
    "  # Fetch and introspect unresolved PR review threads, then exit\n"
    "  fix-die-repeat --pr-threads-introspect-only\n"
    "\b\n"
    "  # Have pi update the user prompt templates from accumulated introspection data\n"
    "  fix-die-repeat --improve-prompts\n"
)


@click.command(help=_MAIN_HELP)
@click.option(
    "-c",
    "--check-cmd",
    help="Command to run checks (default: auto-detected)",
    envvar="FDR_CHECK_CMD",
)
@click.option(
    "-n",
    "--max-iters",
    type=int,
    help="Maximum loop iterations (default: 10)",
    envvar="FDR_MAX_ITERS",
)
@click.option(
    "-m",
    "--model",
    help="Override model selection (e.g., anthropic/claude-sonnet-4-5)",
    envvar="FDR_MODEL",
)
@click.option(
    "--max-pr-threads",
    type=int,
    help="Maximum PR threads to process per iteration (default: 5)",
    envvar="FDR_MAX_PR_THREADS",
)
@click.option(
    "--archive-artifacts",
    is_flag=True,
    help="Archive existing artifacts to a timestamped folder",
    envvar="FDR_ARCHIVE_ARTIFACTS",
)
@click.option(
    "--no-compact",
    is_flag=True,
    help="Skip automatic compaction of large artifacts",
)
@click.option(
    "--pr-review",
    is_flag=True,
    help="Enable PR review mode",
    envvar="FDR_PR_REVIEW",
)
@click.option(
    "--pr-review-introspect",
    is_flag=True,
    help="Enable PR review mode with prompt introspection (implies --pr-review)",
    envvar="FDR_PR_REVIEW_INTROSPECT",
)
@click.option(
    "--contextual-review",
    is_flag=True,
    help=(
        "Smart contextual review (report-only). Reviews uncommitted changes, "
        "branch diff vs default branch, or full codebase if neither applies. "
        "Standalone mode — mutually exclusive with --full-codebase-review, "
        "--pr-threads-introspect-only, and --improve-prompts."
    ),
    envvar="FDR_CONTEXTUAL_REVIEW",
)
@click.option(
    "--full-codebase-review",
    is_flag=True,
    help=(
        "Audit the entire codebase instead of a diff. Report-only: "
        "never attempts fixes. Ignores --pr-review if also set. "
        "Standalone mode — mutually exclusive with --contextual-review, "
        "--pr-threads-introspect-only, and --improve-prompts."
    ),
    envvar="FDR_FULL_CODEBASE_REVIEW",
)
@click.option(
    "--pr-threads-introspect-only",
    is_flag=True,
    help=(
        "Fetch the PR's unresolved review threads, run introspection on them, "
        "then exit. Does not run checks, local review, or attempt fixes. "
        "Standalone mode — mutually exclusive with --contextual-review, "
        "--full-codebase-review, and --improve-prompts."
    ),
    envvar="FDR_PR_THREADS_INTROSPECT_ONLY",
)
@click.option(
    "--improve-prompts",
    is_flag=True,
    help=(
        "Read accumulated introspection data and have pi update the user-owned "
        "prompt templates under <FDR_HOME>/templates/. Seeds copies of the shipped "
        "templates on first use; never mutates the package. Runs once and exits. "
        "Standalone mode — mutually exclusive with --contextual-review, "
        "--full-codebase-review, and --pr-threads-introspect-only."
    ),
    envvar="FDR_IMPROVE_PROMPTS",
)
@click.option(
    "--test-model",
    help="Test model compatibility before running (exits after test)",
    envvar="FDR_TEST_MODEL",
)
@click.option(
    "-d",
    "--debug",
    is_flag=True,
    help="Enable debug mode (timestamped session logs and verbose logging)",
    envvar="FDR_DEBUG",
)
@click.version_option()
def main(**kwargs: str | int | bool | None) -> None:
    """Run the automated check, review, and fix loop.

    fix-die-repeat is an automated tool that:
    1. Runs your check command (CI/tests)
    2. If checks fail, uses pi to fix the errors
    3. If checks pass, reviews the changes using pi
    4. If review finds issues, fixes them
    5. Repeats until all checks pass and no issues are found
    """
    debug = bool(kwargs.get("debug", False))
    exit_code = _run_main_with_error_handling(kwargs, debug=debug)
    raise SystemExit(exit_code)


def _build_cli_options(kwargs: dict[str, str | int | bool | None]) -> CliOptions:
    """Build CliOptions from Click's keyword arguments.

    Click passes each @click.option value as a keyword argument. This
    function maps them into the CliOptions dataclass so downstream code
    works with a typed object instead of a raw dict.

    Click guarantees value types via each option's ``type=`` parameter,
    so the casts below are safe.

    Args:
        kwargs: Keyword arguments injected by Click decorators

    Returns:
        CliOptions with CLI-provided overrides

    """
    check_cmd = kwargs.get("check_cmd")
    max_iters = kwargs.get("max_iters")
    model = kwargs.get("model")
    max_pr_threads = kwargs.get("max_pr_threads")
    test_model = kwargs.get("test_model")
    archive_flag = kwargs.get("archive_artifacts")

    return CliOptions(
        check_cmd=str(check_cmd) if check_cmd is not None else None,
        max_iters=int(max_iters) if max_iters is not None else None,
        model=str(model) if model is not None else None,
        max_pr_threads=int(max_pr_threads) if max_pr_threads is not None else None,
        archive_artifacts=bool(archive_flag) if archive_flag else None,
        no_compact=bool(kwargs.get("no_compact", False)),
        pr_review=bool(kwargs.get("pr_review", False)),
        pr_review_introspect=bool(kwargs.get("pr_review_introspect", False)),
        full_codebase_review=bool(kwargs.get("full_codebase_review", False)),
        contextual_review=bool(kwargs.get("contextual_review", False)),
        pr_threads_introspect_only=bool(kwargs.get("pr_threads_introspect_only", False)),
        improve_prompts=bool(kwargs.get("improve_prompts", False)),
        test_model=str(test_model) if test_model is not None else None,
        debug=bool(kwargs.get("debug", False)),
    )


def _run_main_with_error_handling(
    kwargs: dict[str, str | int | bool | None],
    *,
    debug: bool,
) -> int:
    """Run the main application with error handling.

    Args:
        kwargs: Keyword arguments injected by Click decorators
        debug: Whether debug mode is enabled

    Returns:
        Exit code for the process

    """
    try:
        options = _build_cli_options(kwargs)
        return _run_main(options)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        return 130
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        if debug:
            console.print(traceback.format_exc())
        return 1


_STANDALONE_MODE_FLAGS: tuple[tuple[str, str], ...] = (
    ("pr_threads_introspect_only", "--pr-threads-introspect-only"),
    ("improve_prompts", "--improve-prompts"),
    ("contextual_review", "--contextual-review"),
    ("full_codebase_review", "--full-codebase-review"),
)


def _validate_standalone_modes_mutually_exclusive(settings: object) -> None:
    """Reject combinations of standalone-mode flags.

    Each standalone mode runs a one-shot codepath and exits; combining
    them would silently drop all but one. Raise ValueError so the user
    sees an actionable error instead of surprising behavior.
    """
    active = [cli_flag for attr, cli_flag in _STANDALONE_MODE_FLAGS if getattr(settings, attr)]
    if len(active) > 1:
        joined = ", ".join(active)
        msg = (
            f"The following flags are mutually exclusive: {joined}. "
            "Pick one — each runs a one-shot mode and exits."
        )
        raise ValueError(msg)


def _run_main(options: CliOptions) -> int:
    """Run main application logic using CliOptions.

    Avoids Click's parameter explosion by accepting a grouped options
    object instead of individual parameters.

    Args:
        options: CLI options grouped into a dataclass

    Returns:
        Exit code from PiRunner

    """
    # Show dev mode indicator if running from editable install
    if is_running_in_dev_mode():
        console.print("[cyan]⚡ Running in DEV mode (editable install)[/cyan]")

    # Get settings
    settings = get_settings(options)

    _validate_standalone_modes_mutually_exclusive(settings)

    # --improve-prompts is repo-agnostic: it reads <FDR_HOME>/introspection.yaml
    # and edits <FDR_HOME>/templates/. When there's no pending work, exit before
    # constructing Paths/PiRunner so we don't materialize <FDR_HOME>/repos/<slug>/
    # as a side effect of a no-op run.
    if settings.improve_prompts and not ImprovePromptsManager.has_pending_work(
        logging.getLogger("fix-die-repeat.cli")
    ):
        console.print(
            f"[cyan][ImprovePrompts][/cyan] No pending introspection entries at "
            f"{get_introspection_file_path(create=False)}; nothing to do."
        )
        return 0

    # Initialize paths
    paths = Paths()

    # Standalone modes don't run checks — skip check-cmd resolution entirely
    needs_check_cmd = not (
        settings.full_codebase_review
        or settings.pr_threads_introspect_only
        or settings.contextual_review
        or settings.improve_prompts
    )

    if needs_check_cmd:
        # Resolve check command if not provided via CLI/env
        if settings.check_cmd is None:
            settings.check_cmd = resolve_check_cmd(
                cli_check_cmd=options.check_cmd,
                project_config_path=paths.config_file,
                system_config_path=get_system_config_path(),
                project_root=str(paths.project_root),
            )

        # Ensure we have a concrete check command before validation
        if settings.check_cmd is None:
            console.print(
                "[red]Error:[/red] Unable to determine a check command to run.\n"
                "Please specify one via the [bold]--check-cmd[/bold] option or the "
                "[bold]FDR_CHECK_CMD[/bold] environment variable."
            )
            raise SystemExit(1)

        # Pre-flight validation of resolved check command
        validate_check_cmd_or_exit(settings.check_cmd)

    # Create runner and run the loop. The context manager owns the pi-bridge
    # subprocess lifecycle — it spawns node on __enter__ and shuts down on exit.
    with PiRunner(settings, paths) as runner:
        return runner.run()


if __name__ == "__main__":
    main()
