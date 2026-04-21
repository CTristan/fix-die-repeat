"""Idempotent installer for the pi-bridge Node.js dependencies.

Runs ``npm ci`` (or ``npm install`` if no lockfile) in ``priv/pi-bridge/``
on first use, writes a marker file on success, and short-circuits on
subsequent runs when the marker exists and matches the expected version.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from pathlib import Path

INSTALL_MARKER = ".install-marker"
_INSTALL_TIMEOUT_SECONDS = 600  # npm ci can be slow on cold caches


class BridgeInstallError(RuntimeError):
    """Raised when the pi-bridge dependencies cannot be installed."""


def _read_package_version(package_json: Path, dep_name: str) -> str:
    """Return the pinned version of ``dep_name`` in a bridge ``package.json``."""
    try:
        data = json.loads(package_json.read_text())
    except (OSError, json.JSONDecodeError) as err:
        msg = f"Could not read {package_json}: {err}"
        raise BridgeInstallError(msg) from err
    version = data.get("dependencies", {}).get(dep_name)
    if not isinstance(version, str) or not version:
        msg = f"{package_json} is missing the '{dep_name}' dependency entry"
        raise BridgeInstallError(msg)
    return version


def _marker_matches(marker: Path, expected_version: str) -> bool:
    """Return True when the install marker records ``expected_version``."""
    if not marker.exists():
        return False
    try:
        content = marker.read_text().strip()
    except OSError:
        return False
    return content == expected_version


def _missing_dependency_error(tool: str) -> BridgeInstallError:
    """Build a ``BridgeInstallError`` for a missing CLI tool (node or npm)."""
    msg = (
        f"fix-die-repeat requires {tool} on PATH for the pi bridge. "
        "Install Node.js >=20 (via Homebrew, nvm, or https://nodejs.org) and re-run."
    )
    return BridgeInstallError(msg)


def ensure_bridge_installed(
    bridge_dir: Path,
    *,
    logger: logging.Logger,
    pi_package: str = "@mariozechner/pi-coding-agent",
) -> Path:
    """Install pi-bridge dependencies if needed; return the bridge.js path.

    Raises :class:`BridgeInstallError` if ``node`` or ``npm`` are missing,
    if the install fails, or if the bridge directory is malformed.
    """
    bridge_script = bridge_dir / "bridge.js"
    package_json = bridge_dir / "package.json"
    node_modules = bridge_dir / "node_modules"
    marker = node_modules / INSTALL_MARKER
    lockfile = bridge_dir / "package-lock.json"

    if not bridge_script.exists():
        msg = f"pi-bridge script missing: {bridge_script}"
        raise BridgeInstallError(msg)
    if not package_json.exists():
        msg = f"pi-bridge manifest missing: {package_json}"
        raise BridgeInstallError(msg)

    expected_version = _read_package_version(package_json, pi_package)

    if _marker_matches(marker, expected_version):
        logger.debug(
            "pi-bridge already installed (%s=%s); skipping npm ci", pi_package, expected_version
        )
        return bridge_script

    if shutil.which("node") is None:
        err_node = _missing_dependency_error("Node.js")
        raise err_node
    if shutil.which("npm") is None:
        err_npm = _missing_dependency_error("npm")
        raise err_npm

    install_cmd = ["npm", "ci"] if lockfile.exists() else ["npm", "install"]
    logger.info(
        "Installing pi-bridge dependencies (%s) in %s...", " ".join(install_cmd), bridge_dir
    )

    try:
        result = subprocess.run(  # noqa: S603 — trusted npm binary
            install_cmd,
            cwd=bridge_dir,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        msg = f"pi-bridge dependency install timed out after {_INSTALL_TIMEOUT_SECONDS}s"
        raise BridgeInstallError(msg) from err
    except FileNotFoundError as err:
        err_npm_exec = _missing_dependency_error("npm")
        raise err_npm_exec from err

    if result.returncode != 0:
        msg = (
            f"pi-bridge dependency install failed (exit {result.returncode})."
            f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        raise BridgeInstallError(msg)

    node_modules.mkdir(parents=True, exist_ok=True)
    marker.write_text(expected_version)
    logger.info("pi-bridge dependencies installed (%s=%s)", pi_package, expected_version)
    return bridge_script
