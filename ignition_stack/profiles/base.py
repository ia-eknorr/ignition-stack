"""Profile contract + options + registry.

A *profile* is the small piece of code that turns the user's high-level
intent ("scaleout", "hub-and-spoke with 3 spokes", "mcp-n8n demo") into a
fully-formed :class:`ProjectConfig`. The compose engine and the dependency
resolver are profile-agnostic; profiles only shape the inputs they take.

Two-stage pipeline:

1. Either the CLI flags or the wizard answers populate a
   :class:`ProfileOptions` and pick a profile slug.
2. ``build_profile(slug, name, options)`` looks up the profile and calls
   its ``build()`` method, returning a ``ProjectConfig`` that
   ``services.resolver.resolve()`` then expands the usual implicit deps on.

Each profile is a small dataclass with three pieces:

- ``slug`` - the wizard/flag value users type.
- ``summary`` - one-line description for the wizard menu + docs.
- ``build`` - pure function ``(name, options) -> ProjectConfig``.

Keeping ``build`` pure (no I/O, no prompts) is what lets the wizard layer
and the CLI flag layer share the same code path and stay testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ignition_stack.config import ProjectConfig, ReverseProxyConfig


@dataclass(frozen=True)
class ProfileOptions:
    """Inputs each profile reads to shape the resolved config.

    Every field has a sensible default so callers only set what they
    actually care about. The wizard fills in many of these from prompts;
    the non-interactive CLI path fills in a subset from flags and leaves
    the rest at their defaults.
    """

    spokes: int = 3
    """Hub-and-spoke spoke count. Ignored by other profiles."""

    frontends: int = 1
    """Scaleout frontend gateway count. Ignored by other profiles.

    1 yields a single gateway named ``frontend``; N>1 yields
    ``frontend-1``..``frontend-N``. A ``backend`` gateway is always added
    on top.
    """

    force: bool = False
    """Bypass the hub-and-spoke red-tier advisory. Ignored elsewhere."""

    network_split: bool | None = None
    """Tri-state override for the frontend/backend network split.

    ``None`` lets each profile apply its own default (scaleout splits,
    hub-and-spoke does not). ``True``/``False`` force the split on or off
    regardless of the profile default.
    """

    edge_role: str | None = None
    """Which gateway role (if any) runs the Edge edition.

    For scaleout this is typically 'frontend'; for hub-and-spoke it can
    be 'spoke' (every spoke runs Edge) or None. The profile is free to
    apply its own default when this is None.
    """

    reverse_proxy: ReverseProxyConfig | None = None
    """Reverse-proxy scaffolding. None = plain host-port mapping."""

    database_kind: str | None = "postgres"
    """SQL database for the stack. None = no database (gateway-only)."""

    services: tuple[str, ...] = ()
    """Additional service catalog slugs the user picked beyond profile defaults."""


class Profile(Protocol):
    """A factory that turns ``ProfileOptions`` into a ``ProjectConfig``."""

    slug: str
    summary: str

    def build(self, name: str, options: ProfileOptions) -> ProjectConfig: ...


# Registry populated by the profile modules at import time. Keep alphabetical
# insertion-order for stable wizard menus + --help listings.
_REGISTRY: dict[str, Profile] = {}


def register(profile: Profile) -> Profile:
    """Register a profile by slug. Returns the profile so module-level uses
    can write ``standalone = register(StandaloneProfile())``.
    """
    if profile.slug in _REGISTRY:
        raise ValueError(f"profile '{profile.slug}' is already registered")
    _REGISTRY[profile.slug] = profile
    return profile


def get_profile(slug: str) -> Profile:
    """Look up a registered profile by slug. Raises ``KeyError`` if unknown."""
    try:
        return _REGISTRY[slug]
    except KeyError as exc:
        known = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"unknown profile '{slug}'; known profiles: {known}") from exc


def list_profiles() -> list[Profile]:
    """All registered profiles in stable insertion order."""
    return list(_REGISTRY.values())


def build_profile(slug: str, name: str, options: ProfileOptions) -> ProjectConfig:
    """Materialize a ``ProjectConfig`` for the named profile."""
    return get_profile(slug).build(name, options)
