"""Carry a resolved service registry across a ``switch-profile`` reshape.

``switch-profile`` rebuilds a stack under a new architecture profile. The
profile only knows how to lay down topology + the inputs ``ProfileOptions``
carries; it cannot know about the *richer* registry shapes a hand-authored or
Custom-wizard config can hold:

- a custom instance ``id`` that differs from its service slug;
- per-instance ``image`` / ``env`` / ``user`` / ``password`` overrides;
- a second database (distinct kind) beyond the profile's default;
- partial / role-specific attachment sets (an instance attached to only some
  gateways, not fanned to all).

Anything *not* expressible through ``ProfileOptions.services`` /
``database_kind`` rides here instead: :func:`carry_registry` re-grafts those
instances onto the freshly built config and re-maps their attachments by role
into the new topology. Attachments whose source gateway has no counterpart in
the target topology - or that would violate a new-topology invariant - are
**dropped with a printed advisory** rather than failing the reshape.

The IIoT overlay is *not* carried here: ``switch-profile`` recovers IIoT intent
into ``ProfileOptions.iiot`` / ``iiot_broker`` so ``apply_iiot`` re-wires the
broker + Transmission/Engine roles natively in the new topology. The broker
instance and its ``mqtt-transmission`` / ``mqtt-engine`` attachments are
therefore skipped here to avoid double-wiring.
"""

from __future__ import annotations

from typing import Protocol

from ignition_stack.config.schema import (
    _DB_DEFAULT_IMAGE,
    GatewayConfig,
    ProjectConfig,
    ServiceAttachment,
    ServiceInstance,
)
from ignition_stack.services.loader import load_all_services
from ignition_stack.services.manifest import ServiceManifest

# Roles that aggregate centrally (full gateways) versus roles that sit on the
# edge side and publish/scale out. A role-less gateway (standalone / mcp-n8n)
# is central - it is the one full gateway in its topology. The two-way name
# preference (hub<->backend, spoke<->frontend) is tried before the class match.
_CENTRAL_ROLES = frozenset({"hub", "backend"})
_EDGE_SIDE_ROLES = frozenset({"spoke", "frontend"})
_ROLE_COUNTERPART = {
    "hub": "backend",
    "backend": "hub",
    "spoke": "frontend",
    "frontend": "spoke",
}

# Attachment roles the IIoT overlay owns; carried via ProfileOptions.iiot, never
# re-grafted here (apply_iiot re-wires them in the new topology).
_IIOT_ROLES = frozenset({"mqtt-transmission", "mqtt-engine"})


class _Printer(Protocol):
    def print(self, *args: object, **kwargs: object) -> None: ...


def is_default_representable(
    instance: ServiceInstance,
    config: ProjectConfig,
    catalog: dict[str, ServiceManifest],
) -> bool:
    """True when ``instance`` is faithfully carried by ``ProfileOptions`` alone.

    The "today's behavior" carry: an instance whose ``id`` equals its service
    slug, carries no per-instance override, and is attached as a plain
    ``consumer`` on every *eligible* gateway (every gateway, minus the Edge
    gateways a ``never_on_edge`` service skips). Such an instance is fully
    reproduced by listing its slug in ``ProfileOptions.services`` and letting
    the resolver re-lower it, so it does not need re-grafting.

    Databases are never representable through ``services`` (they ride
    ``database_kind`` for the primary, and a second database is always
    re-grafted), so this returns False for them.
    """
    if instance.is_database:
        return False
    if instance.id != instance.service:
        return False
    manifest = catalog[instance.service]
    # An image override is anything other than the manifest default (the default
    # is filled onto every instance at construction, so a populated image that
    # equals the manifest default is not an override).
    if instance.env or (instance.image and instance.image != manifest.image):
        return False
    never_on_edge = manifest.placement.never_on_edge
    for gw in config.gateways:
        attached = [att for att in gw.services if att.instance == instance.id]
        eligible = not (never_on_edge and gw.ignition_edition == "edge")
        if not eligible:
            if attached:
                return False  # attached somewhere it should not be -> not plain
            continue
        if len(attached) != 1 or attached[0].role != "consumer":
            return False
    return True


def database_carried_by_kind(config: ProjectConfig, catalog: dict[str, ServiceManifest]) -> ServiceInstance | None:
    """The primary database iff it rides ``ProfileOptions.database_kind`` cleanly.

    ``database_kind`` always rebuilds a database with the canonical id ``db``,
    default credentials, the kind's default image, and a consumer attachment on
    every non-Edge gateway. A primary database that matches that shape is
    faithfully carried by ``database_kind`` alone; one that doesn't (custom id,
    image/credential override, partial attachment set) must be re-grafted
    instead, so this returns None for it and the caller leaves
    ``database_kind=None``.

    Only the *primary* (first) database is eligible; a second database is always
    re-grafted.
    """
    dbs = [inst for inst in config.service_instances if inst.is_database]
    if not dbs:
        return None
    primary = dbs[0]
    if len(dbs) > 1:
        return None  # a second DB exists -> re-graft the whole DB story
    if primary.id != "db" or primary.env:
        return None
    # An image override is anything other than the kind's default tag (the
    # default is filled onto every instance by the schema, so compare to it
    # rather than treating a populated image as an override).
    if primary.image and primary.image != _DB_DEFAULT_IMAGE.get(primary.service):
        return None
    if primary.user != "ignition" or primary.password != "ignition":
        return None
    # Attached as a plain consumer on every non-Edge gateway (the lowering shape).
    for gw in config.gateways:
        attached = [att for att in gw.services if att.instance == primary.id]
        if gw.ignition_edition == "edge":
            if attached:
                return None
            continue
        if len(attached) != 1 or attached[0].role != "consumer":
            return None
    return primary


def detect_iiot_broker(config: ProjectConfig) -> str | None:
    """Service slug of the broker an IIoT overlay wired, or None.

    Detection is by attachment role: a stack carrying any ``mqtt-transmission``
    or ``mqtt-engine`` attachment was built with ``apply_iiot``; the instance
    those attachments reference is the broker. Returning its *service slug*
    (not its id) lets ``switch-profile`` re-apply the overlay with
    ``ProfileOptions.iiot_broker``.
    """
    by_id = {inst.id: inst for inst in config.service_instances}
    for gw in config.gateways:
        for att in gw.services:
            if att.role in _IIOT_ROLES:
                inst = by_id.get(att.instance)
                if inst is not None:
                    return inst.service
    return None


def _target_gateways(source: GatewayConfig, new_config: ProjectConfig) -> list[GatewayConfig]:
    """Gateways in the new topology that ``source``'s attachments map onto.

    Resolution order: exact name match first; then the role counterpart
    (hub<->backend, spoke<->frontend); then any gateway in the same role class
    (central vs edge-side). Empty when the new topology has no counterpart -
    the caller drops those attachments with an advisory.
    """
    by_name = {gw.name: gw for gw in new_config.gateways}
    if source.name in by_name:
        return [by_name[source.name]]

    counterpart = _ROLE_COUNTERPART.get(source.role or "")
    if counterpart is not None:
        matches = [gw for gw in new_config.gateways if gw.role == counterpart]
        if matches:
            return matches

    if source.role in _CENTRAL_ROLES or source.role is None:
        target_class = _CENTRAL_ROLES
        role_less_counts = True
    elif source.role in _EDGE_SIDE_ROLES:
        target_class = _EDGE_SIDE_ROLES
        role_less_counts = False
    else:
        return []
    matches = [gw for gw in new_config.gateways if gw.role in target_class or (role_less_counts and gw.role is None)]
    return matches


def carry_registry(
    new_config: ProjectConfig,
    old_config: ProjectConfig,
    console: _Printer,
) -> ProjectConfig:
    """Re-graft ``old_config``'s richer registry onto the freshly built config.

    Mutates and returns ``new_config``. For every old instance that is *not*
    already default-representable (carried via ``ProfileOptions``) and *not* the
    IIoT broker, the instance is added to the registry (preserving id +
    overrides) and its non-IIoT attachments are re-mapped by role into the new
    topology. Attachments are dropped - each with a printed
    ``[yellow]note[/yellow]`` advisory - when:

    - the source gateway has no counterpart in the new topology;
    - re-grafting would attach a ``never_on_edge`` service to an Edge gateway;
    - re-grafting would give a gateway a second database connection.

    The order matters: instances are added before attachments so an attachment's
    referenced instance always exists, and the per-gateway db cap is checked
    against attachments already present (including those the profile/lowering
    produced) so the carry never over-attaches.
    """
    catalog = load_all_services()
    iiot_broker_slug = detect_iiot_broker(old_config)

    new_by_id = {inst.id: inst for inst in new_config.service_instances}
    dropped_instance: dict[str, str] = {}

    for old_inst in old_config.service_instances:
        # The IIoT broker rides ProfileOptions.iiot; apply_iiot re-adds it.
        if iiot_broker_slug is not None and old_inst.service == iiot_broker_slug:
            continue
        if is_default_representable(old_inst, old_config, catalog):
            continue
        if old_inst.id in new_by_id:
            continue
        # A re-grafted database of a kind the new topology already runs would
        # collide on the per-kind image + shared DB_USER/DB_PASSWORD keys
        # (same-kind duplicates are barred), so drop it with an advisory instead
        # of letting resolve() raise.
        if old_inst.is_database:
            existing_kinds = {i.service for i in new_config.service_instances if i.is_database}
            if old_inst.service in existing_kinds:
                dropped_instance[old_inst.id] = f"the new topology already runs a '{old_inst.service}' database"
                continue
        # Add the instance (preserving custom id + overrides).
        grafted = old_inst.model_copy(deep=True)
        new_config.service_instances.append(grafted)
        new_by_id[old_inst.id] = grafted

    for instance_id, reason in sorted(dropped_instance.items()):
        console.print(f"[yellow]note[/yellow]: instance '{instance_id}' was not carried " f"({reason})")

    # Re-map attachments. Collect the (gateway, attachment) pairs first so we can
    # report drops with a stable, deduplicated advisory.
    dropped_no_target: dict[str, set[str]] = {}
    dropped_invariant: dict[str, list[str]] = {}

    for old_gw in old_config.gateways:
        targets = _target_gateways(old_gw, new_config)
        for att in old_gw.services:
            if att.role in _IIOT_ROLES:
                continue  # owned by the IIoT overlay
            inst = new_by_id.get(att.instance)
            if inst is None:
                continue  # carried by options (re-lowered) or representable
            # Skip attachments that the carried-via-options path already
            # reproduces: a representable instance is re-lowered to a consumer on
            # every gateway, so re-grafting it would duplicate.
            old_inst = next(
                (i for i in old_config.service_instances if i.id == att.instance),
                None,
            )
            if old_inst is not None and is_default_representable(old_inst, old_config, catalog):
                continue
            if not targets:
                dropped_no_target.setdefault(att.instance, set()).add(old_gw.name)
                continue
            for tgt in targets:
                _try_attach(tgt, att, inst, catalog, new_config, dropped_invariant)

    for instance_id, sources in sorted(dropped_no_target.items()):
        names = ", ".join(sorted(sources))
        console.print(f"[yellow]note[/yellow]: attachment to '{instance_id}' from " f"gateway(s) {names} was not carried (no counterpart gateway in the " "new topology)")
    for instance_id, reasons in sorted(dropped_invariant.items()):
        for reason in reasons:
            console.print(f"[yellow]note[/yellow]: attachment to '{instance_id}' was " f"dropped on the new topology ({reason})")

    return new_config


def _try_attach(
    target: GatewayConfig,
    att: ServiceAttachment,
    inst: ServiceInstance,
    catalog: dict[str, ServiceManifest],
    new_config: ProjectConfig,
    dropped: dict[str, list[str]],
) -> None:
    """Attach ``inst`` to ``target`` unless it already does or an invariant bars it.

    Invariants mirror the schema/resolver checks but *drop with an advisory*
    instead of raising, so a reshape onto an incompatible topology degrades
    gracefully:

    - ``never_on_edge`` service onto an Edge gateway -> drop;
    - a second database connection on one gateway -> drop.
    """
    manifest = catalog[inst.service]
    if any(a.instance == inst.id and a.role == att.role for a in target.services):
        return  # already attached (idempotent / profile already produced it)

    if manifest.placement.never_on_edge and target.ignition_edition == "edge":
        dropped.setdefault(inst.id, []).append(f"'{inst.service}' must not run on Edge gateway '{target.name}'")
        return

    if manifest.kind == "database":
        db_ids = {i.id for i in new_config.service_instances if catalog[i.service].kind == "database"}
        already = [a for a in target.services if a.instance in db_ids]
        if already:
            dropped.setdefault(inst.id, []).append(f"gateway '{target.name}' already holds a database connection " f"('{already[0].instance}')")
            return

    target.services.append(ServiceAttachment(instance=inst.id, role=att.role))
