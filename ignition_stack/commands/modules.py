"""`ignition-stack modules` subcommands: list, validate, download."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from ignition_stack.catalog.download import (
    DEFAULT_CACHE_DIR,
    DownloadError,
    DownloadOutcome,
    download_entry,
)
from ignition_stack.catalog.loader import CatalogLoadError, load_catalog
from ignition_stack.catalog.schema import SHA256_UNPINNED, ModuleEntry
from ignition_stack.catalog.verify import (
    REACHABILITY_TIMEOUT_SECONDS,
    VerifyIssue,
    verify_reachable,
)
from ignition_stack.completion import complete_module_name

modules_app = typer.Typer(help="Inspect and prepare the module + driver catalog.")
console = Console()
err_console = Console(stderr=True)

CatalogOpt = Annotated[
    Path | None,
    typer.Option("--catalog", help="Path to a modules.yaml. Defaults to the bundled catalog."),
]
IgnVerOpt = Annotated[
    str | None,
    typer.Option("--ignition-version", help="Filter to entries verified for this exact version."),
]


@modules_app.command("list")
def list_entries(
    catalog_path: CatalogOpt = None,
    ignition_version: IgnVerOpt = None,
) -> None:
    """Show every catalog entry as a table."""
    catalog = _load(catalog_path)
    entries = catalog.for_ignition(ignition_version) if ignition_version else catalog.entries
    table = Table(title="ignition-stack catalog")
    table.add_column("name")
    table.add_column("kind")
    table.add_column("vendor")
    table.add_column("ignition")
    table.add_column("identifier / driver")
    table.add_column("manual?")
    for e in entries:
        identifier = e.module_identifier if isinstance(e, ModuleEntry) else "(jdbc)"
        table.add_row(
            e.name,
            e.kind,
            e.vendor,
            ", ".join(e.ignition_versions),
            identifier,
            "yes" if e.requires_manual_download else "no",
        )
    console.print(table)


@modules_app.command("validate")
def validate(
    catalog_path: CatalogOpt = None,
    skip_network: Annotated[
        bool,
        typer.Option("--skip-network", help="Only validate the schema; skip URL reachability."),
    ] = False,
) -> None:
    """Confirm schema integrity, pinned shas, and (optionally) URL reachability."""
    catalog = _load(catalog_path)
    issues: list[VerifyIssue] = []

    for entry in catalog.entries:
        # Manual-download entries (e.g. EA-gated MCP) cannot be sha-pinned
        # while gated; flipping requires_manual_download to false at GA is
        # the moment the maintainer is expected to pin the sha.
        if entry.sha256 == SHA256_UNPINNED and not entry.requires_manual_download:
            issues.append(VerifyIssue(entry.name, "sha256 is UNPINNED"))

    if not skip_network:
        with httpx.Client(timeout=REACHABILITY_TIMEOUT_SECONDS) as client:
            for entry in catalog.entries:
                issue = verify_reachable(entry, client)
                if issue is not None:
                    issues.append(issue)

    if issues:
        err_console.print("[bold red]Catalog validation failed:[/bold red]")
        for issue in issues:
            err_console.print(f"  - {issue.entry_name}: {issue.reason}")
        raise typer.Exit(code=1)

    console.print(
        f"[green]OK[/green]: {len(catalog.entries)} entries valid"
        + (" (schema only)" if skip_network else " (schema + reachability)"),
    )


@modules_app.command("download")
def download(
    names: Annotated[
        list[str] | None,
        typer.Argument(
            help="Entries to download. Omit to download every non-manual entry.",
            autocompletion=complete_module_name,
        ),
    ] = None,
    catalog_path: CatalogOpt = None,
    ignition_version: IgnVerOpt = None,
    cache_dir: Annotated[
        Path,
        typer.Option("--cache-dir", help="Destination directory for cached artifacts."),
    ] = DEFAULT_CACHE_DIR,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="No network calls. Fails if any selected entry is missing from the cache.",
        ),
    ] = False,
) -> None:
    """Materialise selected catalog entries into the host-side cache."""
    catalog = _load(catalog_path)

    selected = list(catalog.entries)
    if ignition_version:
        selected = [e for e in selected if ignition_version in e.ignition_versions]
    if names:
        wanted = set(names)
        present = {e.name for e in selected}
        missing = wanted - present
        if missing:
            err_console.print(
                f"[red]Unknown catalog entries:[/red] {', '.join(sorted(missing))}",
            )
            raise typer.Exit(code=2)
        selected = [e for e in selected if e.name in wanted]

    if not selected:
        err_console.print("[yellow]No catalog entries match the filters.[/yellow]")
        raise typer.Exit(code=2)

    had_error = False
    with httpx.Client() as client:
        for entry in selected:
            try:
                result = download_entry(entry, cache_dir, client=client, offline=offline)
            except DownloadError as exc:
                err_console.print(f"[red]ERROR[/red] {exc}")
                had_error = True
                continue
            style = {
                DownloadOutcome.DOWNLOADED: "green",
                DownloadOutcome.COPIED_FROM_LOCAL: "green",
                DownloadOutcome.SKIPPED_CACHED: "cyan",
                DownloadOutcome.SKIPPED_MANUAL: "yellow",
            }[result.outcome]
            console.print(f"[{style}]{result.outcome.value}[/{style}] {result.message}")

    if had_error:
        raise typer.Exit(code=1)


def _load(catalog_path: Path | None):
    try:
        return load_catalog(catalog_path)
    except CatalogLoadError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
