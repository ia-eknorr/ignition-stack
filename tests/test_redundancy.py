"""Phase 4 acceptance tests: redundancy modeling + generation.

Validation criteria from the Phase 4 plan:

1. Marking the standalone gateway redundant resolves to two gateways
   (``gateway``, ``gateway-backup``); the backup carries ``redundancy.mode ==
   "backup"`` and a ``peer`` pointing at the master.
2. Scaleout ``--redundant backend`` and hub-and-spoke ``--redundant hub`` each
   produce a backup node for that role only; frontends/spokes are never paired.
3. The rendered compose has separate volumes per node, both nodes on the
   backend network, and the backup's ``GATEWAY_NETWORK_0_HOST`` set to the
   master service name (golden + assertion).
4. ``POST-SETUP.md`` contains a redundancy-pairing section naming the master
   and backup and citing the GAN port.
5. A redundant Edge role yields two Edge nodes; a mixed standard/Edge pair is
   rejected with a clear error.

Plus the Phase-2 round-trip contract: re-resolving an already-expanded config
is idempotent (a dumped redundant stack rebuilt with ``-f`` must not grow a
third gateway).
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from ignition_stack.compose.engine import render_compose
from ignition_stack.compose.writer import write_project
from ignition_stack.config import (
    GatewayConfig,
    ProjectConfig,
    RedundancyConfig,
    dump_config,
    load_config,
)
from ignition_stack.postsetup import generate_post_setup
from ignition_stack.profiles import ProfileOptions, build_profile
from ignition_stack.services.resolver import resolve

GOLDEN_DIR = Path(__file__).parent / "golden" / "profiles"


def _parse(text: str) -> dict:
    return YAML().load(StringIO(text))


def _check_or_update_golden(rel_path: str, actual: str) -> None:
    golden = GOLDEN_DIR / rel_path
    if os.environ.get("UPDATE_GOLDENS") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8")
        return
    assert golden.exists(), f"missing golden {golden}; run with UPDATE_GOLDENS=1 to create"
    assert golden.read_text(encoding="utf-8") == actual, f"compose output diverges from golden '{rel_path}'. " "Run with UPDATE_GOLDENS=1 to update if the change is intentional."


# --------------------------------------------------------------------------- #
# 1. Standalone expansion
# --------------------------------------------------------------------------- #


def test_redundant_standalone_expands_to_pair() -> None:
    config = build_profile("standalone", "demo", ProfileOptions(redundant_role="gateway"))
    resolved = resolve(config)

    names = [gw.name for gw in resolved.gateways]
    assert names == ["gateway", "gateway-backup"]

    by_name = {gw.name: gw for gw in resolved.gateways}
    master = by_name["gateway"]
    backup = by_name["gateway-backup"]
    assert master.redundancy is not None and master.redundancy.mode == "master"
    assert master.redundancy.peer == "gateway-backup"
    assert backup.redundancy is not None and backup.redundancy.mode == "backup"
    assert backup.redundancy.peer == "gateway"
    # Backup steps to the next free port; the seed defaults the spike verified.
    assert backup.http_port == master.http_port + 1
    assert backup.redundancy.gan_port == 8088
    assert backup.redundancy.seed_redundancy_xml is True


# --------------------------------------------------------------------------- #
# 2. Per-role scoping: only the named role is paired
# --------------------------------------------------------------------------- #


def test_scaleout_redundant_backend_pairs_only_backend() -> None:
    config = build_profile("scaleout", "demo", ProfileOptions(frontends=2, redundant_role="backend"))
    resolved = resolve(config)
    names = [gw.name for gw in resolved.gateways]
    assert names == ["frontend-1", "frontend-2", "backend", "backend-backup"]

    redundant = {gw.name for gw in resolved.gateways if gw.redundancy is not None}
    assert redundant == {"backend", "backend-backup"}
    # No frontend is ever paired.
    assert not any(gw.name.startswith("frontend") and gw.redundancy for gw in resolved.gateways)


def test_hub_redundant_hub_pairs_only_hub() -> None:
    config = build_profile("hub-and-spoke", "demo", ProfileOptions(spokes=3, redundant_role="hub"))
    resolved = resolve(config)
    redundant = {gw.name for gw in resolved.gateways if gw.redundancy is not None}
    assert redundant == {"hub", "hub-backup"}
    # No spoke is ever paired.
    assert not any((gw.role or "") == "spoke" and gw.redundancy for gw in resolved.gateways)


@pytest.mark.parametrize("role", ["frontend", "spoke"])
def test_replicated_roles_rejected(role: str) -> None:
    profile = "scaleout" if role == "frontend" else "hub-and-spoke"
    with pytest.raises(ValueError, match="horizontally replicated"):
        build_profile(profile, "demo", ProfileOptions(redundant_role=role))


def test_unknown_redundant_role_rejected() -> None:
    with pytest.raises(ValueError, match="no gateway matches"):
        build_profile("standalone", "demo", ProfileOptions(redundant_role="nope"))


# --------------------------------------------------------------------------- #
# 3. Compose wiring (golden + assertions)
# --------------------------------------------------------------------------- #


def _redundant_scaleout() -> ProjectConfig:
    return resolve(build_profile("scaleout", "demo", ProfileOptions(frontends=1, redundant_role="backend")))


def test_redundant_compose_golden() -> None:
    _check_or_update_golden("scaleout-redundant/docker-compose.yaml", render_compose(_redundant_scaleout()))


def test_redundant_compose_wiring() -> None:
    parsed = _parse(render_compose(_redundant_scaleout()))
    services = parsed["services"]
    volumes = parsed["volumes"]

    # Separate data volume per node.
    assert "backend-data" in volumes
    assert "backend-backup-data" in volumes

    # Both nodes on the backend network.
    assert "backend" in services["backend"]["networks"]
    assert "backend" in services["backend-backup"]["networks"]

    # The backup points its generic Gateway Network connection at the master
    # service name, on the plain port, with SSL off (all three, not just HOST).
    backup_env = services["backend-backup"]["environment"]
    assert backup_env["GATEWAY_NETWORK_0_HOST"] == "backend"
    assert str(backup_env["GATEWAY_NETWORK_0_PORT"]) == "8088"
    assert backup_env["GATEWAY_NETWORK_0_ENABLESSL"] == "false"

    # Open incoming GAN policy on both nodes so the plain link auto-approves.
    for node in ("backend", "backend-backup"):
        env = services[node]["environment"]
        assert env["GATEWAY_NETWORK_SECURITYPOLICY"] == "Unrestricted"
        assert env["GATEWAY_NETWORK_REQUIRESSL"] == "false"

    # The backup must NOT be renamed (-n); it adopts the master's name on sync.
    assert "-n" not in services["backend-backup"]["command"]
    assert "-n" in services["backend"]["command"]


def test_redundant_seeds_redundancy_xml(tmp_path: Path) -> None:
    write_project(_redundant_scaleout(), tmp_path / "demo")
    master_xml = (tmp_path / "demo" / "services" / "backend" / "redundancy.xml").read_text()
    backup_xml = (tmp_path / "demo" / "services" / "backend-backup" / "redundancy.xml").read_text()
    assert '<entry key="redundancy.noderole">Master</entry>' in master_xml
    assert '<entry key="redundancy.noderole">Backup</entry>' in backup_xml
    # The backup points at the master service name on the plain port.
    assert '<entry key="redundancy.gan.host">backend</entry>' in backup_xml
    assert '<entry key="redundancy.gan.port">8088</entry>' in backup_xml
    assert '<entry key="redundancy.gan.enableSsl">false</entry>' in backup_xml
    # And the bootstrap copies it into the data root on first boot.
    bootstrap = (tmp_path / "demo" / "scripts" / "docker-bootstrap.sh").read_text()
    assert "redundancy.xml" in bootstrap


# --------------------------------------------------------------------------- #
# 4. POST-SETUP content
# --------------------------------------------------------------------------- #


def test_redundancy_post_setup() -> None:
    body = generate_post_setup(_redundant_scaleout())
    assert "redundancy" in body.lower()
    # Names both nodes and cites the GAN port.
    assert "backend" in body
    assert "backend-backup" in body
    assert "8088" in body
    # Surfaces the demo-only security caveat (plain/no-SSL).
    assert "SSL" in body


def test_no_redundancy_no_post_setup_section() -> None:
    body = generate_post_setup(resolve(build_profile("standalone", "demo", ProfileOptions())))
    assert "redundancy" not in body.lower()


# --------------------------------------------------------------------------- #
# 5. Edge-pair guard
# --------------------------------------------------------------------------- #


def test_redundant_edge_yields_two_edge_nodes() -> None:
    config = build_profile("standalone", "demo", ProfileOptions(edge_role="gateway", redundant_role="gateway"))
    resolved = resolve(config)
    editions = {gw.name: gw.ignition_edition for gw in resolved.gateways}
    assert editions == {"gateway": "edge", "gateway-backup": "edge"}


def test_edge_redundancy_pair_only() -> None:
    """A mixed standard-master / Edge-backup pair is rejected at the model level."""
    master = GatewayConfig(
        name="gateway",
        ignition_edition="standard",
        redundancy=RedundancyConfig(mode="master", peer="gateway-backup"),
    )
    backup = GatewayConfig(
        name="gateway-backup",
        ignition_edition="edge",
        redundancy=RedundancyConfig(mode="backup", peer="gateway"),
    )
    with pytest.raises(ValueError, match="Edge-to-Edge"):
        ProjectConfig(name="demo", gateways=[master, backup])


# --------------------------------------------------------------------------- #
# Phase-2 round-trip: re-resolving a redundant config is idempotent
# --------------------------------------------------------------------------- #


def test_redundant_reresolve_is_idempotent() -> None:
    resolved = _redundant_scaleout()
    again = resolve(resolved)
    assert [gw.name for gw in again.gateways] == [gw.name for gw in resolved.gateways]


def test_redundant_dump_reload_rebuild_is_stable(tmp_path: Path) -> None:
    """Dump a resolved redundant stack, reload it, and rebuild: byte-identical."""
    resolved = _redundant_scaleout()
    dumped = dump_config(resolved, "yaml")
    arch = tmp_path / "arch.yml"
    arch.write_text(dumped, encoding="utf-8")

    reloaded = load_config(arch)
    # Reload + re-resolve must not grow a third backend node.
    re_resolved = resolve(reloaded)
    assert [gw.name for gw in re_resolved.gateways] == [gw.name for gw in resolved.gateways]
    # And the compose rebuilt from the file matches the original.
    assert render_compose(re_resolved) == render_compose(resolved)
