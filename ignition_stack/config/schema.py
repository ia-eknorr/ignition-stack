"""Pydantic schema for resolved project configuration.

Phase 4 generalizes the Phase 2 single-gateway shape into a multi-gateway
model with per-gateway overrides and an opt-in network split. The defaults
are tuned so a bare ``ProjectConfig(name="demo")`` still resolves to exactly
the Phase 2 walking skeleton (one standalone gateway + Postgres on a single
bridge network).

Phases 5-6 extend this with the service catalog and profile-class shaping.
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

    mode: Literal["master", "backup"] = Field(
        description="This node's redundancy role: 'master' or 'backup'."
    )
    peer: str = Field(
        description=(
            "Service name of the other node in the pair. The backup points at "
            "the master here (and over the Gateway Network); the master points "
            "at its backup."
        ),
    )
    gan_port: int = Field(
        default=8088,
        ge=1,
        le=65535,
        description=(
            "Gateway Network port the redundancy link rides. 8088 is plain "
            "(non-SSL) and auto-approves; 8060 is SSL and needs a cert approval."
        ),
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
            "Optional role tag used by the network-split logic and profile "
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
    redundancy: RedundancyConfig | None = Field(
        default=None,
        description=(
            "Redundancy descriptor when this gateway is half of a master/backup "
            "pair. None (default) is a standalone, non-redundant gateway. The "
            "resolver expands a single master-marked gateway into the pair; the "
            "compose engine wires the backup's Gateway Network link to the master."
        ),
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "gateway name must start with a lowercase letter and contain only "
                "lowercase letters, digits, hyphens, or underscores"
            )
        return v

    @field_validator("ignition_edition")
    @classmethod
    def _validate_edition(cls, v: str) -> str:
        if v not in {"standard", "edge"}:
            raise ValueError("ignition_edition must be 'standard' or 'edge'")
        return v

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
    """Optional reverse-proxy scaffolding.

    Default behavior (``ProjectConfig.reverse_proxy is None``) emits a plain
    host-port mapping on each gateway and assumes the user already runs Traefik,
    nginx, or another proxy somewhere - or doesn't need one at all. Setting
    this to a :class:`ReverseProxyConfig` lays down the ``ia-eknorr/traefik-
    reverse-proxy`` README at ``path`` and adds a POST-SETUP entry pointing at
    that repo. The CLI never silently bundles a proxy.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["traefik"] = Field(
        default="traefik",
        description="Reverse-proxy flavor. Only Traefik is supported today.",
    )
    path: str = Field(
        default="reverse-proxy",
        description=(
            "Relative directory under the project root that holds the proxy "
            "README + install instructions (e.g. 'reverse-proxy', "
            "'infra/proxy'). Must be a non-empty relative POSIX path."
        ),
    )

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("reverse-proxy path must not be empty")
        if stripped.startswith("/") or "\\" in stripped:
            raise ValueError(
                "reverse-proxy path must be a relative POSIX path "
                "(no leading '/' and no backslashes)"
            )
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
            "Reverse-proxy scaffolding. None (default) emits plain host-port "
            "mappings. Set when the user accepts the wizard's offer to install "
            "ia-eknorr/traefik-reverse-proxy at a chosen path."
        ),
    )
    mcp_dropin: bool = Field(
        default=False,
        description=(
            "True when the project should scaffold modules/dropin/ for the "
            "EA-gated MCP module. Set by the mcp-n8n profile."
        ),
    )
    profile: str | None = Field(
        default=None,
        description=(
            "Slug of the architecture profile that produced this config "
            "('standalone', 'scaleout', 'hub-and-spoke', 'mcp-n8n'). "
            "Informational - the compose engine reads from the resolved "
            "fields, not this slug - but lets generated files (header "
            "comment, lifecycle records) name the profile they came from."
        ),
    )

    # Phase 2 compatibility shims: these fields used to live on ProjectConfig
    # itself. Resolving them through gateways[0] / database keeps the CLI's
    # existing single-gateway output identical to Phase 2.
    @property
    def gateway_http_port(self) -> int:
        return self.gateways[0].http_port

    @property
    def db_user(self) -> str:
        return self.database.user if self.database else ""

    @property
    def db_password(self) -> str:
        return self.database.password if self.database else ""

    @property
    def postgres_image(self) -> str:
        return self.database.image if self.database else ""

    @property
    def is_multi_gateway(self) -> bool:
        return len(self.gateways) > 1

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "name must start with a lowercase letter and contain only "
                "lowercase letters, digits, hyphens, or underscores"
            )
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
