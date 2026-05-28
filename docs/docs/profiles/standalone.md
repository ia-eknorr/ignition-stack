---
title: Standalone profile
description: One Ignition 8.3 gateway plus an optional SQL database. The default starter shape.
---

# Standalone profile

One Ignition 8.3 gateway plus an optional SQL database. This is the default starter shape and the lightest demo to spin up, and it is the same walking skeleton the rest of the profiles build on.

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
ignition-stack init demo --profile standalone
```

Or pick `standalone` at the wizard's first prompt. Then:

```sh
cd demo
docker compose up -d
# open http://localhost:9088  (admin / password)
```

## When to use it

Reach for standalone when you want a single gateway with a working historian database and nothing else to reason about. Because Postgres seeds its gateway connection, the stack comes up with a `VALID` database connection and no manual setup. To run the gateway as Edge instead of standard, pass `--edge-role gateway`. To drop the database entirely, choose "No database" in the wizard.
