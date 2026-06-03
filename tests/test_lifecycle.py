"""Acceptance tests for the lifecycle record, reset/reshape, and scoped cleanup.

Every generated project records its resolved config under ``.ignition-stack/``;
that record is the one primitive ``reset`` and ``switch-profile`` read back to
regenerate or reshape in place. Three contracts:

1. ``init`` records the resolved config, and ``reset`` round-trips it
   byte-for-byte (config in == config out) while clearing stale generated files.
2. ``reset`` / ``switch-profile`` refuse a directory with no record (one that
   this CLI never generated, or whose record was removed).
3. ``wipe`` is provably scoped to this project: the generated ``Makefile`` and
   ``ignition-stack wipe`` both name the compose project on a ``down -v`` and
   never issue a global prune.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ignition_stack.cli import app
from ignition_stack.lifecycle import LIFECYCLE_DIR, has_record, read_record, record_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _init(runner: CliRunner, tmp_path: Path, *extra: str) -> Path:
    args = ["init", "demo", "--profile", "standalone", "-o", str(tmp_path), *extra]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.stdout
    return tmp_path / "demo"


# --------------------------------------------------------------------------- #
# Record + reset round-trip                                                    #
# --------------------------------------------------------------------------- #


def test_init_records_resolved_config(runner: CliRunner, tmp_path: Path) -> None:
    project = _init(runner, tmp_path)
    assert has_record(project)
    record = read_record(project)
    assert record.name == "demo"
    assert record.profile == "standalone"
    # ...and it is a complete, runnable project alongside the record.
    assert (project / "docker-compose.yaml").is_file()
    assert (project / "Makefile").is_file()
    assert (project / "POST-SETUP.md").is_file()


def test_reset_round_trips_recorded_config(runner: CliRunner, tmp_path: Path) -> None:
    project = _init(runner, tmp_path)
    before = read_record(project).model_dump()

    result = runner.invoke(app, ["reset", "-C", str(project)])
    assert result.exit_code == 0, result.stdout

    after = read_record(project).model_dump()
    assert after == before
    assert (project / "docker-compose.yaml").is_file()
    assert (project / "Makefile").is_file()


def test_reset_keeps_record_but_clears_stale_files(runner: CliRunner, tmp_path: Path) -> None:
    project = _init(runner, tmp_path)
    stray = project / "leftover-from-a-previous-up.txt"
    stray.write_text("stale", encoding="utf-8")

    result = runner.invoke(app, ["reset", "-C", str(project)])
    assert result.exit_code == 0, result.stdout

    assert record_path(project).is_file()  # the primitive survived
    assert not stray.exists()  # the generated tree was cleared first


def test_reset_refuses_a_directory_without_a_record(runner: CliRunner, tmp_path: Path) -> None:
    # A project whose record was removed (or a directory this CLI never made)
    # has nothing to reset from.
    project = _init(runner, tmp_path)
    shutil.rmtree(project / LIFECYCLE_DIR)

    result = runner.invoke(app, ["reset", "-C", str(project)])
    assert result.exit_code == 2, result.stdout
    assert "no lifecycle record" in result.stdout.lower()


def test_switch_profile_reshapes_and_rerecords(runner: CliRunner, tmp_path: Path) -> None:
    # Start from a 2-gateway scaleout project.
    result = runner.invoke(app, ["init", "demo", "--profile", "scaleout", "-o", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    project = tmp_path / "demo"
    assert (project / "services" / "frontend").is_dir()

    result = runner.invoke(app, ["switch-profile", "standalone", "-C", str(project)])
    assert result.exit_code == 0, result.stdout

    record = read_record(project)
    assert record.profile == "standalone"
    assert [gw.name for gw in record.gateways] == ["gateway"]
    # Stale multi-gateway service dirs are gone; the single-gateway tree is back.
    assert not (project / "services" / "frontend").exists()
    assert (project / "services" / "ignition").is_dir()


def test_switch_profile_drops_unhostable_redundancy(runner: CliRunner, tmp_path: Path) -> None:
    # A redundant standalone pins redundancy to the 'gateway' role, which the
    # scaleout topology (frontend/backend) has no place for. The reshape must
    # succeed and drop the intent with an advisory - not fail with exit 2.
    project = _init(runner, tmp_path, "--redundant", "gateway")
    assert any(gw.redundancy is not None for gw in read_record(project).gateways)

    result = runner.invoke(app, ["switch-profile", "scaleout", "-C", str(project)])

    assert result.exit_code == 0, result.stdout
    assert "redundancy on 'gateway' was not carried" in result.stdout
    record = read_record(project)
    assert record.profile == "scaleout"
    assert all(gw.redundancy is None for gw in record.gateways)


# --------------------------------------------------------------------------- #
# Scoped wipe                                                                  #
# --------------------------------------------------------------------------- #


def test_generated_makefile_wipe_is_project_scoped(runner: CliRunner, tmp_path: Path) -> None:
    makefile = (_init(runner, tmp_path) / "Makefile").read_text(encoding="utf-8")

    assert "PROJECT := demo" in makefile
    wipe_line = next(line for line in makefile.splitlines() if "down -v" in line)
    assert "-p $(PROJECT)" in wipe_line

    # Never a host-wide teardown.
    assert "system prune" not in makefile
    assert "volume prune" not in makefile
    assert "volume rm" not in makefile


def test_wipe_dry_run_emits_scoped_command(runner: CliRunner, tmp_path: Path) -> None:
    project = _init(runner, tmp_path)
    result = runner.invoke(app, ["wipe", "-C", str(project), "--dry-run"])
    assert result.exit_code == 0, result.stdout
    assert "docker compose -p demo down -v --remove-orphans" in result.stdout


def test_wipe_resolves_project_name_from_env_when_record_absent(
    runner: CliRunner, tmp_path: Path
) -> None:
    # With the record removed, .env's COMPOSE_PROJECT_NAME still scopes the wipe.
    project = _init(runner, tmp_path)
    shutil.rmtree(project / LIFECYCLE_DIR)
    result = runner.invoke(app, ["wipe", "-C", str(project), "--dry-run"])
    assert result.exit_code == 0, result.stdout
    assert "-p demo " in result.stdout


def test_wipe_errors_without_a_generated_project(runner: CliRunner, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, ["wipe", "-C", str(empty), "--dry-run"])
    assert result.exit_code == 2, result.stdout
