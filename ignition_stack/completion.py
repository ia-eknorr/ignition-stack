"""Shell-completion callbacks for dynamic CLI values.

Typer re-invokes the program on every ``<TAB>``, so these callbacks must be
cheap and must never raise - an exception here would surface as noise on the
user's shell line. They read only the in-memory profile registry and the
local bundled ``modules.yaml``; they never touch the network.

Each callback takes the partial value the user has typed and returns the
matching candidates. Returning ``(value, help)`` tuples gives the richer
two-column completion menu that zsh and fish render.
"""

from __future__ import annotations

from ignition_stack.profiles import list_profiles


def complete_profile(incomplete: str) -> list[tuple[str, str]]:
    """Profile slugs (with their one-line summary) matching the typed prefix."""
    return [(p.slug, p.summary) for p in list_profiles() if p.slug.startswith(incomplete)]


# The Ignition Edge role names the profiles recognise. These are the string
# literals the profile builders match ``edge_role`` against (see
# profiles/*.py); duplicated here as the completion vocabulary because there
# is no single registry of role names. 'none' is the sentinel that disables a
# profile's default Edge role.
EDGE_ROLE_VALUES = ("frontend", "backend", "hub", "spoke", "gateway", "standalone", "none")


def complete_edge_role(incomplete: str) -> list[str]:
    """Edge role names matching the typed prefix."""
    return [role for role in EDGE_ROLE_VALUES if role.startswith(incomplete)]


# Reverse-proxy kinds the CLI can scaffold. Mirrors ReverseProxyConfig.kind
# (only Traefik exists today); kept here as the completion vocabulary since
# the Literal lives in the pydantic model, not a runtime registry.
REVERSE_PROXY_VALUES = ("traefik",)


def complete_reverse_proxy(incomplete: str) -> list[str]:
    """Reverse-proxy kind names matching the typed prefix."""
    return [kind for kind in REVERSE_PROXY_VALUES if kind.startswith(incomplete)]


# Roles `init --redundant` can pair. Only the singleton workhorse roles are
# eligible (a scaleout 'backend', a hub-and-spoke 'hub', a standalone
# 'gateway'); replicated 'frontend'/'spoke' tiers are rejected by the profile
# builder, so they are intentionally absent here.
REDUNDANT_ROLE_VALUES = ("backend", "hub", "gateway")


def complete_redundant_role(incomplete: str) -> list[str]:
    """Redundancy-eligible role names matching the typed prefix."""
    return [role for role in REDUNDANT_ROLE_VALUES if role.startswith(incomplete)]


# Serialization formats `init --dry-run --output-format` accepts. Mirrors the
# `Format` literal in config/io.py; kept here as the completion vocabulary.
OUTPUT_FORMAT_VALUES = ("yaml", "json")


def complete_output_format(incomplete: str) -> list[str]:
    """Config dump format names matching the typed prefix."""
    return [fmt for fmt in OUTPUT_FORMAT_VALUES if fmt.startswith(incomplete)]


def complete_module_name(incomplete: str) -> list[str]:
    """Catalog entry slugs from the bundled catalog matching the typed prefix."""
    try:
        from ignition_stack.catalog.loader import load_catalog

        entries = load_catalog(None).entries
    except Exception:
        # Completion runs on every keystroke-with-TAB; a missing or malformed
        # catalog must degrade to "no suggestions", never break the shell line.
        return []
    return [entry.name for entry in entries if entry.name.startswith(incomplete)]


def complete_disable_builtin(incomplete: str) -> list[tuple[str, str]]:
    """Built-in module slugs (with display name) matching the typed prefix.

    Reads the bundled built-in catalog; degrades to no suggestions on any error
    so a TAB never breaks the shell line.
    """
    try:
        from ignition_stack.catalog.builtins import default_builtin_catalog

        modules = default_builtin_catalog().modules
    except Exception:
        return []
    return [(m.slug, m.name) for m in modules if m.slug.startswith(incomplete)]
