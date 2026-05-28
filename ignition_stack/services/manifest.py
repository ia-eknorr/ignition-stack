"""Pydantic schema for a service ``manifest.yaml``.

Each catalog service ships one manifest describing what the compose engine and
the dependency resolver need to know about it without reading its Jinja2
fragment:

- **identity** - the catalog slug (also the compose service key and the
  ``templates/services/<name>/`` directory name) and the human kind.
- **image** - the default ``image:tag`` plus the ``.env`` key that overrides it.
- **capabilities** - ``provides`` / ``requires`` capability tags the resolver
  uses to wire implicit dependencies (Keycloak ``requires: [sql-database]``).
- **env** - the preset ``.env`` keys this service contributes, with default
  values, so the ``.env`` writer is data-driven instead of hardcoding each
  service's credentials.
- **seeding** - whether the service ships a ``seed/gateway-resources/`` tree
  that the writer overlays onto every gateway's file-config resources (per the
  Phase-1 seedability matrix), and which connections it cannot pre-seed and
  must defer to ``POST-SETUP.md`` (Phase 7).

The fragment itself references ``${ENV_KEY}`` values directly, so the manifest
never duplicates the compose body - it only carries the metadata the engine
cannot infer from YAML text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# A service either lives on the user-facing ``frontend`` network or the private
# ``backend`` network when ``network_split`` is on. Databases, brokers, IDPs,
# simulators, and streaming brokers are backend; the only frontend service is
# n8n (it exposes a UI users hit directly). Gateways always join both.
NetworkTier = Literal["frontend", "backend"]

ServiceKind = Literal[
    "database",
    "mqtt-broker",
    "idp",
    "simulator",
    "streaming",
    "automation",
]


class PostSetupItem(BaseModel):
    """A connection this service cannot fully pre-seed from files.

    Phase 7's ``POST-SETUP.md`` generator turns each of these into a manual
    step. The ``connection`` matches a row in the Phase-1 seedability matrix;
    ``reason`` quotes why the file-seeding path stops short (usually a secret
    or a handshake the matrix marked ``no`` / ``partial``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    connection: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ServiceManifest(BaseModel):
    """Declarative metadata for one catalog service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        min_length=1,
        pattern=r"^[a-z][a-z0-9-]*$",
        description="Catalog slug; also the compose service key and the directory name.",
    )
    kind: ServiceKind
    summary: str = Field(default="", description="One-line description for docs and headers.")
    image: str = Field(min_length=1, description="Default image:tag (overridable via image_env).")
    image_env: str = Field(
        min_length=1,
        pattern=r"^[A-Z][A-Z0-9_]*$",
        description="The .env key the compose fragment reads for this service's image.",
    )
    network: NetworkTier = Field(
        default="backend",
        description="Which network the service joins when network_split is on.",
    )
    provides: list[str] = Field(
        default_factory=list,
        description="Capability tags this service satisfies (e.g. 'sql-database').",
    )
    requires: list[str] = Field(
        default_factory=list,
        description="Capability tags this service needs; the resolver auto-adds a provider.",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Preset .env keys -> default values this service contributes.",
    )
    seeds_gateway_resources: bool = Field(
        default=False,
        description=(
            "True when the service ships seed/gateway-resources/ that the writer "
            "overlays onto every gateway's file-config resource tree."
        ),
    )
    post_setup: list[PostSetupItem] = Field(
        default_factory=list,
        description="Connections this service defers to POST-SETUP.md (Phase 7).",
    )
