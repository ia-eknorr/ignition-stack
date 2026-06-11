"""Phase 2 (issue #43) acceptance tests: manifest growth + edge invariant.

Phase 2 makes three things declarative on the service manifests - ``singleton``,
``placement.never_on_edge``, and the broker ``wires.mqtt`` block - and wires two
consumers off them:

- the :class:`ProjectConfig` **edge invariant** rejects a hand-authored
  attachment from an Edge gateway to a ``never_on_edge`` service (databases);
- the resolver's legacy **lowering skips** Edge gateways when fanning a database
  out, so a hub-and-spoke stack with edge spokes no longer hands the spokes a DB
  connection (the issue's actual fix), and the single-database rule is relaxed to
  *multiple databases of distinct kinds*.

These tests pin the catalog invariants (so a future broker/database inherits the
rules for free), the edge invariant, the lowering skip + JDBC follow, and the
relaxed multi-database bounds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ignition_stack.catalog.loader import load_catalog
from ignition_stack.compose.engine import render_compose
from ignition_stack.compose.writer import _render_env, write_project
from ignition_stack.config.schema import (
    DatabaseConfig,
    GatewayConfig,
    ProjectConfig,
    ServiceAttachment,
    ServiceInstance,
)
from ignition_stack.services.loader import load_all_services
from ignition_stack.services.resolver import ResolveError, resolve


def _gw(config: ProjectConfig, name: str) -> GatewayConfig:
    return next(g for g in config.gateways if g.name == name)


# --------------------------------------------------------------------------- #
# Catalog invariants (a new driver/broker inherits the rules for free)
# --------------------------------------------------------------------------- #


def test_every_database_manifest_is_singleton_and_never_on_edge() -> None:
    catalog = load_all_services()
    dbs = [m for m in catalog.values() if m.kind == "database"]
    assert dbs, "expected at least one database manifest"
    for manifest in dbs:
        assert manifest.singleton, f"{manifest.name} database must be singleton"
        assert manifest.placement.never_on_edge, f"{manifest.name} must be never_on_edge"


def test_every_idp_and_broker_manifest_is_singleton() -> None:
    catalog = load_all_services()
    for manifest in catalog.values():
        if manifest.kind in {"idp", "mqtt-broker"}:
            assert manifest.singleton, f"{manifest.name} ({manifest.kind}) must be singleton"


def test_singleton_scope_separates_global_idp_from_attachment_scoped_infra() -> None:
    """Issue #67: databases and brokers are attachment-scoped singletons (a flat
    duplicate is legal), while the IdP (Keycloak) stays a genuinely stack-global
    singleton (its KEYCLOAK_* keys + realm seed are shared, so one only)."""
    catalog = load_all_services()
    for manifest in catalog.values():
        if not manifest.singleton:
            continue
        if manifest.kind in {"database", "mqtt-broker"}:
            assert manifest.singleton_scope == "attached", f"{manifest.name} should be an attachment-scoped singleton"
        elif manifest.kind == "idp":
            assert manifest.singleton_scope == "global", f"{manifest.name} should stay a stack-global singleton"


def test_every_broker_declares_mqtt_wires_with_real_module_slugs() -> None:
    """Every mqtt-broker manifest carries a wires.mqtt block whose module slugs
    resolve in modules.yaml - so the Phase-3 overlay can trust them."""
    catalog = load_all_services()
    modules = load_catalog()
    brokers = [m for m in catalog.values() if m.kind == "mqtt-broker"]
    assert brokers, "expected at least one broker manifest"
    for manifest in brokers:
        assert manifest.wires is not None and manifest.wires.mqtt is not None, f"{manifest.name} missing wires.mqtt"
        mqtt = manifest.wires.mqtt
        assert mqtt.port == 1883, f"{manifest.name} should front Sparkplug MQTT on 1883"
        # Both module slugs must exist in the modules catalog (raises if not).
        modules.by_name(mqtt.transmission_module)
        modules.by_name(mqtt.engine_module)


def test_non_singleton_services_stay_non_singleton() -> None:
    """Simulators / streaming / automation are not singletons - a stack may hold
    several (two opcua sims, say). Guards against a blanket singleton default."""
    catalog = load_all_services()
    for slug in ("kafka", "opcua-sim", "modbus-sim", "n8n"):
        assert catalog[slug].singleton is False, f"{slug} should not be a singleton"
        assert catalog[slug].placement.never_on_edge is False


# --------------------------------------------------------------------------- #
# Edge invariant: a hand-authored edge->database attachment is rejected
# --------------------------------------------------------------------------- #


def test_edge_gateway_attached_to_database_rejected() -> None:
    with pytest.raises(ValidationError, match="must not run on an Edge gateway"):
        ProjectConfig(
            name="x",
            database=None,
            service_instances=[ServiceInstance(id="db", service="postgres")],
            gateways=[
                GatewayConfig(
                    name="edge",
                    ignition_edition="edge",
                    services=[ServiceAttachment(instance="db")],
                )
            ],
        )


def test_standard_gateway_attached_to_database_allowed() -> None:
    config = ProjectConfig(
        name="x",
        database=None,
        service_instances=[ServiceInstance(id="db", service="postgres")],
        gateways=[
            GatewayConfig(
                name="gw",
                ignition_edition="standard",
                services=[ServiceAttachment(instance="db")],
            )
        ],
    )
    assert _gw(config, "gw").services[0].instance == "db"


def test_edge_gateway_may_attach_to_non_database_service() -> None:
    """Keycloak is not never_on_edge, so an edge gateway may use SSO from it."""
    config = ProjectConfig(
        name="x",
        database=None,
        service_instances=[ServiceInstance(id="kc", service="keycloak")],
        gateways=[
            GatewayConfig(
                name="edge",
                ignition_edition="edge",
                services=[ServiceAttachment(instance="kc")],
            )
        ],
    )
    assert _gw(config, "edge").services[0].instance == "kc"


# --------------------------------------------------------------------------- #
# Lowering skips edge gateways for never_on_edge instances (the issue's fix)
# --------------------------------------------------------------------------- #


def _hub_and_edge_spoke(db_kind: str) -> ProjectConfig:
    return ProjectConfig(
        name="hs",
        database=DatabaseConfig(kind=db_kind),
        gateways=[
            GatewayConfig(name="hub", role="hub", ignition_edition="standard"),
            GatewayConfig(name="spoke", role="spoke", ignition_edition="edge"),
        ],
    )


def test_lowering_skips_edge_spoke_database_attachment() -> None:
    resolved = resolve(_hub_and_edge_spoke("postgres"))
    assert any(a.instance == "db" for a in _gw(resolved, "hub").services)
    assert not any(a.instance == "db" for a in _gw(resolved, "spoke").services)


def test_lowering_skips_edge_spoke_jdbc_driver() -> None:
    """A MySQL stack gives the standard hub the mysql-jdbc driver but not the
    edge spoke (it never attaches to the database, so it needs no driver)."""
    resolved = resolve(_hub_and_edge_spoke("mysql"))
    assert "mysql-jdbc" in _gw(resolved, "hub").modules
    assert "mysql-jdbc" not in _gw(resolved, "spoke").modules


def test_edge_spoke_compose_has_no_db_dependency() -> None:
    rendered = render_compose(resolve(_hub_and_edge_spoke("postgres")))
    # The db service is still rendered (the hub uses it) ...
    assert "\n  db:\n" in rendered
    # ... and the edge spoke depends only on its bootstrap, not the db.
    spoke_block = rendered.split("  spoke:\n", 1)[1].split("\n  db:\n", 1)[0]
    assert "bootstrap-spoke" in spoke_block
    assert "db:\n        condition: service_healthy" not in spoke_block


def test_edge_spoke_gets_no_database_seed(tmp_path: Path) -> None:
    """Writer: the db-connection + secret-provider seed lands on the standard hub
    only; the edge spoke's services/ tree carries no database-connection."""
    write_project(_hub_and_edge_spoke("postgres"), tmp_path / "hs")
    base = tmp_path / "hs" / "services"
    hub_conn = base / "hub" / "config" / "resources" / "core" / "ignition" / "database-connection" / "db" / "config.json"
    assert hub_conn.is_file(), "hub should carry the seeded db-connection"
    spoke_dir = base / "spoke"
    assert spoke_dir.is_dir(), "spoke still gets its base services tree"
    assert not list(spoke_dir.rglob("database-connection")), "edge spoke must carry no db-connection seed"
    assert not list(spoke_dir.rglob("internal-secret-provider")), "edge spoke must carry no db secret seed"


def test_resolve_idempotent_hub_and_spoke_edge() -> None:
    once = resolve(_hub_and_edge_spoke("postgres"))
    twice = resolve(once)
    assert once.model_dump() == twice.model_dump()


# --------------------------------------------------------------------------- #
# Relaxed single-database rule: multiple databases of DISTINCT kinds
# --------------------------------------------------------------------------- #


def test_postgres_and_mongo_coexist() -> None:
    config = ProjectConfig(
        name="x",
        database=None,
        service_instances=[
            ServiceInstance(id="db", service="postgres"),
            ServiceInstance(id="docs", service="mongo"),
        ],
        gateways=[GatewayConfig(name="gw")],
    )
    resolved = resolve(config)
    kinds = sorted(i.service for i in resolved.database_instances())
    assert kinds == ["mongo", "postgres"]
    # Both render, each with its own per-kind image env key.
    rendered = render_compose(resolved)
    assert "\n  db:\n" in rendered and "\n  docs:\n" in rendered
    env = _render_env(resolved)
    assert "POSTGRES_IMAGE=" in env and "MONGO_IMAGE=" in env


def test_two_databases_same_kind_allowed_as_distinct_instances() -> None:
    """Issue #67: same-kind databases get distinct ids (and distinct compose
    services/containers), so two flat Postgres instances are legal - they only
    share the per-kind ${POSTGRES_IMAGE} key, which is identical by definition."""
    resolved = resolve(
        ProjectConfig(
            name="x",
            database=None,
            service_instances=[
                ServiceInstance(id="db", service="postgres"),
                ServiceInstance(id="db2", service="postgres"),
            ],
        )
    )
    assert {inst.id for inst in resolved.service_instances if inst.service == "postgres"} == {"db", "db2"}


def test_two_databases_on_one_gateway_rejected() -> None:
    with pytest.raises(ResolveError, match="at most one database connection"):
        resolve(
            ProjectConfig(
                name="x",
                database=None,
                service_instances=[
                    ServiceInstance(id="db", service="postgres"),
                    ServiceInstance(id="docs", service="mongo"),
                ],
                gateways=[
                    GatewayConfig(
                        name="gw",
                        services=[
                            ServiceAttachment(instance="db"),
                            ServiceAttachment(instance="docs"),
                        ],
                    )
                ],
            )
        )


def test_databases_with_mismatched_credentials_rejected() -> None:
    with pytest.raises(ResolveError, match="share the same user/password"):
        resolve(
            ProjectConfig(
                name="x",
                database=None,
                service_instances=[
                    ServiceInstance(id="db", service="postgres", user="a"),
                    ServiceInstance(id="docs", service="mongo", user="b"),
                ],
            )
        )


def test_second_attached_mqtt_broker_rejected() -> None:
    """Issue #67: the single-broker bound is attachment-scoped - two brokers both
    wired to a gateway still collide on the IIoT seeds and Sparkplug wiring."""
    with pytest.raises(ResolveError, match="only one attached mqtt-broker"):
        resolve(
            ProjectConfig(
                name="x",
                database=None,
                service_instances=[
                    ServiceInstance(id="b1", service="emqx"),
                    ServiceInstance(id="b2", service="hivemq"),
                ],
                gateways=[
                    GatewayConfig(
                        name="gw",
                        services=[
                            ServiceAttachment(instance="b1"),
                            ServiceAttachment(instance="b2"),
                        ],
                    )
                ],
            )
        )


def test_flat_second_mqtt_broker_allowed() -> None:
    """A flat (unattached) extra broker alongside a wired one is legal (#67)."""
    resolved = resolve(
        ProjectConfig(
            name="x",
            database=None,
            service_instances=[
                ServiceInstance(id="b1", service="emqx"),
                ServiceInstance(id="b2", service="hivemq"),
            ],
            gateways=[GatewayConfig(name="gw", services=[ServiceAttachment(instance="b1")])],
        )
    )
    assert {inst.id for inst in resolved.service_instances} >= {"b1", "b2"}


def test_second_instance_of_singleton_slug_rejected() -> None:
    with pytest.raises(ResolveError, match="singleton"):
        resolve(
            ProjectConfig(
                name="x",
                database=None,
                service_instances=[
                    ServiceInstance(id="k1", service="keycloak"),
                    ServiceInstance(id="k2", service="keycloak"),
                ],
            )
        )
