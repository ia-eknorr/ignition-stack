"""Pydantic schema for modules.yaml.

The catalog is the single source of truth for which .modl modules and JDBC
drivers the CLI knows how to download, cache-verify, and wire into a
generated stack. One file (`modules.yaml` at the repo root) is read at
both `modules` subcommand time and `init` time.

Two entry kinds: third-party Ignition modules (.modl) and JDBC drivers
(.jar). They are distinct shapes because Phase 1 confirmed the module
accept env vars (`ACCEPT_MODULE_LICENSES`, `ACCEPT_MODULE_CERTS`) take
**fully-qualified module identifiers, not paths** - while the
volume-mount needs the in-container install path.
JDBC drivers have no identifier and no accept-license env vars; they
are simply files in `user-lib/jdbc/`.

A discriminated union (`kind: module | jdbc_driver`) keeps the two
shapes statically separable so the compose generator emits the right
env vars for each.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

# Sentinel for catalog entries whose sha256 has not yet been pinned by a
# maintainer (e.g. fresh release the maintainer is in the middle of
# bumping). `modules validate` rejects this so a half-bumped catalog
# cannot ship.
SHA256_UNPINNED = "UNPINNED"


class _EntryBase(BaseModel):
    """Fields shared by every catalog entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[
        str,
        Field(
            min_length=1,
            pattern=r"^[a-z0-9][a-z0-9-]*$",
            description="Slug used in CLI output and as the cache filename stem.",
        ),
    ]
    vendor: Annotated[str, Field(min_length=1)]
    ignition_versions: Annotated[
        list[str],
        Field(
            min_length=1,
            description=(
                "Exact Ignition versions this entry is verified against "
                "(e.g. ['8.3.6', '8.3.7']). Resolution is exact-match: bump "
                "the list when a new Ignition patch is validated."
            ),
        ),
    ]
    download_url: HttpUrl | None = Field(
        default=None,
        description=("Public download URL. Required unless requires_manual_download is true."),
    )
    sha256: Annotated[
        str,
        Field(
            pattern=rf"^([0-9a-f]{{64}}|{SHA256_UNPINNED})$",
            description=(
                f"Lowercase hex sha256 of the artifact, or '{SHA256_UNPINNED}' "
                "while a maintainer is mid-bump (rejected by `modules validate`)."
            ),
        ),
    ]
    install_path: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Fully-qualified in-container destination path. The compose "
                "layer mounts/copies the cached artifact here."
            ),
        ),
    ]
    requires_license_env: str | None = Field(
        default=None,
        description=(
            "Name of an env var the user must set with their license key. "
            "None for community-usable modules and unlicensed drivers."
        ),
    )
    requires_manual_download: bool = Field(
        default=False,
        description=(
            "True when the artifact has no public URL (e.g. EA-gated). "
            "`modules download` skips these unless local_source_path is set "
            "and points at an existing file."
        ),
    )
    local_source_path: str | None = Field(
        default=None,
        description=(
            "Optional maintainer-configured absolute path on the host to a "
            "locally-stored copy. Used only when requires_manual_download is "
            "true; when present and the file exists, `download` copies it "
            "into the cache instead of fetching."
        ),
    )

    def cache_filename(self) -> str:
        """Filename used inside the host-side cache dir."""
        return PurePosixPath(self.install_path).name


class ModuleEntry(_EntryBase):
    """A third-party Ignition `.modl` module."""

    kind: Literal["module"] = "module"
    module_identifier: Annotated[
        str,
        Field(
            min_length=1,
            pattern=r"^[a-z0-9.]+$",
            description=(
                "Fully-qualified module identifier (e.g. "
                "'com.cirruslink.mqtt.engine.gateway'). Used verbatim in "
                "ACCEPT_MODULE_LICENSES and ACCEPT_MODULE_CERTS. NOT a path."
            ),
        ),
    ]


class JdbcDriverEntry(_EntryBase):
    """A JDBC driver `.jar` dropped into `user-lib/jdbc/`."""

    kind: Literal["jdbc_driver"] = "jdbc_driver"


CatalogEntry = Annotated[ModuleEntry | JdbcDriverEntry, Field(discriminator="kind")]


class Catalog(BaseModel):
    """Top-level shape of modules.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Annotated[int, Field(ge=1, description="Schema version; bump on breaking change.")]
    entries: Annotated[list[CatalogEntry], Field(min_length=1)]

    def by_name(self, name: str) -> CatalogEntry:
        """Look up an entry by slug; raises KeyError if absent."""
        for entry in self.entries:
            if entry.name == name:
                return entry
        raise KeyError(name)

    def for_ignition(self, ignition_version: str) -> list[CatalogEntry]:
        """Entries verified against the given exact Ignition version."""
        return [e for e in self.entries if ignition_version in e.ignition_versions]
