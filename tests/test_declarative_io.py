"""Acceptance tests for the declarative dump/build path.

``init --dry-run`` dumps the resolved config and writes nothing; ``init -f``
rebuilds from that dump. The two are a closed loop: a project built from a
profile and one built from the profile's dumped config must be byte-identical.
These tests pin that loop, the validation error path, and the mutual-exclusion
and idempotency guarantees the loop relies on.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ignition_stack.cli import app
from ignition_stack.compose import write_project
from ignition_stack.config import dump_config, load_config
from ignition_stack.profiles import ProfileOptions, build_profile
from ignition_stack.services.resolver import resolve


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# --------------------------------------------------------------------------- #
# --dry-run: dump, write nothing, round-trip                                   #
# --------------------------------------------------------------------------- #


def test_dry_run_yaml_writes_nothing(runner: CliRunner, tmp_path: Path) -> None:
    """`init --dry-run` prints a parseable config and creates no directory.

    `-o` here is the output *directory* (Phase 1's flag); the dump format is
    YAML by default. The parent dir must not be created when nothing is written.
    """
    target_parent = tmp_path / "out"
    result = runner.invoke(
        app,
        ["init", "demo", "--profile", "scaleout", "--dry-run", "-o", str(target_parent)],
    )
    assert result.exit_code == 0, result.stdout
    # Nothing on disk: neither the parent nor the project directory was created.
    assert not target_parent.exists()
    assert not (target_parent / "demo").exists()

    # The dump parses back into a ProjectConfig equal to the resolved profile.
    dumped = tmp_path / "arch.yml"
    dumped.write_text(result.stdout, encoding="utf-8")
    parsed = load_config(dumped)
    expected = resolve(build_profile("scaleout", "demo", ProfileOptions()))
    assert parsed.model_dump() == expected.model_dump()


def test_dry_run_defaults_to_yaml(runner: CliRunner) -> None:
    result = runner.invoke(app, ["init", "demo", "--profile", "standalone", "--dry-run"])
    assert result.exit_code == 0, result.stdout
    # YAML, not JSON: the schema-ordered first key is `name`, unquoted.
    assert result.stdout.splitlines()[0] == "name: demo"


def test_dry_run_json_is_valid_json(runner: CliRunner) -> None:
    import json

    result = runner.invoke(
        app,
        ["init", "demo", "--profile", "standalone", "--dry-run", "--output-format", "json"],
    )
    assert result.exit_code == 0, result.stdout
    parsed = json.loads(result.stdout)
    assert parsed["name"] == "demo"


# --------------------------------------------------------------------------- #
# -f: build from file, round-trip byte-equality                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("profile", ["standalone", "scaleout", "hub-and-spoke", "mcp-n8n"])
def test_from_file_round_trip_is_byte_identical(runner: CliRunner, tmp_path: Path, profile: str) -> None:
    """A project built from a profile == one built from that profile's dump."""
    from_profile = tmp_path / "from-profile"
    from_file = tmp_path / "from-file"

    a = runner.invoke(app, ["init", "demo", "--profile", profile, "-o", str(from_profile)])
    assert a.exit_code == 0, a.stdout

    dump = tmp_path / "arch.yml"
    dump.write_text(
        dump_config(resolve(build_profile(profile, "demo", ProfileOptions())), "yaml"),
        encoding="utf-8",
    )
    b = runner.invoke(app, ["init", "demo", "-f", str(dump), "-o", str(from_file)])
    assert b.exit_code == 0, b.stdout

    compose_a = (from_profile / "demo" / "docker-compose.yaml").read_bytes()
    compose_b = (from_file / "demo" / "docker-compose.yaml").read_bytes()
    assert compose_a == compose_b


def test_from_file_name_argument_overrides_file_name(runner: CliRunner, tmp_path: Path) -> None:
    dump = tmp_path / "arch.yml"
    dump.write_text(
        dump_config(resolve(build_profile("standalone", "demo", ProfileOptions())), "yaml"),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["init", "renamed", "-f", str(dump), "-o", str(tmp_path / "out")])
    assert result.exit_code == 0, result.stdout
    built = load_config(tmp_path / "out" / "renamed" / ".ignition-stack" / "config.json")
    assert built.name == "renamed"


# --------------------------------------------------------------------------- #
# Error paths                                                                  #
# --------------------------------------------------------------------------- #


def test_from_file_unknown_field_exits_with_readable_message(runner: CliRunner, tmp_path: Path) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text("name: demo\nnot_a_real_field: 1\n", encoding="utf-8")
    result = runner.invoke(app, ["init", "demo", "-f", str(bad)])
    assert result.exit_code == 2, result.stdout
    # A validation message, not a traceback.
    assert "Traceback" not in result.stdout
    assert "not_a_real_field" in result.stdout
    assert "Extra inputs are not permitted" in result.stdout


def test_from_file_bad_enum_exits_with_readable_message(runner: CliRunner, tmp_path: Path) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "name: demo\ndatabase:\n  kind: oracle\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["init", "demo", "-f", str(bad)])
    assert result.exit_code == 2, result.stdout
    assert "Traceback" not in result.stdout
    assert "unsupported database kind" in result.stdout
    # pydantic's "Value error, " wrapper is stripped so the schema's own message
    # reads cleanly.
    assert "Value error" not in result.stdout


def test_from_file_with_profile_is_mutually_exclusive(runner: CliRunner, tmp_path: Path) -> None:
    dump = tmp_path / "arch.yml"
    dump.write_text(
        dump_config(resolve(build_profile("standalone", "demo", ProfileOptions())), "yaml"),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["init", "demo", "-f", str(dump), "--profile", "scaleout"])
    assert result.exit_code == 2, result.stdout
    assert "cannot be combined" in result.stdout


def test_output_format_without_dry_run_errors(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["init", "demo", "--profile", "standalone", "--output-format", "yaml", "-o", str(tmp_path)],
    )
    assert result.exit_code == 2, result.stdout
    assert "--output-format only applies with --dry-run" in result.stdout


# --------------------------------------------------------------------------- #
# Serialization helpers + resolve idempotency                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fmt", ["yaml", "json"])
def test_dump_load_round_trip(tmp_path: Path, fmt: str) -> None:
    config = resolve(build_profile("scaleout", "demo", ProfileOptions()))
    path = tmp_path / f"arch.{fmt}"
    path.write_text(dump_config(config, fmt), encoding="utf-8")  # type: ignore[arg-type]
    assert load_config(path).model_dump() == config.model_dump()


@pytest.mark.parametrize("profile", ["standalone", "scaleout", "hub-and-spoke", "mcp-n8n"])
def test_resolve_is_idempotent(profile: str) -> None:
    """A dumped resolved config must survive a second resolve() unchanged.

    `write_project` resolves whatever it's handed, so a config dumped after
    resolution is resolved again on rebuild; idempotency is what makes the
    dump/rebuild loop byte-stable.
    """
    once = resolve(build_profile(profile, "demo", ProfileOptions()))
    twice = resolve(once)
    assert once.model_dump() == twice.model_dump()


# --------------------------------------------------------------------------- #
# Phase 6: declarative parity - registry fixed-point + heterogeneous stacks    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("profile", ["standalone", "scaleout", "hub-and-spoke", "mcp-n8n"])
@pytest.mark.parametrize("iiot", [False, True])
@pytest.mark.parametrize("fmt", ["yaml", "json"])
def test_dump_is_a_fixed_point_for_every_profile_and_iiot(tmp_path: Path, profile: str, iiot: bool, fmt: str) -> None:
    """build -> resolve -> dump -> load -> resolve -> dump is byte-identical.

    The dump of a resolved config must survive a full reload + re-resolve + re-dump
    without drifting a single byte, for every profile crossed with the IIoT
    overlay on/off, in both serialization formats. This is the registry-era
    statement of the declarative round-trip contract: the registry (service
    instances + per-gateway attachments) survives the loop exactly.
    """
    config = resolve(build_profile(profile, "demo", ProfileOptions(iiot=iiot)))
    first = dump_config(config, fmt)  # type: ignore[arg-type]
    path = tmp_path / f"arch.{fmt}"
    path.write_text(first, encoding="utf-8")
    second = dump_config(resolve(load_config(path)), fmt)  # type: ignore[arg-type]
    assert first == second


@pytest.mark.parametrize("profile", ["standalone", "scaleout", "hub-and-spoke", "mcp-n8n"])
@pytest.mark.parametrize("iiot", [False, True])
def test_write_project_identical_from_profile_and_from_dump(tmp_path: Path, profile: str, iiot: bool) -> None:
    """write_project from the resolved config == from its reloaded dump.

    Pins the part the byte-level dump fixed-point doesn't: the rendered tree
    (compose + env) must be identical whether built straight from the profile or
    rebuilt from the dumped+reloaded registry config, with IIoT on and off.
    """
    config = resolve(build_profile(profile, "demo", ProfileOptions(iiot=iiot)))
    dump = tmp_path / "arch.yml"
    dump.write_text(dump_config(config, "yaml"), encoding="utf-8")
    reloaded = load_config(dump)

    from_profile = tmp_path / "a"
    from_dump = tmp_path / "b"
    write_project(config, from_profile)
    write_project(reloaded, from_dump)

    for rel in ("docker-compose.yaml", ".env"):
        assert (from_profile / rel).read_text(encoding="utf-8") == (from_dump / rel).read_text(encoding="utf-8"), rel


# The issue's flagship heterogeneous stack, authored as a hand-written -f file:
# gw1 (standard) attaches EMQX + Keycloak; gw2 (standard) attaches the SAME
# shared Keycloak instance + its own Mongo; gw3 (edge, role spoke) attaches EMQX
# with role mqtt-transmission and NO database. Phase-2 rules respected: the
# auto-added Postgres (for Keycloak) and gw2's Mongo are distinct kinds, and no
# gateway holds more than one database attachment.
_FLAGSHIP_YAML = """\
name: plant
database: null
gateways:
  - name: gw1
    role: hub
    http_port: 9088
    services:
      - instance: emqx
      - instance: keycloak
  - name: gw2
    role: hub
    http_port: 9089
    services:
      - instance: keycloak
      - instance: mongo1
  - name: gw3
    role: spoke
    ignition_edition: edge
    http_port: 9090
    services:
      - instance: emqx
        role: mqtt-transmission
service_instances:
  - id: emqx
    service: emqx
  - id: keycloak
    service: keycloak
  - id: mongo1
    service: mongo
"""


def _write_flagship(tmp_path: Path) -> Path:
    p = tmp_path / "flagship.yml"
    p.write_text(_FLAGSHIP_YAML, encoding="utf-8")
    return p


def test_flagship_heterogeneous_round_trips_to_a_fixed_point(tmp_path: Path) -> None:
    resolved = resolve(load_config(_write_flagship(tmp_path)))
    first = dump_config(resolved, "yaml")
    reload_path = tmp_path / "reloaded.yml"
    reload_path.write_text(first, encoding="utf-8")
    second = dump_config(resolve(load_config(reload_path)), "yaml")
    assert first == second


def test_flagship_keycloak_database_is_registry_level_with_no_attachments(
    tmp_path: Path,
) -> None:
    """Keycloak's auto-added SQL database is a registry-level dep, unattached.

    The flagship has no legacy `database`, so lowering creates no DB attachment;
    the Postgres that Keycloak's `requires: sql-database` pulls in must therefore
    carry the `keycloak` logical schema while attaching to *no* gateway. gw2's
    own Mongo (distinct kind) is the only database any gateway connects to.
    """
    resolved = resolve(load_config(_write_flagship(tmp_path)))
    dbs = {inst.service: inst for inst in resolved.service_instances if inst.is_database}
    assert set(dbs) == {"postgres", "mongo"}
    postgres = dbs["postgres"]
    assert "keycloak" in postgres.extra_databases
    # No gateway attaches to the auto-added Postgres.
    for gw in resolved.gateways:
        assert all(att.instance != postgres.id for att in gw.services)


def test_flagship_edge_gateway_holds_no_database(tmp_path: Path) -> None:
    resolved = resolve(load_config(_write_flagship(tmp_path)))
    gw3 = next(gw for gw in resolved.gateways if gw.name == "gw3")
    db_ids = {inst.id for inst in resolved.service_instances if inst.is_database}
    assert all(att.instance not in db_ids for att in gw3.services)
    # gw3 keeps only its broker transmission attachment.
    assert [(a.instance, a.role) for a in gw3.services] == [("emqx", "mqtt-transmission")]


def test_flagship_compose_has_no_db_dependency_for_edge_gateway(tmp_path: Path) -> None:
    """The rendered compose must not give gw3 a depends_on any database."""
    import yaml

    resolved = resolve(load_config(_write_flagship(tmp_path)))
    out = tmp_path / "rendered"
    write_project(resolved, out)
    compose = yaml.safe_load((out / "docker-compose.yaml").read_text(encoding="utf-8"))
    db_ids = {inst.id for inst in resolved.service_instances if inst.is_database}
    depends = compose["services"]["gw3"].get("depends_on", {})
    assert all(dep not in db_ids for dep in depends)
