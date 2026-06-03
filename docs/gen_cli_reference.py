#!/usr/bin/env python3
"""Generate ``docs/docs/reference/cli.md`` from the live Typer command tree.

The CLI reference is generated, never hand-written, so it can never drift from
the actual command surface. This walks the ``ignition_stack.cli.app`` Typer
application (via ``typer.main.get_command``, which yields the underlying Click
command objects) and renders one section per command with its usage line,
arguments, and options.

Run it directly to (re)write the page::

    uv run python docs/gen_cli_reference.py        # from the repo root
    npm --prefix docs run gen:cli                  # equivalent npm script

A pytest drift check (``tests/test_docs_cli_reference.py``) regenerates the
page in memory and fails if it differs from the committed file, so CI catches
an out-of-date reference the moment a command or option changes.

The introspection deliberately duck-types (``param.param_type_name``,
``hasattr(cmd, "commands")``) rather than importing Click classes: Typer
vendors Click under a private path, and the duck-typed checks survive that.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from typer.main import get_command

from ignition_stack.cli import app

PROG = "ignition-stack"

# docs/gen_cli_reference.py -> repo root is two parents up; the page lives in
# the Docusaurus content tree.
_REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = _REPO_ROOT / "docs" / "docs" / "reference" / "cli.md"

_FRONT_MATTER = """\
---
title: CLI reference
description: Every ignition-stack command, argument, and option, generated from the live Typer app.
---
"""

_BANNER = """\
:::info Generated page
This page is generated from the Typer command tree by `docs/gen_cli_reference.py`.
Do not edit it by hand. Regenerate it with `npm run gen:cli` (or
`uv run python docs/gen_cli_reference.py`); a CI drift check fails if it falls
out of sync with the CLI.
:::
"""

# Click type names -> the metavar shown for an option that takes a value.
_TYPE_METAVAR = {
    "text": "TEXT",
    "integer": "INTEGER",
    "integer range": "INTEGER",
    "float": "FLOAT",
    "path": "PATH",
    "filename": "PATH",
    "boolean": "BOOLEAN",
}


def _is_group(command: object) -> bool:
    """A Click group carries a ``commands`` mapping; a leaf command does not."""
    return hasattr(command, "commands")


def _subcommands(group: object) -> list[tuple[str, object]]:
    """Child commands of a group, sorted by name for deterministic output."""
    return sorted(group.commands.items())  # type: ignore[attr-defined]


def walk(command: object, path: str) -> Iterator[tuple[str, object]]:
    """Yield ``(invocation_path, command)`` for ``command`` and its descendants."""
    yield path, command
    if _is_group(command):
        for name, sub in _subcommands(command):
            yield from walk(sub, f"{path} {name}")


def _options(command: object) -> list[object]:
    """Real options for a command, excluding the auto-added ``--help``."""
    return [
        p
        for p in command.params  # type: ignore[attr-defined]
        if p.param_type_name == "option" and not getattr(p, "hidden", False) and p.name != "help"
    ]


def _arguments(command: object) -> list[object]:
    return [
        p
        for p in command.params  # type: ignore[attr-defined]
        if p.param_type_name == "argument"
    ]


def _squeeze(text: str) -> str:
    """Collapse runs of whitespace (incl. newlines) to single spaces."""
    return " ".join(text.split())


def _argument_metavar(arg: object) -> str:
    name = arg.name.upper()  # type: ignore[attr-defined]
    if arg.nargs == -1:  # type: ignore[attr-defined]
        return f"{name}..."
    return name


def _option_metavar(opt: object) -> str | None:
    if getattr(opt, "is_flag", False):
        return None
    return _TYPE_METAVAR.get(opt.type.name, opt.type.name.upper())  # type: ignore[attr-defined]


def _usage_line(path: str, command: object) -> str:
    pieces = [path]
    if _options(command):
        pieces.append("[OPTIONS]")
    if _is_group(command):
        pieces.append("COMMAND [ARGS]...")
    for arg in _arguments(command):
        metavar = _argument_metavar(arg)
        pieces.append(metavar if arg.required else f"[{metavar}]")  # type: ignore[attr-defined]
    return " ".join(pieces)


def _render_arguments(command: object) -> list[str]:
    args = _arguments(command)
    if not args:
        return []
    lines = ["**Arguments**", ""]
    for arg in args:
        metavar = _argument_metavar(arg)
        kind = "required" if arg.required else "optional"  # type: ignore[attr-defined]
        help_text = _squeeze(getattr(arg, "help", "") or "")
        suffix = f": {help_text}" if help_text else ""
        lines.append(f"- `{metavar}` ({kind}){suffix}")
    lines.append("")
    return lines


def _render_options(command: object) -> list[str]:
    opts = _options(command)
    if not opts:
        return []
    lines = ["**Options**", ""]
    for opt in opts:
        flags = ", ".join(f"`{o}`" for o in list(opt.opts) + list(opt.secondary_opts))  # type: ignore[attr-defined]
        metavar = _option_metavar(opt)
        head = flags if metavar is None else f"{flags} `{metavar}`"

        notes: list[str] = []
        if getattr(opt, "required", False):
            notes.append("required")
        elif getattr(opt, "is_flag", False):
            notes.append("flag")
        elif opt.default is not None:  # type: ignore[attr-defined]
            default = opt.default  # type: ignore[attr-defined]
            # Render Path defaults with forward slashes so the generated page is
            # identical on every OS; str(Path(...)) would emit backslashes on
            # Windows and break the drift check.
            shown = default.as_posix() if isinstance(default, Path) else default
            notes.append(f"default `{shown}`")
        note = f" ({'; '.join(notes)})" if notes else ""

        help_text = _squeeze(opt.help or "")  # type: ignore[attr-defined]
        suffix = f": {help_text}" if help_text else ""
        lines.append(f"- {head}{note}{suffix}")
    lines.append("")
    return lines


def _render_command(path: str, command: object) -> list[str]:
    depth = path.count(" ")  # 1 for top-level commands, 2 for group subcommands
    heading = "#" * (depth + 1)
    lines = [f"{heading} `{path}`", ""]

    help_text = (command.help or "").strip()  # type: ignore[attr-defined]
    if help_text:
        lines.append(help_text)
        lines.append("")

    lines.append("```text")
    lines.append(_usage_line(path, command))
    lines.append("```")
    lines.append("")

    lines.extend(_render_arguments(command))
    lines.extend(_render_options(command))
    return lines


def build_reference() -> str:
    """Render the full CLI reference markdown for the current Typer app."""
    root = get_command(app)

    parts: list[str] = [_FRONT_MATTER, "# CLI reference", "", _BANNER]

    root_help = (root.help or "").strip()
    if root_help:
        parts.extend([root_help, ""])

    # Global options live on the root callback (e.g. --version).
    global_opts = _render_options(root)
    if global_opts:
        parts.extend(["## Global options", "", *global_opts])

    # Top-level commands and groups, alphabetical for determinism.
    for name, command in _subcommands(root):
        for sub_path, sub_command in walk(command, f"{PROG} {name}"):
            parts.extend(_render_command(sub_path, sub_command))

    text = "\n".join(parts)
    # Collapse any accidental 3+ blank lines and guarantee a single trailing newline.
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.rstrip("\n") + "\n"


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(build_reference(), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(_REPO_ROOT)}")


if __name__ == "__main__":
    main()
