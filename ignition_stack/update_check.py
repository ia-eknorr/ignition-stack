"""Passive "a newer release is available" notifier.

Mirrors the pattern used by npm/update-notifier, gh, and pip's own notice: on a
real command invocation, check PyPI at most once a day (cached), fail silently
on any error, and never delay or block the command the user actually ran. The
notice is advisory only - this module never installs anything. Presentation
(TTY gating, printing) lives in the CLI; this module is the pure decision layer
so it stays testable without a console.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

from ignition_stack import __version__

_PYPI_URL = "https://pypi.org/pypi/ignition-stack/json"
_CHECK_INTERVAL = 24 * 60 * 60  # seconds between live PyPI checks
_HTTP_TIMEOUT = 1.5  # short on purpose: the check must never stall a command
_OPT_OUT_ENV = "IGNITION_STACK_NO_UPDATE_CHECK"


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "ignition-stack" / "update-check.json"


def _is_newer(latest: str, current: str) -> bool:
    """True when ``latest`` is a strictly higher release than ``current``.

    Both sides are this project's own clean ``X.Y.Z`` releases, so an int-tuple
    compare on the dotted parts is exact. Anything that does not parse cleanly
    returns False, so an unexpected version string can never raise a bogus
    "update available" notice.
    """
    try:
        latest_parts = tuple(int(p) for p in latest.split("."))
        current_parts = tuple(int(p) for p in current.split("."))
    except ValueError:
        return False
    return latest_parts > current_parts


def _read_cache(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, latest: str, now: float) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checked_at": now, "latest": latest}))
    except OSError:
        pass  # caching is best-effort; never fail the CLI over it


def _fetch_latest() -> str | None:
    try:
        resp = httpx.get(_PYPI_URL, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()["info"]["version"]
    except Exception:
        return None  # offline, slow, rate-limited, malformed - all non-fatal


def _latest_version(now: float) -> str | None:
    """Latest version from cache when fresh, otherwise refresh from PyPI.

    The freshness window bounds live network calls to once per ``_CHECK_INTERVAL``,
    so the common path is a local file read with no request at all.
    """
    path = _cache_path()
    cached = _read_cache(path)
    if cached and now - cached.get("checked_at", 0) < _CHECK_INTERVAL:
        return cached.get("latest")
    latest = _fetch_latest()
    if latest is not None:
        _write_cache(path, latest, now)
    return latest


def detect_upgrade_command() -> str:
    """Return the exact command the user should run to upgrade.

    ``sys.prefix`` is the environment the running CLI lives in; both managed
    installers leave an unambiguous marker in that path. Match only those two
    and fall back to plain pip for everything else (a venv, ``--user`` site, a
    system Python) - suggesting ``pipx upgrade`` to someone on plain pip just
    errors for them, so when there is no clear marker the generic command is
    the safe choice rather than a guess.
    """
    if f"pipx{os.sep}venvs" in sys.prefix:
        return "pipx upgrade ignition-stack"
    if f"uv{os.sep}tools" in sys.prefix:
        return "uv tool upgrade ignition-stack"
    return "pip install --upgrade ignition-stack"


def check_for_update(*, now: float | None = None) -> tuple[str, str] | None:
    """Return ``(current, latest)`` when a newer release exists, else ``None``.

    Applies the opt-out gate and the once-a-day cache policy, then compares.
    Presentation (and the TTY gate) is the caller's job.
    """
    if os.environ.get(_OPT_OUT_ENV):
        return None
    now = time.time() if now is None else now
    latest = _latest_version(now)
    if latest and _is_newer(latest, __version__):
        return (__version__, latest)
    return None


def should_notify() -> bool:
    """Whether a notice may be shown at all, independent of version state.

    Suppressed when stdout is not a TTY (CI, pipes, shell completion), so the
    notice never contaminates scripted output or non-interactive runs.
    """
    return sys.stdout.isatty()
