"""Project tree writer.

The writer composes a generated project at ``<output>/<name>/`` by:

1. Copying the static asset tree (``scripts/``, ``services/<dir>/``)
   from the package's template payload, parametrised per-gateway when
   the project has more than one.
2. Rendering ``docker-compose.yaml`` through
   :func:`ignition_stack.compose.engine.render_compose`, which preserves
   YAML anchors via ruamel.yaml.
3. Rendering ``.env`` from the resolved :class:`ProjectConfig`,
   including per-gateway HTTP port entries when the project is
   multi-gateway.

Every file is written in binary with explicit ``\\n`` newlines so the
output is byte-identical on Linux, macOS, and Windows.
``docker-bootstrap.sh`` in particular must be LF-only or bash inside
the container chokes on CR bytes.
"""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from ignition_stack.catalog.loader import CatalogLoadError, load_catalog
from ignition_stack.catalog.schema import Catalog
from ignition_stack.compose.engine import render_compose
from ignition_stack.config.schema import ProjectConfig, ServiceInstance
from ignition_stack.lifecycle.record import write_record
from ignition_stack.postsetup import generate_post_setup
from ignition_stack.services.loader import load_all_services, service_dir
from ignition_stack.services.resolver import resolve

_STATIC_PACKAGE = "ignition_stack.templates"
_STATIC_PROFILE = "standalone-postgres"


def write_project(
    config: ProjectConfig,
    target_dir: Path,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Generate the project tree at ``target_dir``.

    The config is resolved first (implicit dependencies expanded - see
    :func:`ignition_stack.services.resolver.resolve`) so the compose output and
    the on-disk seeds agree on the same fully-expanded stack.

    Every project records its resolved config under ``.ignition-stack/`` so
    ``reset`` / ``switch-profile`` can regenerate or reshape it in place; the
    same artifact can be dumped with ``init --dry-run`` and rebuilt with
    ``init -f``.

    ``overwrite`` lets ``reset`` / ``switch-profile`` write into a directory
    that still holds the preserved ``.ignition-stack/`` record; normal ``init``
    leaves it ``False`` so a stray non-empty directory still refuses to clobber.

    Returns the list of files written (absolute paths), in the order they
    were written. Raises :class:`FileExistsError` if ``target_dir`` already
    has files and ``overwrite`` is ``False``.
    """
    target_dir = Path(target_dir).resolve()
    if not overwrite and target_dir.exists() and any(target_dir.iterdir()):
        raise FileExistsError(f"target directory '{target_dir}' is not empty; refusing to overwrite")
    target_dir.mkdir(parents=True, exist_ok=True)

    config = resolve(config)

    written: list[Path] = []

    written.extend(_copy_static_tree(config, target_dir))
    written.extend(_copy_service_seeds(config, target_dir))
    written.extend(_overlay_gateway_resources(config, target_dir))
    written.extend(_write_redundancy_seeds(config, target_dir))
    _ensure_modules_cache_dir(config, target_dir)
    written.append(_write_compose(config, target_dir))
    written.append(_write_env(config, target_dir))
    written.append(_write_makefile(config, target_dir))

    proxy_file = _write_reverse_proxy_readme(config, target_dir)
    if proxy_file is not None:
        written.append(proxy_file)
    dropin_file = _write_mcp_dropin_readme(config, target_dir)
    if dropin_file is not None:
        written.append(dropin_file)
    written.append(_write_post_setup(config, target_dir))
    written.append(write_record(config, target_dir))

    return written


def _copy_service_seeds(config: ProjectConfig, target_dir: Path) -> list[Path]:
    """Copy each service's ``seed/service/`` tree into ``services/<name>/``.

    These are files mounted into the service's own container (a Postgres
    initdb script, a broker config, a realm export). The destination dir is
    the database name for the DB and the service slug for everyone else - the
    same names the compose fragments mount from.
    """
    written: list[Path] = []
    for src_dir, inst in _seed_sources(config):
        seed_service = src_dir / "seed" / "service"
        if not seed_service.is_dir():
            continue
        for rel, content, executable in _walk_template(seed_service):
            written.append(_write_static(target_dir, f"services/{inst.id}/{rel}", content, executable))
    return written


def _overlay_gateway_resources(config: ProjectConfig, target_dir: Path) -> list[Path]:
    """Overlay each seeding service's ``seed/gateway-resources/`` onto gateways.

    Per the Phase-1 matrix, file-seedable connections (db-connection, the
    internal-secret-provider that holds its password, ...) are dropped into the
    ``config/resources/`` tree of the gateways that **attach** to the service,
    which the bootstrap copies into the gateway data volume on first boot.
    Attachment-driven: a gateway only receives a service's resources if it has a
    :class:`ServiceAttachment` to that instance. Lowered configs attach every
    gateway to the shared services, so the same resource set still lands on
    every gateway - byte-identical to the pre-registry fan-to-all.
    """
    written: list[Path] = []
    for src_dir, inst in _seed_sources(config):
        resources_tree = src_dir / "seed" / "gateway-resources"
        if not resources_tree.is_dir():
            continue
        for rel, content, executable in _walk_template(resources_tree):
            for gw_dir in _attached_gateway_dirs(config, inst.id):
                written.append(_write_static(target_dir, f"services/{gw_dir}/{rel}", content, executable))
    return written


def _attached_gateway_dirs(config: ProjectConfig, instance_id: str) -> list[str]:
    """Template-source dir names of gateways attached to ``instance_id``.

    Single-gateway stacks keep the Phase-2 ``services/ignition/`` layout (the
    one gateway's dir is always ``ignition`` regardless of its name); multi
    gateway stacks use each attached gateway's own name.
    """
    attached = [gw for gw in config.gateways if any(a.instance == instance_id for a in gw.services)]
    if not config.is_multi_gateway:
        return ["ignition"] if attached else []
    return [gw.name for gw in attached]


def _write_redundancy_seeds(config: ProjectConfig, target_dir: Path) -> list[Path]:
    """Drop a per-node ``redundancy.xml`` into each redundant gateway's tree.

    Per the Phase-3 spike, nothing sets the redundancy *role* via env var, so a
    pre-seeded ``data/redundancy.xml`` is what makes a node a master or backup.
    The file lands at ``services/<gateway>/redundancy.xml`` (the gateway's
    template-source root); the bootstrap copies it to the data-volume root on
    first boot. Master and backup differ only in ``noderole`` and ``gan.host``;
    the backup points its host at the master's service name. Generated stacks
    use the plain (non-SSL) link on port 8088, which auto-approves without the
    certificate handshake the SSL path (8060) would force.
    """
    env = _redundancy_jinja_env()
    template = env.get_template("redundancy.xml.j2")
    written: list[Path] = []
    for gw in config.gateways:
        red = gw.redundancy
        if red is None or not red.seed_redundancy_xml:
            continue
        is_master = red.mode == "master"
        rendered = template.render(
            noderole="Master" if is_master else "Backup",
            # The master listens; the backup connects to the master's service
            # name. An empty host on the master matches the verified seed file.
            gan_host="" if is_master else red.peer,
            gan_port=red.gan_port,
            enable_ssl="true" if red.gan_port == 8060 else "false",
        )
        rel = f"services/{gw.name}/redundancy.xml"
        written.append(_write_static(target_dir, rel, rendered.encode(), False))
    return written


def _redundancy_jinja_env() -> Environment:
    return Environment(
        loader=PackageLoader("ignition_stack.templates", "redundancy"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )


def _seed_sources(config: ProjectConfig) -> list[tuple[Traversable, ServiceInstance]]:
    """(service-catalog dir, registry instance) for every instance in the stack.

    Only services whose manifest sets ``seeds_gateway_resources`` contribute
    gateway resources, but ``seed/service/`` is copied for any service that
    ships one regardless. The catalog dir is keyed by the instance's service
    slug (db kind for databases, slug for everyone else).
    """
    return [(service_dir(inst.service), inst) for inst in config.service_instances]


def _ensure_modules_cache_dir(config: ProjectConfig, target_dir: Path) -> None:
    """Create an empty modules/cache/ when any gateway lists modules.

    The compose engine emits a bind mount of this directory into every
    gateway's bootstrap container; absent the directory, `docker compose
    up` fails before the bootstrap runs. The directory stays empty here
    - `ignition-stack modules download` (Phase 3) populates it.
    """
    if not any(gw.modules for gw in config.gateways):
        return
    (target_dir / "modules" / "cache").mkdir(parents=True, exist_ok=True)


def _copy_static_tree(config: ProjectConfig, target_dir: Path) -> list[Path]:
    """Copy the static portion of the project tree.

    For a single-gateway project the scripts + services/ignition layout
    is copied verbatim (Phase 2 walking skeleton). For multi-gateway
    projects the services subtree is duplicated once per gateway under
    ``services/<gateway-name>/`` so each bootstrap container can mount
    its own template-source.
    """
    written: list[Path] = []
    static_root = _static_root()

    for src_rel, content, executable in _walk_template(static_root):
        if src_rel == "docker-compose.yaml":
            # The compose file is rendered separately by the engine; the
            # bundled copy is only there to seed the byte-identical Phase
            # 2 golden during development.
            continue
        if src_rel.startswith("services/"):
            if config.is_multi_gateway:
                for gw in config.gateways:
                    fan_rel = src_rel.replace("services/ignition/", f"services/{gw.name}/", 1)
                    written.append(_write_static(target_dir, fan_rel, content, executable))
            else:
                written.append(_write_static(target_dir, src_rel, content, executable))
        else:
            written.append(_write_static(target_dir, src_rel, content, executable))

    return written


def _write_static(target_dir: Path, rel: str, content: bytes, executable: bool) -> Path:
    dst = target_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(content)
    if executable:
        dst.chmod(0o755)
    return dst


def _write_compose(config: ProjectConfig, target_dir: Path) -> Path:
    catalog = _maybe_load_catalog(config)
    rendered = render_compose(config, catalog=catalog)
    dst = target_dir / "docker-compose.yaml"
    dst.write_bytes(rendered.encode("utf-8"))
    return dst


def _maybe_load_catalog(config: ProjectConfig) -> Catalog | None:
    """Load the bundled catalog only when a gateway actually needs it.

    Saves a parse + I/O for the common module-free single-gateway case
    and keeps test fixtures from needing modules.yaml to be reachable.
    """
    if not any(gw.modules for gw in config.gateways):
        return None
    try:
        return load_catalog()
    except CatalogLoadError as exc:
        raise RuntimeError("modules referenced in project config but modules.yaml could not be loaded") from exc


def _write_env(config: ProjectConfig, target_dir: Path) -> Path:
    env_path = target_dir / ".env"
    env_path.write_bytes(_render_env(config).encode("utf-8"))
    return env_path


def _render_env(config: ProjectConfig) -> str:
    """Render the project .env. Always LF-terminated, no Windows surprises.

    Data-driven: the fixed gateway keys come first, then the database block,
    then each catalog service contributes its image key and preset credentials
    from its manifest ``env`` map, so adding a service needs no writer change.
    """
    lines = [
        "# Generated by ignition-stack. Edit values, do not delete keys.",
        f"COMPOSE_PROJECT_NAME={config.name}",
    ]
    if not config.is_multi_gateway:
        # Phase 2 walking-skeleton compatibility: a single shared
        # GATEWAY_NAME / GATEWAY_HTTP_PORT pair the bootstrap + gateway
        # services both reference.
        gw = config.gateways[0]
        lines += [
            f"GATEWAY_NAME={config.name}",
            f"GATEWAY_HTTP_PORT={gw.http_port}",
        ]
    else:
        for gw in config.gateways:
            lines.append(f"{gw.env_prefix}_HTTP_PORT={gw.http_port}")

    db = config.database_instance()
    lines.append(f"IGNITION_IMAGE={config.ignition_image}")
    # Each database instance emits its own per-kind <KIND>_IMAGE key (registry
    # order); a single-database stack emits exactly one, keeping the historical
    # output. The shared DB_USER/DB_PASSWORD/DB_HOST keys below come from the
    # primary (first) database - all databases share one credential pair.
    for inst in config.database_instances():
        lines.append(f"{inst.image_env}={inst.image}")
    lines += [
        f"ADMIN_USERNAME={config.admin_username}",
        f"ADMIN_PASSWORD={config.admin_password}",
    ]
    if db is not None:
        lines += [
            f"DB_USER={db.user}",
            f"DB_PASSWORD={db.password}",
            f"DB_HOST={db.id}",
        ]
    # EXTRA_DATABASES belongs to whichever SQL instance carries extras (only
    # the resolver's keycloak pass sets them), which is not necessarily the
    # primary: in a heterogeneous stack a Mongo store can sit ahead of the
    # auto-added SQL database Keycloak wires to. Its compose fragment
    # references ${EXTRA_DATABASES}, so the key must exist whenever any SQL
    # instance has extras. At most one instance can: extras are keycloak-only
    # and keycloak targets exactly one SQL database.
    extras_db = next(
        (inst for inst in config.database_instances() if inst.extra_databases and inst.service in {"postgres", "mysql", "mariadb"}),
        None,
    )
    if extras_db is not None:
        lines.append(f"EXTRA_DATABASES={','.join(extras_db.extra_databases)}")

    catalog = load_all_services()
    for inst in sorted(config.non_database_instances(), key=lambda i: i.id):
        manifest = catalog[inst.service]
        lines.append(f"{manifest.image_env}={inst.image}")
        for key, value in manifest.env.items():
            lines.append(f"{key}={inst.env.get(key, value)}")

    lines.append(f"TZ={config.timezone}")
    return "\n".join(lines) + "\n"


def _static_root() -> Traversable:
    return resources.files(_STATIC_PACKAGE) / _STATIC_PROFILE


_TRAEFIK_README = """\
# Reverse proxy: ia-eknorr/traefik-reverse-proxy

This project's wizard offered to install the preferred Traefik reverse
proxy here. The repo lives at https://github.com/ia-eknorr/traefik-reverse-proxy
and is **not** cloned automatically (the CLI never bundles a proxy
silently). Install it manually:

```sh
cd {path}
git clone https://github.com/ia-eknorr/traefik-reverse-proxy.git .
```

Then read that repo's README for routing and TLS setup. The gateway is
already exposed on a host port via `docker-compose.yaml`; the proxy can
either replace that mapping or sit in front of it.
"""

_MCP_DROPIN_README = """\
# MCP module drop-in

The Ignition MCP module is Early-Access and gated behind a survey, so
this CLI cannot bundle it. To enable the MCP service in this stack:

1. Request the module from
   https://inductiveautomation.com/early-access (Ignition MCP).
2. Drop the resulting `.modl` file into this directory:
   `modules/dropin/<filename>.modl`.
3. Re-run `docker compose up -d`. The bootstrap will copy any `.modl`
   present here into the gateway's `user-lib/modules/` on startup.

n8n is already configured in this stack; the MCP module is what
exposes the Ignition side of the conversation to n8n's workflows.
"""


def _write_reverse_proxy_readme(config: ProjectConfig, target_dir: Path) -> Path | None:
    if config.reverse_proxy is None:
        return None
    proxy_dir = target_dir / config.reverse_proxy.path
    proxy_dir.mkdir(parents=True, exist_ok=True)
    dst = proxy_dir / "README.md"
    dst.write_bytes(_TRAEFIK_README.format(path=config.reverse_proxy.path).encode("utf-8"))
    return dst


def _write_mcp_dropin_readme(config: ProjectConfig, target_dir: Path) -> Path | None:
    if not config.mcp_dropin:
        return None
    dropin_dir = target_dir / "modules" / "dropin"
    dropin_dir.mkdir(parents=True, exist_ok=True)
    dst = dropin_dir / "README.md"
    dst.write_bytes(_MCP_DROPIN_README.encode("utf-8"))
    return dst


# `@@PROJECT@@` is substituted with the compose project name. A sentinel is
# used instead of str.format / f-strings because the help recipe contains
# literal awk braces that those would try to interpret. `down -v` is pinned to
# `-p $(PROJECT)` so `wipe` only ever removes this project's labelled resources.
_MAKEFILE = """\
# Generated by ignition-stack for the "@@PROJECT@@" stack.
# `make help` lists targets. These wrap docker compose so the everyday loop
# (up / down / logs) and the scoped teardown (wipe) are one word each.
COMPOSE := docker compose
PROJECT := @@PROJECT@@

.DEFAULT_GOAL := help
.PHONY: help up down logs wipe reset

help:  ## List available targets.
\t@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \\
\t\t| awk 'BEGIN {FS = ":.*?## "} {printf "  %-8s %s\\n", $$1, $$2}'

up:  ## Start the stack in the background.
\t$(COMPOSE) up -d

down:  ## Stop the stack, keeping volumes and data.
\t$(COMPOSE) down

logs:  ## Follow logs for every service.
\t$(COMPOSE) logs -f

wipe:  ## Remove ONLY this project's containers, networks, and volumes.
\t$(COMPOSE) -p $(PROJECT) down -v --remove-orphans

reset:  ## Regenerate this project from its recorded config.
\tignition-stack reset
"""


def _write_makefile(config: ProjectConfig, target_dir: Path) -> Path:
    """Write the project ``Makefile`` (up/down/logs/wipe/reset).

    ``wipe`` is scoped to the compose project name so it cannot reach unrelated
    Docker resources; that scoping is the Phase-7 cleanup contract.
    """
    body = _MAKEFILE.replace("@@PROJECT@@", config.name)
    dst = target_dir / "Makefile"
    dst.write_bytes(body.encode("utf-8"))
    return dst


def _write_post_setup(config: ProjectConfig, target_dir: Path) -> Path:
    """Write ``POST-SETUP.md`` from the matrix-driven generator.

    Always written: a fully-seedable stack gets a "no manual steps required"
    note, anything with a deferred connection gets one section per step. The
    generator owns the content (see :mod:`ignition_stack.postsetup`).
    """
    dst = target_dir / "POST-SETUP.md"
    dst.write_bytes(generate_post_setup(config).encode("utf-8"))
    return dst


def _walk_template(root: Traversable, prefix: str = "") -> list[tuple[str, bytes, bool]]:
    """Yield (relative-path, bytes, executable) for every file under ``root``.

    Skips Python package markers (``__init__.py``) and any pyc artifacts so
    the generated project doesn't contain Python plumbing.
    """
    out: list[tuple[str, bytes, bool]] = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        name = entry.name
        rel = f"{prefix}{name}"
        if entry.is_dir():
            out.extend(_walk_template(entry, prefix=f"{rel}/"))
            continue
        if name == "__init__.py" or name.endswith(".pyc"):
            continue
        # Shell scripts (the bootstrap, Postgres initdb hooks, ...) must be
        # executable so the container can run them directly.
        out.append((rel, entry.read_bytes(), rel.endswith(".sh")))
    return out
