"""Phase 1 automated regression gate.

Brings up `scripts/seeding-poc/baseline/` (Ignition 8.3.6 + template-ignition-project
resources copied in via a bootstrap container), waits for the gateway to reach
state RUNNING, logs into the gateway web UI, navigates to Database Connections,
and asserts the template's `db` PostgreSQL connection appears with status VALID.

This is the headline assertion of the seeding matrix: the `db-connection` row's
"yes/yes" verdicts are only credible if a fresh boot actually shows the seeded
connection wired in. Any future schema or path-template drift in 8.3.x that
breaks file-config seeding will trip this script.

Exit codes:
    0 = boot succeeded and the `db` connection appears with VALID status
    1 = container bring-up failed or the gateway never reached RUNNING
    2 = UI login failed
    3 = Database Connections page did not show the seeded `db` connection,
        or the connection status was not VALID

Run from the repo root:

    .venv/bin/python scripts/seeding-poc/boot.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_DIR = REPO_ROOT / "scripts" / "seeding-poc" / "baseline"
SCREENSHOT_DIR = REPO_ROOT / "scripts" / "seeding-poc" / "screenshots" / "gate"

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:9088")
GATEWAY_READY_TIMEOUT_S = int(os.environ.get("GATEWAY_READY_TIMEOUT_S", "180"))


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    """Run docker compose with the baseline's .env."""
    return subprocess.run(
        ["docker", "compose", "--env-file", ".env", *args],
        cwd=BASELINE_DIR,
        check=False,
        capture_output=True,
        text=True,
    )


def ensure_env_file() -> None:
    """Copy .env.example to .env when the user hasn't yet. CI environments rely on this."""
    env_path = BASELINE_DIR / ".env"
    if env_path.exists():
        return
    example = BASELINE_DIR / ".env.example"
    env_path.write_text(example.read_text())
    print(f"[setup] copied {example.name} -> {env_path.name}")


def wait_for_running(timeout_s: int) -> bool:
    """Poll /StatusPing until state==RUNNING or the deadline passes."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            body = urllib.request.urlopen(f"{GATEWAY_URL}/StatusPing", timeout=5).read().decode()
        except Exception:
            time.sleep(3)
            continue
        if '"state":"RUNNING"' in body and "COMMISSIONING" not in body:
            return True
        time.sleep(3)
    return False


def login_and_assert(page) -> int:
    """Log in, navigate to Database Connections, assert the `db` row exists and is VALID."""
    page.goto(f"{GATEWAY_URL}/data/app/login", wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(2500)
    page.locator('input[name="username"]').fill("admin")
    page.locator("div.submit-button").first.click()
    page.wait_for_timeout(2500)
    page.locator('input[name="password"]').first.fill("password")
    page.locator("div.submit-button").first.click()
    page.wait_for_timeout(4000)

    if "/idp/" in page.url:
        page.screenshot(path=str(SCREENSHOT_DIR / "login-failed.png"), full_page=True)
        print("[fail] login did not complete (still on IDP page)", file=sys.stderr)
        return 2

    page.goto(
        f"{GATEWAY_URL}/app/connections/databases", wait_until="domcontentloaded", timeout=20_000
    )
    page.wait_for_timeout(3000)
    page.screenshot(path=str(SCREENSHOT_DIR / "db-connection.png"), full_page=True)

    body = page.locator("body").text_content() or ""
    if "db" not in body or "PostgreSQL" not in body:
        print("[fail] Database Connections page does not show the seeded `db` row", file=sys.stderr)
        return 3
    # The status badge sometimes renders as an SVG/CSS pseudo-element and
    # doesn't land in text_content(). The "VALID CONNECTIONS" counter at the
    # top of the page is a regular DOM node that DOES, and it goes "1 / 1"
    # when the only seeded connection is valid - that's the headline signal.
    if "1 / 1" not in body and "1/1" not in body:
        print(
            "[fail] `db` row is present but the VALID CONNECTIONS counter "
            "did not reach 1/1 - the connection is errored or still settling",
            file=sys.stderr,
        )
        return 3

    print("[ok] db-connection seeded and VALID on a fresh 8.3.6 gateway")
    return 0


def main() -> int:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_env_file()

    # Always start clean - the JWE-encrypted secret in the template can confuse a
    # warm volume from a prior failed run.
    compose("down", "-v")
    up = compose("up", "-d")
    if up.returncode != 0:
        print(f"[fail] docker compose up failed:\n{up.stderr}", file=sys.stderr)
        return 1

    try:
        if not wait_for_running(GATEWAY_READY_TIMEOUT_S):
            print(
                f"[fail] gateway did not reach RUNNING within {GATEWAY_READY_TIMEOUT_S}s",
                file=sys.stderr,
            )
            return 1

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1600, "height": 1000})
            page = context.new_page()
            try:
                return login_and_assert(page)
            finally:
                browser.close()
    finally:
        compose("down", "-v")


if __name__ == "__main__":
    sys.exit(main())
