# ignition-stack

CLI that generates ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos and SE engagements. Picks an architecture profile, asks a few questions, writes a self-contained project with a hand-readable compose file, env, file-config seed resources, and a `POST-SETUP.md` listing only what could not be pre-seeded.

Status: under construction. The seedability matrix and the walking skeleton are in. See [`docs/ignition-seeding-matrix.md`](docs/ignition-seeding-matrix.md) for which Ignition 8.3 connection types can be provisioned from the filesystem and env on a live 8.3.6 gateway.

## Install

```sh
pipx install git+https://github.com/ia-eknorr/ignition-stack.git
```

This pulls the latest released CLI off `main`. To track a branch:

```sh
pipx install git+https://github.com/ia-eknorr/ignition-stack.git@<branch>
```

## Quickstart

Generate a project and bring it up:

```sh
ignition-stack init demo
cd demo
docker compose up -d
```

The gateway reaches RUNNING with no UI prompts. The admin user is `admin / password` and the gateway is at `http://localhost:9088`. The default Postgres credentials are `ignition / ignition` on the `db` service.

Everything that ships in the generated project is hand-readable: `docker-compose.yaml`, `.env`, `scripts/docker-bootstrap.sh`, and a `services/ignition/` resources tree the gateway reads on first boot.

## Commands

| Command | What it does | Phase |
| --- | --- | --- |
| `init <name>` | Generate a standalone+Postgres project at `./<name>/`. | 2 (this phase) |
| `modules` | Manage the `.modl` / JDBC catalog. | 3 |
| `reset` | Re-run generation from the recorded config. | 7 |
| `wipe` | Remove this project's containers and volumes only. | 7 |

`modules`, `reset`, and `wipe` are registered now so the command surface stays stable; they print a "not yet implemented" message until the relevant phase lands.

## What gets generated

```
demo/
  docker-compose.yaml         # one gateway, one Postgres, one bootstrap init container
  .env                        # values referenced by docker-compose.yaml
  scripts/
    docker-bootstrap.sh       # seeds /data, sets gateway-network UUID, hands ownership to uid 2003
  services/
    ignition/
      config/resources/core/config-mode.json
      config/resources/dev/config-mode.json
      projects/.gitkeep
```

The bootstrap script is run once per data volume. It copies the gateway's base data into a named volume, layers the project's `services/ignition/` tree on top, sets a deterministic gateway-network UUID derived from the project name, and hands ownership of `/data` to uid 2003 so the gateway can write its resource caches.

Commissioning is fully env-driven (`ACCEPT_IGNITION_EULA=Y`, `GATEWAY_ADMIN_PASSWORD`, `IGNITION_EDITION`), so first boot needs no UI.
