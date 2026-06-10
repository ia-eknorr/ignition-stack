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
        _attach_all_gateways(config, db.name, catalog)
        config.database = None

    for slug in config.services:
        if not any(inst.id == slug for inst in config.service_instances):
            config.service_instances.append(ServiceInstance(id=slug, service=slug))
        _attach_all_gateways(config, slug, catalog)
    config.services = []


def _attach_all_gateways(
    config: ProjectConfig,
    instance_id: str,
    catalog: dict[str, ServiceManifest],
    role: str = "consumer",
) -> None:
    """Give every eligible gateway a ``role`` attachment to ``instance_id``.

    Idempotent (skips gateways that already attach). **Edge gateways are skipped
    for ``placement.never_on_edge`` instances** (databases): this is the issue
    #43 fix, so lowering a legacy hub-and-spoke stack with edge spokes no longer
    hands those spokes a database connection. The skip is what intentionally
    changes that golden - the spoke loses the db seed overlay, its ``depends_on``
    on the db, the GATEWAY db env var, and any JDBC driver.
    """
    inst = next((i for i in config.service_instances if i.id == instance_id), None)
    never_on_edge = inst is not None and catalog[inst.service].placement.never_on_edge
    for gw in config.gateways:
        if never_on_edge and gw.ignition_edition == "edge":
            continue
        if not any(att.instance == instance_id for att in gw.services):
            gw.services.append(ServiceAttachment(instance=instance_id, role=role))


def _validate_registry(config: ProjectConfig, catalog: dict[str, ServiceManifest]) -> None:
    """Enforce the Phase-2 multi-instance bounds across the registry.

    Phase 2 relaxes the old single-database rule but only within a bounded
    scope, because the compose fragments and .env share a per-kind/global key
    vocabulary that collides outside these bounds:

    - **Multiple databases allowed iff distinct kinds.** Two ``postgres``
      instances would both want ``${POSTGRES_IMAGE}`` and the shared
      ``${DB_USER}`` / ``${DB_PASSWORD}`` keys - same-kind duplicates collide.
    - **All databases share one user/password** - the fragments read the single
      shared ``DB_USER`` / ``DB_PASSWORD`` pair.
    - **At most one database attachment per gateway** - a gateway seeds one
      ``internal-secret-provider``; a second db's secret would overwrite it.
    - **Singletons** (``singleton: true`` manifests: db / idp / broker) appear at
      most once per slug.
    - **At most one mqtt-broker instance** per stack (multi-broker is a non-goal).
    """
    instances = config.service_instances
    db_instances = [inst for inst in instances if catalog[inst.service].kind == "database"]

    db_kinds = [inst.service for inst in db_instances]
    dup_kinds = sorted({k for k in db_kinds if db_kinds.count(k) > 1})
    if dup_kinds:
        raise ResolveError(
            f"duplicate database kind(s) {dup_kinds}: two instances of the same "
            "database kind collide on the per-kind image and shared "
            "DB_USER/DB_PASSWORD .env keys, so same-kind duplicates are not "
            "supported; use distinct database kinds"
        )

    credentials = {(inst.user, inst.password) for inst in db_instances}
    if len(credentials) > 1:
        raise ResolveError(
            "all database instances must share the same user/password: the "
            "compose fragments consume the single shared DB_USER/DB_PASSWORD "
            f"keys, but the registry declares {len(credentials)} distinct pairs"
        )

    db_ids = {inst.id for inst in db_instances}
    for gw in config.gateways:
        attached = [att.instance for att in gw.services if att.instance in db_ids]
        if len(attached) > 1:
            joined = ", ".join(attached)
            raise ResolveError(
                f"gateway '{gw.name}' attaches to {len(attached)} databases "
                f"({joined}); a gateway may hold at most one database connection "
                "(the seeded internal-secret-provider would collide)"
            )

    singleton_counts: dict[str, int] = {}
    for inst in instances:
        if catalog[inst.service].singleton:
            singleton_counts[inst.service] = singleton_counts.get(inst.service, 0) + 1
    over = sorted(slug for slug, count in singleton_counts.items() if count > 1)
    if over:
        raise ResolveError(f"service(s) {over} are singletons (singleton: true) but the registry declares more than one instance of each; declare a single instance")

    brokers = [inst for inst in instances if catalog[inst.service].kind == "mqtt-broker"]
    if len(brokers) > 1:
        kinds = ", ".join(inst.service for inst in brokers)
        raise ResolveError(f"only one mqtt-broker is supported per stack, but the registry has {len(brokers)} ({kinds}); multi-broker stacks are a non-goal")


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
            raise ResolveError(f"required capability '{cap}' is provided by no selected service and cannot be auto-added")
        # Phase 2 allows multiple databases of distinct kinds, so an existing
        # database that does NOT satisfy this capability (e.g. a Mongo store next
        # to Keycloak's SQL requirement) no longer blocks the auto-add. A
        # same-kind duplicate would have satisfied the capability above, so it
        # never reaches this branch; _validate_registry still guards the bounds.
        # The auto-added database satisfies a *service -> service* infra dep
        # (Keycloak's backing store), so it is registry-level only: no gateway
        # consumer attachment is fanned out. A gateway that wants to use this
        # database as a historian must attach to it explicitly. This is what
        # lets an Edge gateway run Keycloak SSO while never holding a DB
        # connection (issue #43's "two kinds of dependency" split).
        db_id = _free_database_id(config, db_kind)
        config.service_instances.append(ServiceInstance(id=db_id, service=db_kind))


def _free_database_id(config: ProjectConfig, db_kind: str) -> str:
    """Pick the id for an auto-added database, preferring the historical ``db``.

    Single-database stacks keep ``id="db"`` so the rendered service name matches
    today's output exactly. When ``db`` is already taken (a hand-authored
    instance named ``db`` of another kind), fall back to ``db-<kind>`` so the
    auto-add never collides with the existing id.
    """
    taken = {inst.id for inst in config.service_instances}
    if "db" not in taken:
        return "db"
    candidate = f"db-{db_kind}"
    suffix = 2
    while candidate in taken:
        candidate = f"db-{db_kind}-{suffix}"
        suffix += 1
    return candidate


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
    # Keycloak's logical database lands on the SQL server it can actually use,
    # not merely the first database in registry order: a heterogeneous stack may
    # hold a Mongo store ahead of the SQL database Keycloak's requirement added.
    db = next(
        (inst for inst in config.service_instances if inst.service in _KEYCLOAK_SQL_KINDS),
        None,
    )
    if db is None:
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
