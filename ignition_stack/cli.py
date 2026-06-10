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
from dataclasses import replace
from pathlib import Path

import typer
from rich.console import Console

from ignition_stack import __version__
from ignition_stack.commands.modules import modules_app
from ignition_stack.completion import (
    complete_disable_builtin,
    complete_edge_role,
    complete_iiot_broker,
    complete_output_format,
    complete_profile,
    complete_redundant_role,
    complete_reverse_proxy,
)
from ignition_stack.compose import write_project
from ignition_stack.config import (
    ConfigIOError,
    ProjectConfig,
    ReverseProxyConfig,
    dump_config,
    load_config,
)
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
    can_host_redundant_role,
    get_profile,
    list_profiles,
)
from ignition_stack.services.resolver import resolve
from ignition_stack.update_check import (
    check_for_update,
    detect_upgrade_command,
    should_notify,
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
    _notify_update_available()


def _notify_update_available() -> None:
    """Print a one-line advisory when a newer release is on PyPI.

    Runs only for real subcommands (not --version or bare help) and only on an
    interactive terminal. Best-effort: any failure inside the check is swallowed
    rather than allowed to disrupt the command the user actually ran.
    """
    if not should_notify():
        return
    result = check_for_update()
    if result is None:
        return
    current, latest = result
    console.print(
        f"[dim]update available[/dim] [yellow]{current}[/yellow] -> " f"[green]{latest}[/green] · run: [cyan]{detect_upgrade_command()}[/cyan]",
        highlight=False,
    )


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
        help=("Force the frontend/backend network split on or off. Default follows " "the profile (scaleout splits, hub-and-spoke does not)."),
    ),
    reverse_proxy: str | None = typer.Option(
        None,
        "--reverse-proxy",
        help=("Scaffold a reverse proxy of the given kind ('traefik'). Lays down a " "README + POST-SETUP entry at --proxy-path. Omit for plain host-port mapping."),
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
    redundant: str | None = typer.Option(
        None,
        "--redundant",
        help=(
            "Make a single gateway role redundant, expanding it into a master + "
            "backup pair (e.g. 'backend' for scaleout, 'hub' for hub-and-spoke, "
            "'gateway' for standalone). Frontends and spokes are replicated, not "
            "paired, and are rejected."
        ),
        autocompletion=complete_redundant_role,
    ),
    disable_builtin: list[str] = typer.Option(  # noqa: B008 - Typer pattern
        [],
        "--disable-builtin",
        help=(
            "Built-in IA module to turn off on every gateway (repeatable), e.g. "
            "--disable-builtin vision --disable-builtin sfc. Emits a "
            "GATEWAY_MODULES_ENABLED whitelist of everything else. Slugs "
            "tab-complete; an unknown slug is rejected with the full valid list."
        ),
        autocompletion=complete_disable_builtin,
    ),
    iiot: bool = typer.Option(
        False,
        "--iiot/--no-iiot",
        help=(
            "Overlay an MQTT/Sparkplug IIoT pipeline: add a broker and wire the "
            "Cirrus Link Transmission/Engine modules across the gateways by role "
            "(spokes/frontends transmit, hub/backend run Engine; a single gateway "
            "runs both). Defaults the broker to 'chariot'."
        ),
    ),
    iiot_broker: str | None = typer.Option(
        None,
        "--iiot-broker",
        help=(
            "MQTT broker slug the IIoT overlay wires to (implies --iiot). Must be "
            "a catalog 'mqtt-broker' kind (e.g. 'chariot', 'emqx', 'hivemq'). "
            "Defaults to 'chariot' when --iiot is given without this flag."
        ),
        autocompletion=complete_iiot_broker,
    ),
    from_file: Path | None = typer.Option(  # noqa: B008 - Typer pattern
        None,
        "--from-file",
        "-f",
        help=(
            "Build from a saved config file (YAML or JSON, as dumped by "
            "--dry-run) instead of a profile or the wizard. Mutually exclusive "
            "with --profile. The project name argument overrides the file's name."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "Resolve the config and print it (see --output-format) without "
            "writing any files. The dump is the full build input; redirect it to "
            "a file, edit it, and rebuild with --from-file."
        ),
    ),
    output_format: str | None = typer.Option(
        None,
        "--output-format",
        help="Format for the --dry-run dump: 'yaml' (default) or 'json'.",
        autocompletion=complete_output_format,
    ),
    output_dir: Path | None = typer.Option(  # noqa: B008 - Typer pattern
        None,
        "--output-dir",
        "-o",
        help="Parent directory the project is written into. Defaults to the current directory.",
    ),
) -> None:
    """Generate a new Ignition stack at ``<output-dir>/<name>``.

    With ``--from-file``, builds from a saved config file. With ``--profile``,
    runs non-interactively from the named profile and its flags. With neither,
    walks the interactive wizard. ``--dry-run`` resolves the config and prints
    it instead of writing anything.
    """
    target = ((output_dir or Path.cwd()) / name).resolve()

    _validate_init_flags(profile=profile, from_file=from_file, dry_run=dry_run, fmt=output_format)

    # Name validation runs before either the wizard or the profile build so
    # invalid names fail fast with a clear exit code (2), instead of bubbling
    # through the wizard's first prompt or the profile's deep model_validate.
    try:
        ProjectConfig(name=name)
    except ValueError as exc:
        console.print(f"[red]error[/red]: invalid project name: {exc}")
        raise typer.Exit(code=2) from exc

    if from_file is not None:
        config = _load_from_file(from_file, name)
    elif profile is None:
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
            redundant=redundant,
            disable_builtin=disable_builtin,
            iiot=iiot,
            iiot_broker=iiot_broker,
        )

    if dry_run:
        # Dump the resolved config (the writer resolves too, so this is exactly
        # what would be built) and write nothing. `end=""`/`markup=False` keep
        # the output verbatim and parseable - no rich markup, no extra newline.
        console.print(dump_config(resolve(config), output_format or "yaml"), end="", markup=False)
        raise typer.Exit()

    try:
        files = write_project(config, target)
    except FileExistsError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]created[/green] {target}")
    console.print(f"  {len(files)} file(s) written")
    console.print()
    console.print("Next steps:")
    console.print(f"  cd {target}")
    console.print("  docker compose up -d")
    console.print(f"  open http://localhost:{config.gateways[0].http_port}  (admin / {config.admin_password})")
    console.print()
    console.print(f"  config recorded in {LIFECYCLE_DIR}/ - run `ignition-stack reset` to " "regenerate or `switch-profile <name>` to reshape this stack.")


def _validate_init_flags(*, profile: str | None, from_file: Path | None, dry_run: bool, fmt: str | None) -> None:
    """Enforce the mutual-exclusion + flag-applicability rules, or exit code 2.

    ``--from-file`` already fully specifies the topology, so combining it with
    ``--profile`` is ambiguous and rejected. ``--output-format`` only shapes the
    ``--dry-run`` dump, so passing it without ``--dry-run`` is a usage error
    rather than a silent no-op. The value itself is validated against the two
    supported formats here so a bad ``--output-format`` fails before any build.
    """
    if from_file is not None and profile is not None:
        console.print("[red]error[/red]: --from-file cannot be combined with --profile; a " "config file already specifies the full topology.")
        raise typer.Exit(code=2)
    if fmt is not None and not dry_run:
        console.print("[red]error[/red]: --output-format only applies with --dry-run.")
        raise typer.Exit(code=2)
    if fmt is not None and fmt not in {"yaml", "json"}:
        console.print(f"[red]error[/red]: unsupported --output-format '{fmt}'; use 'yaml' or 'json'.")
        raise typer.Exit(code=2)


def _load_from_file(from_file: Path, name: str) -> ProjectConfig:
    """Load a config file, override its name with the CLI argument, or exit cleanly.

    The project-name argument wins over the file's ``name`` so the same dumped
    config can be rebuilt under a new name; everything else comes from the file.
    A parse or validation failure surfaces as a readable error (exit code 2),
    never a traceback.
    """
    try:
        config = load_config(from_file)
    except ConfigIOError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=2) from exc
    if config.name != name:
        config = config.model_copy(update={"name": name})
    return config


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
    redundant: str | None,
    disable_builtin: list[str],
    iiot: bool,
    iiot_broker: str | None,
) -> ProjectConfig:
    """Materialize a config from the named profile + CLI flags, or exit cleanly."""
    try:
        get_profile(profile)
    except KeyError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=2) from exc

    proxy = ReverseProxyConfig(kind=reverse_proxy, path=proxy_path) if reverse_proxy else None
    # --iiot-broker implies --iiot, so naming a broker is enough to turn the
    # overlay on; build_profile defaults the slug to 'chariot' when iiot is on
    # without an explicit broker.
    options = ProfileOptions(
        spokes=spokes,
        frontends=frontends,
        force=force,
        edge_role=edge_role,
        network_split=network_split,
        reverse_proxy=proxy,
        redundant_role=redundant,
        disable_builtins=tuple(disable_builtin),
        iiot=iiot or iiot_broker is not None,
        iiot_broker=iiot_broker,
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
        help="The generated project to reset. Defaults to the current directory.",
    ),
) -> None:
    """Regenerate a project from its recorded config.

    Reads ``.ignition-stack/config.json``, clears the generated tree (keeping the
    record and the modules cache), and re-runs generation. Works on any project
    generated by this CLI; a directory without a record can't be reset.
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
        help="The generated project to reshape. Defaults to the current directory.",
    ),
) -> None:
    """Reshape a project under a different architecture profile.

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
    # Redundancy is pinned to a profile-specific role (e.g. standalone's
    # 'gateway'), which the target profile may not have. Building its base
    # topology lets us check before build_profile's mark_redundant would reject
    # it - drop the intent with an advisory rather than failing the reshape.
    if options.redundant_role is not None and not can_host_redundant_role(get_profile(profile).build(current.name, options), options.redundant_role):
        console.print(
            f"[yellow]note[/yellow]: redundancy on '{options.redundant_role}' was not "
            f"carried to {profile} (no matching gateway); re-apply with --redundant "
            "if the new topology has a role to pair"
        )
        options = replace(options, redundant_role=None)
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
    verbatim so a reshape preserves the user's topology choice. Disabled
    built-in modules are carried over too (see below) so a reshape doesn't
    silently re-enable Vision/SFC/etc.
    """
    edge_roles = [gw.role or gw.name for gw in config.gateways if gw.ignition_edition == "edge"]
    spoke_count = sum(1 for gw in config.gateways if (gw.role or "") == "spoke")
    frontend_count = sum(1 for gw in config.gateways if (gw.role or "") == "frontend")
    # Redundancy intent is carried by the master node (the backup is re-derived
    # by the resolver), so recover the role/name of whichever gateway is the
    # master and let the new profile re-expand the pair.
    redundant_role = next(
        (gw.role or gw.name for gw in config.gateways if gw.redundancy is not None and gw.redundancy.mode == "master"),
        None,
    )
    # Disabled built-ins are applied stack-wide, so carry over the slugs disabled
    # on EVERY gateway (the intersection) - that is the stack-wide intent, and it
    # won't over-disable a module that a hand-authored config turned off on only
    # one node. The target profile re-applies it uniformly.
    disabled_sets = [set(gw.disable_builtins) for gw in config.gateways]
    disable_builtins = tuple(sorted(set.intersection(*disabled_sets))) if disabled_sets else ()
    # A recorded config is resolved: its database + services live in the
    # registry, not the legacy fields. Recover the profile inputs from the
    # registry (sole DB instance's service slug; non-database instance slugs).
    db_instance = config.database_instance()
    return ProfileOptions(
        spokes=spoke_count or 3,
        frontends=frontend_count or 1,
        edge_role=edge_roles[0] if edge_roles else "none",
        network_split=config.network_split,
        reverse_proxy=config.reverse_proxy,
        database_kind=db_instance.service if db_instance is not None else None,
        services=tuple(inst.service for inst in config.non_database_instances()),
        redundant_role=redundant_role,
        disable_builtins=disable_builtins,
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
