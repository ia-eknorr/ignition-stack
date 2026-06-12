---
title: Quickstart
description: Generate your first Ignition 8.3 stack and bring it up with a working database connection in three commands.
---

# Quickstart

This walks you from an installed CLI to a running gateway with a `VALID` database connection. If you have not installed the CLI yet, start with [Installation](./installation.md).

## Generate and run a stack

The fastest path is the default [basic architecture](../architectures/basic.md): one gateway plus a pre-seeded Postgres.

```sh
ignition-stack create demo --arch basic
cd demo
docker compose up -d
```

The gateway reaches `RUNNING` with no UI prompts. Open `http://localhost:9088` and log in with `admin` / `password`. The bundled Postgres comes up with a `VALID` gateway database connection already wired in, so there is nothing to configure by hand.

## Use the wizard instead

Run `create` without `--arch` to walk the interactive [wizard](../guides/wizard.md). It opens architecture-first, then prompts for the database, edition, IIoT, services, and exposure, ending in a summary you can preview, tweak, or generate:

```sh
ignition-stack create demo
```

The wizard is the discoverable path; `--arch` (and the other flags in the [CLI reference](../reference/cli.md)) is the scriptable one. Both produce the same kind of project.

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

Everything in the project is meant to be read and tweaked by hand. Nothing is hidden behind the tool, and the basic stack defers no manual steps, so its `POST-SETUP.md` simply says none are required. See [How generation works](../concepts/how-generation-works.md) for the pipeline that produces this tree.

## Tear it down

```sh
make wipe          # from inside the project
# or, without make
docker compose -p demo down -v --remove-orphans
```

Both remove only this project's containers and volumes. See [Tear down a stack](../guides/teardown.md) for how the scoping works.

## Where to go next

- [Architectures](../architectures/index.md) decide the shape of the stack.
- [Services](../services/index.md) are the databases, brokers, and other containers you can add.
- [The configuration record](../concepts/configuration-record.md) explains how every stack records itself and how to use `create -f` to recreate or clone it.
