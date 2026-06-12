"""Top-level Typer application.

The CLI surface is intentionally narrow: ``create`` generates a new named
stack, and the ``modules`` sub-app manages the module catalog.  Stacks are
short-lived; recreate with the recorded config rather than reshape in place.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from ignition_stack import __version__
from ignition_stack.architectures import (
    ArchitectureError,
    ArchOptions,
    build_architecture,
    get_architecture,
    list_architectures,
)
from ignition_stack.commands.modules import modules_app
from ignition_stack.completion import (
    complete_architecture,
    complete_disable_builtin,
    complete_edge_role,
    complete_iiot_broker,
    complete_output_format,
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
from ignition_stack.record import LIFECYCLE_DIR, RECORD_NAME
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


def _arch_help() -> str:
    """Format the available architectures for ``--arch`` help text."""
    lines = ["System architecture to materialize (skips the wizard):"]
    for a in list_architectures():
        lines.append(f"  - {a.slug}: {a.summary}")
    return "\n".join(lines)


@app.command()
def create(
    name: str = typer.Argument(
        ...,
        help="Project name. Becomes the directory, the compose project, and the gateway name.",
    ),
    arch: str | None = typer.Option(
        None,
        "--arch",
        "-a",
        help=_arch_help(),
        autocompletion=complete_architecture,
    ),
    spokes: int = typer.Option(
        3,
        "--spokes",
        help="Spoke gateway count for the hub-and-spoke architecture (ignored otherwise).",
        min=0,
    ),
    frontends: int = typer.Option(
        1,
        "--frontends",
        help="Frontend gateway count for the scale-out architecture (ignored otherwise).",
        min=1,
    ),
    network_split: bool | None = typer.Option(
        None,
        "--network-split/--no-network-split",
        help=("Force the frontend/backend network split on or off. Default follows " "the architecture (scale-out splits, hub-and-spoke does not)."),
    ),
    reverse_proxy: str | None = typer.Option(
        None,
        "--reverse-proxy",
        help=(
            "Route gateways through a Traefik reverse proxy instead of host "
            "ports. 'external' joins a proxy you already run (on --proxy-network); "
            "'scaffold' also lays down the ia-eknorr/traefik-reverse-proxy README "
            "at --proxy-path. Omit for plain host-port mapping."
        ),
        autocompletion=complete_reverse_proxy,
    ),
    proxy_network: str = typer.Option(
        "proxy",
        "--proxy-network",
        help="External Docker network the proxy routes on (with --reverse-proxy). Defaults to 'proxy'.",
    ),
    proxy_path: str = typer.Option(
        "reverse-proxy",
        "--proxy-path",
        help="Relative directory the scaffolded proxy README lives in (with --reverse-proxy scaffold).",
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
            "Gateway role that runs the Ignition Edge edition. Scale-out runs all "
            "gateways standard by default; hub-and-spoke defaults its spokes to "
            "Edge. Pass 'none' to disable the architecture's edge default; pass a "
            "role name ('frontend', 'hub', 'gateway', ...) to opt that role in."
        ),
        autocompletion=complete_edge_role,
    ),
    redundant: str | None = typer.Option(
        None,
        "--redundant",
        help=(
            "Make a single gateway role redundant, expanding it into a master + "
            "backup pair (e.g. 'backend' for scale-out, 'hub' for hub-and-spoke, "
            "'gateway' for basic). Frontends and spokes are replicated, not "
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
            "--dry-run) instead of an architecture or the wizard. Mutually "
            "exclusive with --arch. The project name argument overrides the "
            "file's name."
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

    With ``--from-file``, builds from a saved config file. With ``--arch``,
    runs non-interactively from the named architecture and its flags. With
    neither, walks the interactive wizard. ``--dry-run`` resolves the config
    and prints it instead of writing anything.
    """
    target = ((output_dir or Path.cwd()) / name).resolve()

    _validate_create_flags(arch=arch, from_file=from_file, dry_run=dry_run, fmt=output_format)

    # Name validation runs before either the wizard or the architecture build so
    # invalid names fail fast with a clear exit code (2), instead of bubbling
    # through the wizard's first prompt or the architecture's deep model_validate.
    try:
        ProjectConfig(name=name)
    except ValueError as exc:
        console.print(f"[red]error[/red]: invalid project name: {exc}")
        raise typer.Exit(code=2) from exc

    if from_file is not None:
        config = _load_from_file(from_file, name)
    elif arch is None:
        config = _run_wizard_or_exit(name)
    else:
        config = _build_from_arch(
            name,
            arch,
            spokes=spokes,
            frontends=frontends,
            force=force,
            edge_role=edge_role,
            network_split=network_split,
            reverse_proxy=reverse_proxy,
            proxy_network=proxy_network,
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
    console.print(f"  open {_gateway_open_url(config)}  (admin / {config.admin_password})")
    console.print()
    console.print(f"  config recorded in {LIFECYCLE_DIR}/{RECORD_NAME} - recreate or clone this stack with `ignition-stack create <name> -f <that file>`.")


def _gateway_open_url(config: ProjectConfig) -> str:
    """The URL the first gateway is reachable at after `compose up`.

    A proxied stack publishes no host port - the proxy's Host rule is the
    front door - so the localhost:<port> form only applies to host-port mode.
    """
    if config.reverse_proxy is not None:
        from ignition_stack.compose.engine import proxy_url

        return proxy_url(config, config.gateways[0])
    return f"http://localhost:{config.gateways[0].http_port}"


def _validate_create_flags(*, arch: str | None, from_file: Path | None, dry_run: bool, fmt: str | None) -> None:
    """Enforce the mutual-exclusion + flag-applicability rules, or exit code 2.

    ``--from-file`` already fully specifies the topology, so combining it with
    ``--arch`` is ambiguous and rejected. ``--output-format`` only shapes the
    ``--dry-run`` dump, so passing it without ``--dry-run`` is a usage error
    rather than a silent no-op. The value itself is validated against the two
    supported formats here so a bad ``--output-format`` fails before any build.
    """
    if from_file is not None and arch is not None:
        console.print("[red]error[/red]: --from-file cannot be combined with --arch; a " "config file already specifies the full topology.")
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


def _build_from_arch(
    name: str,
    arch: str,
    *,
    spokes: int,
    frontends: int,
    force: bool,
    edge_role: str | None,
    network_split: bool | None,
    reverse_proxy: str | None,
    proxy_network: str,
    proxy_path: str,
    redundant: str | None,
    disable_builtin: list[str],
    iiot: bool,
    iiot_broker: str | None,
) -> ProjectConfig:
    """Materialize a config from the named architecture + CLI flags, or exit cleanly."""
    try:
        get_architecture(arch)
    except KeyError as exc:
        console.print(f"[red]error[/red]: {exc}")
        raise typer.Exit(code=2) from exc

    proxy: ReverseProxyConfig | None = None
    if reverse_proxy:
        if reverse_proxy not in {"external", "scaffold"}:
            console.print(f"[red]error[/red]: unsupported --reverse-proxy mode '{reverse_proxy}'; use 'external' or 'scaffold'.")
            raise typer.Exit(code=2)
        proxy = ReverseProxyConfig(mode=reverse_proxy, network=proxy_network, path=proxy_path)
    # --iiot-broker implies --iiot, so naming a broker is enough to turn the
    # overlay on; build_architecture defaults the slug to 'chariot' when iiot is
    # on without an explicit broker.
    options = ArchOptions(
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
        config = build_architecture(arch, name, options)
    except ArchitectureError as exc:
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


if __name__ == "__main__":  # pragma: no cover
    app()
