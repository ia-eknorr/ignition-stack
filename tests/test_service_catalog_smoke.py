"""Opt-in end-to-end smoke for a generated multi-service stack (Phase 5 #3).

Generates a standalone + Postgres + HiveMQ + OPC-UA-sim project, boots it with
``docker compose up -d``, and asserts:

- the gateway reaches RUNNING (not COMMISSIONING),
- Postgres reports healthy,
- HiveMQ accepts a TCP connection on 1883,
- the OPC-UA simulator accepts a TCP connection on 50000,
- the file-seedable ``db`` database connection shows configured in the gateway
  UI (the Phase-1 matrix marks ``db-connection`` file-seedable-config: yes).

Slow (pulls images, boots a gateway), so it is gated behind the ``smoke``
marker and excluded by the default ``-m 'not smoke'`` addopts. Run it with::

    pytest -m smoke

It skips cleanly when Docker or Playwright is unavailable.
"""

from __future__ import annotations

import socket
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from ignition_stack.compose.writer import write_project
from ignition_stack.config.schema import ProjectConfig

pytestmark = pytest.mark.smoke

GATEWAY_PORT = 9088
HIVEMQ_PORT = 1883
OPCUA_PORT = 50000
READY_TIMEOUT_S = 300


def _docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def _compose(project_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "--env-file", ".env", *args],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
    )


def _wait_for_gateway_running(timeout_s: int) -> bool:
    deadline = time.time() + timeout_s
    url = f"http://localhost:{GATEWAY_PORT}/StatusPing"
    while time.time() < deadline:
        try:
            body = urllib.request.urlopen(url, timeout=5).read().decode()
        except Exception:
            time.sleep(3)
            continue
        if '"state":"RUNNING"' in body and "COMMISSIONING" not in body:
            return True
        time.sleep(3)
    return False


def _tcp_open(port: int, timeout_s: int = 60) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            if sock.connect_ex(("localhost", port)) == 0:
                return True
        time.sleep(2)
    return False


def _postgres_healthy(project_dir: Path, timeout_s: int = 120) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = _compose(project_dir, "ps", "db", "--format", "{{.Health}}")
        if "healthy" in result.stdout:
            return True
        time.sleep(3)
    return False


@pytest.fixture
def smoke_stack(tmp_path: Path) -> Iterator[Path]:
    """Generate + boot the smoke stack; tear it down (with volumes) afterwards."""
    if not _docker_available():
        pytest.skip("Docker daemon not available")
    pytest.importorskip("playwright.sync_api", reason="playwright not installed (poc extra)")

    project_dir = tmp_path / "smoke"
    write_project(
        ProjectConfig(name="smoketest", services=["hivemq", "opcua-sim"]),
        project_dir,
    )

    up = _compose(project_dir, "up", "-d")
    if up.returncode != 0:
        _compose(project_dir, "down", "-v")
        pytest.fail(f"docker compose up failed:\n{up.stderr}")
    try:
        yield project_dir
    finally:
        _compose(project_dir, "down", "-v")


def test_generated_stack_boots_and_seeds_db_connection(smoke_stack: Path) -> None:
    from playwright.sync_api import sync_playwright

    assert _wait_for_gateway_running(READY_TIMEOUT_S), "gateway never reached RUNNING"
    assert _postgres_healthy(smoke_stack), "Postgres never reported healthy"
    assert _tcp_open(HIVEMQ_PORT), "HiveMQ not accepting connections on 1883"
    assert _tcp_open(OPCUA_PORT), "OPC-UA simulator not reachable on 50000"

    base = f"http://localhost:{GATEWAY_PORT}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1600, "height": 1000}).new_page()
        try:
            page.goto(f"{base}/data/app/login", wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2500)
            page.locator('input[name="username"]').fill("admin")
            page.locator("div.submit-button").first.click()
            page.wait_for_timeout(2500)
            page.locator('input[name="password"]').first.fill("password")
            page.locator("div.submit-button").first.click()
            page.wait_for_timeout(4000)
            assert "/idp/" not in page.url, "gateway login did not complete"

            page.goto(
                f"{base}/app/connections/databases",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            page.wait_for_timeout(3000)
            body = page.locator("body").text_content() or ""
            # "shows configured" = the seeded db-connection is listed; we do not
            # require VALID status because the lifted secret may not match the
            # preset password (the matrix marks the *config* file-seedable).
            assert (
                "db" in body and "PostgreSQL" in body
            ), "seeded db-connection not shown in the gateway UI"
        finally:
            browser.close()
