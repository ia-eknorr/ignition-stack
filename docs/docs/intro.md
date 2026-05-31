---
title: Overview
description: ignition-stack generates ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos and SE engagements.
slug: /
---

# ignition-stack

`ignition-stack` generates ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos and SE engagements. Pick an [architecture profile](./profiles/index.md), answer a few questions, and it writes a self-contained project: a hand-readable compose file, an `.env`, file-config seed resources the gateway reads on first boot, and a `POST-SETUP.md` listing only what could not be pre-seeded.

The goal is a stack that comes up with no UI prompts. Commissioning is env-driven and connections are file-seeded wherever Ignition 8.3 allows it, so `docker compose up -d` reaches a running gateway on its own.

```sh
pipx install git+https://github.com/ia-eknorr/ignition-stack.git
ignition-stack init demo --profile standalone
cd demo && docker compose up -d
```

That installs the CLI, generates a one-gateway-plus-Postgres project, and brings it up with a `VALID` database connection already wired in. See [Installation](./get-started/installation.md) and [Quickstart](./get-started/quickstart.md) for the full walkthrough.

## How it fits together

A generated project is plain Docker Compose with no hidden state. The CLI's job is to resolve your answers into that project and seed as much as Ignition 8.3 can read from the filesystem.

- **[Profiles](./profiles/index.md)** decide the shape of the stack: how many gateways, in what roles, on what network layout.
- **[Services](./services/index.md)** are the containers that run alongside the gateways: databases, MQTT brokers, an identity provider, simulators, and an automation engine.
- **[Concepts](./concepts/how-generation-works.md)** explain how generation, the capability resolver, lifecycle modes, and seeding work.
- **[Guides](./guides/reset-and-reshape.md)** cover the day-to-day tasks: resetting, reshaping, and tearing a stack down.
- **[Reference](./reference/cli.md)** is the lookup layer: every CLI command and the Ignition 8.3 seeding matrix.

## Two ways to keep a stack

By default `init` leaves a **one-shot** project: fully self-contained, no trace of the CLI. Pass `--keep-cli` for an **SE-demo** project that records its configuration so `ignition-stack reset` and `switch-profile` can regenerate or reshape it later. See [lifecycle modes](./concepts/lifecycle-modes.md) for the trade-off.

## New here?

Start with [Installation](./get-started/installation.md), then generate your first stack in the [Quickstart](./get-started/quickstart.md). If you want to understand what the tool does before running it, read [How generation works](./concepts/how-generation-works.md).
