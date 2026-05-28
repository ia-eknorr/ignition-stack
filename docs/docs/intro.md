---
title: Getting started
description: Install ignition-stack and generate your first ready-to-run Ignition 8.3 Docker Compose stack.
slug: /
---

# Getting started

`ignition-stack` generates ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos and SE engagements. You pick an [architecture profile](./profiles/index.md), answer a few questions, and it writes a self-contained project: a hand-readable compose file, an `.env`, file-config seed resources the gateway reads on first boot, and a `POST-SETUP.md` listing only what could not be pre-seeded.

The goal is a stack that comes up with no UI prompts. Commissioning is env-driven and connections are file-seeded wherever Ignition 8.3 allows it, so `docker compose up -d` reaches a running gateway on its own.

## Install

```sh
pipx install git+https://github.com/ia-eknorr/ignition-stack.git
```

This installs the CLI from `main`. To track a branch:

```sh
pipx install git+https://github.com/ia-eknorr/ignition-stack.git@<branch>
```

You also need Docker (with the Compose plugin) to run the stacks it generates.

## Generate your first stack

The fastest path is the default [standalone profile](./profiles/standalone.md): one gateway plus a pre-seeded Postgres.

```sh
ignition-stack init demo --profile standalone
cd demo
docker compose up -d
```

The gateway reaches `RUNNING` with no UI prompts. Open `http://localhost:9088` and log in with `admin` / `password`. The bundled Postgres comes up with a `VALID` gateway database connection already wired in.

Run `ignition-stack init demo` without `--profile` to walk the interactive wizard instead, which prompts for the profile, database, edition, and reverse proxy.

## What gets generated

```
demo/
  docker-compose.yaml    # gateways, database, bootstrap init container
  .env                   # every value the compose file references
  Makefile               # make up / make wipe shortcuts
  POST-SETUP.md          # only the steps that could not be pre-seeded
  scripts/
    docker-bootstrap.sh  # seeds the data volume on first boot
  services/
    ignition/            # the gateway's file-config resource tree
```

Everything in the project is meant to be read and tweaked by hand. Nothing is hidden behind the tool.

## Two ways to keep a stack

By default `init` leaves a **one-shot** project: fully self-contained, no trace of the CLI. Pass `--keep-cli` for an **SE-demo** project that records its configuration so `ignition-stack reset` and `switch-profile` can regenerate or reshape it later. See [lifecycle modes](./lifecycle/modes.md) for the trade-off.

## Where to go next

- [Architecture profiles](./profiles/index.md) — the four stack shapes and how to choose.
- [Services](./services/index.md) — the database, broker, IdP, simulator, and automation catalog.
- [How generation works](./architecture/how-generation-works.md) — the pipeline from your answers to the written tree.
- [CLI reference](./reference/cli.md) — every command, argument, and option.
- [Lifecycle modes](./lifecycle/modes.md) and [cleanup](./lifecycle/cleanup.md) — resetting, reshaping, and tearing stacks down.
