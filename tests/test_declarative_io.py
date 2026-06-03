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
def test_from_file_round_trip_is_byte_identical(
    runner: CliRunner, tmp_path: Path, profile: str
) -> None:
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


def test_from_file_unknown_field_exits_with_readable_message(
    runner: CliRunner, tmp_path: Path
) -> None:
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
