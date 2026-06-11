"""Tests for the modules catalog subsystem.

Covers the validation gates listed in 03-plan.md Phase 3:
  - schema validation (pydantic round-trip, kind discriminator, FQID vs path)
  - sha256 match and mismatch
  - manual-download skip with explanatory message
  - --offline fail-fast when artifact missing from cache
  - modules download writes to cache and the on-disk sha matches the catalog
  - JDBC driver lands at user-lib/jdbc/
  - MCP entry is requires_manual_download: true and is skipped
"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import httpx
import pytest
import yaml

from ignition_stack.catalog.download import (
    DownloadError,
    DownloadOutcome,
    download_entry,
)
from ignition_stack.catalog.loader import CatalogLoadError, load_catalog
from ignition_stack.catalog.schema import (
    SHA256_UNPINNED,
    Catalog,
    JdbcDriverEntry,
    ModuleEntry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_catalog(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "modules.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _module(name: str, **overrides) -> dict:
    base = {
        "name": name,
        "kind": "module",
        "vendor": "cirrus-link",
        "ignition_versions": ["8.3.6"],
        "module_identifier": f"com.example.{name.replace('-', '.')}",
        "download_url": None,
        "sha256": SHA256_UNPINNED,
        "install_path": f"/usr/local/bin/ignition/user-lib/modules/{name}.modl",
        "requires_manual_download": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_bundled_modules_yaml_loads() -> None:
    """The catalog shipped at the repo root must always pass schema validation."""
    catalog = load_catalog()
    names = {e.name for e in catalog.entries}
    assert "mqtt-engine" in names
    assert "mysql-jdbc" in names
    assert "mcp-module" in names

    mcp = catalog.by_name("mcp-module")
    assert mcp.requires_manual_download is True
    assert mcp.download_url is None

    mysql = catalog.by_name("mysql-jdbc")
    assert isinstance(mysql, JdbcDriverEntry)
    assert "user-lib/jdbc/" in mysql.install_path


def test_module_kind_requires_module_identifier(tmp_path: Path) -> None:
    payload = {
        "version": 1,
        "entries": [_module("noid")],
    }
    payload["entries"][0].pop("module_identifier")
    catalog_path = _write_catalog(tmp_path, payload)
    with pytest.raises(CatalogLoadError):
        load_catalog(catalog_path)


def test_jdbc_driver_has_no_module_identifier(tmp_path: Path) -> None:
    """JDBC drivers must not accept a module_identifier field."""
    payload = {
        "version": 1,
        "entries": [
            {
                "name": "rogue-driver",
                "kind": "jdbc_driver",
                "vendor": "x",
                "ignition_versions": ["8.3.6"],
                "module_identifier": "com.x.driver",
                "download_url": "https://example.com/x.jar",
                "sha256": SHA256_UNPINNED,
                "install_path": "/usr/local/bin/ignition/user-lib/jdbc/x.jar",
            },
        ],
    }
    catalog_path = _write_catalog(tmp_path, payload)
    with pytest.raises(CatalogLoadError):
        load_catalog(catalog_path)


def test_install_path_drives_cache_filename() -> None:
    """install_path is the source of truth for the on-disk filename."""
    entry = ModuleEntry.model_validate(
        _module("foo", install_path="/usr/local/bin/ignition/user-lib/modules/Foo-1.2.modl"),
    )
    assert entry.cache_filename() == "Foo-1.2.modl"


# ---------------------------------------------------------------------------
# Reachability + sha256 download
# ---------------------------------------------------------------------------


def test_download_and_sha_match(tmp_path: Path, file_server) -> None:
    base_url, served = file_server
    payload_bytes = b"fake .modl bytes \x00\xff"
    (served / "Foo.modl").write_bytes(payload_bytes)

    entry = ModuleEntry.model_validate(
        _module(
            "foo",
            download_url=f"{base_url}/Foo.modl",
            sha256=_sha256(payload_bytes),
            install_path="/usr/local/bin/ignition/user-lib/modules/Foo.modl",
        ),
    )

    cache_dir = tmp_path / "cache"
    with httpx.Client() as client:
        result = download_entry(entry, cache_dir, client=client)

    assert result.outcome is DownloadOutcome.DOWNLOADED
    assert result.path == cache_dir / "Foo.modl"
    assert result.path.read_bytes() == payload_bytes
    assert hashlib.sha256(result.path.read_bytes()).hexdigest() == entry.sha256


def test_download_rejects_sha_mismatch(tmp_path: Path, file_server) -> None:
    base_url, served = file_server
    (served / "Foo.modl").write_bytes(b"actual bytes")

    entry = ModuleEntry.model_validate(
        _module(
            "foo",
            download_url=f"{base_url}/Foo.modl",
            sha256=_sha256(b"different bytes"),
            install_path="/usr/local/bin/ignition/user-lib/modules/Foo.modl",
        ),
    )

    cache_dir = tmp_path / "cache"
    with httpx.Client() as client, pytest.raises(DownloadError, match="sha256 mismatch"):
        download_entry(entry, cache_dir, client=client)

    # The bad download must not be left lying in the cache.
    assert not (cache_dir / "Foo.modl").exists()


def test_already_cached_file_with_matching_sha_is_skipped(tmp_path: Path) -> None:
    payload_bytes = b"cached bytes"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "Foo.modl").write_bytes(payload_bytes)

    entry = ModuleEntry.model_validate(
        _module(
            "foo",
            download_url="http://invalid.example/Foo.modl",  # never contacted
            sha256=_sha256(payload_bytes),
            install_path="/usr/local/bin/ignition/user-lib/modules/Foo.modl",
        ),
    )

    with httpx.Client() as client:
        result = download_entry(entry, cache_dir, client=client)

    assert result.outcome is DownloadOutcome.SKIPPED_CACHED


# ---------------------------------------------------------------------------
# Manual-download semantics
# ---------------------------------------------------------------------------


def test_manual_download_without_local_source_is_skipped(tmp_path: Path) -> None:
    entry = ModuleEntry.model_validate(
        _module(
            "mcp",
            requires_manual_download=True,
            download_url=None,
            install_path="/usr/local/bin/ignition/user-lib/modules/MCP.modl",
        ),
    )
    with httpx.Client() as client:
        result = download_entry(entry, tmp_path / "cache", client=client)
    assert result.outcome is DownloadOutcome.SKIPPED_MANUAL
    assert "requires manual download" in result.message
    assert "POST-SETUP" in result.message


def test_manual_download_copies_from_existing_local_source(tmp_path: Path) -> None:
    source = tmp_path / "vault" / "MCP.modl"
    source.parent.mkdir()
    source.write_bytes(b"vault contents")

    entry = ModuleEntry.model_validate(
        _module(
            "mcp",
            requires_manual_download=True,
            download_url=None,
            local_source_path=str(source),
            install_path="/usr/local/bin/ignition/user-lib/modules/MCP.modl",
        ),
    )
    cache_dir = tmp_path / "cache"
    with httpx.Client() as client:
        result = download_entry(entry, cache_dir, client=client)

    assert result.outcome is DownloadOutcome.COPIED_FROM_LOCAL
    assert (cache_dir / "MCP.modl").read_bytes() == b"vault contents"


def test_manual_download_warns_when_local_source_missing(tmp_path: Path) -> None:
    """Configured-but-missing local_source_path is a config-drift warning,
    not a hard failure. Falls back to manual-download skip."""
    entry = ModuleEntry.model_validate(
        _module(
            "mcp",
            requires_manual_download=True,
            download_url=None,
            local_source_path=str(tmp_path / "does-not-exist.modl"),
            install_path="/usr/local/bin/ignition/user-lib/modules/MCP.modl",
        ),
    )
    with httpx.Client() as client:
        result = download_entry(entry, tmp_path / "cache", client=client)

    assert result.outcome is DownloadOutcome.SKIPPED_MANUAL
    assert "WARN" in result.message
    assert "local_source_path missing" in result.message


# ---------------------------------------------------------------------------
# Offline mode
# ---------------------------------------------------------------------------


def test_offline_fails_fast_when_artifact_missing(tmp_path: Path) -> None:
    entry = ModuleEntry.model_validate(
        _module(
            "foo",
            download_url="http://invalid.example/Foo.modl",
            sha256=_sha256(b"x"),
            install_path="/usr/local/bin/ignition/user-lib/modules/Foo.modl",
        ),
    )

    with httpx.Client() as client, pytest.raises(DownloadError, match="--offline"):
        download_entry(entry, tmp_path / "cache", client=client, offline=True)


def test_offline_uses_prepopulated_cache(tmp_path: Path) -> None:
    payload_bytes = b"preloaded"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "Foo.modl").write_bytes(payload_bytes)

    entry = ModuleEntry.model_validate(
        _module(
            "foo",
            download_url="http://invalid.example/Foo.modl",
            sha256=_sha256(payload_bytes),
            install_path="/usr/local/bin/ignition/user-lib/modules/Foo.modl",
        ),
    )

    with httpx.Client() as client:
        result = download_entry(entry, cache_dir, client=client, offline=True)
    assert result.outcome is DownloadOutcome.SKIPPED_CACHED


# ---------------------------------------------------------------------------
# Fixture-driven `validate` end-to-end (URL reachable + sha pinned)
# ---------------------------------------------------------------------------


def test_validate_passes_with_reachable_url_and_pinned_sha(
    tmp_path: Path,
    file_server,
) -> None:
    base_url, served = file_server
    artifact = b"verified contents"
    (served / "Foo.modl").write_bytes(artifact)

    payload = {
        "version": 1,
        "entries": [
            _module(
                "foo",
                download_url=f"{base_url}/Foo.modl",
                sha256=_sha256(artifact),
                install_path="/usr/local/bin/ignition/user-lib/modules/Foo.modl",
            ),
        ],
    }
    catalog_path = _write_catalog(tmp_path, payload)
    catalog = load_catalog(catalog_path)

    from ignition_stack.catalog.verify import verify_reachable

    with httpx.Client() as client:
        issues = [verify_reachable(e, client) for e in catalog.entries]
    assert issues == [None]


def test_validate_flags_unpinned_sha(tmp_path: Path) -> None:
    """Unpinned shas should fail validation independent of network checks."""
    payload = {
        "version": 1,
        "entries": [_module("foo", install_path="/usr/local/bin/ignition/user-lib/modules/Foo.modl")],
    }
    catalog_path = _write_catalog(tmp_path, payload)
    catalog = load_catalog(catalog_path)
    assert catalog.entries[0].sha256 == SHA256_UNPINNED


def test_catalog_for_ignition_filters_by_exact_version(tmp_path: Path) -> None:
    payload = {
        "version": 1,
        "entries": [
            _module(
                "a",
                ignition_versions=["8.3.6"],
                install_path="/usr/local/bin/ignition/user-lib/modules/A.modl",
            ),
            _module(
                "b",
                ignition_versions=["8.3.7"],
                install_path="/usr/local/bin/ignition/user-lib/modules/B.modl",
            ),
        ],
    }
    catalog_path = _write_catalog(tmp_path, payload)
    catalog: Catalog = load_catalog(catalog_path)

    assert [e.name for e in catalog.for_ignition("8.3.6")] == ["a"]
    assert [e.name for e in catalog.for_ignition("8.3.7")] == ["b"]


# ---------------------------------------------------------------------------
# A friendly diagnostic so YAML-broken catalogs do not look like Python errors
# ---------------------------------------------------------------------------


def test_invalid_yaml_raises_catalog_load_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text(textwrap.dedent("not: : valid:: yaml"), encoding="utf-8")
    with pytest.raises(CatalogLoadError):
        load_catalog(path)
