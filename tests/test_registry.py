"""Phase 1 (issue #43) acceptance tests for the service-instance registry.

The registry (``ServiceInstance`` + per-gateway ``ServiceAttachment``) replaces
the flat ``services: list[str]`` + single ``database`` model. The legacy fields
stay loadable as input shims that :func:`resolve` lowers into the registry and
clears. These tests pin the invariants the rest of the codebase depends on:

- lowering is *invisible*: a legacy config and its lowered registry render the
  same compose + ``.env`` (the goldens are the cross-check elsewhere);
- ``resolve`` is idempotent (``resolve(resolve(c)) == resolve(c)``);
- the schema validators (unique ids, attachment refs, db-only fields, unknown
  slug) hold;
- the Phase-2 property shims work pre- and post-lowering;
- a v0.4.0-shaped lifecycle record (legacy fields, no registry) still loads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ignition_stack.compose.engine import render_compose
from ignition_stack.compose.writer import _render_env
from ignition_stack.config.io import load_config
from ignition_stack.config.schema import (
    DatabaseConfig,
    GatewayConfig,
    ProjectConfig,
    ServiceAttachment,
    ServiceInstance,
)
from ignition_stack.services.resolver import ResolveError, resolve

# --------------------------------------------------------------------------- #
# Lowering: legacy fields -> registry, then cleared
# --------------------------------------------------------------------------- #


def test_lowering_database_becomes_registry_instance_on_every_gateway() -> None:
    resolved = resolve(
        ProjectConfig(
            name="multi",
            gateways=[GatewayConfig(name="a"), GatewayConfig(name="b")],
        )
    )
    # Legacy fields cleared.
    assert resolved.database is None
    assert resolved.services == []
    # One db instance keyed by the old DatabaseConfig name ("db").
    db = resolved.database_instance()
    assert db is not None and db.id == "db" and db.service == "postgres"
    # Every gateway carries a consumer attachment to it.
    for gw in resolved.gateways:
        assert any(a.instance == "db" and a.role == "consumer" for a in gw.services)


def test_lowering_services_become_instances_in_input_order() -> None:
    resolved = resolve(ProjectConfig(name="combo", database=None, services=["hivemq", "opcua-sim"]))
    assert resolved.services == []
    non_db = [inst.id for inst in resolved.non_database_instances()]
    assert non_db == ["hivemq", "opcua-sim"]
    for gw in resolved.gateways:
        attached = {a.instance for a in gw.services}
        assert {"hivemq", "opcua-sim"} <= attached


def test_lowering_equivalence_compose_and_env_match_legacy_render() -> None:
    """A legacy config resolves to a registry whose compose + .env are stable.

    The render is taken from the resolved (lowered) config; the goldens pin the
    actual bytes elsewhere, here we assert the db service + DB_* keys survive.
    """
    resolved = resolve(ProjectConfig(name="demo"))
    compose = render_compose(resolved)
    assert "db:" in compose
    env = _render_env(resolved)
    assert "DB_USER=ignition" in env
    assert "DB_PASSWORD=ignition" in env
    assert "DB_HOST=db" in env
    assert "POSTGRES_IMAGE=postgres:18.1" in env


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "config",
    [
        ProjectConfig(name="demo"),
        ProjectConfig(name="kc", database=None, services=["keycloak"]),
        ProjectConfig(name="my", database=DatabaseConfig(kind="mysql")),
        ProjectConfig(
            name="multi",
            gateways=[GatewayConfig(name="a"), GatewayConfig(name="b")],
            services=["hivemq"],
        ),
    ],
)
def test_resolve_is_idempotent(config: ProjectConfig) -> None:
    once = resolve(config)
    twice = resolve(once)
    assert once.model_dump() == twice.model_dump()


# --------------------------------------------------------------------------- #
# Registry-level resolver validation
# --------------------------------------------------------------------------- #


def test_keycloak_database_lands_in_registry_extra_databases() -> None:
    resolved = resolve(ProjectConfig(name="kc", database=None, services=["keycloak"]))
    db = resolved.database_instance()
    assert db is not None and "keycloak" in db.extra_databases


def test_jdbc_driver_attaches_only_to_db_attached_gateways() -> None:
    resolved = resolve(
        ProjectConfig(
            name="my",
            database=DatabaseConfig(kind="mysql"),
            gateways=[GatewayConfig(name="a"), GatewayConfig(name="b")],
        )
    )
    for gw in resolved.gateways:
        assert "mysql-jdbc" in gw.modules


def test_more_than_one_database_instance_rejected() -> None:
    config = ProjectConfig(
        name="x",
        database=None,
        service_instances=[
            ServiceInstance(id="db", service="postgres"),
            ServiceInstance(id="db2", service="mysql"),
        ],
    )
    with pytest.raises(ResolveError, match="only one database"):
        resolve(config)


# --------------------------------------------------------------------------- #
# Schema validators
# --------------------------------------------------------------------------- #


def test_duplicate_instance_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="unique"):
        ProjectConfig(
            name="x",
            database=None,
            service_instances=[
                ServiceInstance(id="db", service="postgres"),
                ServiceInstance(id="db", service="mysql"),
            ],
        )


def test_attachment_referencing_unknown_instance_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown service instance"):
        ProjectConfig(
            name="x",
            database=None,
            service_instances=[ServiceInstance(id="db", service="postgres")],
            gateways=[GatewayConfig(name="gw", services=[ServiceAttachment(instance="ghost")])],
        )


def test_db_fields_on_non_database_instance_rejected() -> None:
    with pytest.raises(ValidationError, match="only valid for database services"):
        ServiceInstance(id="kc", service="keycloak", user="root")


def test_db_fields_on_database_instance_allowed() -> None:
    inst = ServiceInstance(id="db", service="postgres", user="root", extra_databases=["keycloak"])
    assert inst.user == "root" and inst.extra_databases == ["keycloak"]
    assert inst.image == "postgres:18.1"  # filled from manifest default


def test_unknown_service_slug_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown service"):
        ServiceInstance(id="x", service="does-not-exist")


def test_unknown_attachment_role_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown attachment role"):
        ServiceAttachment(instance="db", role="bogus")


def test_invalid_instance_id_rejected() -> None:
    with pytest.raises(ValidationError, match="instance id must start"):
        ServiceInstance(id="9bad", service="postgres")


# --------------------------------------------------------------------------- #
# Phase-2 property shims, pre- and post-lowering
# --------------------------------------------------------------------------- #


def test_property_shims_pre_lowering_read_legacy_database() -> None:
    config = ProjectConfig(name="x", database=DatabaseConfig(kind="postgres", user="u", password="p"))
    assert config.db_user == "u"
    assert config.db_password == "p"
    assert config.postgres_image == "postgres:18.1"


def test_property_shims_post_lowering_read_registry() -> None:
    resolved = resolve(ProjectConfig(name="x", database=DatabaseConfig(kind="postgres", user="u", password="p")))
    assert resolved.database is None
    assert resolved.db_user == "u"
    assert resolved.db_password == "p"
    assert resolved.postgres_image == "postgres:18.1"


def test_property_shims_no_database_return_empty() -> None:
    resolved = resolve(ProjectConfig(name="x", database=None))
    assert resolved.db_user == ""
    assert resolved.db_password == ""
    assert resolved.postgres_image == ""


# --------------------------------------------------------------------------- #
# Backward-compatible lifecycle record (v0.4.0 shape: legacy fields, no registry)
# --------------------------------------------------------------------------- #


def test_v040_shaped_record_loads_and_resolves(tmp_path: Path) -> None:
    """A record written before the registry existed must still load + resolve."""
    record = {
        "name": "legacy",
        "ignition_image": "inductiveautomation/ignition:8.3.6",
        "timezone": "UTC",
        "admin_username": "admin",
        "admin_password": "password",
        "gateways": [{"name": "gateway"}],
        "database": {
            "name": "db",
            "kind": "postgres",
            "image": "postgres:18.1",
            "user": "ignition",
            "password": "ignition",
            "extra_databases": [],
        },
        "services": ["keycloak"],
        "network_split": False,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(record), encoding="utf-8")

    loaded = load_config(path)
    assert loaded.database is not None and loaded.database.kind == "postgres"
    assert loaded.services == ["keycloak"]
    assert loaded.service_instances == []

    resolved = resolve(loaded)
    db = resolved.database_instance()
    assert db is not None and db.id == "db"
    assert "keycloak" in db.extra_databases
    assert any(inst.service == "keycloak" for inst in resolved.service_instances)
