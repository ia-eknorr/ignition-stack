"""Pydantic schema for resolved project configuration.

Phase 2 hardcodes the standalone+Postgres walking skeleton, so this schema
only captures what the CLI actually varies today: the project name, the
gateway HTTP port, the admin password, and the Postgres credentials.

Phases 4-6 extend this with profile, services[], and per-gateway overrides.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# Lowercase letters, digits, hyphen, underscore. Must start with a letter
# because container_name / hostname need to be DNS-safe and Postgres
# database names follow the same shape.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class ProjectConfig(BaseModel):
    """Resolved configuration for a single generated project."""

    name: str = Field(description="Project name; used for compose project, gateway, and DB.")
    ignition_image: str = Field(
        default="inductiveautomation/ignition:8.3.6",
        description="Ignition gateway image tag.",
    )
    postgres_image: str = Field(
        default="postgres:18.1",
        description="Postgres image tag.",
    )
    gateway_http_port: int = Field(
        default=9088,
        ge=1,
        le=65535,
        description="Host port mapped to the gateway's 8088.",
    )
    admin_username: str = Field(default="admin")
    admin_password: str = Field(default="password")
    db_user: str = Field(default="ignition")
    db_password: str = Field(default="ignition")
    timezone: str = Field(default="UTC")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "name must start with a lowercase letter and contain only "
                "lowercase letters, digits, hyphens, or underscores"
            )
        return v
