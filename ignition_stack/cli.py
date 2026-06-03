"""Top-level Typer application.

Phase 2 implements ``init`` for the standalone+Postgres walking skeleton.
Phase 3 wires the real ``modules`` sub-app (catalog list/validate/download);
``reset`` and ``wipe`` remain visible placeholders so the command surface
is stable from day one, with later phases filling them in.

Phase 6 widens ``init`` with ``--profile``, ``--spokes``, ``--force``, and
``--edge-role`` for the four architecture profiles, and falls into the
interactive wizard when no profile is named.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer
from rich.console import Console

from ignition_stack import __version__
from ignition_stack.commands.modules import modules_app
from ignition_stack.completion import (
    complete_edge_role,
    complete_profile,
    complete_reverse_proxy,
)
from ignition_stack.compose import write_project
from ignition_stack.config import ProjectConfig, ReverseProxyConfig
from ignition_stack.lifecycle import (
    LIFECYCLE_DIR,
    RECORD_NAME,
    CleanupError,
    LifecycleError,
    project_name,
    read_record,
    wipe_command,
)
from ignition_stack.lifecycle.regenerate import regenerate
from ignition_stack.profiles import (
    ProfileError,
    ProfileOptions,
    build_profile,
    get_profile,
    list_profiles,
)
from ignition_stack.wizard import run_wizard

app = typer.Typer(
    name="ignition-stack",
    help="Generate ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos.",
    rich_markup_mode="rich",
)
app.add_typer(modules_app, name="modules")

console = Console()


# invoke_without_command=True lets the eager --version handler fire on bare
# `ignition-stack --version`. Typer's `no_args_is_help` short-circuits the
# callback when no subcommand is given, which swallowed --version and made
# it look like the command was missing; handling the "no subcommand" case
# manually here keeps both behaviours: --version prints, bare call shows help.
@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show ignition-stack version and exit.",
        is_eager=True,
    ),
) -> None:
    if version:
        console.print(f"ignition-stack {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit()


def _profile_help() -> str:
    """Format the available profiles for ``--profile`` help text."""
    lines = ["Architecture profile to materialize (skips the wizard):"]
    for p in list_profiles():
        lines.append(f"  - {p.slug}: {p.summary}")
    return "\n".join(lines)


@app.command()
def init(
    name: str = typer.Argument(
        ...,
        help="Project name. Becomes the directory, the compose project, and the gateway name.",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help=_profile_help(),
        autocompletion=complete_profile,
    ),
    spokes: int = typer.Option(
        3,
        "--spokes",
        help="Spoke gateway count for the hub-and-spoke profile (ignored otherwise).",
        min=0,
    ),
    frontends: int = typer.Option(
        1,
        "--frontends",
        help="Frontend gateway count for the scaleout profile (ignored otherwise).",
        min=1,
    ),
    network_split: bool | None = typer.Option(
        None,
        "--network-split/--no-network-split",
        help=(
            "Force the frontend/backend network split on or off. Default follows "
            "the profile (scaleout splits, hub-and-spoke does not)."
        ),
    ),
    reverse_proxy: str | None = typer.Option(
        None,
        "--reverse-proxy",
        help=(
            "Scaffold a reverse proxy of the given kind ('traefik'). Lays down a "
            "README + POST-SETUP entry at --proxy-path. Omit for plain host-port mapping."
        ),
        autocompletion=complete_reverse_proxy,
    ),
    proxy_path: str = typer.Option(
        "reverse-proxy",
        "--proxy-path",
        help="Relative directory the reverse-proxy scaffold lives in (with --reverse-proxy).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Bypass the hub-and-spoke red-tier RAM advisory.",
    ),
    edge_role: str | None = typer.Option(
        None,
        "--edge-role",
        help=(
            "Gateway role that runs the Ignition Edge edition. Scaleout runs all "
            "gateways standard by default; hub-and-spoke defaults its spokes to "
            "Edge. Pass 'none' to disable the profile's edge default; pass a role "
            "name ('frontend', 'hub', 'gateway', ...) to opt that specific role in."
        ),
        autocompletion=complete_edge_role,
    ),
    keep_cli: bool = typer.Option(
        False,
        "--keep-cli",
        help=(
            "SE-demo mode: keep the lifecycle primitives in .ignition-stack/ so "
            "`ignition-stack reset` / `switch-profile` can regenerate the project. "
            "The default (one-shot) leaves a self-contained project with no CLI "
            "primitives behind."
        ),
    ),
    output_dir: Path | None = typer.Option(  # noqa: B008 - Typer pattern
        None,
        "--output-dir",
        "-o",
        help="Parent directory the project is written into. Defaults to the current directory.",
    ),
) -> None:
    """Generate a new Ignition stack at ``<output-dir>/<name>``.

    With ``--profile``, runs non-interactively from the named profile and its
    flags. Without ``--profile``, walks the interactive wizard.
    """
    target = ((output_dir or Path.cwd()) / name).resolve()

    # Name validation runs before either the wizard or the profile build so
    # invalid names fail fast with a clear exit code (2), instead of bubbling
    # through the wizard's first prompt or the profile's deep model_validate.
    try:
        ProjectConfig(name=name)
    except ValueError as exc:
        console.print(f"[red]error[/red]: invalid project name: {exc}")
        raise typer.Exit(code=2) from exc

    if profile is None:
        config = _run_wizard_or_exit(name)
    else:
        config = _build_from_profile(
            name,
            profile,
            spokes=spokes,
            frontends=frontends,
            force=force,
            edge_role=edge_role,
            network_split=network_split,
            reverse_proxy=reverse_proxy,
            proxy_path=proxy_path,
        )

    try:
        files = write_project(config, target, keep_cli=keep_cli)
    except FileExistsError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=1) from exc

    mode = "SE-demo" if keep_cli else "one-shot"
    console.print(f"[green]created[/green] {target} ([cyan]{mode}[/cyan])")
    console.print(f"  {len(files)} file(s) written")
    console.print()
    console.print("Next steps:")
    console.print(f"  cd {target}")
    console.print("  docker compose up -d")
    console.print(
        f"  open http://localhost:{config.gateways[0].http_port}  (admin / {config.admin_password})"
    )
    if keep_cli:
        console.print()
        console.print(
            f"  primitives kept in {LIFECYCLE_DIR}/ - run `ignition-stack reset` to "
            "regenerate or `switch-profile <name>` to reshape this stack."
        )


def _build_from_profile(
    name: str,
    profile: str,
    *,
    spokes: int,
    frontends: int,
    force: bool,
    edge_role: str | None,
    network_split: bool | None,
    reverse_proxy: str | None,
    proxy_path: str,
) -> ProjectConfig:
    """Materialize a config from the named profile + CLI flags, or exit cleanly."""
    try:
        get_profile(profile)
    except KeyError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=2) from exc

    proxy = ReverseProxyConfig(kind=reverse_proxy, path=proxy_path) if reverse_proxy else None
    options = ProfileOptions(
        spokes=spokes,
        frontends=frontends,
        force=force,
        edge_role=edge_role,
        network_split=network_split,
        reverse_proxy=proxy,
    )
    try:
        config = build_profile(profile, name, options)
    except ProfileError as exc:
        # Red-tier advisory: exit code 3 keeps it distinguishable from a config
        # error (2) or a generic write failure (1), so callers and tests can
        # branch on it explicitly.
        console.print(f"[red]advisory[/red]: {exc}")
        raise typer.Exit(code=3) from exc
    except ValueError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=2) from exc
    return config


def _run_wizard_or_exit(name: str) -> ProjectConfig:
    """Run the interactive wizard, surfacing cancellation as a clean non-zero exit."""
    try:
        return run_wizard(name)
    except KeyboardInterrupt as exc:
        console.print("[yellow]cancelled[/yellow]")
        raise typer.Exit(code=130) from exc


@app.command()
def reset(
    project_dir: Path = typer.Option(  # noqa: B008 - Typer pattern
        Path("."),
        "--project-dir",
        "-C",
        help="The generated SE-demo project to reset. Defaults to the current directory.",
    ),
) -> None:
    """Regenerate an SE-demo project from its recorded config.

    Reads ``.ignition-stack/config.json``, clears the generated tree (keeping the
    record and the modules cache), and re-runs generation. Only works on SE-demo
    projects (``init --keep-cli``); a one-shot project has no record to reset from.
    """
    project_dir = project_dir.resolve()
    try:
        config = read_record(project_dir)
    except LifecycleError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=2) from exc

    files = regenerate(project_dir, config)
    console.print(f"[green]reset[/green] {project_dir}")
    console.print(f"  {len(files)} file(s) regenerated from {LIFECYCLE_DIR}/{RECORD_NAME}")


@app.command(name="switch-profile")
def switch_profile(
    profile: str = typer.Argument(
        ...,
        help="Architecture profile to switch this stack to.",
        autocompletion=complete_profile,
    ),
    project_dir: Path = typer.Option(  # noqa: B008 - Typer pattern
        Path("."),
        "--project-dir",
        "-C",
        help="The generated SE-demo project to reshape. Defaults to the current directory.",
    ),
) -> None:
    """Reshape an SE-demo project under a different architecture profile.

    Carries the recorded database, services, reverse-proxy, and edge intent over
    to the new profile, then regenerates in place and re-records the result.
    """
    project_dir = project_dir.resolve()
    try:
        current = read_record(project_dir)
    except LifecycleError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=2) from exc

    try:
        get_profile(profile)
    except KeyError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=2) from exc

    options = _options_from_config(current)
    try:
        new_config = build_profile(profile, current.name, options)
    except ProfileError as exc:
        console.print(f"[red]advisory[/red]: {exc}")
        raise typer.Exit(code=3) from exc
    except ValueError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=2) from exc

    files = regenerate(project_dir, new_config)
    console.print(f"[green]switched[/green] {current.profile or 'custom'} -> {profile}")
    console.print(f"  {len(files)} file(s) regenerated")


def _options_from_config(config: ProjectConfig) -> ProfileOptions:
    """Recover the profile inputs a switch should carry over from a recorded config.

    Edge intent is recovered from whichever gateway runs the Edge edition (or
    'none' to keep the new profile from re-introducing its edge default); the
    spoke count from the number of spoke-role gateways, the frontend count from
    the number of frontend-role gateways, and the network split is carried over
    verbatim so a reshape preserves the user's topology choice.
    """
    edge_roles = [gw.role or gw.name for gw in config.gateways if gw.ignition_edition == "edge"]
    spoke_count = sum(1 for gw in config.gateways if (gw.role or "") == "spoke")
    frontend_count = sum(1 for gw in config.gateways if (gw.role or "") == "frontend")
    return ProfileOptions(
        spokes=spoke_count or 3,
        frontends=frontend_count or 1,
        edge_role=edge_roles[0] if edge_roles else "none",
        network_split=config.network_split,
        reverse_proxy=config.reverse_proxy,
        database_kind=config.database.kind if config.database else None,
        services=tuple(config.services),
    )


@app.command()
def wipe(
    project_dir: Path = typer.Option(  # noqa: B008 - Typer pattern
        Path("."),
        "--project-dir",
        "-C",
        help="The generated project to wipe. Defaults to the current directory.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the scoped teardown command without running it.",
    ),
) -> None:
    """Remove only this project's containers, networks, and volumes.

    Runs ``docker compose -p <project> down -v --remove-orphans``; the ``-p``
    pin scopes the teardown to resources labelled with this compose project, so
    unrelated Docker resources on the host are never touched.
    """
    project_dir = project_dir.resolve()
    try:
        name = project_name(project_dir)
    except CleanupError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=2) from exc

    command = wipe_command(name)
    if dry_run:
        console.print(" ".join(command))
        return

    try:
        completed = subprocess.run(command, cwd=project_dir, check=False)
    except FileNotFoundError as exc:
        console.print("[red]error[/red]: docker not found on PATH; cannot wipe.")
        raise typer.Exit(code=1) from exc

    if completed.returncode != 0:
        console.print(f"[red]error[/red]: `{' '.join(command)}` exited {completed.returncode}")
        raise typer.Exit(code=completed.returncode)
    console.print(f"[green]wiped[/green] project '{name}'")


if __name__ == "__main__":  # pragma: no cover
    app()
