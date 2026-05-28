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

from ignition_stack.catalog.loader import CatalogLoadError, load_catalog
from ignition_stack.catalog.schema import Catalog
from ignition_stack.compose.engine import render_compose
from ignition_stack.config.schema import ProjectConfig

_STATIC_PACKAGE = "ignition_stack.templates"
_STATIC_PROFILE = "standalone-postgres"


def write_project(config: ProjectConfig, target_dir: Path) -> list[Path]:
    """Generate the project tree at ``target_dir``.

    Returns the list of files written (absolute paths), in the order they
    were written. Raises :class:`FileExistsError` if ``target_dir`` already
    has files (refuses to clobber).
    """
    target_dir = Path(target_dir).resolve()
    if target_dir.exists() and any(target_dir.iterdir()):
        raise FileExistsError(
            f"target directory '{target_dir}' is not empty; refusing to overwrite"
        )
    target_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    written.extend(_copy_static_tree(config, target_dir))
    _ensure_modules_cache_dir(config, target_dir)
    written.append(_write_compose(config, target_dir))
    written.append(_write_env(config, target_dir))

    return written


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
        raise RuntimeError(
            "modules referenced in project config but modules.yaml could not be loaded"
        ) from exc


def _write_env(config: ProjectConfig, target_dir: Path) -> Path:
    env_path = target_dir / ".env"
    env_path.write_bytes(_render_env(config).encode("utf-8"))
    return env_path


def _render_env(config: ProjectConfig) -> str:
    """Render the project .env. Always LF-terminated, no Windows surprises."""
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

    lines += [
        f"IGNITION_IMAGE={config.ignition_image}",
    ]
    if config.database is not None:
        lines.append(f"POSTGRES_IMAGE={config.database.image}")
    lines += [
        f"ADMIN_USERNAME={config.admin_username}",
        f"ADMIN_PASSWORD={config.admin_password}",
    ]
    if config.database is not None:
        lines += [
            f"DB_USER={config.database.user}",
            f"DB_PASSWORD={config.database.password}",
        ]
    lines.append(f"TZ={config.timezone}")
    return "\n".join(lines) + "\n"


def _static_root() -> Traversable:
    return resources.files(_STATIC_PACKAGE) / _STATIC_PROFILE


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
        out.append((rel, entry.read_bytes(), rel.endswith("docker-bootstrap.sh")))
    return out
