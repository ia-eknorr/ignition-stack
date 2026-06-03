"""Dependency resolver: expand a user's service selections into a full config.

This is a **pure transformation**: given the raw :class:`ProjectConfig` the
user (or wizard, in Phase 6) built, it returns a new, fully-resolved
``ProjectConfig`` with every implicit dependency made explicit. The compose
engine then renders that resolved config verbatim and never re-resolves at
render time, which keeps the resolution rules unit-testable in isolation.

Two kinds of rule run here, per the hybrid-resolution decision in the design:

1. **Declarative** - each service manifest declares ``requires:`` capability
   tags. If nothing in the stack provides a required capability, the resolver
   auto-adds the default provider (today only databases are auto-addable:
   Keycloak ``requires: [sql-database]`` -> a Postgres database appears).

2. **Imperative** - a small ruleset for couplings that capability tags don't
   express cleanly:
   - Keycloak gets its own logical ``keycloak`` database created on the SQL
     server it lands on.
   - A MySQL database attaches the ``mysql-jdbc`` driver to every gateway so
     the connector ``.jar`` lands in ``user-lib/jdbc/``.
"""

from __future__ import annotations

from ignition_stack.config.schema import (
    DatabaseConfig,
    GatewayConfig,
    ProjectConfig,
    RedundancyConfig,
)
from ignition_stack.services.loader import load_all_services
from ignition_stack.services.manifest import ServiceManifest


class ResolveError(Exception):
    """Raised when a selection can't be satisfied (unknown service, DB conflict)."""


# Capability tags each database kind satisfies.
_DB_CAPABILITIES: dict[str, set[str]] = {
    "postgres": {"sql-database", "postgres-compatible"},
    "mysql": {"sql-database", "mysql-compatible"},
    "mariadb": {"sql-database", "mysql-compatible"},
    "mongo": {"document-store"},
}

# When a required database capability is provided by nothing, add this kind.
_DEFAULT_DB_FOR_CAPABILITY: dict[str, str] = {
    "sql-database": "postgres",
    "postgres-compatible": "postgres",
    "mysql-compatible": "mysql",
    "document-store": "mongo",
}

# Database kinds whose driver is not built into Ignition and must be attached
# to gateways as a catalog JDBC entry (slug -> the modules.yaml driver slug).
_DB_JDBC_DRIVER: dict[str, str] = {"mysql": "mysql-jdbc"}

# Database kinds that host a per-application logical database for Keycloak.
_KEYCLOAK_SQL_KINDS = {"postgres", "mysql", "mariadb"}


def resolve(config: ProjectConfig) -> ProjectConfig:
    """Return a deep copy of ``config`` with implicit dependencies expanded."""
    catalog = load_all_services()
    resolved = config.model_copy(deep=True)

    _validate_services(resolved, catalog)
    _expand_redundancy(resolved)
    _satisfy_required_capabilities(resolved, catalog)
    _apply_keycloak_database(resolved)
    _apply_jdbc_drivers(resolved)

    return resolved


def _expand_redundancy(config: ProjectConfig) -> None:
    """Expand each redundancy master into an explicit master + backup pair.

    A gateway stamped ``redundancy.mode == "master"`` (by ``mark_redundant`` or
    a hand-authored config) gets a sibling backup node appended - unless one
    already exists, which keeps re-resolving an already-expanded config
    idempotent. That idempotency is the Phase-2 dump/rebuild contract: dumping
    a resolved redundant stack and rebuilding it with ``-f`` must not grow a
    third gateway.

    The backup is a normal gateway carrying the master's role, edition, memory,
    and modules, on the next free HTTP port, with a ``backup`` descriptor
    pointing back at the master. Running it through the rest of the resolver
    (JDBC drivers, etc.) afterwards treats it like any other gateway. The
    compose engine wires its Gateway Network link to the master.
    """
    paired_masters = {
        gw.redundancy.peer
        for gw in config.gateways
        if gw.redundancy is not None and gw.redundancy.mode == "backup"
    }
    masters = [
        gw for gw in config.gateways if gw.redundancy is not None and gw.redundancy.mode == "master"
    ]
    for master in masters:
        if master.name in paired_masters:
            continue  # already expanded (e.g. loaded from a dumped config)
        next_port = max(gw.http_port for gw in config.gateways) + 1
        config.gateways.append(
            GatewayConfig(
                name=f"{master.name}-backup",
                role=master.role,
                ignition_edition=master.ignition_edition,
                memory_mb=master.memory_mb,
                http_port=next_port,
                modules=list(master.modules),
                disable_builtins=list(master.disable_builtins),
                redundancy=RedundancyConfig(
                    mode="backup",
                    peer=master.name,
                    gan_port=master.redundancy.gan_port,
                    seed_redundancy_xml=master.redundancy.seed_redundancy_xml,
                ),
            )
        )


def _validate_services(config: ProjectConfig, catalog: dict[str, ServiceManifest]) -> None:
    for svc in config.services:
        if svc not in catalog:
            known = ", ".join(sorted(catalog))
            raise ResolveError(f"unknown service '{svc}'; known services: {known}")
        if catalog[svc].kind == "database":
            raise ResolveError(
                f"'{svc}' is a database; set it as the project's 'database', not in 'services'"
            )


def _satisfy_required_capabilities(
    config: ProjectConfig, catalog: dict[str, ServiceManifest]
) -> None:
    required: set[str] = set()
    for svc in config.services:
        required.update(catalog[svc].requires)

    for cap in sorted(required):
        if _capability_satisfied(cap, config, catalog):
            continue
        db_kind = _DEFAULT_DB_FOR_CAPABILITY.get(cap)
        if db_kind is None:
            raise ResolveError(
                f"required capability '{cap}' is provided by no selected service "
                "and cannot be auto-added"
            )
        if config.database is not None:
            raise ResolveError(
                f"a '{config.database.kind}' database is selected, but capability "
                f"'{cap}' needs a different database and only one database is "
                "supported per stack; choose a compatible database"
            )
        config.database = DatabaseConfig(kind=db_kind)


def _capability_satisfied(
    cap: str, config: ProjectConfig, catalog: dict[str, ServiceManifest]
) -> bool:
    if config.database is not None and cap in _DB_CAPABILITIES.get(config.database.kind, set()):
        return True
    return any(cap in catalog[svc].provides for svc in config.services)


def _apply_keycloak_database(config: ProjectConfig) -> None:
    if "keycloak" not in config.services or config.database is None:
        return
    if config.database.kind not in _KEYCLOAK_SQL_KINDS:
        return
    if "keycloak" not in config.database.extra_databases:
        config.database.extra_databases.append("keycloak")


def _apply_jdbc_drivers(config: ProjectConfig) -> None:
    if config.database is None:
        return
    driver = _DB_JDBC_DRIVER.get(config.database.kind)
    if driver is None:
        return
    for gw in config.gateways:
        if driver not in gw.modules:
            gw.modules.append(driver)
