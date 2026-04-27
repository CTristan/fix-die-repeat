"""Unit tests for ``ensure_bridge_installed``."""

from __future__ import annotations

import json
import logging
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from fix_die_repeat.bridge_install import (
    INSTALL_MARKER,
    BridgeInstallError,
    _check_node_version,
    _compute_install_hash,
    ensure_bridge_installed,
)

PI_PACKAGE = "@mariozechner/pi-coding-agent"


def _make_bridge_dir(
    tmp_path: Path,
    *,
    version: str = "0.67.68",
    with_lockfile: bool = True,
    name: str = "pi-bridge",
) -> Path:
    bridge_dir = tmp_path / name
    bridge_dir.mkdir()
    (bridge_dir / "bridge.js").write_text("// fake\n")
    (bridge_dir / "package.json").write_text(json.dumps({"dependencies": {PI_PACKAGE: version}}))
    if with_lockfile:
        (bridge_dir / "package-lock.json").write_text("{}")
    return bridge_dir


def _logger() -> logging.Logger:
    return logging.getLogger("test-bridge-install")


class TestEnsureBridgeInstalled:
    """Covers marker short-circuit, npm install, and missing-tool errors."""

    def test_marker_short_circuits(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path)
        (bridge_dir / "node_modules").mkdir()
        (bridge_dir / "node_modules" / INSTALL_MARKER).write_text(_compute_install_hash(bridge_dir))

        with patch("fix_die_repeat.bridge_install.subprocess.run") as mock_run:
            script = ensure_bridge_installed(bridge_dir, bridge_dir, logger=_logger())

        assert script == bridge_dir / "bridge.js"
        mock_run.assert_not_called()


class TestEnsureBridgeInstalledSeparateRuntime:
    """When source and runtime dirs differ, install lands in the writable runtime.

    Rationale: in an installed wheel, ``source_dir`` points inside site-packages
    and may be read-only. Writing ``node_modules/`` there would fail first-run
    even though ``FDR_HOME`` is writable. The installer must copy the shipped
    files into the runtime dir and install there.
    """

    def test_copies_shipped_files_into_runtime_before_install(self, tmp_path: Path) -> None:
        source_dir = _make_bridge_dir(tmp_path, name="source")
        runtime_dir = tmp_path / "runtime"
        # runtime_dir does not exist yet — installer must create it.

        fake_result = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch(
                "fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result
            ) as mock_run,
        ):
            script = ensure_bridge_installed(source_dir, runtime_dir, logger=_logger())

        # npm ci runs in runtime_dir, not source_dir
        assert mock_run.call_args.kwargs["cwd"] == runtime_dir
        # Shipped files are staged in runtime_dir
        assert (runtime_dir / "bridge.js").exists()
        assert (runtime_dir / "package.json").exists()
        assert (runtime_dir / "package-lock.json").exists()
        # Marker lives in runtime_dir's node_modules, never in source_dir
        assert (runtime_dir / "node_modules" / INSTALL_MARKER).exists()
        assert not (source_dir / "node_modules").exists()
        # Returned script points at the runtime copy so Node's module resolution
        # finds node_modules next to it.
        assert script == runtime_dir / "bridge.js"

    def test_marker_in_runtime_short_circuits_install(self, tmp_path: Path) -> None:
        source_dir = _make_bridge_dir(tmp_path, name="source")
        runtime_dir = tmp_path / "runtime"
        runtime_dir.mkdir()
        # Pre-stage files + marker matching the source's computed hash
        (runtime_dir / "bridge.js").write_text("// fake\n")
        (runtime_dir / "package.json").write_text(
            json.dumps({"dependencies": {PI_PACKAGE: "0.67.68"}})
        )
        (runtime_dir / "node_modules").mkdir()
        (runtime_dir / "node_modules" / INSTALL_MARKER).write_text(
            _compute_install_hash(source_dir)
        )

        with patch("fix_die_repeat.bridge_install.subprocess.run") as mock_run:
            script = ensure_bridge_installed(source_dir, runtime_dir, logger=_logger())

        mock_run.assert_not_called()
        assert script == runtime_dir / "bridge.js"

    def test_installs_when_marker_missing(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path)

        fake_result = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch(
                "fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result
            ) as mock_run,
        ):
            ensure_bridge_installed(bridge_dir, bridge_dir, logger=_logger())

        # npm ci invoked (lockfile present)
        called_cmd = mock_run.call_args.args[0]
        assert called_cmd == ["npm", "ci"]

        marker = bridge_dir / "node_modules" / INSTALL_MARKER
        assert marker.exists()
        assert marker.read_text() == _compute_install_hash(bridge_dir)

    def test_uses_npm_install_when_no_lockfile(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path, with_lockfile=False)
        fake_result = MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch(
                "fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result
            ) as mock_run,
        ):
            ensure_bridge_installed(bridge_dir, bridge_dir, logger=_logger())

        assert mock_run.call_args.args[0] == ["npm", "install"]

    def test_raises_when_node_missing(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path)
        with patch("fix_die_repeat.bridge_install.shutil.which", return_value=None):
            with pytest.raises(BridgeInstallError, match=r"Node\.js"):
                ensure_bridge_installed(bridge_dir, bridge_dir, logger=_logger())

    def test_raises_when_npm_missing(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path)

        def which_side_effect(tool: str) -> str | None:
            return "/usr/local/bin/node" if tool == "node" else None

        with patch("fix_die_repeat.bridge_install.shutil.which", side_effect=which_side_effect):
            with pytest.raises(BridgeInstallError, match="npm"):
                ensure_bridge_installed(bridge_dir, bridge_dir, logger=_logger())

    def test_raises_when_install_fails(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path)
        fake_result = MagicMock(returncode=1, stdout="npm error", stderr="fetch failed")

        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch("fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result),
        ):
            with pytest.raises(BridgeInstallError, match="install failed"):
                ensure_bridge_installed(bridge_dir, bridge_dir, logger=_logger())

        # Marker should NOT be written on failure
        assert not (bridge_dir / "node_modules" / INSTALL_MARKER).exists()

    def test_raises_when_install_times_out(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path)

        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch(
                "fix_die_repeat.bridge_install.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="npm", timeout=600),
            ),
        ):
            with pytest.raises(BridgeInstallError, match="timed out"):
                ensure_bridge_installed(bridge_dir, bridge_dir, logger=_logger())

    def test_raises_when_bridge_script_missing(self, tmp_path: Path) -> None:
        bridge_dir = tmp_path / "pi-bridge"
        bridge_dir.mkdir()
        # No bridge.js; package.json present
        (bridge_dir / "package.json").write_text(
            json.dumps({"dependencies": {PI_PACKAGE: "0.67.68"}})
        )
        with pytest.raises(BridgeInstallError, match="script missing"):
            ensure_bridge_installed(bridge_dir, bridge_dir, logger=_logger())

    def test_reinstalls_when_version_changes(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path, version="0.68.0")
        (bridge_dir / "node_modules").mkdir()
        # Stale marker (could be from a previous version or any other pre-install state)
        (bridge_dir / "node_modules" / INSTALL_MARKER).write_text("stale-marker")

        fake_result = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch("fix_die_repeat.bridge_install._check_node_version"),
            patch(
                "fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result
            ) as mock_run,
        ):
            ensure_bridge_installed(bridge_dir, bridge_dir, logger=_logger())

        mock_run.assert_called_once()
        assert (bridge_dir / "node_modules" / INSTALL_MARKER).read_text() == _compute_install_hash(
            bridge_dir
        )

    def test_reinstalls_when_bridge_js_changes_but_pi_version_does_not(
        self, tmp_path: Path
    ) -> None:
        """Regression: hash-based marker catches non-version shipped changes.

        A marker keyed only to the pi-coding-agent version missed shipped
        changes that didn't bump that one field (e.g. a new ``bridge.js``
        or a refreshed ``package-lock.json``). The hash catches them.
        """
        bridge_dir = _make_bridge_dir(tmp_path)
        (bridge_dir / "node_modules").mkdir()
        # Simulate a prior install whose marker matched the old bridge.js contents.
        (bridge_dir / "node_modules" / INSTALL_MARKER).write_text(_compute_install_hash(bridge_dir))
        # Ship a new bridge.js while keeping the pi-coding-agent pin identical.
        (bridge_dir / "bridge.js").write_text("// updated bridge\n")

        fake_result = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch("fix_die_repeat.bridge_install._check_node_version"),
            patch(
                "fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result
            ) as mock_run,
        ):
            ensure_bridge_installed(bridge_dir, bridge_dir, logger=_logger())

        mock_run.assert_called_once()
        assert (bridge_dir / "node_modules" / INSTALL_MARKER).read_text() == _compute_install_hash(
            bridge_dir
        )

    def test_stage_removes_stale_runtime_files_when_source_lacks_them(self, tmp_path: Path) -> None:
        """Regression: staging deletes runtime files missing from source.

        When the source no longer ships a ``package-lock.json``, the staged
        copy must go too, so ``npm ci`` / ``npm install`` selection is driven
        by the current source, not a leftover staged lockfile.
        """
        source_dir = _make_bridge_dir(tmp_path, name="source", with_lockfile=False)
        runtime_dir = tmp_path / "runtime"
        runtime_dir.mkdir()
        # A lockfile left behind from a previous source that shipped one.
        stale_lockfile = runtime_dir / "package-lock.json"
        stale_lockfile.write_text("{}")

        fake_result = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch(
                "fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result
            ) as mock_run,
        ):
            ensure_bridge_installed(source_dir, runtime_dir, logger=_logger())

        # Stale lockfile removed; npm install (not npm ci) runs because there's no lockfile now.
        assert not stale_lockfile.exists()
        assert mock_run.call_args.args[0] == ["npm", "install"]


class TestCheckNodeVersion:
    """Covers the Node >=20 version check.

    The bridge's ``package.json`` requires Node 20+; validating at install time
    surfaces a clear upgrade message instead of letting ``npm ci`` or the
    runtime fail with a less-actionable error.
    """

    def test_raises_when_node_is_too_old(self) -> None:
        fake_result = MagicMock(returncode=0, stdout="v18.17.0\n", stderr="")
        with patch("fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result):
            with pytest.raises(BridgeInstallError, match=r"requires Node\.js >=20"):
                _check_node_version("/usr/local/bin/node", _logger())

    def test_passes_when_node_meets_minimum(self) -> None:
        fake_result = MagicMock(returncode=0, stdout="v20.11.0\n", stderr="")
        with patch("fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result):
            _check_node_version("/usr/local/bin/node", _logger())

    def test_passes_when_node_exceeds_minimum(self) -> None:
        fake_result = MagicMock(returncode=0, stdout="v22.3.0\n", stderr="")
        with patch("fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result):
            _check_node_version("/usr/local/bin/node", _logger())

    def test_skips_gracefully_on_unparseable_output(self) -> None:
        """Malformed ``node --version`` output falls through to npm/runtime errors.

        We don't want to synthesize a misleading "too old" error when we can't
        even read the version — npm and the runtime will surface the real issue.
        """
        fake_result = MagicMock(returncode=0, stdout="not-a-version\n", stderr="")
        with patch("fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result):
            _check_node_version("/usr/local/bin/node", _logger())

    def test_skips_gracefully_on_nonzero_exit(self) -> None:
        fake_result = MagicMock(returncode=1, stdout="", stderr="some error")
        with patch("fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result):
            _check_node_version("/usr/local/bin/node", _logger())

    def test_skips_gracefully_on_invocation_failure(self) -> None:
        with patch(
            "fix_die_repeat.bridge_install.subprocess.run",
            side_effect=OSError("node gone"),
        ):
            _check_node_version("/usr/local/bin/node", _logger())

    def test_raises_on_too_old_from_ensure_bridge_installed(self, tmp_path: Path) -> None:
        """``ensure_bridge_installed`` propagates the version-too-old error."""
        bridge_dir = _make_bridge_dir(tmp_path)
        fake_old_node = MagicMock(returncode=0, stdout="v18.17.0\n", stderr="")
        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch("fix_die_repeat.bridge_install.subprocess.run", return_value=fake_old_node),
        ):
            with pytest.raises(BridgeInstallError, match=r"requires Node\.js >=20"):
                ensure_bridge_installed(bridge_dir, bridge_dir, logger=_logger())
