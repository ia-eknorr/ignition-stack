---
title: Installation
description: Install the ignition-stack CLI with pipx, and the Docker prerequisite for running the stacks it generates.
---

# Installation

`ignition-stack` is a Python CLI. Install it with [pipx](https://pipx.pypa.io/) so it lands in its own isolated environment:

```sh
pipx install ignition-stack
```

To install from source instead - the latest off `main`, or a specific branch:

```sh
pipx install git+https://github.com/ia-eknorr/ignition-stack.git
pipx install git+https://github.com/ia-eknorr/ignition-stack.git@<branch>
```

Confirm it is on your path:

```sh
ignition-stack --version
```

## Staying up to date

When a real command runs in an interactive terminal, `ignition-stack` prints a one-line notice if a newer release is on PyPI, with the upgrade command tailored to how you installed it — `pipx upgrade ignition-stack`, `uv tool upgrade ignition-stack`, or `pip install --upgrade ignition-stack`. It only notifies; it never installs anything or restarts a running command.

The check is cached and runs at most once a day, uses a short network timeout so it never delays a command, and fails silently when you are offline. It is suppressed whenever output is not a terminal — scripts, pipes, and CI stay clean — and you can turn it off entirely:

```sh
export IGNITION_STACK_NO_UPDATE_CHECK=1
```

## Shell completion

Enable tab-completion for your shell once, then reload it:

```sh
ignition-stack --install-completion
```

This completes command names, subcommands, and option flags in bash, zsh, fish, and PowerShell. It also completes dynamic values where the choice is a known set: architecture names for `create --arch`, the `--edge-role` values, and catalog entry names for `modules download`. To print the completion script without installing it (to inspect or manage it yourself), use `ignition-stack --show-completion`.

## Prerequisites

- **Docker** with the Compose plugin. The CLI only generates files; Docker is what runs the stacks it writes. Any recent Docker Engine or Docker Desktop with `docker compose` works.
- **Python 3.11+**, which pipx uses to host the CLI.

You do not need a local Ignition install. Every gateway runs in a container from the official `inductiveautomation/ignition` image.

## Supported platforms

The CLI runs on macOS, Linux, and Windows. Continuous integration generates a project and runs the full test suite on all three on every change, and confirms that the files it writes use LF line endings everywhere, so a stack generated on Windows behaves the same as one generated on macOS or Linux. See [Continuous integration](../contribute/ci.md) for what each run proves.

## Next

Generate and run your first stack in the [Quickstart](./quickstart.md).
