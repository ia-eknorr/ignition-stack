"""Pydantic schema for resolved project configuration.

Phase 4 generalizes the Phase 2 single-gateway shape into a multi-gateway
model with per-gateway overrides and an opt-in network split. The defaults
are tuned so a bare ``ProjectConfig(name="demo")`` still resolves to exactly
the Phase 2 walking skeleton (one standalone gateway + Postgres on a single
bridge network).

Phases 5-6 extend this with the service catalog and architecture-class shaping.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Lowercase letters, digits, hyphen, underscore. Must start with a letter
# because container_name / hostname need to be DNS-safe and Postgres
# database names follow the same shape.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# Supported database kinds and their default images + .env image keys. The
# default image is filled in when DatabaseConfig.image is left blank so a bare
# DatabaseConfig(kind="mysql") resolves to a runnable image. Postgres stays
# pinned to 18.1 to match the Phase-2 walking-skeleton golden; the others track
# the current major tag (a demo tool, not a production pin).
_DB_DEFAULT_IMAGE = {
    "postgres": "postgres:18.1",
    "mysql": "mysql:9",
    "mariadb": "mariadb:11",
    "mongo": "mongo:7",
}
_DB_IMAGE_ENV = {
    "postgres": "POSTGRES_IMAGE",
    "mysql": "MYSQL_IMAGE",
    "mariadb": "MARIADB_IMAGE",
    "mongo": "MONGO_IMAGE",
}

# A service slug names a database instance exactly when it is one of the four
# database kinds (the database catalog is keyed by kind). Membership in this
# table is the catalog-free way the registry helpers and the Phase-2 property
# shims tell a database instance apart from any other catalog service without
# loading the service catalog at schema-import time.
_DB_SERVICE_SLUGS = frozenset(_DB_DEFAULT_IMAGE)

# Environment-variable override keys must be POSIX-shell-safe identifiers: a
# letter or underscore, then letters/digits/underscores. This is the shape both
# Docker Compose and the gateway image accept, and it rejects the ``KEY=VALUE``
# typos (spaces, '=', lowercase shell metacharacters) the wizard prompt invites.
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_env_keys(env: dict[str, str]) -> dict[str, str]:
    """Reject env-override keys that are not shell-safe identifiers.

    Shared by :class:`GatewayConfig` and :class:`ServiceInstance` so both
    override surfaces enforce the same vocabulary; a bad key fails loudly at
    construction (and at the wizard's reconstruct-and-revalidate choke point)
    rather than emitting a malformed compose ``environment:`` entry.
    """
    for key in env:
        if not _ENV_KEY_RE.match(key):
            raise ValueError(f"invalid environment-variable key '{key}': must start with a letter or underscore and contain only letters, digits, and underscores")
    return env


# Roles a gateway attachment may declare. Phase 1 only renders "consumer"
# (a plain DB consumer, the lowering default); "owner" and the two mqtt roles
# are reserved for the Phase 2-3 Edge invariant + IIoT overlay but are accepted
# now so hand-authored configs can express them ahead of the wiring.
_ATTACHMENT_ROLES = frozenset({"consumer", "owner", "mqtt-transmission", "mqtt-engine"})


class ServiceInstance(BaseModel):
    """One concrete service in the stack-level registry.

    Phase 1 of issue #43 replaces the flat ``services: list[str]`` + single
    ``database`` model with this registry: each entry is an addressable
    instance keyed by ``id`` and backed by a catalog ``service`` slug. The
    legacy fields stay loadable as input shims that ``resolve()`` lowers into
    registry entries (see :func:`ignition_stack.services.resolver._lower_legacy`),
    so existing configs keep rendering byte-identical output.

    The database-only extras (``user`` / ``password`` / ``extra_databases``)
    fold the old :class:`DatabaseConfig` into the instance. They are rejected on
    a non-database ``service`` by a catalog-aware validator so a typo can't
    silently set credentials on, say, a broker.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique registry key; also the compose service name and seed dir.")
    service: str = Field(description="Catalog slug backing this instance (manifest lookup key).")
    image: str = Field(default="", description="image:tag override; filled from the manifest default when blank.")
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Per-instance .env overrides layered over the manifest's preset env.",
    )
    user: str = Field(default="ignition", description="Database user (database services only).")
    password: str = Field(default="ignition", description="Database password (database services only).")
    extra_databases: list[str] = Field(
        default_factory=list,
        description="Extra logical databases to create on first init (database services only).",
    )

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError("instance id must start with a lowercase letter and contain only lowercase letters, digits, hyphens, or underscores")
        return v

    @field_validator("env")
    @classmethod
    def _validate_env(cls, v: dict[str, str]) -> dict[str, str]:
        return _validate_env_keys(v)

    @field_validator("service")
    @classmethod
    def _validate_service_exists(cls, v: str) -> str:
        # Catalog lookup in a validator, precedented by
        # GatewayConfig._validate_disable_builtins; imported locally to keep the
        # schema free of a load-time catalog dependency.
        from ignition_stack.services.loader import load_all_services

        catalog = load_all_services()
        if v not in catalog:
            known = ", ".join(sorted(catalog))
            raise ValueError(f"unknown service '{v}'; known services: {known}")
        return v

    @model_validator(mode="after")
    def _validate_db_fields_and_fill_image(self) -> ServiceInstance:
        from ignition_stack.services.loader import load_service

        manifest = load_service(self.service)
        if manifest.kind != "database" and (self.user != "ignition" or self.password != "ignition" or self.extra_databases):
            raise ValueError(f"user/password/extra_databases are only valid for database services; '{self.service}' is kind '{manifest.kind}'")
        if not self.image:
            self.image = manifest.image
        return self

    @property
    def is_database(self) -> bool:
        """True when this instance is one of the four database kinds."""
        return self.service in _DB_SERVICE_SLUGS

    @property
    def image_env(self) -> str:
        """The ``.env`` key the compose fragment reads for this instance's image.

        Database instances map their kind to the fixed ``<KIND>_IMAGE`` key;
        non-database instances carry the key on their manifest, so this property
        is only meaningful for databases (the writer reads the manifest directly
        for the rest).
        """
        return _DB_IMAGE_ENV[self.service]


class ServiceAttachment(BaseModel):
    """A per-gateway reference to a registry :class:`ServiceInstance`.

    The attachment is the gateway -> service edge: it names the instance the
    gateway uses and the ``role`` it plays (Phase 1 lowers everything to
    ``consumer``). Keeping the edge explicit is what lets a future Edge gateway
    use Keycloak SSO while never holding a database connection.
    """

    model_config = ConfigDict(extra="forbid")

    instance: str = Field(description="The ServiceInstance.id this gateway attaches to.")
    role: str = Field(default="consumer", description="consumer | owner | mqtt-transmission | mqtt-engine.")

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in _ATTACHMENT_ROLES:
            allowed = ", ".join(sorted(_ATTACHMENT_ROLES))
            raise ValueError(f"unknown attachment role '{v}'; allowed: {allowed}")
        return v


class RedundancyConfig(BaseModel):
    """Redundancy descriptor attached to a gateway that is part of a pair.

    Ignition redundancy is strictly two-node master/backup. A role marked
    redundant resolves into two gateways (see ``services.resolver``): the
    master keeps the role's name and carries ``mode="master"``; the backup is
    named ``<master>-backup`` and carries ``mode="backup"`` with ``peer`` set
    to the master's service name. ``peer`` always names the *other* node.

    The Phase-3 spike (``verification/redundancy-spike/``) verified a fully
    zero-touch pair on 8.3.6 using a **plain** (non-SSL) Gateway Network link
    on port 8088 plus a pre-seeded ``data/redundancy.xml`` per node. So
    ``gan_port`` defaults to 8088 and ``seed_redundancy_xml`` defaults on -
    the seed is what actually sets the redundancy *role* (no env var does),
    and plain/8088 sidesteps the GAN certificate-approval handshake that the
    SSL path (8060) would force into a manual UI step. Plain transport is a
    demo-only default; cross-host deployments should use SSL + approved certs.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["master", "backup"] = Field(description="This node's redundancy role: 'master' or 'backup'.")
    peer: str = Field(
        description=("Service name of the other node in the pair. The backup points at the master here (and over the Gateway Network); the master points at its backup."),
    )
    gan_port: int = Field(
        default=8088,
        ge=1,
        le=65535,
        description=("Gateway Network port the redundancy link rides. 8088 is plain (non-SSL) and auto-approves; 8060 is SSL and needs a cert approval."),
    )
    seed_redundancy_xml: bool = Field(
        default=True,
        description=(
            "Pre-seed data/redundancy.xml so the node boots straight into its "
            "role. On by default per the Phase-3 spike: nothing else sets the "
            "redundancy mode, so without the seed the pair never forms."
        ),
    )


class GatewayConfig(BaseModel):
    """A single Ignition gateway in the stack.

    ``name`` doubles as the compose service key for this gateway, the
    directory name under ``services/`` that holds its file-config
    resources, and the prefix for its per-gateway env vars in the
    generated ``.env``. The Phase-2 default ``name="gateway"`` keeps the
    walking skeleton's existing layout intact.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="gateway")
    role: str | None = Field(
        default=None,
        description=(
            "Optional role tag used by the network-split logic and architecture "
            "classes (e.g. 'frontend', 'backend', 'hub', 'spoke'). When "
            "network_split is on the role decides which network the "
            "gateway joins."
        ),
    )
    ignition_edition: str = Field(
        default="standard",
        description="Value of the IGNITION_EDITION env var: 'standard' or 'edge'.",
    )
    memory_mb: int = Field(default=2048, ge=256)
    http_port: int = Field(default=9088, ge=1, le=65535)
    modules: list[str] = Field(
        default_factory=list,
        description=(
            "Module catalog entry names (slugs) to attach to this gateway. "
            "The compose engine wires each cached .modl into the gateway "
            "volume AND enumerates it in ACCEPT_MODULE_LICENSES + "
            "ACCEPT_MODULE_CERTS per the resolved q-module-install finding. "
            "These added identifiers are also folded into the "
            "GATEWAY_MODULES_ENABLED whitelist when disable_builtins is set, "
            "so disabling a built-in never quarantines an added module."
        ),
    )
    disable_builtins: list[str] = Field(
        default_factory=list,
        description=(
            "Slugs of built-in IA modules to turn off on this gateway, e.g. "
            "['vision', 'sfc']. Because the gateway's GATEWAY_MODULES_ENABLED "
            "env var is a strict whitelist (anything unlisted is quarantined), "
            "the engine inverts this into 'enable every built-in except these, "
            "plus any added modules'. Empty (default) emits no whitelist and "
            "leaves all built-ins on - the historical behavior. Slugs are "
            "validated against builtin_modules.yaml; an unknown slug raises."
        ),
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Free-form environment-variable overrides emitted verbatim into this "
            "gateway's compose ``environment:`` block (issue #66 Phase B). Layered "
            "after the shared x-ignition-environment anchor, so a key here wins "
            "over the anchor default. The wizard/composer 'env' action prompts "
            "KEY=VALUE pairs; keys are validated as POSIX-shell-safe identifiers. "
            "Empty (default) emits nothing, keeping the historical env block."
        ),
    )
    redundancy: RedundancyConfig | None = Field(
        default=None,
        description=(
            "Redundancy descriptor when this gateway is half of a master/backup "
            "pair. None (default) is a standalone, non-redundant gateway. The "
            "resolver expands a single master-marked gateway into the pair; the "
            "compose engine wires the backup's Gateway Network link to the master."
        ),
    )
    services: list[ServiceAttachment] = Field(
        default_factory=list,
        description=(
            "Registry attachments: which ServiceInstances this gateway uses and "
            "the role it plays. Empty for a legacy input config; resolve() lowers "
            "the stack-wide 'database'/'services' fields into per-gateway consumer "
            "attachments here, so a resolved config carries all edges explicitly."
        ),
    )
    gan_outgoing: list[str] = Field(
        default_factory=list,
        description=(
            "Service names of peer gateways this one opens an outgoing Gateway "
            "Network connection to. Multi-gateway architectures set this to auto-form "
            "the GAN with no UI approval (scale-out: each frontend -> backend; "
            "hub-and-spoke: each spoke -> hub). The compose engine renders one "
            "GATEWAY_NETWORK_<i>_HOST/PORT/ENABLESSL trio per entry on the plain, "
            "non-SSL port 8088, and opens an Unrestricted incoming policy on every "
            "GAN participant so the plain link auto-approves - the same proven "
            "pattern the redundancy link rides. Plain transport is a demo-only "
            "default; cross-host deployments should switch to SSL + approved certs."
        ),
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError("gateway name must start with a lowercase letter and contain only lowercase letters, digits, hyphens, or underscores")
        return v

    @field_validator("ignition_edition")
    @classmethod
    def _validate_edition(cls, v: str) -> str:
        if v not in {"standard", "edge"}:
            raise ValueError("ignition_edition must be 'standard' or 'edge'")
        return v

    @field_validator("env")
    @classmethod
    def _validate_env(cls, v: dict[str, str]) -> dict[str, str]:
        return _validate_env_keys(v)

    @field_validator("disable_builtins")
    @classmethod
    def _validate_disable_builtins(cls, v: list[str]) -> list[str]:
        # Reject unknown slugs loudly: a typo here would otherwise be a silent
        # no-op (the slug isn't in the catalog, so nothing gets disabled),
        # which is exactly the surprise the whitelist inversion must avoid.
        # Imported locally to keep the config schema free of a load-time catalog
        # dependency (and dodge any import-order coupling).
        from ignition_stack.catalog.builtins import validate_disable_slugs

        validate_disable_slugs(v)
        return v

    @property
    def env_prefix(self) -> str:
        """Uppercase prefix for this gateway's per-gateway env-var keys.

        Used by the compose engine when there are 2+ gateways to scope each
        gateway's HTTP port and other per-instance settings in ``.env``.
        For the single-gateway Phase-2 default (name == "gateway") the
        prefix collapses to just "GATEWAY" so the walking skeleton's .env
        keys stay unchanged.
        """
        if self.name == "gateway":
            return "GATEWAY"
        scrubbed = self.name.upper().replace("-", "_")
        if scrubbed.startswith("GATEWAY_"):
            return scrubbed
        return f"GATEWAY_{scrubbed}"


class DatabaseConfig(BaseModel):
    """A single database service.

    Phase 5 widens ``kind`` to the four catalog databases. ``image`` is filled
    from the kind's default when left blank so the resolver can auto-add a
    database (DatabaseConfig(kind="postgres")) without knowing the tag.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="db")
    kind: str = Field(default="postgres")
    image: str = Field(default="", description="image:tag; filled from the kind default if blank.")
    user: str = Field(default="ignition")
    password: str = Field(default="ignition")
    extra_databases: list[str] = Field(
        default_factory=list,
        description=(
            "Additional logical databases to create on first init beyond the "
            "default one named after the user. The resolver appends 'keycloak' "
            "here when Keycloak is selected against this database."
        ),
    )

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        if v not in _DB_DEFAULT_IMAGE:
            supported = ", ".join(sorted(_DB_DEFAULT_IMAGE))
            raise ValueError(f"unsupported database kind '{v}'; supported: {supported}")
        return v

    @model_validator(mode="after")
    def _fill_default_image(self) -> DatabaseConfig:
        if not self.image:
            self.image = _DB_DEFAULT_IMAGE[self.kind]
        return self

    @property
    def image_env(self) -> str:
        """The ``.env`` key the database fragment reads for its image."""
        return _DB_IMAGE_ENV[self.kind]


class ReverseProxyConfig(BaseModel):
    """Reverse-proxy routing for the gateways.

    Default behavior (``ProjectConfig.reverse_proxy is None``) emits a plain
    host-port mapping on each gateway - the gateway is reachable at
    ``http://localhost:<port>`` and no proxy is involved.

    Setting this routes every gateway through a Traefik reverse proxy instead:
    the gateway services join the proxy's external Docker ``network`` and carry
    the Traefik labels (``traefik.enable``, a project-scoped router ``Host`` rule
    on ``.localtest.me``, and the ``loadbalancer.server.port=8088`` that points
    Traefik at the gateway web port), and the host-port mapping is dropped (the
    proxy is the front door). Two modes:

    - ``mode="external"`` - join a proxy the user already runs (the default
      ``ia-eknorr/traefik-reverse-proxy`` creates a network named ``proxy``).
      Nothing is scaffolded; the stack only attaches to ``network``.
    - ``mode="scaffold"`` - lay down the ``ia-eknorr/traefik-reverse-proxy``
      README at ``path`` (the CLI never clones it silently) AND wire the stack to
      the ``network`` that scaffold will create. ``path`` is only meaningful here.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["traefik"] = Field(
        default="traefik",
        description="Reverse-proxy flavor. Only Traefik is supported today.",
    )
    mode: Literal["external", "scaffold"] = Field(
        default="external",
        description=(
            "'external' joins a proxy the user already runs; 'scaffold' also lays "
            "down the ia-eknorr/traefik-reverse-proxy README at 'path' and wires "
            "the stack to the network that scaffold creates."
        ),
    )
    network: str = Field(
        default="proxy",
        description=(
            "External Docker network the proxy routes on. 'proxy' is the default "
            "ia-eknorr/traefik-reverse-proxy creates; the gateways join it as an "
            "'external: true' network so Traefik can reach them."
        ),
    )
    path: str = Field(
        default="reverse-proxy",
        description=(
            "Relative directory under the project root that holds the proxy "
            "README + install instructions (e.g. 'reverse-proxy', "
            "'infra/proxy'). Only used in 'scaffold' mode. Must be a non-empty "
            "relative POSIX path."
        ),
    )

    @field_validator("network")
    @classmethod
    def _validate_network(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("reverse-proxy network must not be empty")
        # Docker network names start alphanumeric, then allow [a-zA-Z0-9_.-].
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$", stripped):
            raise ValueError("reverse-proxy network must be a valid Docker network name (start alphanumeric; letters, digits, '_', '.', '-')")
        return stripped

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("reverse-proxy path must not be empty")
        if stripped.startswith("/") or "\\" in stripped:
            raise ValueError("reverse-proxy path must be a relative POSIX path (no leading '/' and no backslashes)")
        # Normalize "./foo" -> "foo" so the writer can join cleanly.
        return stripped.removeprefix("./")


class ProjectConfig(BaseModel):
    """Resolved configuration for a single generated project."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Project name; used for compose project and gateway naming.")
    ignition_image: str = Field(default="inductiveautomation/ignition:8.3.6")
    timezone: str = Field(default="UTC")
    admin_username: str = Field(default="admin")
    admin_password: str = Field(default="password")

    gateways: list[GatewayConfig] = Field(
        default_factory=lambda: [GatewayConfig()],
        min_length=1,
        description="One or more Ignition gateways. Default is a single standard gateway.",
    )
    database: DatabaseConfig | None = Field(
        default_factory=DatabaseConfig,
        description="Database service. Set to None for a gateway-only stack.",
    )
    services: list[str] = Field(
        default_factory=list,
        description=(
            "Non-database catalog services to include (e.g. 'keycloak', "
            "'hivemq', 'opcua-sim'). Slugs are validated against the service "
            "catalog by the resolver, which also auto-adds implicit "
            "dependencies (Keycloak -> a SQL database)."
        ),
    )
    service_instances: list[ServiceInstance] = Field(
        default_factory=list,
        description=(
            "The stack-level service registry. Empty on a freshly authored "
            "legacy config; resolve() lowers 'database'/'services' into it and "
            "clears those shims, making the registry the single source of truth "
            "the compose engine and writer render from."
        ),
    )
    network_split: bool = Field(
        default=False,
        description=(
            "When False (default), all services share a single bridge network. "
            "When True, Ignition + reverse-proxy services land on 'frontend' and "
            "DB + broker services land on 'backend' (per 02-design.md)."
        ),
    )
    reverse_proxy: ReverseProxyConfig | None = Field(
        default=None,
        description=(
            "Reverse-proxy routing. None (default) emits plain host-port "
            "mappings. Set to route every gateway through a Traefik proxy: the "
            "gateways join the proxy's external network and carry Traefik labels "
            "instead of publishing a host port."
        ),
    )
    mcp_dropin: bool = Field(
        default=False,
        description=("True when the project should scaffold modules/dropin/ for the EA-gated MCP module. Set when a config carries the MCP drop-in."),
    )
    architecture: str | None = Field(
        default=None,
        description=(
            "Slug of the system architecture that produced this config "
            "('basic', 'scale-out', 'hub-and-spoke'). "
            "Informational - the compose engine reads from the resolved "
            "fields, not this slug - but lets generated files (header "
            "comment, lifecycle records) name the architecture they came from."
        ),
    )

    # Phase 2 compatibility shims: these fields used to live on ProjectConfig
    # itself. Resolving them through gateways[0] / database keeps the CLI's
    # existing single-gateway output identical to Phase 2.
    @property
    def gateway_http_port(self) -> int:
        return self.gateways[0].http_port

    def database_instance(self) -> ServiceInstance | None:
        """The *primary* database instance in the registry, or None.

        Phase 2 allows multiple database instances (of distinct kinds); the
        primary is the first one in registry (input) order. The shared
        ``DB_USER`` / ``DB_PASSWORD`` / ``DB_HOST`` env keys come from it, and a
        single-database stack (the common case) has exactly one, so this stays
        the unambiguous "the database" for callers that predate multi-DB.
        """
        return next((inst for inst in self.service_instances if inst.is_database), None)

    def database_instances(self) -> list[ServiceInstance]:
        """All database instances in registry (input) order.

        Phase 2: the engine renders one fragment per database here, and each
        emits its own per-kind ``<KIND>_IMAGE`` env key. The first entry is the
        primary (see :meth:`database_instance`).
        """
        return [inst for inst in self.service_instances if inst.is_database]

    def non_database_instances(self) -> list[ServiceInstance]:
        """Registry instances that are not databases, in registry (input) order."""
        return [inst for inst in self.service_instances if not inst.is_database]

    @property
    def db_user(self) -> str:
        # Prefer the legacy field pre-lowering; fall back to the registry's sole
        # database instance post-lowering; else the historical empty default.
        if self.database is not None:
            return self.database.user
        inst = self.database_instance()
        return inst.user if inst is not None else ""

    @property
    def db_password(self) -> str:
        if self.database is not None:
            return self.database.password
        inst = self.database_instance()
        return inst.password if inst is not None else ""

    @property
    def postgres_image(self) -> str:
        if self.database is not None:
            return self.database.image
        inst = self.database_instance()
        return inst.image if inst is not None else ""

    @property
    def is_multi_gateway(self) -> bool:
        return len(self.gateways) > 1

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError("name must start with a lowercase letter and contain only lowercase letters, digits, hyphens, or underscores")
        return v

    @model_validator(mode="after")
    def _unique_gateway_names(self) -> ProjectConfig:
        names = [g.name for g in self.gateways]
        if len(set(names)) != len(names):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError(f"gateway names must be unique; duplicates: {dupes}")
        return self

    @model_validator(mode="after")
    def _unique_services(self) -> ProjectConfig:
        if len(set(self.services)) != len(self.services):
            dupes = sorted({s for s in self.services if self.services.count(s) > 1})
            raise ValueError(f"services must be unique; duplicates: {dupes}")
        return self

    @model_validator(mode="after")
    def _unique_instance_ids(self) -> ProjectConfig:
        ids = [inst.id for inst in self.service_instances]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"service instance ids must be unique; duplicates: {dupes}")
        return self

    @model_validator(mode="after")
    def _attachments_reference_instances(self) -> ProjectConfig:
        """Every gateway attachment must name a declared instance.

        Only enforced once the registry is populated: a legacy input config
        carries no ``service_instances`` (the resolver creates both the
        instances and the attachments together when it lowers the legacy
        fields), so a hand-authored config that declares attachments must also
        declare the instances they reference.
        """
        if not self.service_instances:
            return self
        ids = {inst.id for inst in self.service_instances}
        for gw in self.gateways:
            for att in gw.services:
                if att.instance not in ids:
                    raise ValueError(f"gateway '{gw.name}' attaches to unknown service instance '{att.instance}'; declared instances: {sorted(ids)}")
        return self

    @model_validator(mode="after")
    def _edge_gateways_hold_no_never_on_edge_service(self) -> ProjectConfig:
        """An Edge gateway must not attach to a ``placement.never_on_edge`` service.

        This is the issue #43 fix made declarative: Edge is a leaf edition that
        must never hold a database connection, so a hand-authored attachment from
        an ``ignition_edition == "edge"`` gateway to any instance whose manifest
        declares ``never_on_edge`` (every database) is rejected here. The
        resolver's legacy lowering never *produces* such an attachment (it skips
        edge gateways when fanning a database out), so this guards declarative /
        ``-f`` configs. Only meaningful once the registry is populated; a legacy
        input config carries no attachments yet.
        """
        if not self.service_instances:
            return self
        from ignition_stack.services.loader import load_all_services

        catalog = load_all_services()
        by_id = {inst.id: inst for inst in self.service_instances}
        for gw in self.gateways:
            if gw.ignition_edition != "edge":
                continue
            for att in gw.services:
                inst = by_id.get(att.instance)
                if inst is None:
                    continue
                manifest = catalog.get(inst.service)
                if manifest is not None and manifest.placement.never_on_edge:
                    raise ValueError(
                        f"edge gateway '{gw.name}' attaches to instance "
                        f"'{inst.id}' ({inst.service}), which must not run on an "
                        "Edge gateway; Edge gateways are leaf nodes and must not "
                        "hold database connections"
                    )
        return self

    @model_validator(mode="after")
    def _redundant_pair_shares_edition(self) -> ProjectConfig:
        """Edge redundancy is Edge-to-Edge only; reject a mixed-edition pair.

        Checked from the backup side: a backup names its master via
        ``redundancy.peer``, and a backup only exists once the pair is fully
        formed (post-resolve, or in a hand-authored ``-f`` file), so there is
        no construction-time false positive against a master whose backup the
        resolver has not appended yet. The master+backup must share an edition
        because an Edge gateway can only fail over to another Edge gateway.
        """
        by_name = {gw.name: gw for gw in self.gateways}
        for gw in self.gateways:
            if gw.redundancy is None or gw.redundancy.mode != "backup":
                continue
            master = by_name.get(gw.redundancy.peer)
            if master is None or master.ignition_edition == gw.ignition_edition:
                continue
            raise ValueError(
                f"redundant pair '{master.name}' ({master.ignition_edition}) / "
                f"'{gw.name}' ({gw.ignition_edition}) mixes editions; Ignition "
                "redundancy is Edge-to-Edge only, so both nodes must share an edition"
            )
        return self

    @model_validator(mode="after")
    def _gan_aggregation_target_is_standard(self) -> ProjectConfig:
        """A Gateway Network aggregation link must terminate on a standard gateway.

        Edge is a leaf edition: gateways aggregate *into* a full (standard)
        gateway, never into an Edge one. So a ``gan_outgoing`` link may be
        ``edge -> standard`` or ``standard -> standard``, but it may never target
        an Edge node - this rejects both ``edge -> edge`` and ``standard -> edge``
        (the latter is what ``scaleout --edge-role backend`` would produce).
        Redundancy is unaffected: its Edge-to-Edge pair link rides
        ``redundancy.peer``, not ``gan_outgoing``, and stays valid.
        """
        by_name = {gw.name: gw for gw in self.gateways}
        for gw in self.gateways:
            for peer in gw.gan_outgoing:
                target = by_name.get(peer)
                if target is None or target.ignition_edition != "edge":
                    continue
                raise ValueError(
                    f"gateway '{gw.name}' opens a Gateway Network link to "
                    f"'{target.name}', which runs the Edge edition; aggregate into "
                    "a standard gateway instead (Edge is a leaf edition). "
                    "Edge-to-Edge is supported only for redundancy pairs."
                )
        return self
