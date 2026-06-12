"""Acceptance tests for ``ignition-stack create`` standalone+Postgres.

Three things this file proves end-to-end:

1. ``create demo`` writes the expected file tree (compose, .env, services,
   bootstrap script).
2. Every generated file is LF-only - the cross-platform contract this
   project rests on.
3. The compose output is byte-identical to the committed golden snapshot.

It also exercises the CLI surface (``create`` with no name exits non-zero),
the configuration-record contract (every generation writes a record the
``-f`` clone path can re-read), and the Makefile wipe-scoping invariant.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ignition_stack.cli import app
from ignition_stack.record import RECORD_DIR, has_record, read_record, record_path

GOLDEN_DIR = Path(__file__).parent / "golden" / "standalone-postgres"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _create(runner: CliRunner, tmp_path: Path, *extra: str) -> Path:
    """Helper: run ``create demo --arch basic`` and return the project path."""
    args = ["create", "demo", "--arch", "basic", "-o", str(tmp_path), *extra]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.stdout
    return tmp_path / "demo"


def test_create_without_name_exits_non_zero(runner: CliRunner) -> None:
    """Required positional 'name' missing -> Typer exits non-zero with a clear message."""
    result = runner.invoke(app, ["create"])
    assert result.exit_code != 0
    # Typer/Click writes the error to stderr.
    err = result.stderr.lower()
    assert "missing" in err or "name" in err, result.stderr


def test_create_with_invalid_name_exits_non_zero(runner: CliRunner, tmp_path: Path) -> None:
    """Names that violate the pydantic regex fail with exit code 2."""
    result = runner.invoke(app, ["create", "Bad Name", "--arch", "basic", "-o", str(tmp_path)])
    assert result.exit_code == 2, result.stdout


def test_create_writes_expected_tree(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["create", "demo", "--arch", "basic", "-o", str(tmp_path)])
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


def test_create_compose_matches_golden(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["create", "demo", "--arch", "basic", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    generated = (tmp_path / "demo" / "docker-compose.yaml").read_bytes()
    golden = (GOLDEN_DIR / "docker-compose.yaml").read_bytes()
    assert generated == golden


def test_create_env_carries_resolved_values(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["create", "my-stack", "--arch", "basic", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    env_text = (tmp_path / "my-stack" / ".env").read_text(encoding="utf-8")
    assert "COMPOSE_PROJECT_NAME=my-stack\n" in env_text
    assert "GATEWAY_NAME=my-stack\n" in env_text
    assert "IGNITION_IMAGE=inductiveautomation/ignition:8.3.6\n" in env_text
    assert "POSTGRES_IMAGE=postgres:18.1\n" in env_text


def test_every_generated_file_is_lf_only(runner: CliRunner, tmp_path: Path) -> None:
    """The cross-platform contract. No CR bytes anywhere in generated text."""
    result = runner.invoke(app, ["create", "demo", "--arch", "basic", "-o", str(tmp_path)])
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


def test_create_refuses_to_clobber_existing_project(runner: CliRunner, tmp_path: Path) -> None:
    """Running create twice into the same name fails rather than silently overwriting."""
    first = runner.invoke(app, ["create", "demo", "--arch", "basic", "-o", str(tmp_path)])
    assert first.exit_code == 0, first.stdout

    second = runner.invoke(app, ["create", "demo", "--arch", "basic", "-o", str(tmp_path)])
    assert second.exit_code != 0
    # Rich wraps console output to the terminal width (80 cols when there is no
    # TTY, as in CI), which can split the message mid-phrase. Collapse
    # whitespace so we assert on the message's meaning, not its wrap points.
    message = " ".join(second.stdout.lower().split())
    assert "not empty" in message or "exists" in message


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="NTFS does not track the Unix execute bit; the script is run as `bash script` " "inside a Linux container, so the host-side bit is irrelevant on Windows.",
)
def test_bootstrap_script_is_executable(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(app, ["create", "demo", "--arch", "basic", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    script = tmp_path / "demo" / "scripts" / "docker-bootstrap.sh"
    mode = script.stat().st_mode & 0o777
    # User must be able to execute; container runs the script via /bin/bash
    # but a chmod +x is still expected for local invocations.
    assert mode & 0o100, f"docker-bootstrap.sh is not user-executable (mode={oct(mode)})"


# --------------------------------------------------------------------------- #
# Configuration record                                                         #
# --------------------------------------------------------------------------- #


def test_create_records_resolved_config(runner: CliRunner, tmp_path: Path) -> None:
    project = _create(runner, tmp_path)
    assert has_record(project)
    record = read_record(project)
    assert record.name == "demo"
    assert record.architecture == "basic"
    # ...and it is a complete, runnable project alongside the record.
    assert (project / "docker-compose.yaml").is_file()
    assert (project / "Makefile").is_file()
    assert (project / "POST-SETUP.md").is_file()


def test_generated_makefile_wipe_is_project_scoped(runner: CliRunner, tmp_path: Path) -> None:
    makefile = (_create(runner, tmp_path) / "Makefile").read_text(encoding="utf-8")

    assert "PROJECT := demo" in makefile
    wipe_line = next(line for line in makefile.splitlines() if "down -v" in line)
    assert "-p $(PROJECT)" in wipe_line

    # Never a host-wide teardown.
    assert "system prune" not in makefile
    assert "volume prune" not in makefile
    assert "volume rm" not in makefile


def test_generated_makefile_has_no_reset_target(runner: CliRunner, tmp_path: Path) -> None:
    """The reset: target was removed in 0.7.0; ensure it is not re-introduced."""
    makefile = (_create(runner, tmp_path) / "Makefile").read_text(encoding="utf-8")
    assert "reset:" not in makefile
    assert "ignition-stack reset" not in makefile


# --------------------------------------------------------------------------- #
# Clone path: create <name> -f <record>                                        #
# --------------------------------------------------------------------------- #


def test_create_from_file_clones_stack_under_new_name(runner: CliRunner, tmp_path: Path) -> None:
    """``create beta -f <alpha>/.ignition-stack/config.json`` is the clone story.

    The positional name overrides the recorded one, so the same config file
    produces a distinct compose project. The record must be re-written with the
    new name, and the compose project naming must reflect ``beta`` not ``alpha``.
    """
    # Generate the source stack.
    alpha_dir = tmp_path / "alpha"
    result = runner.invoke(app, ["create", "alpha", "--arch", "basic", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout

    config_file = alpha_dir / RECORD_DIR / "config.json"
    assert config_file.is_file()

    # Clone it under a new name.
    beta_dir = tmp_path / "out"
    result = runner.invoke(app, ["create", "beta", "-f", str(config_file), "-o", str(beta_dir)])
    assert result.exit_code == 0, result.stdout

    # The recorded config carries the new name.
    beta_project = beta_dir / "beta"
    assert beta_project.is_dir()
    beta_record = read_record(beta_project)
    assert beta_record.name == "beta"

    # The generated .env sets the compose project to the new name.
    env_text = (beta_project / ".env").read_text(encoding="utf-8")
    assert "COMPOSE_PROJECT_NAME=beta" in env_text
    assert "COMPOSE_PROJECT_NAME=alpha" not in env_text

    # The source stack is untouched.
    assert record_path(alpha_dir).is_file()
    assert read_record(alpha_dir).name == "alpha"
