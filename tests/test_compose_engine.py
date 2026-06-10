"""Phase 4 acceptance tests for the compose generation engine.

The three validation criteria from 03-plan.md Phase 4:

1. Given a fixed multi-gateway config object, the engine emits a
   docker-compose.yaml that re-parses as valid YAML, preserves the
   x-ignition-common / x-ignition-environment anchors, and matches a
   golden snapshot.
2. With network_split=True, DB/broker services land only on backend
   and the Ignition services land on frontend (plus backend so they
   can reach the DB); with the default, all services share one bridge.
3. Two gateways with different per-role env overrides emit distinct
   merged env blocks (frontend gets IGNITION_EDITION=edge when
   configured, backend does not).

Set ``UPDATE_GOLDENS=1`` in the env to regenerate every golden snapshot
this file checks. Use sparingly - drift in goldens is exactly what
they exist to catch.
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from ignition_stack.catalog.loader import load_catalog
from ignition_stack.compose.engine import render_compose
from ignition_stack.config.schema import DatabaseConfig, GatewayConfig, ProjectConfig
from ignition_stack.services.resolver import resolve

GOLDEN_DIR = Path(__file__).parent / "golden"


def _scaleout_config() -> ProjectConfig:
    """Fixed multi-gateway config exercised by every Phase 4 acceptance test.

    Resolved because the engine renders from the resolved service registry
    (issue #43 Phase 1): the legacy ``database``/``services`` fields are lowered
    into ``service_instances`` by :func:`resolve`, which the engine then reads.
    """
    return resolve(
        ProjectConfig(
            name="scaleout",
            network_split=True,
            gateways=[
                GatewayConfig(
                    name="frontend",
                    role="frontend",
                    ignition_edition="edge",
                    http_port=9088,
                    modules=["mqtt-engine"],
                ),
                GatewayConfig(name="backend", http_port=9089),
            ],
        )
    )


def _check_or_update_golden(rel_path: str, actual: str) -> None:
    """Write the golden when UPDATE_GOLDENS=1 is set, otherwise assert equality."""
    golden = GOLDEN_DIR / rel_path
    if os.environ.get("UPDATE_GOLDENS") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(actual, encoding="utf-8")
        return
    assert golden.exists(), f"missing golden {golden}; run with UPDATE_GOLDENS=1 to create"
    expected = golden.read_text(encoding="utf-8")
    if expected != actual:
        # Surface a small diff snippet so failures are debuggable from CI logs
        # without exfiltrating the entire file every time.
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


def _parse_yaml(text: str) -> object:
    """Round-trip parse so we can assert structure invariants."""
    yaml = YAML()
    return yaml.load(StringIO(text))


def test_standalone_default_matches_phase2_golden() -> None:
    """The Phase 2 walking skeleton must remain byte-stable through the engine."""
    rendered = render_compose(resolve(ProjectConfig(name="demo")))
    _check_or_update_golden("standalone-postgres/docker-compose.yaml", rendered)


def test_scaleout_matches_golden() -> None:
    """Validation 1: multi-gateway config matches its golden snapshot."""
    rendered = render_compose(_scaleout_config(), catalog=load_catalog())
    _check_or_update_golden("scaleout-skeleton/docker-compose.yaml", rendered)


def test_output_is_valid_yaml() -> None:
    """Validation 1: re-parsing the rendered output as YAML succeeds."""
    rendered = render_compose(_scaleout_config(), catalog=load_catalog())
    parsed = _parse_yaml(rendered)
    assert isinstance(parsed, dict)
    assert "services" in parsed
    assert "x-ignition-common" in parsed
    assert "x-ignition-environment" in parsed


def test_anchors_present_in_output_text() -> None:
    """Validation 1: anchor definitions AND references survive in the literal text.

    Checking the text (not just parsed semantics) is what proves ruamel
    didn't collapse the anchors into inlined dicts.
    """
    rendered = render_compose(_scaleout_config(), catalog=load_catalog())
    assert "&ignition-common" in rendered
    assert "&ignition-environment" in rendered
    assert "&ignition-image" in rendered
    assert "*ignition-common" in rendered
    assert "*ignition-environment" in rendered
    assert "*ignition-image" in rendered


def test_network_split_places_db_only_on_backend() -> None:
    """Validation 2: DB lands on backend only when network_split is on."""
    rendered = render_compose(_scaleout_config(), catalog=load_catalog())
    parsed = _parse_yaml(rendered)
    db_networks = parsed["services"]["db"]["networks"]
    assert list(db_networks) == ["backend"]


def test_network_split_places_gateways_on_both_networks() -> None:
    """Validation 2: gateways need frontend (UI plane) AND backend (DB access)."""
    rendered = render_compose(_scaleout_config(), catalog=load_catalog())
    parsed = _parse_yaml(rendered)
    for service_name in ("frontend", "backend"):
        gw_networks = parsed["services"][service_name]["networks"]
        assert set(gw_networks) == {
            "frontend",
            "backend",
        }, f"gateway '{service_name}' must be on both networks"


def test_network_split_declares_both_networks_at_top_level() -> None:
    """Validation 2: top-level networks block has both names with bridge driver."""
    rendered = render_compose(_scaleout_config(), catalog=load_catalog())
    parsed = _parse_yaml(rendered)
    assert set(parsed["networks"].keys()) == {"frontend", "backend"}
    for name, spec in parsed["networks"].items():
        assert spec["driver"] == "bridge", f"network '{name}' should be bridge"


def test_default_topology_has_no_per_service_networks() -> None:
    """Validation 2: with the default (no split), services share the implicit bridge."""
    rendered = render_compose(ProjectConfig(name="demo"))
    parsed = _parse_yaml(rendered)
    assert "networks" not in parsed, "default topology should not declare networks"
    for service in parsed["services"].values():
        assert "networks" not in service, "default topology must not write per-service networks"


def test_frontend_gets_edition_override_backend_does_not() -> None:
    """Validation 3: per-role env overrides produce distinct merged env blocks."""
    rendered = render_compose(_scaleout_config(), catalog=load_catalog())
    parsed = _parse_yaml(rendered)

    # The frontend gateway's environment has IGNITION_EDITION explicitly
    # set to 'edge'; ruamel exposes the merge-key value plus overrides
    # so we walk the rendered text directly to confirm the literal key
    # is present on frontend but absent on backend.
    front_env = parsed["services"]["frontend"]["environment"]
    back_env = parsed["services"]["backend"]["environment"]

    # The merge-key value (the *ignition-environment alias) supplies
    # IGNITION_EDITION: standard. ruamel resolves the merge so the
    # mapping holds 'IGNITION_EDITION' either way; the difference is
    # whether the *explicit* key was set on the gateway.
    assert front_env["IGNITION_EDITION"] == "edge"
    # The backend gateway didn't override IGNITION_EDITION, so the
    # merged value is the anchor default 'standard'.
    assert back_env["IGNITION_EDITION"] == "standard"


def test_module_env_vars_only_emitted_on_gateways_with_modules() -> None:
    """Modules attached to a gateway add the two ACCEPT_MODULE_* vars.

    Resolved q-module-install (Phase-1 matrix): place the .modl and set
    ACCEPT_MODULE_LICENSES + ACCEPT_MODULE_CERTS to its identifier. We do
    NOT emit GATEWAY_MODULES_ENABLED - the matrix found it is a strict
    whitelist that quarantines the built-in IA modules (OPC-UA, SQL
    Historian) and breaks any stack that pairs a third-party module with a
    built-in subsystem (e.g. an MQTT broker module + OPC-UA).
    """
    rendered = render_compose(_scaleout_config(), catalog=load_catalog())
    parsed = _parse_yaml(rendered)

    front_env = parsed["services"]["frontend"]["environment"]
    back_env = parsed["services"]["backend"]["environment"]

    expected_id = "com.cirruslink.mqtt.engine.gateway"
    assert front_env["ACCEPT_MODULE_LICENSES"] == expected_id
    assert front_env["ACCEPT_MODULE_CERTS"] == expected_id
    # The whitelist var must never be emitted - it quarantines built-ins.
    assert "GATEWAY_MODULES_ENABLED" not in front_env

    # Backend has no modules attached -> none of the module env vars
    # should land on its environment.
    assert "GATEWAY_MODULES_ENABLED" not in back_env
    assert "ACCEPT_MODULE_LICENSES" not in back_env
    assert "ACCEPT_MODULE_CERTS" not in back_env


def test_unknown_module_slug_is_rejected() -> None:
    """A gateway referencing a module the catalog doesn't know about is a config bug."""
    config = ProjectConfig(
        name="bad",
        gateways=[GatewayConfig(modules=["does-not-exist"])],
    )
    with pytest.raises(ValueError, match="unknown module"):
        render_compose(config, catalog=load_catalog())


def test_modules_without_catalog_is_rejected() -> None:
    """Forgetting to pass the catalog when a gateway lists modules surfaces clearly."""
    config = ProjectConfig(
        name="oops",
        gateways=[GatewayConfig(modules=["mqtt-engine"])],
    )
    with pytest.raises(ValueError, match="no catalog"):
        render_compose(config)


def test_multi_gateway_emits_per_gateway_env_keys_in_writer() -> None:
    """The .env writer emits one HTTP-port key per gateway when there are 2+."""
    from ignition_stack.compose.writer import _render_env

    env = _render_env(_scaleout_config())
    assert "GATEWAY_FRONTEND_HTTP_PORT=9088" in env
    assert "GATEWAY_BACKEND_HTTP_PORT=9089" in env
    # Single-shared GATEWAY_HTTP_PORT is the single-gateway shape; it
    # must not appear when there are multiple gateways.
    assert "GATEWAY_HTTP_PORT=" not in env


def test_single_gateway_env_keeps_phase2_shape() -> None:
    """The .env writer keeps Phase 2's GATEWAY_NAME / GATEWAY_HTTP_PORT pair."""
    from ignition_stack.compose.writer import _render_env

    env = _render_env(ProjectConfig(name="demo"))
    assert "GATEWAY_NAME=demo" in env
    assert "GATEWAY_HTTP_PORT=9088" in env
    # Multi-gateway-style suffixed key must not appear for single-gateway.
    assert "GATEWAY_FRONTEND_HTTP_PORT" not in env


def test_gateway_without_database_skips_db_service() -> None:
    """A gateway-only stack (no DB) should not emit a db service or depends_on."""
    config = ProjectConfig(name="solo", database=None)
    rendered = render_compose(config)
    parsed = _parse_yaml(rendered)
    assert "db" not in parsed["services"]
    # The gateway's depends_on must not reference the missing db service.
    deps = parsed["services"]["gateway"].get("depends_on", {})
    assert "db" not in deps


def test_duplicate_gateway_names_rejected() -> None:
    """Two gateways with the same name would collide in compose service keys."""
    with pytest.raises(ValueError, match="unique"):
        ProjectConfig(
            name="bad",
            gateways=[GatewayConfig(name="frontend"), GatewayConfig(name="frontend")],
        )


def test_writer_creates_modules_cache_when_any_gateway_has_modules(
    tmp_path: Path,
) -> None:
    """The bootstrap binds-mounts modules/cache; the dir must exist before `up`."""
    from ignition_stack.compose.writer import write_project

    config = ProjectConfig(
        name="with-modules",
        gateways=[GatewayConfig(modules=["mqtt-engine"])],
    )
    write_project(config, tmp_path / "with-modules")
    cache_dir = tmp_path / "with-modules" / "modules" / "cache"
    assert cache_dir.is_dir(), "modules/cache/ must be created when any gateway has modules"


def test_writer_skips_modules_cache_when_no_gateway_has_modules(tmp_path: Path) -> None:
    """Module-free stacks should not create the modules/cache scaffolding."""
    from ignition_stack.compose.writer import write_project

    write_project(ProjectConfig(name="plain"), tmp_path / "plain")
    assert not (tmp_path / "plain" / "modules").exists()


def test_bootstrap_script_drops_cached_modules_into_user_lib() -> None:
    """The bootstrap script copies /modules-cache/*.modl into the gateway volume.

    Without this, the GATEWAY_MODULES_ENABLED env var references modules
    Ignition can't find on the filesystem. The two halves are paired by
    the resolved q-module-install finding.
    """
    from pathlib import Path as _P

    # Read the vendored bootstrap script directly so we assert against the
    # source-of-truth artifact, not an indirect rendering.
    script = _P("ignition_stack/templates/standalone-postgres/scripts/docker-bootstrap.sh").read_text(encoding="utf-8")
    assert "/modules-cache" in script, "bootstrap must check for /modules-cache mount"
    assert "user-lib/modules" in script, "bootstrap must drop modules into user-lib/modules"


def test_unsupported_database_kind_rejected() -> None:
    """The four catalog databases are valid; anything else is a config error."""
    for kind in ("postgres", "mysql", "mariadb", "mongo"):
        assert DatabaseConfig(kind=kind).kind == kind
    with pytest.raises(ValueError, match="unsupported database kind"):
        DatabaseConfig(kind="oracle")
