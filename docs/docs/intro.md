---
title: Overview
description: ignition-stack generates ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos and SE engagements.
slug: /
---

# ignition-stack

`ignition-stack` generates ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos and SE engagements. Pick an [architecture](./architectures/index.md), answer a few questions, and it writes a self-contained project: a hand-readable compose file, an `.env`, file-config seed resources the gateway reads on first boot, and a `POST-SETUP.md` listing only what could not be pre-seeded.

The goal is a stack that comes up with no UI prompts. Commissioning is env-driven and connections are file-seeded wherever Ignition 8.3 allows it, so `docker compose up -d` reaches a running gateway on its own.

```sh
pipx install git+https://github.com/ia-eknorr/ignition-stack.git
ignition-stack init demo --arch basic
cd demo && docker compose up -d
```

That generates a one-gateway-plus-Postgres project and brings it up with a `VALID` database connection already wired in. See [Installation](./get-started/installation.md) and [Quickstart](./get-started/quickstart.md) for the full walkthrough.

## How it fits together

A generated project is plain Docker Compose with no hidden state. The CLI resolves your answers into that project and seeds as much as Ignition 8.3 can read from the filesystem.

- **[Architectures](./architectures/index.md)** decide the shape of the stack: how many gateways, in what roles, on what network layout.
- **[Services](./services/index.md)** are the containers that run alongside the gateways: databases, MQTT brokers, an identity provider, simulators, and an automation engine.
- **[Concepts](./concepts/how-generation-works.md)** explain how generation, the capability resolver, the configuration record, and seeding work.
- **[Guides](./guides/reset-and-reshape.md)** cover the day-to-day tasks: resetting, reshaping, and tearing a stack down.
- **[Reference](./reference/cli.md)** is the lookup layer: every CLI command and the Ignition 8.3 seeding matrix.
- **[Changelog](https://github.com/ia-eknorr/ignition-stack/blob/main/CHANGELOG.md)** is what's new in each release.

## Every stack is reshapeable

Every `init` writes a [configuration record](./concepts/configuration-record.md) alongside the runnable tree, so `ignition-stack reset` and `switch-arch` can regenerate or reshape any project later. The same record is portable: `init --dry-run` dumps it and `init -f` rebuilds from it, for a [fully declarative workflow](./guides/declarative-config.md).

## New here?

Start with [Installation](./get-started/installation.md), then generate your first stack in the [Quickstart](./get-started/quickstart.md). If you want to understand what the tool does before running it, read [How generation works](./concepts/how-generation-works.md).
