"""Phase 3 (issue #43) acceptance tests: the IIoT MQTT overlay.

``apply_iiot`` adds an MQTT broker instance and wires the Cirrus Link
Transmission/Engine modules across a stack's gateways **by role**, using the
module slugs the broker manifest's ``wires.mqtt`` block names. These tests pin:

- the role mapping for each profile shape (hub-and-spoke, scaleout, standalone);
- the broker default (``chariot``) and an explicit override (``emqx``);
- the validation errors (unknown slug, non-broker slug, broker without wires);
- idempotency of the overlay and of ``resolve()`` on an overlaid config;
- that a redundant Engine gateway carries the Engine module on master + backup;
- that the catalog's Cirrus entries are present and identified, and that
  ``modules validate`` behaves consistently with the catalog's pinned state;
- a compose golden for hub-and-spoke + IIoT.

Set ``UPDATE_GOLDENS=1`` to regenerate the golden snapshot this file checks.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from ignition_stack.catalog.loader import load_catalog
from ignition_stack.catalog.schema import SHA256_UNPINNED, ModuleEntry
from ignition_stack.compose.engine import render_compose
from ignition_stack.config import ProjectConfig
from ignition_stack.profiles import ProfileOptions, apply_iiot, build_profile
from ignition_stack.services.loader import load_all_services
from ignition_stack.services.resolver import resolve

GOLDEN_DIR = Path(__file__).parent / "golden"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CIRRUS_ENTRIES = ("mqtt-engine", "mqtt-transmission", "mqtt-distributor")


def _attachments(gw: object) -> set[tuple[str, str]]:
    return {(att.instance, att.role) for att in gw.services}  # type: ignore[attr-defined]


def _gw(config: ProjectConfig, name: str):
    return next(g for g in config.gateways if g.name == name)


# --------------------------------------------------------------------------- #
# Role mapping per profile shape
# --------------------------------------------------------------------------- #


def test_hub_and_spoke_iiot_transmits_on_spokes_engine_on_hub() -> None:
    config = build_profile("hub-and-spoke", "hs", ProfileOptions(iiot=True, spokes=3))

    assert any(inst.id == "chariot" for inst in config.service_instances)
    hub = _gw(config, "hub")
    assert ("chariot", "mqtt-engine") in _attachments(hub)
    assert "mqtt-engine" in hub.modules
    for i in (1, 2, 3):
        spoke = _gw(config, f"spoke-{i}")
        assert ("chariot", "mqtt-transmission") in _attachments(spoke)
        assert "mqtt-transmission" in spoke.modules
        # Spokes run Edge; a broker is not never_on_edge, so this is valid.
        assert spoke.ignition_edition == "edge"


def test_scaleout_iiot_transmits_on_frontends_engine_on_backend() -> None:
    config = build_profile("scaleout", "so", ProfileOptions(iiot=True, frontends=2))

    backend = _gw(config, "backend")
    assert ("chariot", "mqtt-engine") in _attachments(backend)
    assert "mqtt-engine" in backend.modules
    for i in (1, 2):
        front = _gw(config, f"frontend-{i}")
        assert ("chariot", "mqtt-transmission") in _attachments(front)
        assert "mqtt-transmission" in front.modules


def test_standalone_iiot_runs_both_roles_on_the_one_gateway() -> None:
    """No transmission/engine roles in this topology -> the loop closes locally."""
    config = build_profile("standalone", "solo", ProfileOptions(iiot=True))

    gw = _gw(config, "gateway")
    assert _attachments(gw) == {
        ("chariot", "mqtt-transmission"),
        ("chariot", "mqtt-engine"),
    }
    assert set(gw.modules) == {"mqtt-transmission", "mqtt-engine"}


# --------------------------------------------------------------------------- #
# Broker selection + validation
# --------------------------------------------------------------------------- #


def test_iiot_defaults_to_chariot() -> None:
    config = build_profile("standalone", "d", ProfileOptions(iiot=True))
    assert [i.id for i in config.service_instances if i.service == "chariot"] == ["chariot"]


def test_iiot_broker_explicit_emqx_is_honored() -> None:
    config = build_profile("standalone", "d", ProfileOptions(iiot=True, iiot_broker="emqx"))
    assert any(i.id == "emqx" and i.service == "emqx" for i in config.service_instances)
    assert not any(i.service == "chariot" for i in config.service_instances)
    # The module slugs still come from emqx's own wires.mqtt block.
    gw = _gw(config, "gateway")
    assert "mqtt-transmission" in gw.modules and "mqtt-engine" in gw.modules


def test_iiot_none_broker_leaves_config_untouched() -> None:
    config = build_profile("standalone", "d", ProfileOptions(iiot=False))
    assert config.service_instances == []
    assert _gw(config, "gateway").modules == []
    # apply_iiot with an explicit None is a no-op too.
    assert apply_iiot(config, None) is config


def test_unknown_broker_slug_rejected() -> None:
    config = ProjectConfig(name="x")
    with pytest.raises(ValueError, match="unknown iiot broker"):
        apply_iiot(config, "not-a-broker")


def test_non_broker_slug_rejected() -> None:
    config = ProjectConfig(name="x")
    with pytest.raises(ValueError, match="not an mqtt-broker"):
        apply_iiot(config, "postgres")


def test_broker_without_wires_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broker manifest missing its wires.mqtt block can't name the modules."""
    import ignition_stack.services.loader as loader

    real = load_all_services()
    nowire = real["emqx"].model_copy(update={"name": "nowire", "wires": None})
    fake_catalog = {"nowire": nowire}
    monkeypatch.setattr(loader, "load_all_services", lambda: fake_catalog)

    config = ProjectConfig(name="x")
    with pytest.raises(ValueError, match="no wires.mqtt block"):
        apply_iiot(config, "nowire")


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


def test_apply_iiot_is_idempotent() -> None:
    config = build_profile("hub-and-spoke", "hs", ProfileOptions(iiot=True, spokes=2))
    before = config.model_dump()
    apply_iiot(config, "chariot")
    assert config.model_dump() == before


def test_resolve_is_idempotent_on_an_iiot_config() -> None:
    config = build_profile("scaleout", "so", ProfileOptions(iiot=True, frontends=2))
    once = resolve(config)
    twice = resolve(once)
    assert once.model_dump() == twice.model_dump()


# --------------------------------------------------------------------------- #
# Redundancy interaction (backup inherits the Engine module + attachment)
# --------------------------------------------------------------------------- #


def test_redundant_hub_engine_module_on_master_and_backup() -> None:
    config = build_profile("hub-and-spoke", "redhub", ProfileOptions(iiot=True, redundant_role="hub", spokes=2))
    resolved = resolve(config)
    for name in ("hub", "hub-backup"):
        gw = _gw(resolved, name)
        assert "mqtt-engine" in gw.modules, f"{name} missing engine module"
        assert ("chariot", "mqtt-engine") in _attachments(gw), f"{name} missing engine attachment"


# --------------------------------------------------------------------------- #
# Catalog: Cirrus entries + the broker wires reference them
# --------------------------------------------------------------------------- #


def test_broker_wires_modules_resolve_to_catalog_entries() -> None:
    """Every mqtt-broker's wires.mqtt names module slugs that exist in modules.yaml."""
    catalog = load_catalog()
    module_slugs = {e.name for e in catalog.entries}
    services = load_all_services()
    brokers = [m for m in services.values() if m.kind == "mqtt-broker"]
    assert brokers, "expected at least one mqtt-broker in the service catalog"
    for manifest in brokers:
        assert manifest.wires is not None and manifest.wires.mqtt is not None
        assert manifest.wires.mqtt.transmission_module in module_slugs
        assert manifest.wires.mqtt.engine_module in module_slugs


def test_cirrus_entries_present_and_identified() -> None:
    catalog = load_catalog()
    for slug in _CIRRUS_ENTRIES:
        entry = catalog.by_name(slug)
        assert isinstance(entry, ModuleEntry)
        assert entry.module_identifier.startswith("com.cirruslink.mqtt.")


def test_cirrus_entries_are_pinned() -> None:
    text = (_REPO_ROOT / "modules.yaml").read_text(encoding="utf-8")
    # Isolate each Cirrus entry's block and assert no UNPINNED sha remains.
    for slug in _CIRRUS_ENTRIES:
        block = re.search(rf"- name: {re.escape(slug)}\n(?:.*\n)*?(?=\n  - name:|\Z)", text)
        assert block is not None, f"{slug} entry not found in modules.yaml"
        assert SHA256_UNPINNED not in block.group(0), f"{slug} sha256 still UNPINNED"


def test_modules_validate_matches_pinned_state() -> None:
    """`modules validate --skip-network` passes iff no non-manual entry is UNPINNED.

    This mirrors the gate the smoke harness asserts (verification/smoke/run-test.sh)
    and stays green regardless of whether the maintainer has pinned the Cirrus
    artifacts yet, so it documents the gate without asserting a state the
    environment cannot reach.
    """
    from typer.testing import CliRunner

    from ignition_stack.cli import app

    catalog = load_catalog()
    has_unpinned = any(e.sha256 == SHA256_UNPINNED and not e.requires_manual_download for e in catalog.entries)
    result = CliRunner().invoke(app, ["modules", "validate", "--skip-network"])
    if has_unpinned:
        assert result.exit_code != 0
    else:
        assert result.exit_code == 0


# --------------------------------------------------------------------------- #
# Golden: hub-and-spoke + IIoT (chariot)
# --------------------------------------------------------------------------- #


def _check_or_update_golden(rel_path: str, actual: str) -> None:
    golden = GOLDEN_DIR / rel_path
    if os.environ.get("UPDATE_GOLDENS") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8")
        return
    assert golden.exists(), f"missing golden {golden}; run with UPDATE_GOLDENS=1 to create"
    expected = golden.read_text(encoding="utf-8")
    if expected != actual:
        from difflib import unified_diff

        diff = "\n".join(
            unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile=f"golden:{rel_path}",
                tofile="rendered",
                lineterm="",
                n=2,
            )
        )
        pytest.fail(f"compose output diverges from golden '{rel_path}'.\n" "Run with UPDATE_GOLDENS=1 to update if the change is intentional.\n\n" f"{diff}")


def test_hub_and_spoke_iiot_compose_golden() -> None:
    config = resolve(build_profile("hub-and-spoke", "hsiiot", ProfileOptions(iiot=True, spokes=2)))
    rendered = render_compose(config, catalog=load_catalog())
    _check_or_update_golden("combos/hub-and-spoke-iiot-chariot/docker-compose.yaml", rendered)
