"""Capture UI evidence for the baseline rows of the seedability matrix.

Run baseline/compose.yml first (see scripts/seeding-poc/README.md).
Logs into the gateway at GATEWAY_URL (default http://localhost:9088) with admin
credentials, then walks each top-level Config navigation area, saving a screenshot
to scripts/seeding-poc/screenshots/baseline/<row>.png.

Notes on the 8.3 web UI quirks this script handles:
    - Login is at /data/app/login and is a JS-driven two-step (username then
      password) with no <form> element; the submit control is a <div
      class="submit-button"> with an onclick handler, not a real <button>.
    - The web UI keeps long-lived connections, so "networkidle" never fires;
      use "domcontentloaded" plus an explicit wait_for_timeout.
    - The 8.3 nav lives under /app/{platform,connections,network,services,diagnostics}/...
      with /app/home as the unauthenticated landing.

Exit codes:
    0 = login OK and every page reachable
    1 = login failed
    2 = at least one navigation page failed to load
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import Page, sync_playwright

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:9088")
ADMIN_USER = os.environ.get("GATEWAY_ADMIN_USERNAME", "admin")
ADMIN_PASS = os.environ.get("GATEWAY_ADMIN_PASSWORD", "password")

SCREENSHOT_DIR = Path(__file__).parent / "screenshots" / "baseline"

# Top-level config navigation areas in Ignition 8.3. Each is screenshotted as
# evidence for one or more matrix rows. The deeper-link probes below try the
# subsystem-specific URLs first and fall back to the area landing.
NAV_AREAS = {
    "platform": "/app/platform/overview",
    "connections": "/app/connections/overview",
    "network": "/app/network/overview",
    "services": "/app/services/overview",
    "diagnostics": "/app/diagnostics/overview",
}

# Each entry: matrix-row key -> ordered list of candidate URLs. The first one
# whose page renders without showing "Page Not Found" gets the screenshot.
# URL slugs taken from Ignition 8.3.6 by walking each /app/<area>/overview
# sidebar.
ROWS_TO_CAPTURE: dict[str, list[str]] = {
    "db-connection": ["/app/connections/databases"],
    "jdbc-driver": ["/app/connections/databases/settings/drivers-jdbc"],
    "database-translator": ["/app/connections/databases/settings/translators"],
    "opc-ua-connection": ["/app/connections/opc-client-connections"],
    "opc-ua-server-config": ["/app/connections/opc/security/client"],
    "identity-provider": ["/app/platform/security/idps"],
    "security-levels": ["/app/platform/security/levels"],
    "security-zones": ["/app/platform/security/zones"],
    "secret-provider": ["/app/platform/security/secret-providers"],
    "gateway-network": [
        "/app/network/gateway/settings",
        "/app/network/gateway/connections/outgoing",
    ],
    "modules": ["/app/platform/system/modules"],
    "store-and-forward": ["/app/platform/system/store-and-forward"],
    "tag-provider": ["/app/services/tags"],
    "alarm-pipeline": ["/app/services/alarming/pipelines"],
    "historian-provider": ["/app/services/historian/providers"],
    "perspective-themes": [
        "/app/services/perspective/themes",
        "/app/services/perspective",
    ],
}


def login(page: Page) -> bool:
    """Drive the two-step IDP login. Returns True on success."""
    page.goto(
        urljoin(GATEWAY_URL, "/data/app/login"), wait_until="domcontentloaded", timeout=30_000
    )
    page.wait_for_timeout(2500)

    if "/idp/" not in page.url:
        # Already authenticated (session cookie from a previous run).
        return True

    # Step 1: username
    user_field = page.locator('input[name="username"]')
    user_field.first.wait_for(state="visible", timeout=10_000)
    user_field.first.fill(ADMIN_USER)
    page.locator("div.submit-button").first.click()
    page.wait_for_timeout(2500)

    # Step 2: password
    pwd_field = page.locator('input[name="password"]')
    pwd_field.first.wait_for(state="visible", timeout=10_000)
    pwd_field.first.fill(ADMIN_PASS)
    page.locator("div.submit-button").first.click()
    page.wait_for_timeout(4000)

    if "/idp/" in page.url:
        page.screenshot(path=str(SCREENSHOT_DIR / "login-failed.png"), full_page=True)
        return False
    return True


def capture(page: Page, name: str, candidates: list[str]) -> bool:
    """Try each URL; screenshot the first that doesn't bounce to /app/home."""
    for path in candidates:
        url = urljoin(GATEWAY_URL, path)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        except Exception as exc:
            print(f"[{name}] error visiting {path}: {exc}", file=sys.stderr)
            continue
        page.wait_for_timeout(2500)
        if page.url.rstrip("/").endswith("/app/home"):
            continue
        # The 8.3 SPA renders "Page Not Found" inside the chrome for unknown
        # routes (200 OK + "Return to Home" button) instead of redirecting.
        # Detect that explicitly.
        if page.locator("text=Page Not Found").count() > 0:
            continue
        target = SCREENSHOT_DIR / f"{name}.png"
        page.screenshot(path=str(target), full_page=True)
        print(f"[ok] {name} -> {path}")
        return True
    print(f"[miss] {name}: none of {candidates} loaded", file=sys.stderr)
    return False


def main() -> int:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()

        if not login(page):
            print("login failed - see screenshots/baseline/login-failed.png", file=sys.stderr)
            return 1

        # First snapshot every top-level area so the matrix has at least one
        # screenshot of the sidebar in each section.
        misses: list[str] = []
        for area, path in NAV_AREAS.items():
            if not capture(page, f"area-{area}", [path]):
                misses.append(area)

        # Then try the per-row deep links.
        for row, candidates in ROWS_TO_CAPTURE.items():
            if not capture(page, row, candidates):
                misses.append(row)

        browser.close()

    if misses:
        print(f"\nMissed: {misses}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
