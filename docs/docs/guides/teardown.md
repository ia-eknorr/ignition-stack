---
title: Tear down a stack
description: How to scope teardown to this project's containers, networks, and volumes — and nothing else on the host.
---

# Tear down a stack

Tearing a stack down should remove exactly what `ignition-stack create` brought up — this project's containers, networks, and volumes — and nothing else on the host. Both teardown paths below honour that boundary by pinning the compose project name.

## How scoping works

Docker Compose labels every resource it creates with `com.docker.compose.project=<name>`. The generated stack sets that name explicitly through `COMPOSE_PROJECT_NAME` in `.env`, so the gateway containers, the project bridge network, and the named data volumes all carry the same label.

A teardown that names the project resolves only those labelled resources:

```
docker compose -p demo down -v --remove-orphans
```

- `-p demo` scopes every operation to the `demo` project label.
- `down` stops and removes the project's containers and its default network.
- `-v` removes the named volumes declared in the project's compose file.
- `--remove-orphans` clears containers that were once in the compose file but have since been removed from it.

Nothing here is host-wide. There is no `docker system prune`, no `docker volume prune`, and no `docker volume rm` against an unscoped list — a different stack's volumes are invisible to a `-p`-pinned `down`.

## Two ways to run it

### `make wipe`

The generated `Makefile` carries the project name as a literal and wraps the scoped teardown:

```make
PROJECT := demo

wipe:
  $(COMPOSE) -p $(PROJECT) down -v --remove-orphans
```

`make wipe` works in any generated project because it needs nothing beyond the compose file and the baked-in name. It is the path to reach for when `ignition-stack` is not installed on the machine running the demo.

### The raw docker compose command

The same command works anywhere, including on Windows where `make` may not be available:

```sh
docker compose -p demo down -v --remove-orphans
```

Replace `demo` with your compose project name (`COMPOSE_PROJECT_NAME` in `.env`). The `-p` flag scopes the teardown exactly as `make wipe` does — same command, no intermediary.

## What survives a wipe

A wipe removes runtime state, not the project on disk. The generated tree — `docker-compose.yaml`, `.env`, the seed directories, the `Makefile` — is untouched, so `docker compose up -d` brings the same stack back. The `.ignition-stack/` record survives too; use it with [`create <name> -f`](../concepts/configuration-record.md) to recreate or clone the stack without re-walking the wizard.
