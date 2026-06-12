---
title: Basic architecture
description: One Ignition 8.3 gateway plus an optional SQL database. The default starter shape.
---

# Basic architecture

One Ignition 8.3 gateway plus an optional SQL database — the default starter shape, and the skeleton the other architectures build on.

## What you get

| | |
| --- | --- |
| Gateways | 1 (`gateway`) |
| HTTP port | 9088 |
| Edition | standard, or Edge via `--edge-role gateway` |
| Default database | [Postgres](../services/postgres.md), with a pre-seeded connection |
| Network split | off (single bridge network) |
| Reverse proxy | optional (the wizard can scaffold `ia-eknorr/traefik-reverse-proxy`) |

The single gateway is named `gateway`, so its per-gateway `.env` keys collapse to the familiar `GATEWAY_*` form.

## Run it

```sh
ignition-stack create demo --arch basic
```

Or pick `basic` at the wizard's first prompt. Then:

```sh
cd demo
docker compose up -d
# open http://localhost:9088  (admin / password)
```

## When to use it

Reach for basic when you want a single gateway with a working historian and nothing else to reason about. Postgres seeds its gateway connection, so the stack comes up `VALID` with no manual setup. Pass `--edge-role gateway` to run the gateway as Edge, or choose `None` at the database prompt to drop the database.
