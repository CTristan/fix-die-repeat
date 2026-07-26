"""Command-line interface for fix-die-repeat."""

from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Never, cast, override

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
from fix_die_repeat.sequencer_engine import (
    DoneOptions,
    SequencerResult,
    SequencerService,
)
from fix_die_repeat.sequencer_evaluator import EvaluationError
from fix_die_repeat.sequencer_git import GitProbeError
from fix_die_repeat.sequencer_state import StateError
from fix_die_repeat.sequencer_workflow import ID_PATTERN, WorkflowValidationError
from fix_die_repeat.utils import is_running_in_dev_mode

if TYPE_CHECKING:
    from collections.abc import Callable

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


def _sequencer_error(
    command: str,
    outcome: str,
    message: str,
    *,
    context: _SequencerContext | None = None,
    gaps: list[dict[str, str]] | None = None,
) -> SequencerResult:
    """Build a protocol error without requiring initialized state."""
    return SequencerResult(
        command=command,
        outcome=outcome,
        message=message,
        repository=(str(context.repository.resolve(strict=False)) if context is not None else None),
        run_id=context.run_id if context is not None else None,
        gaps=gaps or [],
    )


def _emit_sequencer_result(result: SequencerResult, *, diagnostic: bool = False) -> int:
    """Write one JSON response and an optional human diagnostic."""
    click.echo(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
    if diagnostic:
        click.echo(f"Error: {result.message}", err=True)
    return result.exit_code


def _exit_with_sequencer_result(
    result: SequencerResult,
    *,
    diagnostic: bool = False,
) -> Never:
    """Emit a response, then preserve its nonzero process status through Click."""
    raise click.exceptions.Exit(
        _emit_sequencer_result(result, diagnostic=diagnostic),
    )


class _RootGroup(click.Group):
    """Preserve Click help while converting sequencer usage failures to JSON."""

    @override
    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except click.UsageError as exc:
            if ctx.invoked_subcommand != "sequencer":
                raise
            command, context = _sequencer_usage_context(exc, ctx)
            result = _sequencer_error(
                command,
                "usage_error",
                exc.format_message(),
                context=context,
            )
            ctx.exit(_emit_sequencer_result(result, diagnostic=True))


def _sequencer_usage_context(
    error: click.UsageError,
    root: click.Context,
) -> tuple[str, _SequencerContext | None]:
    """Recover the leaf command and parsed identity from Click's context chain."""
    leaf = error.ctx or root
    command = leaf.info_name or leaf.command.name or "sequencer"
    current: click.Context | None = leaf
    context: _SequencerContext | None = None
    while current is not None:
        if isinstance(current.obj, _SequencerContext):
            context = current.obj
            break
        current = current.parent
    return command, context


@click.group(
    cls=_RootGroup,
    help=_MAIN_HELP,
    invoke_without_command=True,
    no_args_is_help=False,
)
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
@click.pass_context
def main(ctx: click.Context, **kwargs: str | int | bool | None) -> None:
    """Run the automated check, review, and fix loop.

    fix-die-repeat is an automated tool that:
    1. Runs your check command (CI/tests)
    2. If checks fail, uses pi to fix the errors
    3. If checks pass, reviews the changes using pi
    4. If review finds issues, fixes them
    5. Repeats until all checks pass and no issues are found
    """
    if ctx.invoked_subcommand is not None:
        return
    debug = bool(kwargs.get("debug", False))
    exit_code = _run_main_with_error_handling(kwargs, debug=debug)
    raise SystemExit(exit_code)


@dataclass(frozen=True)
class _SequencerContext:
    """Repository and run identity shared by sequencer commands."""

    repository: Path
    run_id: str


@main.group(name="sequencer")
@click.option(
    "--run-id",
    required=True,
    help="Repository-scoped run identifier.",
)
@click.option(
    "--repo",
    "repository",
    type=click.Path(path_type=Path, file_okay=False),
    default=".",
    show_default=True,
    help="Target Git repository.",
)
@click.pass_context
def sequencer(ctx: click.Context, run_id: str, repository: Path) -> None:
    """Drive a persisted, externally executed workflow."""
    ctx.obj = _SequencerContext(repository=repository, run_id=run_id)
    _validate_protocol_id(run_id, "run ID")


def _validate_protocol_id(value: str, label: str) -> None:
    """Reject identifiers that cannot enter the version 1 protocol."""
    if not ID_PATTERN.fullmatch(value):
        message = f"Invalid {label}: {value!r}"
        raise click.UsageError(message, ctx=click.get_current_context())


def _parse_flags(values: tuple[str, ...]) -> list[tuple[str, str]]:
    """Split repeated NAME=VALUE flag arguments without interpreting values."""
    flags: list[tuple[str, str]] = []
    names: set[str] = set()
    for value in values:
        name, separator, flag_value = value.partition("=")
        if not separator or not name or not flag_value:
            message = "--flag must use NAME=VALUE"
            raise click.UsageError(message, ctx=click.get_current_context())
        _validate_protocol_id(name, "flag name")
        if name in names:
            message = f"Duplicate flag: {name!r}"
            raise click.UsageError(message, ctx=click.get_current_context())
        names.add(name)
        flags.append((name, flag_value))
    return flags


def _run_sequencer(
    command: str,
    context: _SequencerContext,
    operation: Callable[[], SequencerResult],
) -> Never:
    """Execute one service operation behind the stable response boundary."""
    try:
        result = operation()
    except WorkflowValidationError as exc:
        gaps = [
            {"code": gap.code, "subject": gap.subject, "message": gap.message} for gap in exc.gaps
        ]
        unreadable = any(gap["code"] == "workflow_unreadable" for gap in gaps)
        invalid_flags = any(
            gap["code"]
            in {
                "duplicate_flag",
                "invalid_flag_value",
                "missing_flag",
                "unknown_flag",
            }
            for gap in gaps
        )
        outcome = (
            "environment_error"
            if unreadable
            else "usage_error"
            if invalid_flags
            else "configuration_error"
        )
        result = _sequencer_error(
            command,
            outcome,
            gaps[0]["message"],
            context=context,
            gaps=gaps,
        )
        _exit_with_sequencer_result(
            result,
            diagnostic=True,
        )
    except (GitProbeError, StateError, EvaluationError, OSError) as exc:
        result = _sequencer_error(
            command,
            "environment_error",
            str(exc),
            context=context,
        )
        _exit_with_sequencer_result(result, diagnostic=True)
    except KeyboardInterrupt:
        result = _sequencer_error(
            command,
            "interrupted",
            "Interrupted by user",
            context=context,
        )
        _exit_with_sequencer_result(result, diagnostic=True)
    except Exception as exc:
        result = _sequencer_error(
            command,
            "internal_error",
            str(exc),
            context=context,
        )
        _exit_with_sequencer_result(result, diagnostic=True)
    _exit_with_sequencer_result(result)


@sequencer.command(name="init")
@click.option(
    "--workflow",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
    help="Workflow YAML file.",
)
@click.option(
    "--flag",
    "flags",
    multiple=True,
    metavar="NAME=VALUE",
    help="Resolve one declared workflow flag.",
)
@click.pass_obj
def sequencer_init(
    context: _SequencerContext,
    workflow: Path,
    flags: tuple[str, ...],
) -> int:
    """Initialize a workflow run."""
    parsed_flags = _parse_flags(flags)
    return _run_sequencer(
        "init",
        context,
        lambda: SequencerService().init(
            context.repository,
            context.run_id,
            workflow,
            parsed_flags,
        ),
    )


def _workflow_option[**Parameters, Return](
    function: Callable[Parameters, Return],
) -> Callable[Parameters, Return]:
    """Add the common optional workflow source override."""
    decorator = click.option(
        "--workflow",
        type=click.Path(path_type=Path, dir_okay=False),
        help="Workflow YAML source override.",
    )
    return cast("Callable[Parameters, Return]", decorator(function))


@sequencer.command(name="next")
@_workflow_option
@click.pass_obj
def sequencer_next(context: _SequencerContext, workflow: Path | None) -> int:
    """Return the current instruction without advancing."""
    return _run_sequencer(
        "next",
        context,
        lambda: SequencerService().next(context.repository, context.run_id, workflow),
    )


@sequencer.command(name="status")
@_workflow_option
@click.pass_obj
def sequencer_status(context: _SequencerContext, workflow: Path | None) -> int:
    """Report the persisted cursor and configuration health."""
    return _run_sequencer(
        "status",
        context,
        lambda: SequencerService().status(context.repository, context.run_id, workflow),
    )


@sequencer.command(name="done")
@click.argument("step")
@_workflow_option
@click.option("--force", is_flag=True, help="Advance despite failed postconditions.")
@click.option("--recover", is_flag=True, help="Acknowledge and reissue a mutating step.")
@click.pass_obj
def sequencer_done(
    context: _SequencerContext,
    *,
    step: str,
    workflow: Path | None,
    force: bool,
    recover: bool,
) -> int:
    """Validate and advance the current step."""
    _validate_protocol_id(step, "step ID")
    if force and recover:
        message = "--force and --recover are mutually exclusive"
        raise click.UsageError(message, ctx=click.get_current_context())
    return _run_sequencer(
        "done",
        context,
        lambda: SequencerService().done(
            context.repository,
            context.run_id,
            step,
            DoneOptions(workflow_path=workflow, force=force, recover=recover),
        ),
    )


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
