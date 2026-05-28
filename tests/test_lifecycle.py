"""Phase 7 acceptance tests for the two lifecycle modes + scoped cleanup.

Three contracts, mapped to the phase's validation criteria:

1. One-shot ``init`` (default) leaves a self-contained project with no CLI
   primitives - ``.ignition-stack/`` is absent, so ``reset`` has nothing to
   work from.
2. SE-demo ``init --keep-cli`` records the resolved config under
   ``.ignition-stack/``; ``reset`` regenerates from it and the record round-trips
   byte-for-byte (config in == config out).
3. ``wipe`` is provably scoped to this project: the generated ``Makefile`` and
   ``ignition-stack wipe`` both name the compose project on a ``down -v`` and
   never issue a global prune.
"""

from __future__ import annotations

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
# One-shot mode (default)                                                      #
# --------------------------------------------------------------------------- #


def test_one_shot_leaves_no_lifecycle_primitives(runner: CliRunner, tmp_path: Path) -> None:
    project = _init(runner, tmp_path)
    assert not (project / LIFECYCLE_DIR).exists()
    assert not has_record(project)
    # ...but it is still a complete, runnable project.
    assert (project / "docker-compose.yaml").is_file()
    assert (project / "Makefile").is_file()
    assert (project / "POST-SETUP.md").is_file()


def test_reset_refuses_a_one_shot_project(runner: CliRunner, tmp_path: Path) -> None:
    project = _init(runner, tmp_path)
    result = runner.invoke(app, ["reset", "-C", str(project)])
    assert result.exit_code == 2, result.stdout
    assert "no lifecycle record" in result.stdout.lower()


# --------------------------------------------------------------------------- #
# SE-demo mode (--keep-cli) + reset round-trip                                 #
# --------------------------------------------------------------------------- #


def test_se_demo_persists_recorded_config(runner: CliRunner, tmp_path: Path) -> None:
    project = _init(runner, tmp_path, "--keep-cli")
    assert record_path(project).is_file()
    record = read_record(project)
    assert record.name == "demo"
    assert record.profile == "standalone"


def test_reset_round_trips_recorded_config(runner: CliRunner, tmp_path: Path) -> None:
    project = _init(runner, tmp_path, "--keep-cli")
    before = read_record(project).model_dump()

    result = runner.invoke(app, ["reset", "-C", str(project)])
    assert result.exit_code == 0, result.stdout

    after = read_record(project).model_dump()
    assert after == before
    # Regeneration reproduced the runnable tree.
    assert (project / "docker-compose.yaml").is_file()
    assert (project / "Makefile").is_file()


def test_reset_keeps_record_but_clears_stale_files(runner: CliRunner, tmp_path: Path) -> None:
    project = _init(runner, tmp_path, "--keep-cli")
    stray = project / "leftover-from-a-previous-up.txt"
    stray.write_text("stale", encoding="utf-8")

    result = runner.invoke(app, ["reset", "-C", str(project)])
    assert result.exit_code == 0, result.stdout

    assert record_path(project).is_file()  # the primitive survived
    assert not stray.exists()  # the generated tree was cleared first


def test_switch_profile_reshapes_and_rerecords(runner: CliRunner, tmp_path: Path) -> None:
    # Start from a 2-gateway scaleout SE-demo project.
    result = runner.invoke(
        app, ["init", "demo", "--profile", "scaleout", "--keep-cli", "-o", str(tmp_path)]
    )
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
    project = _init(runner, tmp_path, "--keep-cli")
    result = runner.invoke(app, ["wipe", "-C", str(project), "--dry-run"])
    assert result.exit_code == 0, result.stdout
    assert "docker compose -p demo down -v --remove-orphans" in result.stdout


def test_wipe_resolves_project_name_from_env_for_one_shot(
    runner: CliRunner, tmp_path: Path
) -> None:
    # One-shot has no record, but .env carries COMPOSE_PROJECT_NAME for scoping.
    project = _init(runner, tmp_path)
    result = runner.invoke(app, ["wipe", "-C", str(project), "--dry-run"])
    assert result.exit_code == 0, result.stdout
    assert "-p demo " in result.stdout


def test_wipe_errors_without_a_generated_project(runner: CliRunner, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, ["wipe", "-C", str(empty), "--dry-run"])
    assert result.exit_code == 2, result.stdout
