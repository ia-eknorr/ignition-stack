"""Phase 2 acceptance tests for ``ignition-stack init`` standalone+Postgres.

Three things this file proves end-to-end:

1. ``init demo`` writes the expected file tree (compose, .env, services,
   bootstrap script).
2. Every generated file is LF-only - the cross-platform contract this
   project rests on.
3. The compose output is byte-identical to the committed golden snapshot.

It also exercises the CLI surface: ``--help`` mentions all four commands,
and ``init`` with no name exits non-zero.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ignition_stack.cli import app

GOLDEN_DIR = Path(__file__).parent / "golden" / "standalone-postgres"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_help_lists_all_four_commands(runner: CliRunner) -> None:
    """--help must show init plus the three placeholder commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.stdout
    for command in ("init", "modules", "reset", "wipe"):
        assert command in result.stdout, f"expected '{command}' in --help output"


def test_init_without_name_exits_non_zero(runner: CliRunner) -> None:
    """Required positional 'name' missing -> Typer exits non-zero with a clear message."""
    result = runner.invoke(app, ["init"])
    assert result.exit_code != 0
    # Typer/Click writes the error to stderr.
    err = result.stderr.lower()
    assert "missing" in err or "name" in err, result.stderr


def test_init_with_invalid_name_exits_non_zero(runner: CliRunner, tmp_path: Path) -> None:
    """Names that violate the pydantic regex fail with exit code 2."""
    result = runner.invoke(app, ["init", "Bad Name", "-o", str(tmp_path)])
    assert result.exit_code == 2, result.stdout


def test_init_writes_expected_tree(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "demo", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    project = tmp_path / "demo"
    expected_files = {
        project / "docker-compose.yaml",
        project / ".env",
        project / "scripts" / "docker-bootstrap.sh",
        project / "services" / "ignition" / "config" / "resources" / "core" / "config-mode.json",
        project / "services" / "ignition" / "config" / "resources" / "dev" / "config-mode.json",
        project / "services" / "ignition" / "projects" / ".gitkeep",
    }
    for f in expected_files:
        assert f.exists(), f"missing expected file: {f}"


def test_init_compose_matches_golden(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "demo", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    generated = (tmp_path / "demo" / "docker-compose.yaml").read_bytes()
    golden = (GOLDEN_DIR / "docker-compose.yaml").read_bytes()
    assert generated == golden


def test_init_env_carries_resolved_values(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "my-stack", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    env_text = (tmp_path / "my-stack" / ".env").read_text(encoding="utf-8")
    assert "COMPOSE_PROJECT_NAME=my-stack\n" in env_text
    assert "GATEWAY_NAME=my-stack\n" in env_text
    assert "IGNITION_IMAGE=inductiveautomation/ignition:8.3.6\n" in env_text
    assert "POSTGRES_IMAGE=postgres:18.1\n" in env_text


def test_every_generated_file_is_lf_only(runner: CliRunner, tmp_path: Path) -> None:
    """The cross-platform contract. No CR bytes anywhere in generated text."""
    result = runner.invoke(app, ["init", "demo", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    project = tmp_path / "demo"
    text_files = [
        project / "docker-compose.yaml",
        project / ".env",
        project / "scripts" / "docker-bootstrap.sh",
        project / "services" / "ignition" / "config" / "resources" / "core" / "config-mode.json",
        project / "services" / "ignition" / "config" / "resources" / "dev" / "config-mode.json",
    ]
    for f in text_files:
        data = f.read_bytes()
        assert b"\r" not in data, f"{f} contains CR bytes; must be LF-only"


def test_init_refuses_to_clobber_existing_project(runner: CliRunner, tmp_path: Path) -> None:
    """Running init twice into the same name fails rather than silently overwriting."""
    first = runner.invoke(app, ["init", "demo", "-o", str(tmp_path)])
    assert first.exit_code == 0, first.stdout

    second = runner.invoke(app, ["init", "demo", "-o", str(tmp_path)])
    assert second.exit_code != 0
    assert "not empty" in second.stdout.lower() or "exists" in second.stdout.lower()


def test_bootstrap_script_is_executable(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "demo", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    script = tmp_path / "demo" / "scripts" / "docker-bootstrap.sh"
    mode = script.stat().st_mode & 0o777
    # User must be able to execute; container runs the script via /bin/bash
    # but a chmod +x is still expected for local invocations.
    assert mode & 0o100, f"docker-bootstrap.sh is not user-executable (mode={oct(mode)})"


@pytest.mark.parametrize(
    "command",
    ["modules", "reset", "wipe"],
)
def test_placeholder_commands_exit_non_zero(runner: CliRunner, command: str) -> None:
    """Phase-3+ commands are visible but explicitly not-yet-implemented."""
    result = runner.invoke(app, [command])
    assert result.exit_code == 2, result.stdout
    assert "not yet implemented" in result.stdout.lower()
