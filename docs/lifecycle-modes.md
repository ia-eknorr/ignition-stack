# Lifecycle modes

Every `ignition-stack init` runs the same single generation pass, then chooses what to leave behind. That choice is the lifecycle mode, and there are two.

## One-shot (default)

`ignition-stack init demo --profile standalone` produces a self-contained project: `docker-compose.yaml`, `.env`, the seed tree, a `Makefile`, and `POST-SETUP.md`. No CLI primitives are written - there is no `.ignition-stack/` directory. The result is something you can hand to anyone with Docker and nothing else; they run `docker compose up -d` and never need `ignition-stack` installed.

Because a one-shot project records nothing about how it was generated, `ignition-stack reset` and `switch-profile` refuse to run against it. `make wipe` still works - it scopes itself to the compose project name read from `.env`.

## SE-demo (`--keep-cli`)

`ignition-stack init demo --profile scaleout --keep-cli` writes the same project, plus a `.ignition-stack/config.json` holding the resolved configuration. That recorded config is the one primitive the lifecycle commands need:

- **`ignition-stack reset`** reads the record, clears the generated tree (keeping `.ignition-stack/` and the `modules/cache/` so pinned downloads survive), and re-runs generation. The record round-trips exactly, so a reset reproduces the original project byte-for-byte.
- **`ignition-stack switch-profile <name>`** reads the record, carries its database, services, reverse-proxy, and edge intent over to a different architecture profile, regenerates in place, and re-records the result.

SE-demo mode is the right pick when you iterate on a demo stack repeatedly - reshaping it between profiles, resetting it to a clean baseline between customer sessions - without re-walking the wizard each time.

## Choosing a mode

| | One-shot | SE-demo (`--keep-cli`) |
| --- | --- | --- |
| `.ignition-stack/` written | no | yes |
| Self-contained handoff | yes | yes (plus a small dot-dir) |
| `reset` / `switch-profile` | unavailable | available |
| `make wipe` | available | available |

Both modes emit an identical runnable stack; the only difference is whether the generation primitives persist for repeated reshaping.
