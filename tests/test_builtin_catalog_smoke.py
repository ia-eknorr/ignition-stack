"""Opt-in drift guard: builtin_modules.yaml must match the live gateway image.

The ``disable_builtins`` feature inverts a blocklist into the gateway's strict
GATEWAY_MODULES_ENABLED whitelist, so the whitelist must enumerate EVERY built-in
the user did not disable. If the image ships a built-in our catalog doesn't list,
that module would be silently quarantined the moment a user disables anything.

This test re-derives the built-in set from the pinned image the same way the
catalog was originally captured - boot with no whitelist, read the modules the
gateway logs as it starts them - and asserts it equals builtin_modules.yaml. A
stale catalog (e.g. after an image bump) fails here loudly instead of dropping
modules silently in generated stacks.

Slow (pulls + boots a gateway), so it is gated behind the ``smoke`` marker and
excluded by the default ``-m 'not smoke'`` addopts. Run it with::

    pytest -m smoke tests/test_builtin_catalog_smoke.py

It skips cleanly when Docker is unavailable.
"""

from __future__ import annotations

import re
import subprocess
import time

import pytest

from ignition_stack.catalog.builtins import load_builtin_catalog

pytestmark = pytest.mark.smoke

CONTAINER_NAME = "ignition-stack-builtin-guard"
BOOT_TIMEOUT_S = 300
# Gateway logs one line per module as it starts:
#   ... Starting up module 'com.inductiveautomation.opcua' v... module-name=OPC-UA
_MODULE_LINE = re.compile(r"Starting up module '([a-z0-9.\-]+)'")
_GATEWAY_STARTED = "Gateway started in"


def _docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def _logs() -> str:
    # The Ignition wrapper writes startup lines across both streams; merge them.
    return subprocess.run(
        ["docker", "logs", CONTAINER_NAME],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    ).stdout


def test_builtin_catalog_matches_live_image() -> None:
    if not _docker_available():
        pytest.skip("Docker daemon not available")

    catalog = load_builtin_catalog()
    image = f"inductiveautomation/ignition:{catalog.ignition_version}"

    # Boot with NO whitelist so every built-in loads and logs its identifier.
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True, check=False)
    run = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER_NAME,
            "-e",
            "ACCEPT_IGNITION_EULA=Y",
            "-e",
            "GATEWAY_ADMIN_PASSWORD=password",
            "-e",
            "IGNITION_EDITION=standard",
            image,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        pytest.fail(f"docker run failed for {image}:\n{run.stderr}")

    try:
        deadline = time.time() + BOOT_TIMEOUT_S
        logs = ""
        while time.time() < deadline:
            logs = _logs()
            if _GATEWAY_STARTED in logs:
                break
            time.sleep(3)
        else:
            pytest.fail(f"gateway never finished startup within {BOOT_TIMEOUT_S}s")

        live = set(_MODULE_LINE.findall(logs))
        pinned = {m.identifier for m in catalog.modules}

        missing_from_catalog = sorted(live - pinned)
        stale_in_catalog = sorted(pinned - live)
        assert not missing_from_catalog and not stale_in_catalog, (
            f"builtin_modules.yaml is out of sync with {image}.\n"
            f"  in image but NOT in catalog (would be silently quarantined): "
            f"{missing_from_catalog}\n"
            f"  in catalog but NOT in image (stale entry): {stale_in_catalog}"
        )
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True, check=False)
