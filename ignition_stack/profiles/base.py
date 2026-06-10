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

from ignition_stack.config import (
    GatewayConfig,
    ProjectConfig,
    RedundancyConfig,
    ReverseProxyConfig,
    ServiceAttachment,
    ServiceInstance,
)


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

    redundant_role: str | None = None
    """Role (or gateway name) to make redundant, expanding it into a master +
    backup pair. ``None`` (default) builds no redundancy. Must name a singleton
    workhorse role (scaleout 'backend', hub-and-spoke 'hub', standalone
    'gateway'); replicated tiers ('frontend', 'spoke') are rejected."""

    disable_builtins: tuple[str, ...] = ()
    """Built-in module slugs to turn off on every gateway in the stack.

    Empty (default) leaves all built-ins on. Applied uniformly by
    ``build_profile`` - the demo intent is "drop Vision/SFC everywhere", and
    per-gateway disabling stays a declarative-config-only feature. Slugs are
    validated against builtin_modules.yaml by ``GatewayConfig``."""

    iiot: bool = False
    """Overlay an MQTT/Sparkplug IIoT pipeline (a broker + Cirrus Link
    Transmission/Engine) onto the stack. Off by default; ``build_profile`` calls
    :func:`apply_iiot` when this is set, defaulting the broker to ``chariot``."""

    iiot_broker: str | None = None
    """MQTT broker slug the IIoT overlay wires to. ``None`` with ``iiot`` on
    means the confirmed default ``chariot`` (Cirrus Link's own broker). Ignored
    when ``iiot`` is False. Validated against the catalog by :func:`apply_iiot`."""


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


# Roles that are horizontally replicated, not paired. Ignition redundancy is a
# two-node master/backup arrangement, so a tier that can have many members
# (frontends, spokes) is never the redundancy target - those scale out, they
# don't fail over. Marking one redundant is a usage error.
_NON_REDUNDANT_ROLES = frozenset({"frontend", "spoke"})


def _matching_gateways(config: ProjectConfig, redundant_role: str) -> list:
    """Gateways a redundant role names, matched by role or name.

    The single matching rule shared by :func:`mark_redundant` (which errors on a
    bad match) and :func:`can_host_redundant_role` (which only reports), so the
    two can never drift on what "matches" means.
    """
    return [gw for gw in config.gateways if redundant_role in (gw.role, gw.name)]


def can_host_redundant_role(config: ProjectConfig, redundant_role: str) -> bool:
    """True when ``config`` has exactly one gateway that can be paired as master.

    ``switch-profile`` uses this to decide whether redundancy intent recovered
    from the old stack can carry to the target profile, without raising the way
    :func:`mark_redundant` does - a profile-specific role (e.g. standalone's
    ``gateway``) simply may not exist in the destination.
    """
    if redundant_role in _NON_REDUNDANT_ROLES:
        return False
    return len(_matching_gateways(config, redundant_role)) == 1


def mark_redundant(config: ProjectConfig, redundant_role: str | None) -> ProjectConfig:
    """Stamp the gateway named/roled ``redundant_role`` as a redundancy master.

    Returns ``config`` unchanged when ``redundant_role`` is None. The expansion
    into a master+backup pair happens later in
    :func:`ignition_stack.services.resolver.resolve`; this only marks which
    gateway to pair, so the same logic serves every profile and the wizard.

    Raises ``ValueError`` (surfaced by the CLI as a usage error, exit code 2)
    when the role is a replicated frontend/spoke tier, unknown, or ambiguous
    (matches more than one gateway - you can't pair a horizontally-scaled tier).
    """
    if redundant_role is None:
        return config
    if redundant_role in _NON_REDUNDANT_ROLES:
        raise ValueError(
            f"role '{redundant_role}' is horizontally replicated, not paired; "
            "redundancy applies to a single gateway (e.g. a scaleout 'backend' "
            "or a hub-and-spoke 'hub'), never to frontends or spokes"
        )
    matches = _matching_gateways(config, redundant_role)
    if not matches:
        known = ", ".join(sorted({gw.role or gw.name for gw in config.gateways}))
        raise ValueError(f"no gateway matches redundant role '{redundant_role}'; available roles: {known}")
    if len(matches) > 1:
        raise ValueError(f"redundant role '{redundant_role}' matches {len(matches)} gateways; " "redundancy pairs a single gateway, so name a singleton role")
    master = matches[0]
    master.redundancy = RedundancyConfig(mode="master", peer=f"{master.name}-backup")
    return config


def build_profile(slug: str, name: str, options: ProfileOptions) -> ProjectConfig:
    """Materialize a ``ProjectConfig`` for the named profile.

    The profile builds the base topology; ``mark_redundant`` then stamps the
    redundancy master when ``options.redundant_role`` is set, leaving the
    resolver to expand the pair. Keeping the stamp here (not in each profile)
    means one eligibility rule serves every profile and the wizard alike.
    """
    config = get_profile(slug).build(name, options)
    config = mark_redundant(config, options.redundant_role)
    config = apply_disable_builtins(config, options.disable_builtins)
    broker = (options.iiot_broker or _IIOT_DEFAULT_BROKER) if options.iiot else None
    return apply_iiot(config, broker)


def apply_disable_builtins(config: ProjectConfig, disable_builtins: tuple[str, ...]) -> ProjectConfig:
    """Stamp ``disable_builtins`` onto every gateway in ``config``.

    Applied centrally (like :func:`mark_redundant`) so one rule serves every
    profile and the wizard. Uniform across gateways: the demo intent is to drop
    a module everywhere, and a redundant pair must agree on its module set. The
    resolver later copies the list onto any expanded backup node.
    """
    if not disable_builtins:
        return config
    # pydantic does not re-validate on attribute assignment, so validate here -
    # this is the wizard/CLI choke point (the declarative path is checked at
    # construction). Raises ValueError on an unknown slug; the CLI maps that to
    # exit code 2.
    from ignition_stack.catalog.builtins import validate_disable_slugs

    validate_disable_slugs(list(disable_builtins))
    for gw in config.gateways:
        gw.disable_builtins = list(disable_builtins)
    return config


# The confirmed default IIoT broker (Cirrus Link's own Chariot, the most
# official pairing with their Transmission/Engine modules). Used when the
# overlay is requested without an explicit broker slug.
_IIOT_DEFAULT_BROKER = "chariot"

# Gateway roles that run MQTT Transmission (edge-side: publish Sparkplug to the
# broker) versus MQTT Engine (central: subscribe and aggregate). A
# standalone/mcp-n8n shape has neither role, so its single gateway runs both for
# a self-contained demo loop through the broker.
_TRANSMISSION_ROLES = frozenset({"spoke", "frontend"})
_ENGINE_ROLES = frozenset({"hub", "backend"})


def apply_iiot(config: ProjectConfig, broker: str | None) -> ProjectConfig:
    """Overlay an MQTT/Sparkplug IIoT pipeline onto ``config``.

    Returns ``config`` unchanged when ``broker`` is None. Otherwise it adds the
    broker as a stack-level :class:`ServiceInstance` (singleton, enforced by the
    resolver) and wires each gateway by role, using the **module slugs the broker
    manifest's ``wires.mqtt`` block names** (never hardcoded):

    - ``spoke`` / ``frontend`` gateways get an ``mqtt-transmission`` attachment
      plus the Transmission module (they publish Sparkplug to the broker);
    - ``hub`` / ``backend`` gateways get an ``mqtt-engine`` attachment plus the
      Engine module (they subscribe and aggregate);
    - if NO gateway carries any of those roles (standalone / mcp-n8n shapes), the
      single/first gateway gets BOTH attachments + both modules - a self-contained
      demo loop through the broker.

    Brokers are not ``never_on_edge``, so an Edge spoke attaching with role
    ``mqtt-transmission`` is correct and expected. Idempotent: guards on
    ``(instance, role)`` attachment pairs and on module-already-present, so
    ``apply_iiot(apply_iiot(c)) == apply_iiot(c)``. The expansion of a redundancy
    master happens later in :func:`~ignition_stack.services.resolver.resolve`,
    which copies the master's attachments + modules onto the backup, so a
    redundant Engine gateway ends up with the Engine module on both nodes.

    Raises ``ValueError`` (surfaced by the CLI as exit code 2) when the broker
    slug is unknown, is not an ``mqtt-broker``, or carries no ``wires.mqtt`` block.
    """
    if broker is None:
        return config

    from ignition_stack.services.loader import load_all_services

    catalog = load_all_services()
    manifest = catalog.get(broker)
    if manifest is None:
        brokers = ", ".join(sorted(slug for slug, m in catalog.items() if m.kind == "mqtt-broker"))
        raise ValueError(f"unknown iiot broker '{broker}'; known mqtt brokers: {brokers}")
    if manifest.kind != "mqtt-broker":
        raise ValueError(f"iiot broker '{broker}' is a '{manifest.kind}' service, not an mqtt-broker")
    if manifest.wires is None or manifest.wires.mqtt is None:
        raise ValueError(f"mqtt broker '{broker}' declares no wires.mqtt block, so the IIoT overlay cannot find its Transmission/Engine module slugs")
    mqtt = manifest.wires.mqtt

    # Add the broker instance once (id == slug); the resolver's singleton check
    # catches an accidental duplicate broker elsewhere in the registry.
    if not any(inst.id == broker for inst in config.service_instances):
        config.service_instances.append(ServiceInstance(id=broker, service=broker))

    transmission_gws = [gw for gw in config.gateways if gw.role in _TRANSMISSION_ROLES]
    engine_gws = [gw for gw in config.gateways if gw.role in _ENGINE_ROLES]

    if not transmission_gws and not engine_gws:
        # No transmission/engine roles in this topology: run the whole loop on
        # the single/first gateway so the demo is self-contained.
        only = config.gateways[0]
        _wire_iiot_gateway(only, broker, "mqtt-transmission", mqtt.transmission_module)
        _wire_iiot_gateway(only, broker, "mqtt-engine", mqtt.engine_module)
    else:
        for gw in transmission_gws:
            _wire_iiot_gateway(gw, broker, "mqtt-transmission", mqtt.transmission_module)
        for gw in engine_gws:
            _wire_iiot_gateway(gw, broker, "mqtt-engine", mqtt.engine_module)
    return config


def _wire_iiot_gateway(gw: GatewayConfig, instance_id: str, role: str, module: str) -> None:
    """Attach ``gw`` to the broker with ``role`` and add ``module`` to it.

    Role-aware guard (not the phase-1 instance-only ``_attach_all_gateways``): a
    self-loop gateway holds two attachments to the same broker instance, one per
    role, so the guard must key on the ``(instance, role)`` pair. Module presence
    is guarded separately so a second pass adds nothing.
    """
    if not any(att.instance == instance_id and att.role == role for att in gw.services):
        gw.services.append(ServiceAttachment(instance=instance_id, role=role))
    if module not in gw.modules:
        gw.modules.append(module)
