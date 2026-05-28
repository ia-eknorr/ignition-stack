# Cleanup

Tearing a stack down should remove exactly what `ignition-stack init` brought up - this project's containers, networks, and volumes - and nothing else on the host. Both teardown paths honour that boundary by pinning the compose project name.

## How scoping works

Docker Compose labels every resource it creates with `com.docker.compose.project=<name>`. The generated stack sets that name explicitly through `COMPOSE_PROJECT_NAME` in `.env`, so the gateway containers, the project bridge network, and the named data volumes all carry the same label.

A teardown that names the project resolves only those labelled resources:

```
docker compose -p demo down -v --remove-orphans
```

- `-p demo` scopes every operation to the `demo` project label.
- `down` stops and removes the project's containers and its default network.
- `-v` removes the named volumes declared in the project's compose file.
- `--remove-orphans` clears containers that were once in the compose file but have since been removed from it (for example, a gateway dropped by a `switch-profile`).

Nothing here is host-wide. There is no `docker system prune`, no `docker volume prune`, and no `docker volume rm` against an unscoped list - a different stack's volumes are invisible to a `-p`-pinned `down`.

## Two ways to run it

### `make wipe`

The generated `Makefile` carries the project name as a literal and wraps the scoped teardown:

```make
PROJECT := demo

wipe:
  $(COMPOSE) -p $(PROJECT) down -v --remove-orphans
```

`make wipe` works in any generated project, one-shot or SE-demo, because it needs nothing beyond the compose file and the baked-in name. It is the path to reach for when `ignition-stack` is not installed on the machine running the demo.

### `ignition-stack wipe`

The CLI resolves the same command, then runs it from the project directory:

```
ignition-stack wipe -C ./demo            # tear it down
ignition-stack wipe -C ./demo --dry-run  # print the command, run nothing
```

`--dry-run` prints the exact `docker compose -p demo down -v --remove-orphans` it would execute, which is handy for confirming the scope before committing to it.

To resolve the project name, `wipe` prefers an SE-demo project's recorded config and falls back to `COMPOSE_PROJECT_NAME` in `.env`. A one-shot project keeps no record, but its `.env` still carries the name, so `wipe` scopes correctly either way. Pointed at a directory that holds neither, it exits without guessing.

## What survives a wipe

A wipe removes runtime state, not the project on disk. The generated tree - `docker-compose.yaml`, `.env`, the seed directories, the `Makefile` - is untouched, so `docker compose up -d` brings the same stack back. For an SE-demo project the `.ignition-stack/` record survives too; pair `wipe` with [`reset`](./modes.md) to return to a clean baseline between customer sessions without re-walking the wizard.
