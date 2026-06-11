"""The Custom-track composer: an interactive editor for the service registry.

Phase 7's second door. Where the Quick track (``wizard.walk``) walks a linear
profile flow, the composer lets the user attach services to individual
gateways, share one instance across gateways, and wire the IIoT pipeline - the
heterogeneous shapes the flat profile flow cannot express. Both tracks produce
the same :class:`ProjectConfig`; the composer simply edits one directly.

The canonical working config is always **already resolved**: legacy
``database``/``services`` shims are empty, the registry is populated, and every
gateway -> instance edge is an explicit :class:`ServiceAttachment`. The edit
loop therefore mutates ``service_instances`` and ``gateway.services`` in place
rather than the legacy fields.

Mutate/validate pattern (the crux). pydantic does not re-validate on attribute
assignment, and ``resolve()`` only deep-copies - it does not re-run the
``ProjectConfig`` model validators. So a mutation is applied to a *deep copy*,
then the copy is **reconstructed** through ``ProjectConfig.model_validate`` (to
fire the construction-time validators: dangling refs, the edge/never_on_edge
rule, redundancy edition, GAN targets) and **re-resolved** (to fire the
resolver bounds: distinct DB kinds, shared DB creds, one DB per gateway,
singletons, one broker). If either step raises, the error is printed and the
*unchanged* working config is returned - the user never loses state and the CLI
never crashes. On success the re-resolved config becomes canonical, so auto-added
capability dependencies (Keycloak's backing database) appear in the registry and
the composition table immediately.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from ignition_stack.catalog.builtins import default_builtin_catalog, jdbc_driver_for
from ignition_stack.config import ProjectConfig, ServiceAttachment, ServiceInstance
from ignition_stack.profiles import ProfileOptions, apply_iiot
from ignition_stack.services.loader import load_all_services
from ignition_stack.services.manifest import ServiceManifest
from ignition_stack.services.resolver import ResolveError, resolve

if TYPE_CHECKING:
    from ignition_stack.wizard import Prompter

console = Console()

# Roles a broker attachment can play, offered explicitly when sharing/attaching
# a broker (the IIoT vocabulary). Non-broker instances always attach as consumer.
_BROKER_ROLE_CHOICES: list[tuple[str, str]] = [
    ("mqtt-transmission", "MQTT Transmission (edge gateway publishes Sparkplug)"),
    ("mqtt-engine", "MQTT Engine (central gateway subscribes/aggregates)"),
    ("consumer", "Consumer"),
]

# The edit-loop menu. Order is the natural build flow: shape services, then
# cross-reference, then per-gateway knobs, then finish.
_ACTIONS: list[tuple[str, str]] = [
    ("add", "Add a service to a gateway"),
    ("share", "Share an existing instance with another gateway"),
    ("stack", "Add a stack-level service (no gateway attachment)"),
    ("remove", "Remove an attachment"),
    ("modules", "Set a gateway's enabled modules"),
    ("edition", "Set a gateway's edition (standard / edge)"),
    ("iiot", "Add or remove IIoT (MQTT/Sparkplug)"),
    ("rename", "Rename an instance"),
    ("done", "Done - review and generate"),
]


@dataclass
class ComposerResult:
    """What the composer hands back to ``wizard.walk``.

    ``config`` is the resolved working config; ``confirmed`` is True only when
    the user chose *generate* at the composer summary (a *cancel* surfaces as
    the same exit-130 path the Quick track uses). ``profile``/``options`` carry
    the starting preset so the :class:`~ignition_stack.wizard.WizardOutcome`
    stays populated for the lifecycle record and tests.
    """

    config: ProjectConfig
    confirmed: bool
    profile: str
    options: ProfileOptions
    summary_lines: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def edit_loop(
    prompter: Prompter,
    working: ProjectConfig,
    preset_slug: str,
    preset_options: ProfileOptions,
) -> ComposerResult:
    """Run the composer edit loop over an already-resolved ``working`` config."""
    while True:
        _print_composition(working)
        action = prompter.select("Composer action?", _ACTIONS, default="done")
        if action == "done":
            choice = prompter.select(
                "Generate this composition?",
                [
                    ("generate", "Generate the project"),
                    ("edit", "Keep editing"),
                    ("cancel", "Cancel"),
                ],
                default="generate",
            )
            if choice == "edit":
                continue
            return ComposerResult(
                config=working,
                confirmed=(choice == "generate"),
                profile=preset_slug,
                options=preset_options,
                summary_lines=_summary_lines(working),
            )
        working = _dispatch(action, prompter, working)


def _dispatch(action: str, prompter: Prompter, working: ProjectConfig) -> ProjectConfig:
    handlers: dict[str, Callable[[Prompter, ProjectConfig], ProjectConfig]] = {
        "add": _action_add,
        "share": _action_share,
        "stack": _action_stack,
        "remove": _action_remove,
        "modules": _action_modules,
        "edition": _action_edition,
        "iiot": _action_iiot,
        "rename": _action_rename,
    }
    return handlers[action](prompter, working)


# --------------------------------------------------------------------------- #
# Mutate / validate
# --------------------------------------------------------------------------- #


def _try_mutate(
    working: ProjectConfig,
    mutate: Callable[[ProjectConfig], None],
) -> ProjectConfig:
    """Apply ``mutate`` to a deep copy, validate + resolve it, accept or revert.

    Returns the new resolved config on success; on any validation/resolve error
    it prints a single ``[red]error[/red]`` line and returns the *unchanged*
    working config. See the module docstring for why both reconstruct and
    re-resolve are needed.
    """
    candidate = working.model_copy(deep=True)
    try:
        mutate(candidate)
        validated = ProjectConfig.model_validate(candidate.model_dump(mode="python"))
        return resolve(validated)
    except (ValidationError, ResolveError, ValueError) as exc:
        console.print(f"[red]error[/red]: {_explain(exc)}")
        return working


def _explain(exc: Exception) -> str:
    """A one-line, user-facing reason from a pydantic or resolver error."""
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            return str(errors[0].get("msg", exc))
    return str(exc)


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #


def _action_add(prompter: Prompter, working: ProjectConfig) -> ProjectConfig:
    catalog = load_all_services()
    gw = _pick_gateway(prompter, working, "Add a service to which gateway?")
    if gw is None:
        return working
    choices = service_choices_for_gateway(gw, catalog)
    if not choices:
        console.print("[yellow]note[/yellow]: no services are available for that gateway.")
        return working
    if _hidden_on_edge(gw, catalog):
        console.print(f"[dim](databases are hidden: not available on the Edge gateway '{gw.name}')[/dim]")
    slug = prompter.select("Which service?", choices, default=choices[0][0])
    manifest = catalog[slug]
    existing = [inst for inst in working.service_instances if inst.service == slug]

    if manifest.singleton and existing:
        inst = existing[0]
        if not prompter.confirm(
            f"'{slug}' already exists as instance '{inst.id}' (singleton); attach '{gw.name}' to it?",
            default=True,
        ):
            return working
        role = _pick_role(prompter, manifest)
        return _try_mutate(working, lambda c: _attach(c, gw.name, inst.id, role))

    new_id = _free_instance_id(working, slug)
    role = _pick_role(prompter, manifest)

    def mutate(c: ProjectConfig) -> None:
        c.service_instances.append(ServiceInstance(id=new_id, service=slug))
        _attach(c, gw.name, new_id, role)

    return _try_mutate(working, mutate)


def _action_share(prompter: Prompter, working: ProjectConfig) -> ProjectConfig:
    catalog = load_all_services()
    if not working.service_instances:
        console.print("[yellow]note[/yellow]: no instances to share yet - add one first.")
        return working
    inst = _pick_instance(prompter, working, "Share which instance?")
    if inst is None:
        return working
    manifest = catalog[inst.service]
    targets = [
        gw for gw in working.gateways if not any(att.instance == inst.id for att in gw.services) and not (manifest.placement.never_on_edge and gw.ignition_edition == "edge")
    ]
    if not targets:
        console.print(f"[yellow]note[/yellow]: '{inst.id}' has no eligible target gateway (all attached, or Edge-blocked).")
        return working
    gw = _pick_gateway(prompter, working, "Share with which gateway?", gateways=targets)
    if gw is None:
        return working
    role = _pick_role(prompter, manifest)
    return _try_mutate(working, lambda c: _attach(c, gw.name, inst.id, role))


def _action_stack(prompter: Prompter, working: ProjectConfig) -> ProjectConfig:
    catalog = load_all_services()
    choices = _all_service_choices(catalog)
    slug = prompter.select("Add which stack-level service?", choices, default=choices[0][0])
    manifest = catalog[slug]
    existing = [inst for inst in working.service_instances if inst.service == slug]
    if manifest.singleton and existing:
        console.print(f"[red]error[/red]: '{slug}' is a singleton and already exists as '{existing[0].id}'.")
        return working
    new_id = _free_instance_id(working, slug)
    return _try_mutate(working, lambda c: c.service_instances.append(ServiceInstance(id=new_id, service=slug)))


def _action_remove(prompter: Prompter, working: ProjectConfig) -> ProjectConfig:
    rows = [(gw.name, att.instance, att.role) for gw in working.gateways for att in gw.services]
    if not rows:
        console.print("[yellow]note[/yellow]: there are no attachments to remove.")
        return working
    choices = [(f"{i}", f"{gw} -> {inst} ({role})") for i, (gw, inst, role) in enumerate(rows)]
    picked = prompter.select("Remove which attachment?", choices, default="0")
    gw_name, inst_id, role = rows[int(picked)]

    def mutate(c: ProjectConfig) -> None:
        target = next(g for g in c.gateways if g.name == gw_name)
        target.services = [a for a in target.services if not (a.instance == inst_id and a.role == role)]
        # Prune an instance whose last attachment was just removed: detaching the
        # last user of a service drops the container. A deliberately stack-level
        # instance (added with no attachment) is never "detached", so it survives.
        still_used = any(a.instance == inst_id for g in c.gateways for a in g.services)
        if not still_used:
            c.service_instances = [inst for inst in c.service_instances if inst.id != inst_id]

    return _try_mutate(working, mutate)


def _action_modules(prompter: Prompter, working: ProjectConfig) -> ProjectConfig:
    gw = _pick_gateway(prompter, working, "Set modules on which gateway?")
    if gw is None:
        return working
    choices = module_choices_for_gateway(working, gw)
    chosen = set(prompter.checkbox(f"Modules to enable on '{gw.name}':", choices))
    all_slugs = default_builtin_catalog().slugs
    disable = sorted(all_slugs - chosen)

    def mutate(c: ProjectConfig) -> None:
        target = next(g for g in c.gateways if g.name == gw.name)
        target.disable_builtins = list(disable)

    return _try_mutate(working, mutate)


def _action_edition(prompter: Prompter, working: ProjectConfig) -> ProjectConfig:
    gw = _pick_gateway(prompter, working, "Set edition on which gateway?")
    if gw is None:
        return working
    edition = prompter.select(
        f"Edition for '{gw.name}'?",
        [("standard", "Standard"), ("edge", "Edge (leaf node; no database)")],
        default=gw.ignition_edition,
    )

    def mutate(c: ProjectConfig) -> None:
        target = next(g for g in c.gateways if g.name == gw.name)
        target.ignition_edition = edition

    return _try_mutate(working, mutate)


def _action_iiot(prompter: Prompter, working: ProjectConfig) -> ProjectConfig:
    catalog = load_all_services()
    brokers = [inst for inst in working.service_instances if catalog[inst.service].kind == "mqtt-broker"]
    if brokers:
        if not prompter.confirm(f"Unwire the MQTT pipeline (broker '{brokers[0].id}')?", default=False):
            return working
        return _try_mutate(working, lambda c: _unwire_iiot(c, catalog))
    broker = prompter.select("MQTT broker?", mqtt_broker_choices(catalog), default="chariot")
    return _try_mutate(working, lambda c: apply_iiot(c, broker))


def _action_rename(prompter: Prompter, working: ProjectConfig) -> ProjectConfig:
    if not working.service_instances:
        console.print("[yellow]note[/yellow]: there are no instances to rename.")
        return working
    inst = _pick_instance(prompter, working, "Rename which instance?")
    if inst is None:
        return working
    new_id = prompter.text(f"New id for '{inst.id}'?", default=inst.id)
    if new_id == inst.id:
        return working

    def mutate(c: ProjectConfig) -> None:
        old = inst.id
        for entry in c.service_instances:
            if entry.id == old:
                entry.id = new_id
        for gw in c.gateways:
            for att in gw.services:
                if att.instance == old:
                    att.instance = new_id

    return _try_mutate(working, mutate)


# --------------------------------------------------------------------------- #
# Pure helpers (choice lists + selection)
# --------------------------------------------------------------------------- #


def _hidden_on_edge(gw, catalog: dict[str, ServiceManifest]) -> bool:
    return gw.ignition_edition == "edge" and any(m.placement.never_on_edge for m in catalog.values())


def service_choices_for_gateway(gw, catalog: dict[str, ServiceManifest]) -> list[tuple[str, str]]:
    """Catalog services attachable to ``gw``, grouped by kind, never_on_edge filtered.

    Databases (and any ``never_on_edge`` service) are filtered OUT for an Edge
    gateway so the user can't pick one and hit a validation error after the
    fact; the caller prints a one-line annotation explaining the omission.
    """
    rows = []
    for slug, manifest in catalog.items():
        if gw.ignition_edition == "edge" and manifest.placement.never_on_edge:
            continue
        rows.append((manifest.kind, slug, manifest.summary))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [(slug, f"{slug} [{kind}] - {summary}") for kind, slug, summary in rows]


def _all_service_choices(catalog: dict[str, ServiceManifest]) -> list[tuple[str, str]]:
    rows = sorted(((m.kind, slug, m.summary) for slug, m in catalog.items()), key=lambda r: (r[0], r[1]))
    return [(slug, f"{slug} [{kind}] - {summary}") for kind, slug, summary in rows]


def mqtt_broker_choices(catalog: dict[str, ServiceManifest] | None = None) -> list[tuple[str, str]]:
    """``(slug, label)`` for every ``mqtt-broker`` catalog service, chariot first."""
    catalog = catalog or load_all_services()
    brokers = sorted(slug for slug, m in catalog.items() if m.kind == "mqtt-broker")
    brokers.sort(key=lambda s: (s != "chariot", s))
    return [(slug, f"{slug} - {catalog[slug].summary}") for slug in brokers]


def _gateway_db_kind(working: ProjectConfig, gw) -> str | None:
    """The kind of the database ``gw`` attaches to, if any (drives its JDBC driver)."""
    by_id = {inst.id: inst for inst in working.service_instances if inst.is_database}
    for att in gw.services:
        inst = by_id.get(att.instance)
        if inst is not None:
            return inst.service
    return None


def module_choices_for_gateway(working: ProjectConfig, gw) -> list[tuple[str, str, bool]]:
    """``(slug, name, checked)`` triples for a gateway's per-gateway module checkbox.

    A pristine gateway (no ``disable_builtins`` yet) is prechecked with the #42
    curated default set plus the JDBC driver matching *this* gateway's attached
    database. An already-customized gateway is prechecked with its current
    enabled set (every built-in not in ``disable_builtins``).
    """
    catalog = default_builtin_catalog()
    all_slugs = catalog.slugs
    if gw.disable_builtins:
        prechecked = all_slugs - set(gw.disable_builtins)
    else:
        prechecked = set(catalog.default_enabled_slugs)
        driver = jdbc_driver_for(_gateway_db_kind(working, gw))
        if driver is not None:
            prechecked.add(driver)
    return [(m.slug, m.name, m.slug in prechecked) for m in sorted(catalog.modules, key=lambda m: m.name.lower())]


def _free_instance_id(working: ProjectConfig, slug: str) -> str:
    taken = {inst.id for inst in working.service_instances}
    if slug not in taken:
        return slug
    suffix = 2
    while f"{slug}-{suffix}" in taken:
        suffix += 1
    return f"{slug}-{suffix}"


def _pick_role(prompter: Prompter, manifest: ServiceManifest) -> str:
    if manifest.kind == "mqtt-broker":
        return prompter.select("Attachment role?", _BROKER_ROLE_CHOICES, default="consumer")
    return "consumer"


def _pick_gateway(prompter: Prompter, working: ProjectConfig, message: str, gateways=None):
    pool = gateways if gateways is not None else working.gateways
    choices = [(gw.name, f"{gw.name} ({gw.ignition_edition}{', ' + gw.role if gw.role else ''})") for gw in pool]
    name = prompter.select(message, choices, default=choices[0][0])
    return next((gw for gw in pool if gw.name == name), None)


def _pick_instance(prompter: Prompter, working: ProjectConfig, message: str):
    choices = [(inst.id, f"{inst.id} ({inst.service})") for inst in working.service_instances]
    chosen = prompter.select(message, choices, default=choices[0][0])
    return next((inst for inst in working.service_instances if inst.id == chosen), None)


def _attach(config: ProjectConfig, gw_name: str, instance_id: str, role: str) -> None:
    """Attach ``instance_id`` to a gateway, adding the Cirrus module for mqtt roles.

    A manual ``mqtt-transmission`` / ``mqtt-engine`` attachment also installs the
    broker manifest's matching Transmission/Engine module on that gateway, so
    fine-grained wiring through *share* stays functionally equivalent to the
    role-based ``apply_iiot`` overlay (which adds the same modules).
    """
    gw = next(g for g in config.gateways if g.name == gw_name)
    if not any(att.instance == instance_id and att.role == role for att in gw.services):
        gw.services.append(ServiceAttachment(instance=instance_id, role=role))
    if role in {"mqtt-transmission", "mqtt-engine"}:
        inst = next((i for i in config.service_instances if i.id == instance_id), None)
        if inst is None:
            return
        manifest = load_all_services().get(inst.service)
        if manifest is None or manifest.wires is None or manifest.wires.mqtt is None:
            return
        module = manifest.wires.mqtt.transmission_module if role == "mqtt-transmission" else manifest.wires.mqtt.engine_module
        if module not in gw.modules:
            gw.modules.append(module)


def _unwire_iiot(config: ProjectConfig, catalog: dict[str, ServiceManifest]) -> None:
    """Remove every broker instance, its mqtt attachments, and the Cirrus modules."""
    broker_ids = {inst.id for inst in config.service_instances if catalog[inst.service].kind == "mqtt-broker"}
    modules = set()
    for inst in config.service_instances:
        wires = catalog[inst.service].wires
        if catalog[inst.service].kind == "mqtt-broker" and wires is not None and wires.mqtt is not None:
            modules.add(wires.mqtt.transmission_module)
            modules.add(wires.mqtt.engine_module)
    for gw in config.gateways:
        gw.services = [a for a in gw.services if not (a.instance in broker_ids and a.role in {"mqtt-transmission", "mqtt-engine"})]
        gw.modules = [m for m in gw.modules if m not in modules]
    config.service_instances = [inst for inst in config.service_instances if inst.id not in broker_ids]


# --------------------------------------------------------------------------- #
# Rendering (rich tables, like wizard._summarize but printed directly)
# --------------------------------------------------------------------------- #


def _attachers(working: ProjectConfig, instance_id: str) -> list[str]:
    out = []
    for gw in working.gateways:
        for att in gw.services:
            if att.instance == instance_id:
                out.append(f"{gw.name}:{att.role}")
    return out


def _print_composition(working: ProjectConfig) -> None:
    catalog = load_all_services()
    builtin_total = len(default_builtin_catalog().slugs)

    gateways = Table(title="Gateways", show_lines=False)
    for col in ("gateway", "edition", "role", "attachments", "modules", "builtins"):
        gateways.add_column(col)
    for gw in working.gateways:
        atts = ", ".join(f"{a.instance}:{a.role}" for a in gw.services) or "-"
        mods = ", ".join(gw.modules) or "-"
        enabled = builtin_total - len(gw.disable_builtins)
        gateways.add_row(gw.name, gw.ignition_edition, gw.role or "-", atts, mods, f"{enabled}/{builtin_total}")
    console.print(gateways)

    registry = Table(title="Service registry", show_lines=False)
    for col in ("instance", "service", "kind", "attached by"):
        registry.add_column(col)
    for inst in working.service_instances:
        manifest = catalog.get(inst.service)
        kind = manifest.kind if manifest else "?"
        attachers = _attachers(working, inst.id)
        if attachers:
            attached = ", ".join(attachers)
        elif inst.is_database and inst.extra_databases:
            # A database with logical extra_databases but no gateway attachment is
            # a capability dependency the resolver auto-added (e.g. Keycloak's
            # backing store). Label it so the user understands why it appeared.
            attached = f"[dim]auto-added (backs {', '.join(inst.extra_databases)})[/dim]"
        else:
            attached = "[dim]stack-level (no gateway attachment)[/dim]"
        label = inst.id
        if manifest is not None and inst.image and inst.image != manifest.image:
            label = f"{inst.id} [dim](image override)[/dim]"
        registry.add_row(label, inst.service, kind, attached)
    if not working.service_instances:
        registry.add_row("[dim](none)[/dim]", "", "", "")
    console.print(registry)


def _summary_lines(working: ProjectConfig) -> list[str]:
    """Plain-text recap stored on the outcome (the rich tables are printed live)."""
    lines = [
        f"project name : {working.name}",
        f"gateways     : {len(working.gateways)} ({', '.join(f'{g.name}={g.ignition_edition}' for g in working.gateways)})",
        f"instances    : {', '.join(f'{i.id}({i.service})' for i in working.service_instances) or '(none)'}",
    ]
    for gw in working.gateways:
        atts = ", ".join(f"{a.instance}:{a.role}" for a in gw.services) or "(none)"
        lines.append(f"  {gw.name:<12}: {atts}")
    return lines
