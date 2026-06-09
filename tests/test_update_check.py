"""Tests for the passive update notifier.

The notifier must be unobtrusive above all: it caps live PyPI calls at once a
day, swallows every network error, and never raises an "update available"
notice on a bogus or older version. These tests pin those guarantees without
touching the network - the single fetch seam is monkeypatched.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from ignition_stack import update_check


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Point the cache at a throwaway dir and clear the opt-out env per test."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv(update_check._OPT_OUT_ENV, raising=False)


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("0.4.0", "0.3.0", True),
        ("0.3.1", "0.3.0", True),
        ("1.0.0", "0.9.9", True),
        ("0.3.0", "0.3.0", False),
        ("0.2.0", "0.3.0", False),
        ("0.3", "0.3.0", False),  # 0.3 == 0.3.0 numerically, not strictly newer
        ("nonsense", "0.3.0", False),  # unparseable never triggers a notice
    ],
)
def test_is_newer(latest, current, expected):
    assert update_check._is_newer(latest, current) is expected


def test_fresh_cache_avoids_network(tmp_path, monkeypatch):
    path = update_check._cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"checked_at": time.time(), "latest": "9.9.9"}))

    def _fail():
        raise AssertionError("fetch must not run while the cache is fresh")

    monkeypatch.setattr(update_check, "_fetch_latest", _fail)
    assert update_check._latest_version(time.time()) == "9.9.9"


def test_stale_cache_refetches_and_rewrites(monkeypatch):
    path = update_check._cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stale = time.time() - update_check._CHECK_INTERVAL - 1
    path.write_text(json.dumps({"checked_at": stale, "latest": "0.1.0"}))

    monkeypatch.setattr(update_check, "_fetch_latest", lambda: "0.5.0")
    now = time.time()
    assert update_check._latest_version(now) == "0.5.0"
    rewritten = json.loads(path.read_text())
    assert rewritten["latest"] == "0.5.0"
    assert rewritten["checked_at"] == pytest.approx(now)


def test_fetch_failure_is_silent(monkeypatch):
    monkeypatch.setattr(update_check, "_fetch_latest", lambda: None)
    assert update_check._latest_version(time.time()) is None


def test_check_reports_newer_version(monkeypatch):
    monkeypatch.setattr(update_check, "__version__", "0.3.0")
    monkeypatch.setattr(update_check, "_latest_version", lambda now: "0.4.0")
    assert update_check.check_for_update() == ("0.3.0", "0.4.0")


def test_check_silent_when_current(monkeypatch):
    monkeypatch.setattr(update_check, "__version__", "0.4.0")
    monkeypatch.setattr(update_check, "_latest_version", lambda now: "0.4.0")
    assert update_check.check_for_update() is None


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (["pipx", "venvs", "ignition-stack"], "pipx upgrade ignition-stack"),
        (["uv", "tools", "ignition-stack"], "uv tool upgrade ignition-stack"),
        ([".venv"], "pip install --upgrade ignition-stack"),
        (["usr"], "pip install --upgrade ignition-stack"),
    ],
)
def test_detect_upgrade_command(parts, expected, monkeypatch):
    prefix = os.sep + os.sep.join(parts)
    monkeypatch.setattr(update_check.sys, "prefix", prefix)
    assert update_check.detect_upgrade_command() == expected


def test_opt_out_env_skips_everything(monkeypatch):
    monkeypatch.setenv(update_check._OPT_OUT_ENV, "1")

    def _fail(now):
        raise AssertionError("opt-out must short-circuit before any lookup")

    monkeypatch.setattr(update_check, "_latest_version", _fail)
    assert update_check.check_for_update() is None
