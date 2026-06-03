# ignition-stack

[![CI](https://github.com/ia-eknorr/ignition-stack/actions/workflows/ci.yml/badge.svg)](https://github.com/ia-eknorr/ignition-stack/actions/workflows/ci.yml)
[![Docs](https://github.com/ia-eknorr/ignition-stack/actions/workflows/docs.yml/badge.svg)](https://github.com/ia-eknorr/ignition-stack/actions/workflows/docs.yml)
[![Documentation](https://img.shields.io/badge/docs-ia--eknorr.github.io-blue)](https://ia-eknorr.github.io/ignition-stack/)

CLI that generates ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos and SE engagements. Picks an architecture profile, asks a few questions, writes a self-contained project with a hand-readable compose file, env, file-config seed resources, and a `POST-SETUP.md` listing only what could not be pre-seeded.

See [`docs/docs/reference/seeding-matrix.md`](docs/docs/reference/seeding-matrix.md) for which Ignition 8.3 connection types can be provisioned from the filesystem and env on a live 8.3.6 gateway. Full documentation lives in the [`docs/`](docs/) Docusaurus site.

## Install

```sh
pipx install ignition-stack
```

To install from source instead of PyPI - the latest off `main`, or a specific branch:

```sh
pipx install git+https://github.com/ia-eknorr/ignition-stack.git
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

| Command | What it does |
| --- | --- |
| `init <name>` | Generate a project at `./<name>/` from a profile and a few prompts. |
| `modules` | Download, verify, and manage the `.modl` / JDBC catalog. |
| `reset` | Re-run generation from an SE-demo project's recorded config. |
| `switch-profile` | Reshape an SE-demo project under a different profile. |
| `wipe` | Remove this project's containers and volumes only. |

See the [CLI reference](docs/docs/reference/cli.md) for every command, argument, and option.

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
