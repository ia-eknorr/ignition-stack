"""End-to-end CLI tests for `ignition-stack modules ...`.

These drive the typer app through `CliRunner` rather than calling
library functions directly. The Phase 3 validation gates name the
command form on purpose - the CLI wiring is the contract a user sees.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from typer.testing import CliRunner

from ignition_stack.catalog.schema import SHA256_UNPINNED
from ignition_stack.cli import app

runner = CliRunner()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fixture_catalog(
    tmp_path: Path,
    *,
    mqtt_url: str,
    mqtt_sha: str,
    mcp_local_source: str | None = None,
) -> Path:
    """A minimal fixture catalog with one mqtt-engine entry pointed at the
    local file server, plus the MCP manual-download entry. Used in both
    `validate` and `download` CLI tests so the asserted command line
    matches the validation gate verbatim.
    """
    entries = [
        {
            "name": "mqtt-engine",
            "kind": "module",
            "vendor": "cirrus-link",
            "ignition_versions": ["8.3.6"],
            "module_identifier": "com.cirruslink.mqtt.engine.gateway",
            "download_url": mqtt_url,
            "sha256": mqtt_sha,
            "install_path": "/usr/local/bin/ignition/user-lib/modules/MQTT-Engine.modl",
        },
        {
            "name": "mcp-module",
            "kind": "module",
            "vendor": "inductive-automation",
            "ignition_versions": ["8.3.6"],
            "module_identifier": "com.inductiveautomation.mcp.gateway",
            "download_url": None,
            "sha256": SHA256_UNPINNED,
            "install_path": "/usr/local/bin/ignition/user-lib/modules/MCP.modl",
            "requires_manual_download": True,
            "local_source_path": mcp_local_source,
        },
    ]
    path = tmp_path / "modules.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "entries": entries}, sort_keys=False),
        encoding="utf-8",
    )
    return path


def test_modules_list_exits_zero(tmp_path: Path, file_server) -> None:
    base_url, served = file_server
    (served / "MQTT-Engine.modl").write_bytes(b"x")
    catalog = _fixture_catalog(tmp_path, mqtt_url=f"{base_url}/MQTT-Engine.modl", mqtt_sha=_sha256(b"x"))

    result = runner.invoke(app, ["modules", "list", "--catalog", str(catalog)])

    assert result.exit_code == 0, result.output
    assert "mqtt-engine" in result.output
    assert "mcp-module" in result.output


def test_modules_validate_exit_zero_when_reachable_and_pinned(
    tmp_path: Path,
    file_server,
) -> None:
    """Gate: `validate` exits 0 when every URL is reachable and every checksum matches."""
    base_url, served = file_server
    artifact = b"mqtt engine fixture bytes"
    (served / "MQTT-Engine.modl").write_bytes(artifact)
    catalog = _fixture_catalog(
        tmp_path,
        mqtt_url=f"{base_url}/MQTT-Engine.modl",
        mqtt_sha=_sha256(artifact),
    )

    result = runner.invoke(app, ["modules", "validate", "--catalog", str(catalog)])

    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_modules_validate_names_unreachable_entry(tmp_path: Path) -> None:
    """Gate: non-zero exit names the offending entry when a URL fails."""
    catalog = _fixture_catalog(
        tmp_path,
        mqtt_url="http://127.0.0.1:1/nope.modl",  # nothing listening
        mqtt_sha=_sha256(b"unused"),
    )

    result = runner.invoke(app, ["modules", "validate", "--catalog", str(catalog)])

    assert result.exit_code == 1
    assert "mqtt-engine" in result.output


def test_modules_download_writes_to_cache_with_matching_sha(
    tmp_path: Path,
    file_server,
) -> None:
    """Gate: `modules download --ignition-version 8.3.6 mqtt-engine` writes
    the .modl to modules/cache/ and the file's sha256 matches the catalog."""
    base_url, served = file_server
    artifact = b"mqtt engine fixture bytes for download gate"
    (served / "MQTT-Engine.modl").write_bytes(artifact)
    catalog = _fixture_catalog(
        tmp_path,
        mqtt_url=f"{base_url}/MQTT-Engine.modl",
        mqtt_sha=_sha256(artifact),
    )

    cache_dir = tmp_path / "cache"
    result = runner.invoke(
        app,
        [
            "modules",
            "download",
            "--catalog",
            str(catalog),
            "--ignition-version",
            "8.3.6",
            "--cache-dir",
            str(cache_dir),
            "mqtt-engine",
        ],
    )

    assert result.exit_code == 0, result.output
    cached = cache_dir / "MQTT-Engine.modl"
    assert cached.is_file()
    assert hashlib.sha256(cached.read_bytes()).hexdigest() == _sha256(artifact)


def test_modules_download_skips_mcp_with_explanation(
    tmp_path: Path,
    file_server,
) -> None:
    """Gate: the MCP entry is marked requires_manual_download: true and is
    skipped by download with an explanatory message."""
    base_url, served = file_server
    (served / "MQTT-Engine.modl").write_bytes(b"x")
    catalog = _fixture_catalog(
        tmp_path,
        mqtt_url=f"{base_url}/MQTT-Engine.modl",
        mqtt_sha=_sha256(b"x"),
    )

    cache_dir = tmp_path / "cache"
    result = runner.invoke(
        app,
        [
            "modules",
            "download",
            "--catalog",
            str(catalog),
            "--cache-dir",
            str(cache_dir),
            "mcp-module",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "skipped-manual" in result.output
    assert "manual download" in result.output
    # No file should land in the cache for the manual entry.
    assert not (cache_dir / "MCP.modl").exists()


def test_modules_download_offline_fails_when_cache_missing(
    tmp_path: Path,
    file_server,
) -> None:
    base_url, served = file_server
    (served / "MQTT-Engine.modl").write_bytes(b"x")
    catalog = _fixture_catalog(
        tmp_path,
        mqtt_url=f"{base_url}/MQTT-Engine.modl",
        mqtt_sha=_sha256(b"x"),
    )

    result = runner.invoke(
        app,
        [
            "modules",
            "download",
            "--catalog",
            str(catalog),
            "--cache-dir",
            str(tmp_path / "empty-cache"),
            "--offline",
            "mqtt-engine",
        ],
    )

    assert result.exit_code == 1
    assert "--offline" in result.output


def test_modules_download_unknown_entry_exits_2(tmp_path: Path, file_server) -> None:
    base_url, served = file_server
    (served / "MQTT-Engine.modl").write_bytes(b"x")
    catalog = _fixture_catalog(
        tmp_path,
        mqtt_url=f"{base_url}/MQTT-Engine.modl",
        mqtt_sha=_sha256(b"x"),
    )

    result = runner.invoke(
        app,
        [
            "modules",
            "download",
            "--catalog",
            str(catalog),
            "--cache-dir",
            str(tmp_path / "cache"),
            "no-such-entry",
        ],
    )

    assert result.exit_code == 2
    assert "no-such-entry" in result.output
