"""Phase 5 acceptance tests for the service catalog.

Validation criteria from 03-plan.md Phase 5:

1. For each service, a golden test asserts its fragment emits with credentials
   sourced from .env and its seed directory is copied into the generated
   project.
2. Selecting Keycloak with no DB auto-adds a Postgres-compatible DB and a
   keycloak schema; selecting MySQL places the JDBC driver in user-lib/jdbc/.
3. (Phase-5 smoke, opt-in) a generated standalone+Postgres+HiveMQ+OPC-UA-sim
   stack boots and the file-seedable db-connection shows configured in the UI.
   Lives in ``test_service_catalog_smoke.py`` behind the ``smoke`` marker.

Set ``UPDATE_GOLDENS=1`` to regenerate the golden snapshots this file checks.
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from ignition_stack.catalog.loader import load_catalog
from ignition_stack.compose.engine import render_compose
from ignition_stack.compose.writer import write_project
from ignition_stack.config.schema import DatabaseConfig, GatewayConfig, ProjectConfig
from ignition_stack.services.loader import load_all_services, load_service
from ignition_stack.services.resolver import ResolveError, resolve

GOLDEN_DIR = Path(__file__).parent / "golden"

# Every service the Phase-5 catalog must ship.
EXPECTED_SERVICES = {
    "postgres",
    "mysql",
    "mariadb",
    "mongo",
    "keycloak",
    "hivemq",
    "emqx",
    "rabbitmq",
    "chariot",
    "opcua-sim",
    "modbus-sim",
    "kafka",
    "n8n",
}
NON_DB_SERVICES = sorted(s for s in EXPECTED_SERVICES if load_service(s).kind != "database")
DB_KINDS = ["postgres", "mysql", "mariadb", "mongo"]


def _parse(text: str) -> dict:
    return YAML().load(StringIO(text))


def _check_or_update_golden(rel_path: str, actual: str) -> None:
    golden = GOLDEN_DIR / rel_path
    if os.environ.get("UPDATE_GOLDENS") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8")
        return
    assert golden.exists(), f"missing golden {golden}; run with UPDATE_GOLDENS=1 to create"
    expected = golden.read_text(encoding="utf-8")
    if expected != actual:
        from difflib import unified_diff

        diff = "\n".join(
            unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile=f"golden:{rel_path}",
                tofile="rendered",
                lineterm="",
                n=2,
            )
        )
        pytest.fail(
            f"compose output diverges from golden '{rel_path}'.\n"
            "Run with UPDATE_GOLDENS=1 to update if the change is intentional.\n\n"
            f"{diff}"
        )


def _render(config: ProjectConfig) -> str:
    """Resolve then render, loading the modules catalog when modules appear."""
    resolved = resolve(config)
    needs_catalog = any(gw.modules for gw in resolved.gateways)
    return render_compose(resolved, catalog=load_catalog() if needs_catalog else None)


# --------------------------------------------------------------------------- #
# Catalog integrity
# --------------------------------------------------------------------------- #


def test_catalog_loads_every_expected_service() -> None:
    catalog = load_all_services()
    assert set(catalog) == EXPECTED_SERVICES


def test_database_defaults_agree_with_manifests() -> None:
    """The DatabaseConfig image/image_env table must match the DB manifests."""
    for kind in DB_KINDS:
        manifest = load_service(kind)
        db = DatabaseConfig(kind=kind)
        assert db.image == manifest.image, f"{kind} default image drifted from manifest"
        assert db.image_env == manifest.image_env, f"{kind} image_env drifted from manifest"


# --------------------------------------------------------------------------- #
# Per-service: fragment sources everything from .env; seed dir is copied
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("svc", NON_DB_SERVICES)
def test_service_fragment_sources_image_and_env_from_dotenv(svc: str) -> None:
    """Every manifest env key (and the image) is referenced as ${...} in the fragment."""
    manifest = load_service(svc)
    rendered = _render(ProjectConfig(name=svc.replace("-", "-"), database=None, services=[svc]))
    parsed = _parse(rendered)
    assert svc in parsed["services"], f"{svc} missing from rendered services"

    assert f"${{{manifest.image_env}}}" in rendered, f"{svc} image not sourced from .env"
    for key in manifest.env:
        assert f"${{{key}}}" in rendered, f"{svc} env key {key} not referenced as ${{{key}}}"


@pytest.mark.parametrize("svc", NON_DB_SERVICES)
def test_service_seed_directory_is_copied(svc: str, tmp_path: Path) -> None:
    """Selecting a service copies its seed/service tree into services/<svc>/."""
    write_project(ProjectConfig(name="seedcheck", database=None, services=[svc]), tmp_path / svc)
    seed_dir = tmp_path / svc / "services" / svc
    assert seed_dir.is_dir(), f"{svc} seed directory not copied"
    assert any(seed_dir.rglob("*")), f"{svc} seed directory is empty"


@pytest.mark.parametrize("kind", DB_KINDS)
def test_database_seed_directory_is_copied(kind: str, tmp_path: Path) -> None:
    """Each database ships at least one seed file copied into services/<db-name>/."""
    write_project(ProjectConfig(name=kind, database=DatabaseConfig(kind=kind)), tmp_path / kind)
    seed_dir = tmp_path / kind / "services" / "db"
    assert seed_dir.is_dir() and any(seed_dir.rglob("*")), f"{kind} seed not copied"


@pytest.mark.parametrize("svc", NON_DB_SERVICES)
def test_service_env_credentials_appear_in_dotenv(svc: str, tmp_path: Path) -> None:
    """Preset credentials land in .env with the service's manifest defaults."""
    manifest = load_service(svc)
    write_project(ProjectConfig(name="envcheck", database=None, services=[svc]), tmp_path / svc)
    env_text = (tmp_path / svc / ".env").read_text(encoding="utf-8")
    assert f"{manifest.image_env}={manifest.image}" in env_text
    for key, value in manifest.env.items():
        assert f"{key}={value}" in env_text, f"{svc}: {key}={value} missing from .env"


# --------------------------------------------------------------------------- #
# Dependency resolver
# --------------------------------------------------------------------------- #


def test_keycloak_without_database_adds_postgres_and_schema() -> None:
    resolved = resolve(ProjectConfig(name="kc", database=None, services=["keycloak"]))
    assert resolved.database is not None
    assert resolved.database.kind == "postgres"
    assert "keycloak" in resolved.database.extra_databases


def test_keycloak_compose_depends_on_the_database() -> None:
    rendered = _render(ProjectConfig(name="kc", database=None, services=["keycloak"]))
    parsed = _parse(rendered)
    assert "keycloak" in parsed["services"]
    assert "db" in parsed["services"]
    deps = parsed["services"]["keycloak"]["depends_on"]
    assert "db" in deps
    assert deps["db"]["condition"] == "service_healthy"


def test_keycloak_with_existing_postgres_reuses_it() -> None:
    """A Postgres already present satisfies Keycloak; no second database appears."""
    resolved = resolve(
        ProjectConfig(name="kc", database=DatabaseConfig(kind="postgres"), services=["keycloak"])
    )
    assert resolved.database.kind == "postgres"
    assert "keycloak" in resolved.database.extra_databases


def test_keycloak_with_mongo_only_is_a_conflict() -> None:
    """Mongo can't satisfy Keycloak's sql-database need and we allow only one DB."""
    with pytest.raises(ResolveError, match="needs a different database"):
        resolve(
            ProjectConfig(
                name="kc", database=DatabaseConfig(kind="mongo"), services=["keycloak"]
            )
        )


def test_mysql_attaches_jdbc_driver_to_every_gateway() -> None:
    resolved = resolve(
        ProjectConfig(
            name="my",
            database=DatabaseConfig(kind="mysql"),
            gateways=[GatewayConfig(name="a"), GatewayConfig(name="b")],
        )
    )
    for gw in resolved.gateways:
        assert "mysql-jdbc" in gw.modules


def test_mysql_jdbc_driver_targets_user_lib_jdbc() -> None:
    """The attached driver's install path is user-lib/jdbc/ (where the gateway looks)."""
    entry = load_catalog().by_name("mysql-jdbc")
    assert "user-lib/jdbc/" in entry.install_path
    assert entry.install_path.endswith(".jar")


def test_mysql_stack_creates_modules_cache_and_bootstrap_copies_jars(tmp_path: Path) -> None:
    write_project(ProjectConfig(name="my", database=DatabaseConfig(kind="mysql")), tmp_path / "my")
    assert (tmp_path / "my" / "modules" / "cache").is_dir()
    bootstrap = (tmp_path / "my" / "scripts" / "docker-bootstrap.sh").read_text(encoding="utf-8")
    assert "user-lib/jdbc" in bootstrap and "*.jar" in bootstrap


def test_unknown_service_is_rejected() -> None:
    with pytest.raises(ResolveError, match="unknown service"):
        resolve(ProjectConfig(name="x", services=["does-not-exist"]))


def test_database_in_services_list_is_rejected() -> None:
    with pytest.raises(ResolveError, match="is a database"):
        resolve(ProjectConfig(name="x", services=["postgres"]))


# --------------------------------------------------------------------------- #
# Gateway-resource seeding (the file-seedable db-connection)
# --------------------------------------------------------------------------- #


def test_postgres_seeds_db_connection_onto_gateway(tmp_path: Path) -> None:
    """Postgres overlays a pre-seeded db-connection + secret into the gateway tree."""
    write_project(ProjectConfig(name="pg"), tmp_path / "pg")
    base = tmp_path / "pg" / "services" / "ignition" / "config" / "resources" / "core" / "ignition"
    assert (base / "database-connection" / "db" / "config.json").is_file()
    assert (base / "secret-provider" / "internal-secret-provider" / "config.json").is_file()


def test_gateway_resources_overlay_every_gateway(tmp_path: Path) -> None:
    """In a multi-gateway stack the db-connection lands on each gateway's tree."""
    config = ProjectConfig(
        name="multi",
        gateways=[GatewayConfig(name="frontend"), GatewayConfig(name="backend")],
    )
    write_project(config, tmp_path / "multi")
    for gw in ("frontend", "backend"):
        conn = (
            tmp_path / "multi" / "services" / gw / "config" / "resources" / "core" / "ignition"
            / "database-connection" / "db" / "config.json"
        )
        assert conn.is_file(), f"db-connection missing on gateway '{gw}'"


# --------------------------------------------------------------------------- #
# Goldens: one minimal per service + key combinations
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("svc", NON_DB_SERVICES)
def test_service_golden(svc: str) -> None:
    _check_or_update_golden(f"services/{svc}/docker-compose.yaml", _render(
        ProjectConfig(name=svc, database=None, services=[svc])
    ))


@pytest.mark.parametrize("kind", DB_KINDS)
def test_database_golden(kind: str) -> None:
    _check_or_update_golden(
        f"services/db-{kind}/docker-compose.yaml",
        _render(ProjectConfig(name=kind, database=DatabaseConfig(kind=kind))),
    )


def test_combo_smoke_stack_golden() -> None:
    _check_or_update_golden(
        "combos/smoke-stack/docker-compose.yaml",
        _render(ProjectConfig(name="smoke", services=["hivemq", "opcua-sim"])),
    )


def test_combo_network_split_golden() -> None:
    _check_or_update_golden(
        "combos/network-split/docker-compose.yaml",
        _render(
            ProjectConfig(
                name="split",
                network_split=True,
                services=["hivemq", "keycloak", "n8n"],
            )
        ),
    )
