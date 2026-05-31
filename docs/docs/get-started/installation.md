---
title: Installation
description: Install the ignition-stack CLI with pipx, and the Docker prerequisite for running the stacks it generates.
---

# Installation

`ignition-stack` is a Python CLI. Install it with [pipx](https://pipx.pypa.io/) so it lands in its own isolated environment:

```sh
pipx install git+https://github.com/ia-eknorr/ignition-stack.git
```

This installs the CLI from `main`. To track a branch instead:

```sh
pipx install git+https://github.com/ia-eknorr/ignition-stack.git@<branch>
```

Confirm it is on your path:

```sh
ignition-stack --version
```

## Prerequisites

- **Docker** with the Compose plugin. The CLI only generates files; Docker is what runs the stacks it writes. Any recent Docker Engine or Docker Desktop with `docker compose` works.
- **Python 3.11+**, which pipx uses to host the CLI.

You do not need a local Ignition install. Every gateway runs in a container from the official `inductiveautomation/ignition` image.

## Next

Generate and run your first stack in the [Quickstart](./quickstart.md).
