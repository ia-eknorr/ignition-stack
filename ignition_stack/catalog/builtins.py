"""Load and query the built-in IA module catalog (``builtin_modules.yaml``).

The third-party catalog (``modules.yaml`` + ``catalog/schema.py``) covers
modules the CLI *adds*. This module covers the modules that *already ship*
inside the gateway image, which the engine needs in order to translate a
gateway's ``disable_builtins`` slugs into a ``GATEWAY_MODULES_ENABLED``
whitelist.

The whitelist is strict: anything not listed is quarantined at boot. So
"disable Vision" is expressed as "enable every built-in except Vision",
which requires the *complete* built-in set - hence a pinned data file plus a
smoke guard test that re-derives it from the live image.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class BuiltinCatalogLoadError(Exception):
    """Raised when builtin_modules.yaml cannot be read or fails validation."""


DEFAULT_BUILTIN_CATALOG_NAME = "builtin_modules.yaml"


class BuiltinModule(BaseModel):
    """One built-in IA module that ships inside the gateway image."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: Annotated[
        str,
        Field(
            min_length=1,
            pattern=r"^[a-z0-9][a-z0-9-]*$",
            description="Friendly kebab name a user puts in `disable_builtins`.",
        ),
    ]
    identifier: Annotated[
        str,
        Field(
            min_length=1,
            pattern=r"^[a-z0-9.-]+$",
            description="Fully-qualified module id, used verbatim in GATEWAY_MODULES_ENABLED.",
        ),
    ]
    name: Annotated[str, Field(min_length=1, description="Gateway display name (wizard label).")]


class BuiltinCatalog(BaseModel):
    """Top-level shape of builtin_modules.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Annotated[int, Field(ge=1)]
    ignition_version: Annotated[
        str,
        Field(min_length=1, description="Image tag this built-in set was captured from."),
    ]
    modules: Annotated[list[BuiltinModule], Field(min_length=1)]

    @field_validator("modules")
    @classmethod
    def _slugs_unique(cls, modules: list[BuiltinModule]) -> list[BuiltinModule]:
        slugs = [m.slug for m in modules]
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        if dupes:
            raise ValueError(f"duplicate built-in slugs: {', '.join(dupes)}")
        return modules

    @property
    def slugs(self) -> set[str]:
        """Every known built-in slug."""
        return {m.slug for m in self.modules}

    def identifiers_excluding(self, disabled_slugs: list[str]) -> list[str]:
        """FQ identifiers of every built-in whose slug is not in ``disabled_slugs``.

        Order follows the catalog (already alphabetical by slug) so generated
        whitelists are deterministic and golden-stable.
        """
        disabled = set(disabled_slugs)
        return [m.identifier for m in self.modules if m.slug not in disabled]


def load_builtin_catalog(path: Path | None = None) -> BuiltinCatalog:
    """Load and validate the built-in catalog.

    With ``path=None`` the catalog shipped with the installed package is used;
    a path overrides it (test fixtures). Mirrors ``catalog.loader.load_catalog``.
    """
    yaml_text = _read_yaml_text(path)
    name = DEFAULT_BUILTIN_CATALOG_NAME
    try:
        raw = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise BuiltinCatalogLoadError(f"{name} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise BuiltinCatalogLoadError(f"{name} top-level must be a mapping.")

    try:
        return BuiltinCatalog.model_validate(raw)
    except ValidationError as exc:
        raise BuiltinCatalogLoadError(
            f"{DEFAULT_BUILTIN_CATALOG_NAME} failed schema validation:\n{exc}"
        ) from exc


@lru_cache(maxsize=1)
def default_builtin_catalog() -> BuiltinCatalog:
    """The built-in catalog shipped with the package, loaded once and cached.

    The data file is immutable package data, so both config validation and the
    compose engine can share a single memoized read rather than re-parsing YAML
    on every gateway.
    """
    return load_builtin_catalog()


def builtin_slugs() -> frozenset[str]:
    """Slugs of the shipped built-in catalog, for cheap ``disable_builtins`` validation."""
    return frozenset(default_builtin_catalog().slugs)


def validate_disable_slugs(slugs: list[str]) -> None:
    """Raise ``ValueError`` if any slug is not a known built-in.

    Shared by ``GatewayConfig`` field validation (construction-time) and
    ``profiles.apply_disable_builtins`` (post-construction mutation, which
    pydantic does not re-validate), so the wizard/CLI path is guarded too. A
    typo would otherwise be a silent no-op - the slug just isn't disabled.
    """
    known = builtin_slugs()
    unknown = [s for s in slugs if s not in known]
    if unknown:
        raise ValueError(
            f"unknown built-in module slug(s): {', '.join(unknown)}. "
            f"Valid slugs are: {', '.join(sorted(known))}"
        )


def _read_yaml_text(path: Path | None) -> str:
    if path is not None:
        if not path.is_file():
            raise BuiltinCatalogLoadError(f"Built-in catalog not found at {path}.")
        return path.read_text(encoding="utf-8")

    # Installed wheels: force-included as ignition_stack/builtin_modules.yaml.
    # Editable dev installs: it lives at the repo root next to pyproject.toml.
    try:
        bundled = resources.files("ignition_stack").joinpath(DEFAULT_BUILTIN_CATALOG_NAME)
        if bundled.is_file():
            return bundled.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, ModuleNotFoundError):
        pass

    repo_root = Path(__file__).resolve().parents[2]
    dev_path = repo_root / DEFAULT_BUILTIN_CATALOG_NAME
    if not dev_path.is_file():
        raise BuiltinCatalogLoadError(
            f"{DEFAULT_BUILTIN_CATALOG_NAME} not found in package data or at {dev_path}."
        )
    return dev_path.read_text(encoding="utf-8")
