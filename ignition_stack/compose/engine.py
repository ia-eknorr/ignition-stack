"""Anchor-preserving compose generation engine.

The engine composes a ``docker-compose.yaml`` by concatenating a header
(holding the ``x-ignition-common`` / ``x-ignition-environment`` anchors)
with per-service Jinja2 fragments and a footer (volumes + networks),
then round-tripping the whole text through ruamel.yaml so anchors,
quotes, block scalars, and the list-form merge key survive intact.

The two design constraints that drive the implementation:

1. **Anchors must survive.** Jinja2 doesn't know about anchors, so we
   never build per-fragment Python dicts and try to wire references
   between them. We render fragments to *text*, glue them together
   into a single document, then parse + emit once - anchors resolve
   inside that single parse context.

2. **The Phase 2 golden is byte-stable through the engine.** Empirical
   verification: ruamel 0.19 with ``preserve_quotes`` + ``explicit_start``
   + ``indent(mapping=2, sequence=4, offset=2)`` + ``width=200``
   round-trips Phase 2's compose file unchanged. Future-you: if you
   change the YAML emitter settings here, expect the standalone-postgres
   golden to need a regeneration.

The render pipeline is intentionally small so per-service templates carry
all the parametric complexity. The engine renders the gateway plumbing
(``bootstrap`` + ``ignition``) from its own ``compose/templates``, then
renders the database and every selected service from the Phase-5 service
catalog at ``templates/services/<name>/compose.yaml.j2``. The config is
expected to be already resolved (see ``services.resolver``); the engine
never adds or re-resolves services at render time.
"""

from __future__ import annotations

import io
import textwrap
from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader, StrictUndefined
from ruamel.yaml import YAML

from ignition_stack.catalog.builtins import default_builtin_catalog
from ignition_stack.services.loader import load_all_services, load_service

if TYPE_CHECKING:
    from ignition_stack.catalog.schema import Catalog, ModuleEntry
    from ignition_stack.config.schema import GatewayConfig, ProjectConfig


# Network names used when network_split is on. The wizard (Phase 6) and
# CLI flag (Phase 6 too) keep the same names so users can reason about
# generated stacks in one consistent vocabulary.
NETWORK_FRONTEND = "frontend"
NETWORK_BACKEND = "backend"

# Canonical render order for catalog services so goldens are deterministic.
# Databases render in their historical position (right after the gateways),
# handled separately; the rest follow this kind ordering, alphabetical within
# a kind.
_SERVICE_KIND_ORDER = ["mqtt-broker", "idp", "simulator", "streaming", "automation"]


def render_compose(
    config: ProjectConfig,
    catalog: Catalog | None = None,
) -> str:
    """Render a ``docker-compose.yaml`` for the given project config.

    ``catalog`` is required when any gateway lists modules; the engine
    looks up each module's fully-qualified identifier and emits the
    ``ACCEPT_MODULE_LICENSES`` / ``ACCEPT_MODULE_CERTS`` env vars per the
    resolved q-module-install finding. ``GATEWAY_MODULES_ENABLED`` is
    deliberately omitted: the Phase-1 matrix found it acts as a strict
    whitelist that quarantines the built-in IA modules (OPC-UA, SQL
    Historian, ...). Pass ``None`` for module-free stacks.

    Returns LF-terminated text suitable for writing to disk.
    """
    env = _jinja_env()
    description = _describe(config)

    header_text = env.get_template("header.yaml.j2").render(
        description_lines=_wrap_description(description),
    )
    service_blocks = _render_services(env, config, catalog)
    footer_text = env.get_template("footer.yaml.j2").render(
        volumes=_volume_names(config),
        networks=_network_names(config),
    )

    # Blank line between each service block keeps the emitted YAML
    # readable AND matches Phase 2's golden spacing (ruamel preserves
    # blank-line whitespace it sees in the source text on round-trip).
    indented = "\n\n".join(textwrap.indent(b, "  ").rstrip() for b in service_blocks)
    raw = f"{header_text}{indented}\n\n{footer_text}"

    return _round_trip(raw)


def _render_services(
    env: Environment,
    config: ProjectConfig,
    catalog: Catalog | None,
) -> list[str]:
    """Render every service fragment to text, ready to indent and concat."""
    blocks: list[str] = []
    multi = config.is_multi_gateway

    bootstrap_tpl = env.get_template("services/bootstrap.yaml.j2")
    ignition_tpl = env.get_template("services/ignition.yaml.j2")

    for gw in config.gateways:
        ctx = _gateway_context(gw, config, catalog)
        blocks.append(bootstrap_tpl.render(**_bootstrap_context(ctx)))
        blocks.append(ignition_tpl.render(**_ignition_context(ctx, config, multi)))

    if config.database is not None:
        blocks.append(_render_database(config))

    for svc_name in _ordered_services(config):
        blocks.append(_render_catalog_service(svc_name, config))

    return blocks


def _render_database(config: ProjectConfig) -> str:
    """Render the database fragment from the service catalog (keyed by kind).

    The container name keeps the Phase-2 conventions for byte-stability:
    single-gateway uses ``db-${GATEWAY_NAME}`` (GATEWAY_NAME equals the project
    name there); multi-gateway uses ``db-${COMPOSE_PROJECT_NAME}``.
    """
    db = config.database
    assert db is not None
    if config.is_multi_gateway:
        container_name_ref = f"{db.name}-${{COMPOSE_PROJECT_NAME}}"
    else:
        container_name_ref = f"{db.name}-${{GATEWAY_NAME}}"
    tpl = _service_jinja_env().get_template(f"{db.kind}/compose.yaml.j2")
    return tpl.render(
        name=db.name,
        container_name_ref=container_name_ref,
        networks=[NETWORK_BACKEND] if config.network_split else [],
        extra_databases=db.extra_databases,
    )


def _render_catalog_service(svc_name: str, config: ProjectConfig) -> str:
    """Render one non-database catalog service from its compose fragment."""
    manifest = load_service(svc_name)
    tpl = _service_jinja_env().get_template(f"{svc_name}/compose.yaml.j2")
    networks = [manifest.network] if config.network_split else []
    return tpl.render(
        name=svc_name,
        image_ref=f"${{{manifest.image_env}}}",
        container_name_ref=f"{svc_name}-${{COMPOSE_PROJECT_NAME}}",
        networks=networks,
        depends_on=_service_dependencies(manifest, config),
        db_host=config.database.name if config.database else None,
        db_kind=config.database.kind if config.database else None,
    )


def _ordered_services(config: ProjectConfig) -> list[str]:
    """Selected non-database services in canonical (kind, name) order."""
    catalog = load_all_services()
    order = {kind: i for i, kind in enumerate(_SERVICE_KIND_ORDER)}

    def sort_key(name: str) -> tuple[int, str]:
        return (order.get(catalog[name].kind, len(order)), name)

    return sorted(config.services, key=sort_key)


def _service_dependencies(manifest: object, config: ProjectConfig) -> list[str]:
    """Compose service names this service depends on (each rendered healthy).

    A service's ``requires:`` capabilities map to the provider already present
    in the resolved config. Today the only requirable capability is a SQL
    database, so the dependency is the database service name when present.
    """
    requires = getattr(manifest, "requires", [])
    deps: list[str] = []
    db_caps = {"sql-database", "postgres-compatible", "mysql-compatible", "document-store"}
    if config.database is not None and any(cap in db_caps for cap in requires):
        deps.append(config.database.name)
    return deps


def _gateway_context(
    gw: GatewayConfig, config: ProjectConfig, catalog: Catalog | None
) -> dict[str, object]:
    """Build the per-gateway context dict shared by the bootstrap + gateway fragments."""
    multi = config.is_multi_gateway

    # Single-gateway keeps Phase 2 conventions (bootstrap, ignition-data,
    # services/ignition); multi-gateway switches to per-gateway names.
    if multi:
        service_name = gw.name
        bootstrap_service_name = f"bootstrap-{gw.name}"
        volume_name = f"{gw.name}-data"
        template_source_dir = gw.name
        gateway_name_ref = f"${{COMPOSE_PROJECT_NAME}}-{gw.name}"
        port_ref = f"${{{gw.env_prefix}_HTTP_PORT}}"
    else:
        service_name = "gateway"
        bootstrap_service_name = "bootstrap"
        volume_name = "ignition-data"
        template_source_dir = "ignition"
        gateway_name_ref = "${GATEWAY_NAME}"
        port_ref = "${GATEWAY_HTTP_PORT}"

    networks: list[str] = []
    if config.network_split:
        # A gateway always touches the frontend (UI/HTTP) plus the backend
        # (DB/broker access). Gateways with no role tag default to
        # frontend membership; explicit role=backend lands a gateway on
        # only the backend (rare; used for backend-only edge cases).
        if gw.role == "backend":
            networks = [NETWORK_BACKEND]
        else:
            networks = [NETWORK_FRONTEND, NETWORK_BACKEND]

    module_identifiers = _module_identifiers_for(gw, catalog)
    cached_modules = bool(gw.modules)

    return {
        "gw": gw,
        "service_name": service_name,
        "bootstrap_service_name": bootstrap_service_name,
        "volume_name": volume_name,
        "template_source_dir": template_source_dir,
        "gateway_name_ref": gateway_name_ref,
        "port_ref": port_ref,
        "networks": networks,
        "module_identifiers": module_identifiers,
        "cached_modules": cached_modules,
    }


def _bootstrap_context(ctx: dict[str, object]) -> dict[str, object]:
    # Bootstrap is a short-lived init container that only writes into the
    # gateway data volume. It does not need network access, so we omit
    # the networks block entirely; with network_split on, compose
    # attaches it to the auto-default network which is fine for a
    # service-completed_successfully exit gate.
    return {
        "bootstrap_service_name": ctx["bootstrap_service_name"],
        "gateway_name_ref": ctx["gateway_name_ref"],
        "volume_name": ctx["volume_name"],
        "template_source_dir": ctx["template_source_dir"],
        "networks": [],
        "cached_modules": ctx["cached_modules"],
    }


def _ignition_context(
    ctx: dict[str, object], config: ProjectConfig, multi: bool
) -> dict[str, object]:
    gw: GatewayConfig = ctx["gw"]  # type: ignore[assignment]
    # IGNITION_EDITION lives in the anchor as "standard", so only emit an
    # override when this gateway differs - keeps Phase 2's environment
    # block as the bare anchor reference.
    edition_override = gw.ignition_edition if gw.ignition_edition != "standard" else None

    # Redundancy wiring (Phase 4, per the verified Phase-3 spike):
    #  - Any node in a pair opens its incoming Gateway Network policy
    #    (Unrestricted + no-SSL) so the plain redundancy link auto-approves.
    #  - The backup additionally points a generic outgoing GAN connection at
    #    the master (HOST/PORT/ENABLESSL - all three, not just HOST, or it
    #    defaults to SSL:8060 and faults) and must NOT be renamed via -n: it
    #    adopts the master's system name on first sync.
    is_redundant = gw.redundancy is not None
    is_backup = is_redundant and gw.redundancy.mode == "backup"
    return {
        "service_name": ctx["service_name"],
        "bootstrap_service_name": ctx["bootstrap_service_name"],
        "gateway_name_ref": ctx["gateway_name_ref"],
        "port_ref": ctx["port_ref"],
        "volume_name": ctx["volume_name"],
        "memory_mb": gw.memory_mb,
        "edition_override": edition_override,
        "module_identifiers": ctx["module_identifiers"],
        # disable_active drives template emission (not the value's truthiness) so
        # that disabling EVERY built-in emits an empty whitelist - which quarantines
        # all, matching intent - instead of omitting the var and re-enabling all.
        "disable_active": bool(gw.disable_builtins),
        "modules_enabled": _modules_enabled_for(gw, ctx["module_identifiers"]),  # type: ignore[arg-type]
        "database_service": config.database.name if config.database else None,
        "networks": ctx["networks"],
        "redundant": is_redundant,
        "rename": not is_backup,
        "gan_peer_host": gw.redundancy.peer if is_backup else None,
        "gan_port": gw.redundancy.gan_port if is_backup else None,
    }


def _module_identifiers_for(gw: GatewayConfig, catalog: Catalog | None) -> str:
    """Comma-separated FQ module identifiers attached to this gateway, or ''."""
    if not gw.modules:
        return ""
    if catalog is None:
        raise ValueError(
            f"gateway '{gw.name}' lists modules {gw.modules} but no catalog "
            "was passed to render_compose; load modules.yaml first"
        )
    identifiers: list[str] = []
    for slug in gw.modules:
        try:
            entry = catalog.by_name(slug)
        except KeyError as exc:
            raise ValueError(
                f"gateway '{gw.name}' references unknown module '{slug}'; "
                "check modules.yaml and the gateway config"
            ) from exc
        # Modules-only env vars: JDBC drivers shouldn't be enumerated here.
        if not _is_module(entry):
            continue
        identifiers.append(entry.module_identifier)  # type: ignore[union-attr]
    return ",".join(identifiers)


def _modules_enabled_for(gw: GatewayConfig, module_identifiers: str) -> str:
    """The GATEWAY_MODULES_ENABLED whitelist VALUE for this gateway.

    GATEWAY_MODULES_ENABLED is a strict whitelist: if set, every built-in not
    listed is quarantined at boot. The template emits the var based on
    ``disable_active`` (whether any built-in was disabled), not on this value's
    truthiness - so disabling every built-in yields an empty string here and an
    empty whitelist downstream (quarantines all), rather than silently omitting
    the var and re-enabling everything.

    When something is disabled the whitelist must be complete: every built-in the
    user did not disable, PLUS any third-party modules we added
    (``module_identifiers``) - or those added modules would be quarantined too.
    Returns '' when nothing is disabled (the var is omitted in that case).
    """
    if not gw.disable_builtins:
        return ""
    enabled = default_builtin_catalog().identifiers_excluding(gw.disable_builtins)
    added = [ident for ident in module_identifiers.split(",") if ident]
    enabled.extend(ident for ident in added if ident not in enabled)
    return ",".join(enabled)


def _is_module(entry: ModuleEntry | object) -> bool:
    return getattr(entry, "kind", None) == "module"


def _volume_names(config: ProjectConfig) -> list[str]:
    if not config.is_multi_gateway:
        return ["ignition-data"]
    return [f"{g.name}-data" for g in config.gateways]


def _network_names(config: ProjectConfig) -> list[str]:
    if not config.network_split:
        return []
    return [NETWORK_FRONTEND, NETWORK_BACKEND]


def _wrap_description(description: str) -> list[str]:
    """Wrap the header comment at a width that matches Phase 2's golden.

    The first line carries the ``Generated by ignition-stack.`` prefix
    so we wrap with a 73-char target width that leaves room for the
    ``# `` comment marker.
    """
    prefix = "Generated by ignition-stack. "
    body = prefix + description
    wrapped = textwrap.wrap(body, width=73)
    return wrapped or [body]


def _describe(config: ProjectConfig) -> str:
    """Human-readable header comment summarizing the stack."""
    n = len(config.gateways)
    if n == 1 and config.database and config.database.kind == "postgres" and not config.services:
        return (
            "Walking skeleton: one Ignition 8.3 gateway, one Postgres, "
            "env-driven commissioning so first boot needs no UI."
        )
    parts = [f"{n} Ignition 8.3 gateway{'s' if n != 1 else ''}"]
    if config.database:
        parts.append(f"one {config.database.kind}")
    parts.extend(config.services)
    if config.network_split:
        parts.append("frontend/backend network split")
    return ", ".join(parts) + "."


def _jinja_env() -> Environment:
    return Environment(
        loader=PackageLoader("ignition_stack.compose", "templates"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        # Compose YAML uses {{ }}-style env interpolation throughout, but
        # those are literal ${...} expressions - Jinja2's default
        # delimiters don't collide. We still set autoescape off because
        # this is YAML, not HTML.
        autoescape=False,
    )


def _service_jinja_env() -> Environment:
    """Jinja env rooted at the service catalog (``templates/services/``).

    Template names are ``<service>/compose.yaml.j2``; the service catalog dir
    is named by slug for non-databases and by database kind for databases.
    """
    return Environment(
        loader=PackageLoader("ignition_stack.templates", "services"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def _round_trip(raw: str) -> str:
    """Parse + emit through ruamel.yaml so anchors and quotes are normalized.

    The emitter settings are tuned so Phase 2's golden round-trips
    byte-identical; see the docstring at the top of this module.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.explicit_start = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 200
    parsed = yaml.load(raw)
    out = io.StringIO()
    yaml.dump(parsed, out)
    return out.getvalue()
