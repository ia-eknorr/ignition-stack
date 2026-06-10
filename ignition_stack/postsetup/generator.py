"""Matrix-driven ``POST-SETUP.md`` generator.

``ignition-stack`` pre-seeds every connection the Phase-1 seedability matrix
marks file-seedable (db-connection, the internal-secret-provider that holds
its password, OPC-UA endpoints, ...). The connections it *cannot* fully seed -
a secret generated at runtime, a module that isn't publicly downloadable, a
gateway-network handshake approved in the UI - are deferred to ``POST-SETUP.md``
and finished by hand once the stack is up.

This module turns the deferred set into that document. It is purely a function
of the resolved :class:`~ignition_stack.config.schema.ProjectConfig`:

1. Each catalog service's manifest declares its deferred connections in
   ``post_setup`` (a list of ``connection``/``reason`` pairs). The database and
   every selected service contribute theirs.
2. Three profile-level flags add steps the matrix flags as not-fully-seedable:
   ``profile == "scaleout"`` (the gateway-network link is UI-approved),
   ``mcp_dropin`` (the EA-gated MCP module), and a set ``reverse_proxy`` (the
   Traefik scaffold).

Each step renders through a per-connection Jinja2 snippet at
``templates/post-setup/<connection>.md.j2`` (falling back to ``_default.md.j2``)
so adding a new fallback connection is a manifest entry + a snippet, with no
change here. Every snippet gives the reader the three things the validation
contract requires: the **deep-link URL** to open, the **in-UI screen path** to
navigate to, and the exact **``.env`` variable name** to copy.
"""

from __future__ import annotations

from dataclasses import dataclass

from jinja2 import Environment, PackageLoader, StrictUndefined, TemplateNotFound

from ignition_stack.config.schema import ProjectConfig
from ignition_stack.services.loader import load_all_services

_HEADER = """\
# Post-setup steps

`ignition-stack` pre-seeds everything the Phase-1 seedability matrix marks
file-seedable. The connections below carry a secret or a handshake that
cannot travel in a file, so finish them by hand after `docker compose up -d`.
Each step names the screen to open and the `.env` value to copy into it.
"""

_NO_MANUAL_STEPS = """\
# Post-setup steps

**No manual steps required.** Every connection in this stack is pre-seeded
from files. Bring it up with `docker compose up -d` and the gateway is ready.
"""

# Reasons for the profile-level steps that aren't tied to a single service
# manifest. Kept here (not in a manifest) because they're a property of the
# resolved topology, not of any one catalog entry.
_GATEWAY_NETWORK_LINK_REASON = (
    "This stack auto-forms its gateway-network links: each connecting gateway "
    "opens a plain (non-SSL, port 8088) outgoing connection and every node runs "
    "an Unrestricted incoming policy, so the links are accepted on sight with no "
    "UI approval. This step is a verification, not a manual procedure - confirm "
    "the links came up, and reach for the runbook only if one did not."
)
_MCP_MODULE_REASON = "The Ignition MCP module is Early-Access and gated behind a survey, so the " "CLI cannot bundle it. Request the .modl, drop it in, and re-up the stack."
_REVERSE_PROXY_REASON = (
    "The CLI never clones a proxy silently. The wizard scaffolded a README that " "walks through installing ia-eknorr/traefik-reverse-proxy in front of the " "stack."
)
_REDUNDANCY_PAIRING_REASON = (
    "This stack seeds redundancy fully: a pre-seeded redundancy.xml sets each "
    "node's role and an open Gateway Network policy lets the plain link "
    "auto-approve, so the pair forms with no UI clicks. This step is a "
    "verification, not a manual procedure - confirm the pair came up, and reach "
    "for the runbook only if it did not."
)


@dataclass(frozen=True)
class _Step:
    """One manual follow-up: a deferred connection plus why it's deferred.

    ``service`` is the catalog slug the step came from (so the renderer can pull
    that service's ``.env`` keys), or ``""`` for the profile-level steps that
    aren't owned by a single service.
    """

    connection: str
    reason: str
    service: str


def generate_post_setup(config: ProjectConfig) -> str:
    """Render the body of ``POST-SETUP.md`` for a resolved project config.

    Always returns a document: a "no manual steps required" note when the stack
    is fully seedable, or a header plus one section per deferred connection.
    """
    steps = _collect_steps(config)
    if not steps:
        return _NO_MANUAL_STEPS

    env = _jinja_env()
    sections = [_render_step(env, _context(config, step)) for step in steps]
    return _HEADER + "\n" + "\n\n".join(sections) + "\n"


def _collect_steps(config: ProjectConfig) -> list[_Step]:
    """Gather every deferred connection, service steps first then profile steps."""
    catalog = load_all_services()
    steps: list[_Step] = []

    # The registry instance's service slug == its catalog key (db kind for
    # databases, slug for everyone else), so look each up by service slug.
    # Database first to preserve the historical step ordering.
    db = config.database_instance()
    slugs: list[str] = []
    if db is not None:
        slugs.append(db.service)
    slugs.extend(inst.service for inst in config.non_database_instances())

    for slug in slugs:
        manifest = catalog.get(slug)
        if manifest is None:
            continue
        for item in manifest.post_setup:
            steps.append(_Step(item.connection, item.reason, slug))

    if any(gw.gan_outgoing for gw in config.gateways):
        steps.append(_Step("gateway-network-link", _GATEWAY_NETWORK_LINK_REASON, ""))
    if any(gw.redundancy is not None for gw in config.gateways):
        steps.append(_Step("redundancy-pairing", _REDUNDANCY_PAIRING_REASON, ""))
    if config.mcp_dropin:
        steps.append(_Step("mcp-module", _MCP_MODULE_REASON, ""))
    if config.reverse_proxy is not None:
        steps.append(_Step("reverse-proxy", _REVERSE_PROXY_REASON, ""))

    return steps


def _context(config: ProjectConfig, step: _Step) -> dict[str, object]:
    """Build the render context one snippet sees.

    ``env_vars`` is the (key, value) list the reader copies into the gateway
    screen: a service step exposes that service's preset ``.env`` keys. The
    gateway-network-link step copies nothing - the links auto-form from env, so
    it carries ``gan_links`` (who connects to whom) for a verification readout
    instead.
    """
    catalog = load_all_services()
    gateways = [
        {
            "name": gw.name,
            "role": gw.role or gw.name,
            "edition": gw.ignition_edition,
            "url": f"http://localhost:{gw.http_port}",
        }
        for gw in config.gateways
    ]

    env_vars = sorted(catalog[step.service].env.items()) if step.service else []

    return {
        "project_name": config.name,
        "connection": step.connection,
        "reason": step.reason,
        "service": step.service,
        "gateway_url": gateways[0]["url"],
        "gateways": gateways,
        "redundancy_pairs": _redundancy_pairs(config),
        "gan_links": _gan_links(config),
        "mqtt": _mqtt_wiring(config),
        "env_vars": env_vars,
        "env_map": dict(env_vars),
        "proxy_path": config.reverse_proxy.path if config.reverse_proxy else "",
        "dropin_dir": "modules/dropin",
    }


def _mqtt_wiring(config: ProjectConfig) -> dict[str, object] | None:
    """The resolved Sparkplug pipeline (broker + per-gateway roles), or None.

    Reads the registry for the single ``mqtt-broker`` instance and its
    ``wires.mqtt`` block, then splits the gateways attached to it by role so the
    mqtt step can name which gateway runs Transmission (each an Edge Node with a
    pre-filled Sparkplug identity: Group ID = project name, Edge Node ID = the
    gateway name, per the issue #43 convention) versus Engine. Returns ``None``
    when no broker is wired or no gateway carries an mqtt attachment (a broker
    selected without the IIoT overlay), so the mqtt step falls back to its
    generic single-gateway form.
    """
    catalog = load_all_services()
    broker = next(
        (inst for inst in config.service_instances if catalog[inst.service].kind == "mqtt-broker"),
        None,
    )
    if broker is None:
        return None
    wires = catalog[broker.service].wires
    mqtt = wires.mqtt if wires is not None else None
    if mqtt is None:
        return None

    transmission: list[dict[str, object]] = []
    engine: list[dict[str, object]] = []
    for gw in config.gateways:
        roles = {att.role for att in gw.services if att.instance == broker.id}
        base = {
            "name": gw.name,
            "role": gw.role or gw.name,
            "edition": gw.ignition_edition,
            "url": f"http://localhost:{gw.http_port}",
        }
        if "mqtt-transmission" in roles:
            transmission.append({**base, "group_id": config.name, "edge_node_id": gw.name})
        if "mqtt-engine" in roles:
            engine.append(base)

    if not transmission and not engine:
        return None
    # The exact module slugs to fetch into modules/cache/, in the order a user
    # would type them, so the step can hand over a copy-paste `modules download`.
    module_slugs = []
    if engine:
        module_slugs.append(mqtt.engine_module)
    if transmission:
        module_slugs.append(mqtt.transmission_module)
    return {
        "broker": broker.id,
        "broker_url": f"tcp://{broker.id}:{mqtt.port}",
        "transmission_gateways": transmission,
        "engine_gateways": engine,
        "modules": " ".join(module_slugs),
    }


def _redundancy_pairs(config: ProjectConfig) -> list[dict[str, object]]:
    """Master/backup pairs in the stack, for the redundancy-pairing step.

    Keyed off each backup so a partial (master-only) config contributes nothing;
    each entry names both nodes, their UIs, and the Gateway Network port the
    redundancy link rides.
    """
    by_name = {gw.name: gw for gw in config.gateways}
    pairs: list[dict[str, object]] = []
    for gw in config.gateways:
        if gw.redundancy is None or gw.redundancy.mode != "backup":
            continue
        master = by_name.get(gw.redundancy.peer)
        if master is None:
            continue
        pairs.append(
            {
                "master": master.name,
                "backup": gw.name,
                "master_url": f"http://localhost:{master.http_port}",
                "backup_url": f"http://localhost:{gw.http_port}",
                "gan_port": gw.redundancy.gan_port,
                "edition": master.ignition_edition,
            }
        )
    return pairs


def _gan_links(config: ProjectConfig) -> list[dict[str, object]]:
    """Auto-formed Gateway Network links, for the gateway-network-link step.

    One entry per outgoing connection a gateway declares in ``gan_outgoing``
    (scaleout frontend -> backend, hub-and-spoke spoke -> hub): it names the
    source and target, their UIs, and the plain port the link rides so the
    verification readout can point the reader at each end.
    """
    by_name = {gw.name: gw for gw in config.gateways}
    links: list[dict[str, object]] = []
    for gw in config.gateways:
        for peer in gw.gan_outgoing:
            target = by_name.get(peer)
            links.append(
                {
                    "source": gw.name,
                    "source_role": gw.role or gw.name,
                    "source_url": f"http://localhost:{gw.http_port}",
                    "target": peer,
                    "target_role": (target.role or target.name) if target else peer,
                    "target_url": f"http://localhost:{target.http_port}" if target else "",
                    "port": 8088,
                }
            )
    return links


def _render_step(env: Environment, ctx: dict[str, object]) -> str:
    connection = ctx["connection"]
    try:
        template = env.get_template(f"{connection}.md.j2")
    except TemplateNotFound:
        template = env.get_template("_default.md.j2")
    return template.render(**ctx).rstrip()


def _jinja_env() -> Environment:
    return Environment(
        loader=PackageLoader("ignition_stack.templates", "post-setup"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
