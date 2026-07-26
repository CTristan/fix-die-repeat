"""Tests for read-only sequencer Git predicates."""

import re
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fix_die_repeat import sequencer_git
from fix_die_repeat.sequencer_git import (
    MAX_UNTRACKED_BYTES,
    GitProbeError,
    GitSnapshot,
    capture_snapshot,
    evaluate_git_operation,
    resolve_repository,
)


def _resolve_git_path() -> str:
    path = shutil.which("git")
    if path is None:
        pytest.skip("Git is required for sequencer Git tests", allow_module_level=True)
    return path


GIT_PATH = _resolve_git_path()


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep test repositories independent of ambient Git configuration."""
    empty_config = tmp_path / "empty-gitconfig"
    empty_config.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(variable, raising=False)


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


def test_repository_key_sanitizes_worktree_name(tmp_path: Path) -> None:
    """State directory keys contain only portable filename characters."""
    repo = _init_repo(tmp_path / "unsafe name")

    key = resolve_repository(repo).key

    assert re.fullmatch(r"[A-Za-z0-9._-]+", key)
    assert key.startswith("unsafe_name-")


def test_snapshot_detects_edit_when_tree_was_already_dirty(tmp_path: Path) -> None:
    """Content snapshots distinguish two dirty states."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("dirty before\n")
    before = capture_snapshot(resolve_repository(repo))

    (repo / "tracked.txt").write_text("dirty after\n")
    after = capture_snapshot(resolve_repository(repo))

    assert before.dirty.unstaged
    assert after.digest != before.digest


def test_snapshot_rejects_excessive_untracked_content(tmp_path: Path) -> None:
    """Untracked hashing fails closed before exceeding its content budget."""
    repo = _init_repo(tmp_path / "repo")
    large = repo / "large.bin"
    with large.open("wb") as handle:
        handle.truncate(MAX_UNTRACKED_BYTES + 1)

    with pytest.raises(GitProbeError, match="64 MiB"):
        capture_snapshot(resolve_repository(repo))


def test_untracked_snapshot_enforces_limit_while_reading(tmp_path: Path) -> None:
    """A file that grows after metadata capture cannot exceed the content budget."""
    repo = _init_repo(tmp_path / "repo")
    path = repo / "growing.bin"
    path.write_bytes(b"x" * 17)
    metadata = path.lstat()

    with (
        patch.object(sequencer_git, "MAX_UNTRACKED_BYTES", 16),
        patch.object(
            sequencer_git,
            "_untracked_entries",
            return_value=[("growing.bin", path, metadata)],
        ),
        pytest.raises(GitProbeError, match="snapshot limit"),
    ):
        sequencer_git._update_untracked_digest(repo, sha256(), ["growing.bin"])


def test_snapshot_rejects_excessive_tracked_diff_output(tmp_path: Path) -> None:
    """Tracked diff capture stops at its configured content budget."""
    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("changed content\n")

    with (
        patch.object(sequencer_git, "MAX_TRACKED_DIFF_BYTES", 16),
        pytest.raises(GitProbeError, match="Tracked diff content") as raised,
    ):
        capture_snapshot(resolve_repository(repo))

    assert "16 byte snapshot budget" in str(raised.value)


def test_bounded_git_output_reports_timeout(tmp_path: Path) -> None:
    """Bounded Git execution converts subprocess deadlines into probe errors."""
    process = MagicMock()
    process.stdout.read.return_value = b""
    process.wait.side_effect = [
        subprocess.TimeoutExpired(["git"], 30),
        0,
    ]

    with (
        patch.object(subprocess, "Popen", return_value=process),
        pytest.raises(GitProbeError, match="timed out"),
    ):
        sequencer_git._run_git_bounded(tmp_path, ["diff"], 16)


def test_bounded_git_output_reports_nonzero_output(tmp_path: Path) -> None:
    """Bounded Git failures include their decoded diagnostic output."""
    process = MagicMock()
    process.stdout.read.side_effect = [b"fatal: failed\n", b""]
    process.wait.return_value = 1

    with (
        patch.object(subprocess, "Popen", return_value=process),
        pytest.raises(GitProbeError, match="fatal: failed"),
    ):
        sequencer_git._run_git_bounded(tmp_path, ["diff"], 64)


def test_bounded_git_output_reports_reader_failure(tmp_path: Path) -> None:
    """Reader-thread failures cannot turn partial output into a snapshot."""
    process = MagicMock()
    process.stdout.read.side_effect = OSError("read failed")
    process.wait.return_value = 0

    with (
        patch.object(subprocess, "Popen", return_value=process),
        pytest.raises(GitProbeError, match="Cannot read Git probe output"),
    ):
        sequencer_git._run_git_bounded(tmp_path, ["diff"], 64)


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
    options = run_command.call_args.kwargs["options"]
    assert options.encoding_errors == "surrogateescape"
    assert options.timeout == sequencer_git.GIT_TIMEOUT_SECONDS


def test_head_changed_detects_symbolic_ref_change_at_same_commit(tmp_path: Path) -> None:
    """Switching branches at one commit still counts as HEAD drift."""
    repo = _init_repo(tmp_path / "repo")
    info = resolve_repository(repo)
    baseline = capture_snapshot(info)
    _git(repo, "branch", "other")
    _git(repo, "switch", "other")

    assert evaluate_git_operation("git.head_changed", info, initial=baseline)


def test_head_changed_does_not_capture_content_snapshot(tmp_path: Path) -> None:
    """HEAD comparison avoids hashing repository content."""
    repo = _init_repo(tmp_path / "repo")
    info = resolve_repository(repo)
    baseline = capture_snapshot(info)

    with patch.object(
        sequencer_git,
        "capture_snapshot",
        side_effect=AssertionError("content snapshot was requested"),
    ):
        assert not evaluate_git_operation("git.head_changed", info, initial=baseline)


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


def test_head_probe_timeout_is_not_an_unborn_repository(tmp_path: Path) -> None:
    """A failed HEAD probe cannot silently become an unborn snapshot."""
    result = sequencer_git._GitResult(
        returncode=sequencer_git.COMMAND_TIMEOUT_EXIT_CODE,
        stdout="",
        stderr="Command timed out after 30 seconds",
    )

    with (
        patch.object(sequencer_git, "_run_git", return_value=result),
        pytest.raises(GitProbeError, match="Git HEAD probe failed"),
    ):
        sequencer_git._head(tmp_path)


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"head": 1, "symbolic_ref": None, "dirty": {}, "digest": ""},
        {
            "head": None,
            "symbolic_ref": None,
            "dirty": {"staged": 1, "unstaged": False, "untracked": False},
            "digest": "digest",
        },
    ],
)
def test_snapshot_rejects_invalid_persisted_fields(value: dict[str, object]) -> None:
    """Persisted snapshot corruption uses the Git probe error contract."""
    with pytest.raises(GitProbeError, match="Invalid persisted Git snapshot"):
        GitSnapshot.from_dict(value)


def test_unpushed_rejects_unparsable_rev_list_count(tmp_path: Path) -> None:
    """Malformed Git count output fails closed."""
    repository = sequencer_git.RepositoryInfo(
        root=tmp_path,
        common_dir=tmp_path / ".git",
        key="repo",
    )

    with (
        patch.object(sequencer_git, "_head", return_value=("head", "refs/heads/main")),
        patch.object(
            sequencer_git,
            "_configured_upstream",
            return_value=("origin/main", True),
        ),
        patch.object(sequencer_git, "_stdout", return_value="not-a-count"),
        pytest.raises(GitProbeError, match="unparsable count"),
    ):
        sequencer_git._has_unpushed(repository)


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


def test_working_tree_changed_checks_baseline_before_snapshot(tmp_path: Path) -> None:
    """A missing issued baseline fails before an expensive content snapshot."""
    repo = _init_repo(tmp_path / "repo")
    info = resolve_repository(repo)

    with (
        patch.object(
            sequencer_git,
            "capture_snapshot",
            side_effect=AssertionError("content snapshot was requested"),
        ),
        pytest.raises(GitProbeError, match="requires an issued-step snapshot"),
    ):
        evaluate_git_operation("git.working_tree_changed", info)


def test_git_probe_rejects_non_repository(tmp_path: Path) -> None:
    """Probe failures remain environment errors."""
    with pytest.raises(GitProbeError, match="Git working tree"):
        resolve_repository(tmp_path)


def test_resolve_repository_reports_probe_timeout(tmp_path: Path) -> None:
    """Repository discovery distinguishes a timed-out Git probe."""
    with (
        patch.object(
            sequencer_git,
            "_run_git",
            return_value=sequencer_git._GitResult(
                returncode=sequencer_git.COMMAND_TIMEOUT_EXIT_CODE,
                stdout="",
                stderr="Command timed out after 30 seconds",
            ),
        ),
        pytest.raises(GitProbeError, match="timed out"),
    ):
        resolve_repository(tmp_path)


def test_resolve_repository_rejects_empty_success_output(tmp_path: Path) -> None:
    """Repository discovery requires a root path from a successful probe."""
    with (
        patch.object(
            sequencer_git,
            "_run_git",
            return_value=sequencer_git._GitResult(returncode=0, stdout="", stderr=""),
        ),
        pytest.raises(GitProbeError, match="empty repository root"),
    ):
        resolve_repository(tmp_path)
