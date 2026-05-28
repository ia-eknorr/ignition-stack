"""Load service manifests from the bundled ``templates/services/`` catalog."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable

import yaml
from pydantic import ValidationError

from ignition_stack.services.manifest import ServiceManifest

_SERVICES_PACKAGE = "ignition_stack.templates"
_SERVICES_SUBDIR = "services"
_MANIFEST_NAME = "manifest.yaml"


class ServiceLoadError(Exception):
    """Raised when a service manifest is missing or fails schema validation."""


def services_root() -> Traversable:
    """The ``templates/services/`` directory inside the installed package."""
    return resources.files(_SERVICES_PACKAGE) / _SERVICES_SUBDIR


def service_dir(name: str) -> Traversable:
    """The ``templates/services/<name>/`` directory for one service."""
    return services_root() / name


def load_service(name: str) -> ServiceManifest:
    """Load and validate one service's manifest by catalog slug."""
    manifest_file = service_dir(name) / _MANIFEST_NAME
    if not manifest_file.is_file():
        raise ServiceLoadError(f"no manifest for service '{name}' (looked for {manifest_file}).")
    try:
        raw = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ServiceLoadError(f"service '{name}' manifest is not valid YAML: {exc}") from exc
    try:
        manifest = ServiceManifest.model_validate(raw)
    except ValidationError as exc:
        raise ServiceLoadError(f"service '{name}' manifest failed validation:\n{exc}") from exc
    if manifest.name != name:
        raise ServiceLoadError(
            f"service '{name}' manifest declares name '{manifest.name}'; they must match."
        )
    return manifest


@lru_cache(maxsize=1)
def load_all_services() -> dict[str, ServiceManifest]:
    """Load every service manifest, keyed by slug.

    Cached because the catalog is read-only package data: a single process
    never sees it change. Tests that need a fresh read can call
    ``load_all_services.cache_clear()``.
    """
    catalog: dict[str, ServiceManifest] = {}
    for entry in services_root().iterdir():
        if not entry.is_dir():
            continue
        if not (entry / _MANIFEST_NAME).is_file():
            continue
        manifest = load_service(entry.name)
        catalog[manifest.name] = manifest
    return catalog
