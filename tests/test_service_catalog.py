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

import json
import os
from io import StringIO
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from ignition_stack.catalog.loader import load_catalog
from ignition_stack.compose.engine import render_compose
from ignition_stack.compose.writer import write_project
from ignition_stack.config.schema import DatabaseConfig, GatewayConfig, ProjectConfig
from ignition_stack.services.loader import load_all_services, load_service, service_dir
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
        pytest.fail(f"compose output diverges from golden '{rel_path}'.\n" "Run with UPDATE_GOLDENS=1 to update if the change is intentional.\n\n" f"{diff}")


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
    # Phase 1 (issue #43): resolve lowers the database into the registry and
    # clears the legacy field, so assert against the resolved registry instance.
    resolved = resolve(ProjectConfig(name="kc", database=None, services=["keycloak"]))
    assert resolved.database is None
    db = resolved.database_instance()
    assert db is not None
    assert db.id == "db"
    assert db.service == "postgres"
    assert "keycloak" in db.extra_databases


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
    resolved = resolve(ProjectConfig(name="kc", database=DatabaseConfig(kind="postgres"), services=["keycloak"]))
    dbs = [inst for inst in resolved.service_instances if inst.is_database]
    assert len(dbs) == 1
    assert dbs[0].service == "postgres"
    assert "keycloak" in dbs[0].extra_databases


def test_keycloak_with_mongo_coexists_with_an_auto_added_sql_database() -> None:
    """Mongo can't satisfy Keycloak's sql-database need, so a SQL DB is auto-added.

    Phase 2 relaxed the single-database rule to allow distinct kinds, so a Mongo
    historian and the Postgres that backs Keycloak now coexist (issue #43's
    heterogeneous model) instead of conflicting. The auto-added SQL database is a
    registry-level dependency: it carries Keycloak's logical schema but attaches
    to no gateway (the gateway uses Keycloak SSO, not the DB), while the Mongo it
    was handed stays the gateway's one database connection.
    """
    resolved = resolve(ProjectConfig(name="kc", database=DatabaseConfig(kind="mongo"), services=["keycloak"]))
    dbs = [inst for inst in resolved.service_instances if inst.is_database]
    assert sorted(inst.service for inst in dbs) == ["mongo", "postgres"]
    postgres = next(inst for inst in dbs if inst.service == "postgres")
    assert "keycloak" in postgres.extra_databases
    # The auto-added SQL database has no gateway attachment; the Mongo does.
    for gw in resolved.gateways:
        attached = {att.instance for att in gw.services}
        assert postgres.id not in attached
        assert any(att.instance == inst.id for inst in dbs if inst.service == "mongo" for att in gw.services)


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
        conn = tmp_path / "multi" / "services" / gw / "config" / "resources" / "core" / "ignition" / "database-connection" / "db" / "config.json"
        assert conn.is_file(), f"db-connection missing on gateway '{gw}'"


# --------------------------------------------------------------------------- #
# Keycloak OIDC seeding (Phase 5): IdP config + realm invariants
# --------------------------------------------------------------------------- #

_KC_IDP_REL = Path("config") / "resources" / "core" / "ignition" / "identity-provider" / "keycloak"
_KC_DEMO_SECRET = "ignition-oidc-demo-secret"


def test_keycloak_seeds_oidc_identity_provider_onto_gateway(tmp_path: Path) -> None:
    """Keycloak overlays a pre-seeded OIDC identity-provider into the gateway tree."""
    write_project(ProjectConfig(name="kc", services=["keycloak"]), tmp_path / "kc")
    idp = tmp_path / "kc" / "services" / "ignition" / _KC_IDP_REL
    assert (idp / "config.json").is_file()
    assert (idp / "resource.json").is_file()


def test_keycloak_idp_config_is_valid_oidc_with_embedded_secret(tmp_path: Path) -> None:
    """The seeded IdP config is valid JSON, type=oidc, and carries an embedded JWE secret.

    The client secret rides *inside* the IdP config as a JWE blob
    (``clientSecret.type=Embedded``), the same file-seeding the matrix verifies
    for db-connection. Keeping it embedded - not in the shared
    internal-secret-provider - is what avoids colliding with the Postgres
    db-password secret when a gateway attaches to both.
    """
    write_project(ProjectConfig(name="kc", services=["keycloak"]), tmp_path / "kc")
    config = json.loads((tmp_path / "kc" / "services" / "ignition" / _KC_IDP_REL / "config.json").read_text())

    assert config["profile"]["type"] == "oidc"
    settings = config["settings"]
    assert settings["clientId"] == "ignition-gateway"
    # Embedded JWE secret: a ciphertext blob, not a plaintext value.
    secret = settings["clientSecret"]
    assert secret["type"] == "Embedded"
    assert set(secret["data"]) >= {"ciphertext", "encrypted_key", "iv", "protected", "tag"}
    # RS256 must be permitted or the gateway rejects Keycloak's signed ID token.
    assert "RS256" in settings["allowedIdTokenSigningAlgorithms"]
    # Issuer + browser-facing endpoints stay on the published Keycloak host;
    # back-channel endpoints reach Keycloak over the Docker network.
    assert settings["providerId"] == "http://localhost:8081/realms/ignition"
    assert settings["authorizationEndpoint"].startswith("http://localhost:8081/")
    assert settings["tokenEndpoint"].startswith("http://keycloak:8080/")


def test_keycloak_does_not_clobber_postgres_secret(tmp_path: Path) -> None:
    """A gateway attached to both keeps the Postgres db-password secret intact.

    The Keycloak IdP secret is embedded in the IdP config, so the shared
    internal-secret-provider config.json that Postgres seeds is left untouched -
    no last-write-wins collision in the gateway-resources overlay.
    """
    write_project(ProjectConfig(name="both", services=["keycloak"]), tmp_path / "both")
    base = tmp_path / "both" / "services" / "ignition" / "config" / "resources" / "core" / "ignition"
    secret_provider = json.loads((base / "secret-provider" / "internal-secret-provider" / "config.json").read_text())
    secrets = secret_provider["settings"]["secrets"]
    # Postgres' db-password survives; Keycloak added no entry here.
    assert "db-password" in secrets
    assert _KC_DEMO_SECRET not in json.dumps(secret_provider)


def test_keycloak_realm_pins_secret_demo_user_and_admin_role() -> None:
    """The realm import carries the fixed client secret, a usable demo user, and the role."""
    realm_file = service_dir("keycloak") / "seed" / "service" / "import" / "ignition-realm.json"
    realm = json.loads(realm_file.read_text())

    client = next(c for c in realm["clients"] if c["clientId"] == "ignition-gateway")
    assert client["secret"] == _KC_DEMO_SECRET
    assert client["publicClient"] is False

    role_names = {r["name"] for r in realm["roles"]["realm"]}
    assert "ignition-admin" in role_names

    demo = next(u for u in realm["users"] if u["username"] == "demo")
    assert "ignition-admin" in demo["realmRoles"]
    pw = next(c for c in demo["credentials"] if c["type"] == "password")
    assert pw["value"] == "demo" and pw["temporary"] is False


# --------------------------------------------------------------------------- #
# Goldens: one minimal per service + key combinations
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("svc", NON_DB_SERVICES)
def test_service_golden(svc: str) -> None:
    _check_or_update_golden(
        f"services/{svc}/docker-compose.yaml",
        _render(ProjectConfig(name=svc, database=None, services=[svc])),
    )


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
