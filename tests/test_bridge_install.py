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
    ensure_bridge_installed,
)

PI_PACKAGE = "@mariozechner/pi-coding-agent"


def _make_bridge_dir(
    tmp_path: Path,
    *,
    version: str = "0.67.68",
    with_lockfile: bool = True,
) -> Path:
    bridge_dir = tmp_path / "pi-bridge"
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
        (bridge_dir / "node_modules" / INSTALL_MARKER).write_text("0.67.68")

        with patch("fix_die_repeat.bridge_install.subprocess.run") as mock_run:
            script = ensure_bridge_installed(bridge_dir, logger=_logger())

        assert script == bridge_dir / "bridge.js"
        mock_run.assert_not_called()

    def test_installs_when_marker_missing(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path)

        fake_result = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch(
                "fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result
            ) as mock_run,
        ):
            ensure_bridge_installed(bridge_dir, logger=_logger())

        # npm ci invoked (lockfile present)
        called_cmd = mock_run.call_args.args[0]
        assert called_cmd == ["npm", "ci"]

        marker = bridge_dir / "node_modules" / INSTALL_MARKER
        assert marker.exists()
        assert marker.read_text() == "0.67.68"

    def test_uses_npm_install_when_no_lockfile(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path, with_lockfile=False)
        fake_result = MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch(
                "fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result
            ) as mock_run,
        ):
            ensure_bridge_installed(bridge_dir, logger=_logger())

        assert mock_run.call_args.args[0] == ["npm", "install"]

    def test_raises_when_node_missing(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path)
        with patch("fix_die_repeat.bridge_install.shutil.which", return_value=None):
            with pytest.raises(BridgeInstallError, match=r"Node\.js"):
                ensure_bridge_installed(bridge_dir, logger=_logger())

    def test_raises_when_npm_missing(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path)

        def which_side_effect(tool: str) -> str | None:
            return "/usr/local/bin/node" if tool == "node" else None

        with patch("fix_die_repeat.bridge_install.shutil.which", side_effect=which_side_effect):
            with pytest.raises(BridgeInstallError, match="npm"):
                ensure_bridge_installed(bridge_dir, logger=_logger())

    def test_raises_when_install_fails(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path)
        fake_result = MagicMock(returncode=1, stdout="npm error", stderr="fetch failed")

        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch("fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result),
        ):
            with pytest.raises(BridgeInstallError, match="install failed"):
                ensure_bridge_installed(bridge_dir, logger=_logger())

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
                ensure_bridge_installed(bridge_dir, logger=_logger())

    def test_raises_when_bridge_script_missing(self, tmp_path: Path) -> None:
        bridge_dir = tmp_path / "pi-bridge"
        bridge_dir.mkdir()
        # No bridge.js; package.json present
        (bridge_dir / "package.json").write_text(
            json.dumps({"dependencies": {PI_PACKAGE: "0.67.68"}})
        )
        with pytest.raises(BridgeInstallError, match="script missing"):
            ensure_bridge_installed(bridge_dir, logger=_logger())

    def test_reinstalls_when_version_changes(self, tmp_path: Path) -> None:
        bridge_dir = _make_bridge_dir(tmp_path, version="0.68.0")
        (bridge_dir / "node_modules").mkdir()
        # Old marker from a previous version
        (bridge_dir / "node_modules" / INSTALL_MARKER).write_text("0.67.68")

        fake_result = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch("fix_die_repeat.bridge_install.shutil.which", return_value="/usr/local/bin/node"),
            patch(
                "fix_die_repeat.bridge_install.subprocess.run", return_value=fake_result
            ) as mock_run,
        ):
            ensure_bridge_installed(bridge_dir, logger=_logger())

        mock_run.assert_called_once()
        assert (bridge_dir / "node_modules" / INSTALL_MARKER).read_text() == "0.68.0"
