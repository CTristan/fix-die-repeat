"""Tests for read-only sequencer Git predicates."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from fix_die_repeat import sequencer_git
from fix_die_repeat.sequencer_git import (
    GitProbeError,
    capture_snapshot,
    evaluate_git_operation,
    resolve_repository,
)


def _resolve_git_path() -> str:
    path = shutil.which("git")
    if path is None:
        pytest.skip("Git is required for sequencer Git tests")
    return path


GIT_PATH = _resolve_git_path()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [GIT_PATH, "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path, *, commit: bool = True) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "tests@example.invalid")
    _git(path, "config", "user.name", "Tests")
    _git(path, "config", "commit.gpgsign", "false")
    if commit:
        (path / "tracked.txt").write_text("initial\n")
        _git(path, "add", "tracked.txt")
        _git(path, "commit", "-m", "Initial")
    return path


def test_resolve_repository_uses_worktree_specific_identity(tmp_path: Path) -> None:
    """Separate clones cannot share a sequencer state key."""
    first = _init_repo(tmp_path / "first")
    second = _init_repo(tmp_path / "second")

    first_info = resolve_repository(first)
    second_info = resolve_repository(second)

    assert first_info.key != second_info.key
    assert first_info.root == first.resolve()


def test_snapshot_detects_edit_when_tree_was_already_dirty(tmp_path: Path) -> None:
    """Content snapshots distinguish two dirty states."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("dirty before\n")
    before = capture_snapshot(resolve_repository(repo))

    (repo / "tracked.txt").write_text("dirty after\n")
    after = capture_snapshot(resolve_repository(repo))

    assert before.dirty.unstaged
    assert after.digest != before.digest


def test_snapshot_reuses_diff_and_untracked_probe_output(tmp_path: Path) -> None:
    """One snapshot does not repeat its dirty-state Git probes."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "untracked.txt").write_text("untracked\n")

    with patch.object(
        sequencer_git,
        "_run_git",
        wraps=sequencer_git._run_git,
    ) as run_git:
        capture_snapshot(resolve_repository(repo))

    arguments = [call.args[1] for call in run_git.call_args_list]
    assert arguments.count(["ls-files", "--others", "--exclude-standard", "-z"]) == 1
    assert ["diff", "--cached", "--name-only"] not in arguments
    assert ["diff", "--name-only"] not in arguments


def test_git_probes_decode_raw_paths_with_surrogateescape(tmp_path: Path) -> None:
    """Git path output uses a byte-preserving decoder."""
    with patch(
        "fix_die_repeat.sequencer_git.run_command",
        return_value=(0, "invalid-\udcff.txt\0", ""),
    ) as run_command:
        result = sequencer_git._run_git(
            tmp_path,
            ["ls-files", "--others", "--exclude-standard", "-z"],
        )

    assert result.stdout == "invalid-\udcff.txt\0"
    assert run_command.call_args.kwargs["encoding_errors"] == "surrogateescape"


def test_head_changed_detects_symbolic_ref_change_at_same_commit(tmp_path: Path) -> None:
    """Switching branches at one commit still counts as HEAD drift."""
    repo = _init_repo(tmp_path / "repo")
    info = resolve_repository(repo)
    baseline = capture_snapshot(info)
    _git(repo, "branch", "other")
    _git(repo, "switch", "other")

    assert evaluate_git_operation("git.head_changed", info, initial=baseline)


def test_dirty_predicates_cover_staged_unstaged_and_untracked(tmp_path: Path) -> None:
    """Each live dirty predicate reports only its own Git state."""
    repo = _init_repo(tmp_path / "repo")
    info = resolve_repository(repo)
    (repo / "tracked.txt").write_text("unstaged\n")
    (repo / "untracked.txt").write_text("untracked\n")

    assert evaluate_git_operation("git.has_unstaged_changes", info)
    assert evaluate_git_operation("git.has_untracked_changes", info)
    assert not evaluate_git_operation("git.has_staged_changes", info)

    _git(repo, "add", "tracked.txt")
    assert evaluate_git_operation("git.has_staged_changes", info)


def test_dirty_predicates_do_not_capture_content_snapshot(tmp_path: Path) -> None:
    """Boolean-only predicates avoid hashing repository content."""
    repo = _init_repo(tmp_path / "repo")
    info = resolve_repository(repo)

    with patch.object(
        sequencer_git,
        "capture_snapshot",
        side_effect=AssertionError("content snapshot was requested"),
    ):
        assert evaluate_git_operation("git.is_clean", info)


def test_unpushed_is_false_for_unborn_repository(tmp_path: Path) -> None:
    """An unborn repository has no commit to push."""
    repo = _init_repo(tmp_path / "repo", commit=False)

    assert not evaluate_git_operation(
        "git.has_unpushed_commits",
        resolve_repository(repo),
    )


def test_unpushed_requires_remote_tracking_truth(tmp_path: Path) -> None:
    """A committed repository without remote refs fails closed."""
    repo = _init_repo(tmp_path / "repo")

    with pytest.raises(GitProbeError, match="remote-tracking"):
        evaluate_git_operation("git.has_unpushed_commits", resolve_repository(repo))


def test_unpushed_supports_detached_head_contained_by_remote_ref(tmp_path: Path) -> None:
    """Detached HEAD is pushed when a local remote-tracking ref contains it."""
    remote = _init_repo(tmp_path / "remote")
    _git(remote, "config", "receive.denyCurrentBranch", "ignore")
    repo = tmp_path / "clone"
    subprocess.run(
        [GIT_PATH, "clone", str(remote), str(repo)],
        check=True,
        capture_output=True,
    )
    _git(repo, "checkout", "--detach")

    assert not evaluate_git_operation(
        "git.has_unpushed_commits",
        resolve_repository(repo),
    )


def test_deleted_configured_upstream_fails_closed(tmp_path: Path) -> None:
    """A configured upstream that no longer resolves is not ordinary false."""
    remote = _init_repo(tmp_path / "remote")
    repo = tmp_path / "clone"
    subprocess.run(
        [GIT_PATH, "clone", str(remote), str(repo)],
        check=True,
        capture_output=True,
    )
    _git(repo, "branch", "--set-upstream-to", "origin/main")
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")

    with pytest.raises(GitProbeError, match="configured upstream"):
        evaluate_git_operation("git.has_unpushed_commits", resolve_repository(repo))


def test_working_tree_changed_requires_issued_snapshot(tmp_path: Path) -> None:
    """The mutating-step predicate compares against its issue baseline."""
    repo = _init_repo(tmp_path / "repo")
    info = resolve_repository(repo)
    issued = capture_snapshot(info)
    assert not evaluate_git_operation(
        "git.working_tree_changed",
        info,
        issued=issued,
    )

    (repo / "new.txt").write_text("work\n")
    assert evaluate_git_operation(
        "git.working_tree_changed",
        info,
        issued=issued,
    )


def test_git_probe_rejects_non_repository(tmp_path: Path) -> None:
    """Probe failures remain environment errors."""
    with pytest.raises(GitProbeError, match="Git working tree"):
        resolve_repository(tmp_path)
