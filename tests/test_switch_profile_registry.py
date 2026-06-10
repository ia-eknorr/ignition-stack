"""switch-profile carries the service registry across a reshape (issue #43 p6).

These tests pin the Phase-6 carry semantics:

- IIoT intent (broker + Transmission/Engine roles) is recovered into
  ``ProfileOptions`` and re-applied by ``apply_iiot`` in the new topology, so the
  pipeline re-maps onto the new roles (spokes' Transmission -> frontends';
  hub's Engine -> backend's).
- Richer registry shapes ``ProfileOptions`` can't express - custom instance ids,
  per-instance overrides, partial / role-specific attachments, a second database
  - are re-grafted onto the rebuilt config by role, with attachments the new
  topology can't host dropped and a printed advisory.
- Re-grafting respects the new topology's invariants (Edge never_on_edge,
  one database per gateway): a violating attachment is dropped, not raised.

The driver is the real ``switch-profile`` CLI command via ``CliRunner`` so the
advisory output is captured exactly as a user would see it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ignition_stack.cli import app
from ignition_stack.config import load_config
from ignition_stack.lifecycle import read_record
from ignition_stack.profiles import ProfileOptions, build_profile
from ignition_stack.services.resolver import resolve


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _attachments(gw) -> set[tuple[str, str]]:
    return {(att.instance, att.role) for att in gw.services}


def _init_from_file(runner: CliRunner, tmp_path: Path, yaml_text: str, name: str) -> Path:
    arch = tmp_path / "arch.yml"
    arch.write_text(yaml_text, encoding="utf-8")
    result = runner.invoke(app, ["init", name, "-f", str(arch), "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    return tmp_path / name


# --------------------------------------------------------------------------- #
# IIoT carry: hub-and-spoke -> scaleout                                        #
# --------------------------------------------------------------------------- #


def test_iiot_carries_hub_and_spoke_to_scaleout(runner: CliRunner, tmp_path: Path) -> None:
    """spokes' Transmission becomes frontends'; hub's Engine becomes backend's.

    A hub-and-spoke + IIoT stack reshaped to scaleout must keep the pipeline:
    the broker re-appears, the edge-side role (now `frontend`) carries
    mqtt-transmission, and the central role (now `backend`) carries mqtt-engine -
    re-mapped by ``apply_iiot`` from the recovered iiot intent, not re-grafted.
    """
    result = runner.invoke(
        app,
        ["init", "demo", "--profile", "hub-and-spoke", "--spokes", "2", "--iiot", "-o", str(tmp_path)],
    )
    assert result.exit_code == 0, result.stdout
    project = tmp_path / "demo"

    result = runner.invoke(app, ["switch-profile", "scaleout", "-C", str(project)])
    assert result.exit_code == 0, result.stdout

    record = read_record(project)
    by_name = {gw.name: gw for gw in record.gateways}
    # The broker instance survived.
    assert any(inst.service == "chariot" for inst in record.service_instances)
    # Edge-side role (frontend) publishes; central role (backend) aggregates.
    assert ("chariot", "mqtt-transmission") in _attachments(by_name["frontend"])
    assert ("chariot", "mqtt-engine") in _attachments(by_name["backend"])
    # No gateway carries the *old* role mapping anymore.
    for gw in record.gateways:
        roles = {role for _, role in _attachments(gw)}
        if gw.role == "frontend":
            assert "mqtt-engine" not in roles
        if gw.role == "backend":
            assert "mqtt-transmission" not in roles


def test_iiot_carries_cleanly_without_advisory(runner: CliRunner, tmp_path: Path) -> None:
    """A clean IIoT carry prints no drop advisory."""
    runner.invoke(
        app,
        ["init", "demo", "--profile", "hub-and-spoke", "--spokes", "2", "--iiot", "-o", str(tmp_path)],
    )
    result = runner.invoke(app, ["switch-profile", "scaleout", "-C", str(tmp_path / "demo")])
    assert result.exit_code == 0, result.stdout
    assert "note" not in result.stdout


# --------------------------------------------------------------------------- #
# Custom-id instance + override carry                                          #
# --------------------------------------------------------------------------- #


_CUSTOM_DB_YAML = """\
name: plant
database: null
network_split: true
gateways:
  - name: frontend
    role: frontend
    http_port: 9088
    services:
      - instance: keycloak
  - name: backend
    role: backend
    http_port: 9089
    services:
      - instance: ts-store
      - instance: keycloak
service_instances:
  - id: ts-store
    service: mongo
    image: mongo:8
  - id: keycloak
    service: keycloak
"""


def test_custom_id_instance_carries_with_overrides(runner: CliRunner, tmp_path: Path) -> None:
    """A custom-id Mongo (image override) re-grafts onto the central role.

    `ts-store` (id != slug, image override) can't ride ProfileOptions, so it is
    re-grafted: switching scaleout->hub-and-spoke maps the backend's attachment
    onto the hub (backend<->hub counterpart) and preserves the custom id +
    image. Keycloak rides `services` and lands on every gateway.
    """
    project = _init_from_file(runner, tmp_path, _CUSTOM_DB_YAML, "plant")

    result = runner.invoke(app, ["switch-profile", "hub-and-spoke", "-C", str(project)])
    assert result.exit_code == 0, result.stdout

    record = read_record(project)
    ts = next((i for i in record.service_instances if i.id == "ts-store"), None)
    assert ts is not None and ts.service == "mongo" and ts.image == "mongo:8"
    by_name = {gw.name: gw for gw in record.gateways}
    assert ("ts-store", "consumer") in _attachments(by_name["hub"])
    # The hub (central) holds the custom store; spokes do not.
    for name, gw in by_name.items():
        if name != "hub":
            assert ("ts-store", "consumer") not in _attachments(gw)


def test_custom_id_instance_carries_to_standalone(runner: CliRunner, tmp_path: Path) -> None:
    """Collapsing to standalone lands the central-role store on the one gateway."""
    project = _init_from_file(runner, tmp_path, _CUSTOM_DB_YAML, "plant")
    result = runner.invoke(app, ["switch-profile", "standalone", "-C", str(project)])
    assert result.exit_code == 0, result.stdout
    record = read_record(project)
    assert [gw.name for gw in record.gateways] == ["gateway"]
    assert ("ts-store", "consumer") in _attachments(record.gateways[0])


# --------------------------------------------------------------------------- #
# Drop advisories: no counterpart, edge invariant                             #
# --------------------------------------------------------------------------- #


_DUAL_STORE_YAML = """\
name: plant
database: null
gateways:
  - name: frontend
    role: frontend
    http_port: 9088
    services:
      - instance: store
  - name: backend
    role: backend
    http_port: 9089
    services:
      - instance: store
service_instances:
  - id: store
    service: mongo
    image: mongo:8
"""


class _Recorder:
    """Captures the rich-console advisory lines carry_registry prints."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args: object, **kwargs: object) -> None:
        self.lines.append(" ".join(str(a) for a in args))


def test_edge_invariant_drops_database_with_advisory(tmp_path: Path) -> None:
    """A re-grafted DB the new topology can't host is dropped with an advisory.

    Driven at the carry level so we can force the Edge target topology that the
    switch CLI would only reach if the source already ran Edge. The custom Mongo
    `store` maps onto every gateway of the hub-and-spoke target, where two new
    invariants bar it: it must not run on the Edge `spoke-*` gateways, and the
    `hub` already holds the profile's default Postgres (one DB per gateway). Both
    drops fire with `[yellow]note[/yellow]` advisories naming the reason, and the
    result resolves cleanly instead of raising.
    """
    from ignition_stack.profiles.carry import carry_registry

    arch = tmp_path / "arch.yml"
    arch.write_text(_DUAL_STORE_YAML, encoding="utf-8")
    old = resolve(load_config(arch))

    # Resolve first (as switch-profile does) so the profile's default database is
    # already lowered into attachments before the carry runs.
    new = resolve(build_profile("hub-and-spoke", "plant", ProfileOptions(spokes=2, edge_role="spoke")))
    recorder = _Recorder()
    new = carry_registry(new, old, recorder)
    new = resolve(new)  # must not raise

    advisory = "\n".join(recorder.lines)
    assert "note" in advisory
    assert "store" in advisory
    assert "Edge" in advisory  # the never_on_edge drop named the reason
    assert "already holds a database" in advisory  # the one-DB-per-gateway drop

    # The store instance rode along registry-level, but no gateway attaches to it.
    assert any(i.id == "store" for i in new.service_instances)
    for gw in new.gateways:
        assert ("store", "consumer") not in _attachments(gw)


def test_no_counterpart_drop_prints_advisory(tmp_path: Path) -> None:
    """An attachment whose source gateway has no target counterpart is dropped.

    A role-less standalone gateway holding a custom store, reshaped to a topology
    with only edge-side roles... is hard to construct, so this exercises the
    branch directly: an attachment on a role the target lacks entirely.
    """
    from ignition_stack.config.schema import (
        GatewayConfig,
        ProjectConfig,
        ServiceAttachment,
        ServiceInstance,
    )
    from ignition_stack.profiles.carry import carry_registry

    # Old: a gateway with an unusual role 'oddball' holding a custom store.
    old = resolve(
        ProjectConfig(
            name="plant",
            database=None,
            gateways=[
                GatewayConfig(
                    name="odd",
                    role="oddball",
                    services=[ServiceAttachment(instance="store", role="consumer")],
                )
            ],
            service_instances=[ServiceInstance(id="store", service="mongo", image="mongo:8")],
        )
    )
    # New: hub-and-spoke - 'oddball' is neither central nor edge-side, so no
    # counterpart gateway exists and the store attachment is dropped.
    new = resolve(build_profile("hub-and-spoke", "plant", ProfileOptions(spokes=2, edge_role="spoke")))
    recorder = _Recorder()
    new = carry_registry(new, old, recorder)
    new = resolve(new)

    advisory = "\n".join(recorder.lines)
    assert "no counterpart" in advisory
    assert "store" in advisory
    # The instance still rode along (registry-level), just unattached.
    assert any(i.id == "store" for i in new.service_instances)


def test_reshape_result_is_valid_after_carry(runner: CliRunner, tmp_path: Path) -> None:
    """Whatever the carry produces must survive a fresh resolve() unchanged.

    The recorded config is the carry's output; re-resolving it (as write_project
    does) must not raise and must be a fixed point - the strongest statement that
    the carry never emits an invalid registry.
    """
    project = _init_from_file(runner, tmp_path, _CUSTOM_DB_YAML, "plant")
    runner.invoke(app, ["switch-profile", "hub-and-spoke", "-C", str(project)])
    record = read_record(project)
    once = resolve(record)
    assert once.model_dump() == resolve(once).model_dump()


# --------------------------------------------------------------------------- #
# Unit-level: _options_from_config recovery                                    #
# --------------------------------------------------------------------------- #


def test_options_recover_iiot_intent() -> None:
    from ignition_stack.cli import _options_from_config

    config = resolve(build_profile("hub-and-spoke", "demo", ProfileOptions(spokes=2, iiot=True)))
    options = _options_from_config(config)
    assert options.iiot is True
    assert options.iiot_broker == "chariot"
    # The broker is not double-counted in `services`.
    assert "chariot" not in options.services


def test_options_leave_database_kind_none_for_custom_primary_db(tmp_path: Path) -> None:
    from ignition_stack.cli import _options_from_config

    arch = tmp_path / "arch.yml"
    arch.write_text(_CUSTOM_DB_YAML, encoding="utf-8")
    config = resolve(load_config(arch))
    options = _options_from_config(config)
    # The custom-id Mongo must not be carried as database_kind (it would rebuild
    # a colliding id="db" default); it rides the re-graft instead.
    assert options.database_kind is None
