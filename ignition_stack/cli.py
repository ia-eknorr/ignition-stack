"""Top-level Typer application.

Phase 2 implements ``init`` for the standalone+Postgres walking skeleton.
Phase 3 wires the real ``modules`` sub-app (catalog list/validate/download);
``reset`` and ``wipe`` remain visible placeholders so the command surface
is stable from day one, with later phases filling them in.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from ignition_stack import __version__
from ignition_stack.commands.modules import modules_app
from ignition_stack.compose import write_project
from ignition_stack.config import ProjectConfig

app = typer.Typer(
    name="ignition-stack",
    help="Generate ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos.",
    add_completion=False,
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


@app.command()
def init(
    name: str = typer.Argument(
        ...,
        help="Project name. Becomes the directory, the compose project, and the gateway name.",
    ),
    output_dir: Path | None = typer.Option(  # noqa: B008 - Typer pattern
        None,
        "--output-dir",
        "-o",
        help="Parent directory the project is written into. Defaults to the current directory.",
    ),
) -> None:
    """Generate a new standalone+Postgres Ignition stack at ``<output-dir>/<name>``."""
    target = ((output_dir or Path.cwd()) / name).resolve()
    try:
        config = ProjectConfig(name=name)
    except ValueError as exc:
        console.print(f"[red]error[/red]: invalid project name: {exc}")
        raise typer.Exit(code=2) from exc

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
    console.print(
        f"  open http://localhost:{config.gateway_http_port}  (admin / {config.admin_password})"
    )


@app.command()
def reset() -> None:
    """Reset a generated project to a clean baseline. (placeholder; arrives in Phase 7)"""
    console.print(
        "[yellow]ignition-stack reset[/yellow] is not yet implemented. "
        "Arrives in Phase 7 (lifecycle modes)."
    )
    raise typer.Exit(code=2)


@app.command()
def wipe() -> None:
    """Remove this project's containers and volumes. (placeholder; arrives in Phase 7)"""
    console.print(
        "[yellow]ignition-stack wipe[/yellow] is not yet implemented. "
        "Arrives in Phase 7 (lifecycle modes)."
    )
    raise typer.Exit(code=2)


if __name__ == "__main__":  # pragma: no cover
    app()
