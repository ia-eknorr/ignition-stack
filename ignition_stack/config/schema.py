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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Lowercase letters, digits, hyphen, underscore. Must start with a letter
# because container_name / hostname need to be DNS-safe and Postgres
# database names follow the same shape.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


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
            "volume AND enumerates it in GATEWAY_MODULES_ENABLED + "
            "ACCEPT_MODULE_LICENSES + ACCEPT_MODULE_CERTS per the resolved "
            "q-module-install finding."
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

    Phase 4 only ships the Postgres variant; the ``kind`` discriminator is
    a forward-looking marker for Phase 5's MySQL/MariaDB/Mongo branches.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="db")
    kind: str = Field(default="postgres")
    image: str = Field(default="postgres:18.1")
    user: str = Field(default="ignition")
    password: str = Field(default="ignition")

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        if v not in {"postgres"}:
            raise ValueError(f"unsupported database kind '{v}'; Phase 4 ships postgres only")
        return v


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
    network_split: bool = Field(
        default=False,
        description=(
            "When False (default), all services share a single bridge network. "
            "When True, Ignition + reverse-proxy services land on 'frontend' and "
            "DB + broker services land on 'backend' (per 02-design.md)."
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
