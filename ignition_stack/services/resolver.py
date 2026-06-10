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
    GatewayConfig,
    ProjectConfig,
    RedundancyConfig,
    ServiceAttachment,
    ServiceInstance,
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

    # Validate the legacy input shims first (unchanged ResolveError messages),
    # then lower them into the registry so every later pass reads one source of
    # truth. Validating before lowering keeps the unknown-slug / db-in-services
    # errors as ResolveError rather than the pydantic ValidationError the
    # ServiceInstance schema validator would raise once a slug is lowered.
    _validate_services(resolved, catalog)
    _lower_legacy(resolved, catalog)
    _validate_registry(resolved, catalog)
    _expand_redundancy(resolved)
    _satisfy_required_capabilities(resolved, catalog)
    _apply_keycloak_database(resolved, catalog)
    _apply_jdbc_drivers(resolved, catalog)

    return resolved


def _lower_legacy(config: ProjectConfig, catalog: dict[str, ServiceManifest]) -> None:
    """Lower the legacy ``database`` + ``services`` shims into the registry.

    Each legacy field becomes a :class:`ServiceInstance` plus a ``consumer``
    :class:`ServiceAttachment` on every gateway, then the legacy field is
    cleared so the registry is the single source of truth. Idempotent: on a
    re-resolve both legacy fields are already empty and the guard clauses skip
    instances/attachments that already exist, so ``resolve(resolve(c))`` equals
    ``resolve(c)``.

    The database keeps its old ``name`` as the instance ``id`` (the rendered DB
    service stays ``db``) and fans a consumer attachment to all gateways,
    matching the pre-registry behavior where every gateway connected to the one
    shared database.
    """
    if config.database is not None:
        db = config.database
        if not any(inst.id == db.name for inst in config.service_instances):
            config.service_instances.append(
                ServiceInstance(
                    id=db.name,
                    service=db.kind,
                    image=db.image,
                    user=db.user,
                    password=db.password,
                    extra_databases=list(db.extra_databases),
                )
            )
        _attach_all_gateways(config, db.name)
        config.database = None

    for slug in config.services:
        if not any(inst.id == slug for inst in config.service_instances):
            config.service_instances.append(ServiceInstance(id=slug, service=slug))
        _attach_all_gateways(config, slug)
    config.services = []


def _attach_all_gateways(config: ProjectConfig, instance_id: str, role: str = "consumer") -> None:
    """Give every gateway a ``role`` attachment to ``instance_id`` (idempotent)."""
    for gw in config.gateways:
        if not any(att.instance == instance_id for att in gw.services):
            gw.services.append(ServiceAttachment(instance=instance_id, role=role))


def _validate_registry(config: ProjectConfig, catalog: dict[str, ServiceManifest]) -> None:
    """Reject more than one database instance (multi-DB is Phase 2)."""
    db_instances = [inst for inst in config.service_instances if catalog[inst.service].kind == "database"]
    if len(db_instances) > 1:
        kinds = ", ".join(inst.service for inst in db_instances)
        raise ResolveError(f"only one database is supported per stack, but the registry has {len(db_instances)} ({kinds}); multi-database stacks arrive in a later phase")


def _database_instance(config: ProjectConfig, catalog: dict[str, ServiceManifest]) -> ServiceInstance | None:
    """The sole database-kind instance in the registry, or None."""
    return next(
        (inst for inst in config.service_instances if catalog[inst.service].kind == "database"),
        None,
    )


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
    paired_masters = {gw.redundancy.peer for gw in config.gateways if gw.redundancy is not None and gw.redundancy.mode == "backup"}
    masters = [gw for gw in config.gateways if gw.redundancy is not None and gw.redundancy.mode == "master"]
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
                services=[att.model_copy(deep=True) for att in master.services],
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
            raise ResolveError(f"'{svc}' is a database; set it as the project's 'database', not in 'services'")


def _satisfy_required_capabilities(config: ProjectConfig, catalog: dict[str, ServiceManifest]) -> None:
    """Auto-add a database instance for any unmet ``requires`` capability.

    Operates on the registry: collect ``requires`` across every instance's
    manifest and, for each capability nothing provides, add the default
    database as a :class:`ServiceInstance`. The auto-added database is wired to
    every gateway with a consumer attachment, preserving the pre-registry
    behavior where the implicit Keycloak database connected to (and seeded onto)
    all gateways. Its id is ``db`` so the rendered service name matches today's
    output exactly.
    """
    required: set[str] = set()
    for inst in config.service_instances:
        required.update(catalog[inst.service].requires)

    for cap in sorted(required):
        if _capability_satisfied(cap, config, catalog):
            continue
        db_kind = _DEFAULT_DB_FOR_CAPABILITY.get(cap)
        if db_kind is None:
            raise ResolveError(f"required capability '{cap}' is provided by no selected service " "and cannot be auto-added")
        existing = _database_instance(config, catalog)
        if existing is not None:
            raise ResolveError(
                f"a '{existing.service}' database is selected, but capability "
                f"'{cap}' needs a different database and only one database is "
                "supported per stack; choose a compatible database"
            )
        config.service_instances.append(ServiceInstance(id="db", service=db_kind))
        _attach_all_gateways(config, "db")


def _capability_satisfied(cap: str, config: ProjectConfig, catalog: dict[str, ServiceManifest]) -> bool:
    for inst in config.service_instances:
        manifest = catalog[inst.service]
        if manifest.kind == "database" and cap in _DB_CAPABILITIES.get(inst.service, set()):
            return True
        if cap in manifest.provides:
            return True
    return False


def _apply_keycloak_database(config: ProjectConfig, catalog: dict[str, ServiceManifest]) -> None:
    if not any(inst.service == "keycloak" for inst in config.service_instances):
        return
    db = _database_instance(config, catalog)
    if db is None or db.service not in _KEYCLOAK_SQL_KINDS:
        return
    if "keycloak" not in db.extra_databases:
        db.extra_databases.append("keycloak")


def _apply_jdbc_drivers(config: ProjectConfig, catalog: dict[str, ServiceManifest]) -> None:
    """Attach the JDBC driver module to each gateway bound to a needy database.

    Per-gateway: a gateway gets the driver only when it attaches to a database
    instance whose kind has no built-in Ignition driver. For lowered configs
    every gateway attaches to the database, so this reproduces the old
    fan-to-all-gateways behavior; a future edge gateway with no DB attachment
    keeps a clean module list.
    """
    instances = {inst.id: inst for inst in config.service_instances}
    for gw in config.gateways:
        for att in gw.services:
            inst = instances.get(att.instance)
            if inst is None:
                continue
            driver = _DB_JDBC_DRIVER.get(inst.service)
            if driver is not None and driver not in gw.modules:
                gw.modules.append(driver)
