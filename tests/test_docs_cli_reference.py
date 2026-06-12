"""Drift and coverage guards for the generated CLI reference page.

The CLI reference (``docs/docs/reference/cli.md``) is generated from the live
Typer app by ``docs/gen_cli_reference.py``. These tests are the contract that
keeps it honest:

* ``test_cli_reference_in_sync`` regenerates the page in memory and fails if it
  differs from the committed file, so adding or changing any command or option
  without regenerating breaks CI.
* ``test_cli_reference_covers_*`` independently walk the Typer command tree and
  assert every command path and every option flag actually appears in the
  rendered page, guarding against a generator bug that silently drops surface.
* ``test_required_docs_pages_exist`` asserts the required page set (get-started,
  concepts, every architecture, every service, guides, reference, contribute) is
  present on disk.

The generator lives outside any importable package, so it is loaded by path.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from typer.main import get_command

from ignition_stack.cli import app

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GENERATOR_PATH = _REPO_ROOT / "docs" / "gen_cli_reference.py"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_cli_reference", _GENERATOR_PATH)
    assert spec and spec.loader, f"cannot load generator at {_GENERATOR_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


def _all_command_paths() -> list[str]:
    """Every invocation path in the live app, e.g. ``ignition-stack modules list``."""
    root = get_command(app)
    paths: list[str] = []
    for name, command in gen._subcommands(root):
        for sub_path, _ in gen.walk(command, f"{gen.PROG} {name}"):
            paths.append(sub_path)
    return paths


def _all_option_flags() -> list[tuple[str, str]]:
    """``(command_path, flag)`` for every option on every command, plus globals."""
    root = get_command(app)
    flags: list[tuple[str, str]] = []
    for opt in gen._options(root):
        for flag in opt.opts:
            flags.append((gen.PROG, flag))
    for name, command in gen._subcommands(root):
        for sub_path, sub in gen.walk(command, f"{gen.PROG} {name}"):
            for opt in gen._options(sub):
                for flag in opt.opts:
                    flags.append((sub_path, flag))
    return flags


def test_cli_reference_in_sync() -> None:
    """The committed page must match a fresh render of the live Typer app."""
    committed = gen.OUTPUT_PATH.read_text(encoding="utf-8")
    rendered = gen.build_reference()
    assert committed == rendered, (
        "docs/docs/reference/cli.md is out of date with the CLI. " "Regenerate it: `uv run python docs/gen_cli_reference.py` " "(or `npm --prefix docs run gen:cli`)."
    )


def test_cli_reference_covers_every_command() -> None:
    page = gen.OUTPUT_PATH.read_text(encoding="utf-8")
    for path in _all_command_paths():
        assert f"`{path}`" in page, f"command missing from CLI reference: {path}"


def test_cli_reference_covers_every_option() -> None:
    page = gen.OUTPUT_PATH.read_text(encoding="utf-8")
    for path, flag in _all_option_flags():
        assert f"`{flag}`" in page, f"option {flag} (from {path}) missing from CLI reference"


@pytest.mark.parametrize(
    "relpath",
    [
        "intro.md",
        "get-started/installation.md",
        "get-started/quickstart.md",
        "concepts/how-generation-works.md",
        "concepts/seeding.md",
        "concepts/configuration-record.md",
        "architectures/index.md",
        "architectures/basic.md",
        "architectures/scale-out.md",
        "architectures/hub-and-spoke.md",
        "services/index.md",
        "services/postgres.md",
        "services/mysql.md",
        "services/mariadb.md",
        "services/mongo.md",
        "services/keycloak.md",
        "services/chariot.md",
        "services/hivemq.md",
        "services/emqx.md",
        "services/rabbitmq.md",
        "services/kafka.md",
        "services/opcua-sim.md",
        "services/modbus-sim.md",
        "services/n8n.md",
        "guides/declarative-config.md",
        "guides/redundancy.md",
        "guides/teardown.md",
        "reference/cli.md",
        "reference/seeding-matrix.md",
        "contribute/add-a-service.md",
        "contribute/update-module-versions.md",
    ],
)
def test_required_docs_pages_exist(relpath: str) -> None:
    page = _REPO_ROOT / "docs" / "docs" / relpath
    assert page.is_file(), f"required docs page missing: docs/docs/{relpath}"
