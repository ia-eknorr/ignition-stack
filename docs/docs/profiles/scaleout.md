---
title: Scaleout profile
description: Frontend + backend Ignition gateways networked together, with the network split on.
---

# Scaleout profile

Two Ignition gateways networked together through the gateway network: a `frontend` (user-facing UI and OPC-UA aggregation) and a `backend` (the database-connected workhorse), plus a shared SQL database. This is the canonical scaleout shape in the field, and the network split is on by default because separating the tiers is the whole point of the demo.

## What you get

| Gateway | Role | HTTP port | Edition |
| --- | --- | --- | --- |
| `frontend` | frontend | 9088 | Edge by default |
| `backend` | backend | 9089 | standard |

| | |
| --- | --- |
| Default database | [Postgres](../services/postgres.md) |
| Network split | on: `frontend` and `backend` networks |
| Reverse proxy | optional |

Both gateways join both networks, so the frontend can reach the database the backend owns. The frontend defaults to the Edge edition because that matches the typical field deployment; override with `--edge-role`.

## Run it

```sh
ignition-stack init demo --profile scaleout
```

Then:

```sh
cd demo
docker compose up -d
# frontend: http://localhost:9088   backend: http://localhost:9089
```

## Edition choices

`--edge-role` controls which role runs Edge:

- `--edge-role frontend` — frontend runs Edge (the default).
- `--edge-role backend` — backend runs Edge instead.
- `--edge-role none` — both gateways run the standard edition.

## When to use it

Use scaleout to demonstrate a two-tier architecture: a lightweight Edge frontend aggregating data and a standard backend owning the database and history. The gateway-network link between the two is the piece an SE typically confirms after boot; see the [seeding matrix](../reference/seeding-matrix.md) for what travels in the seed and what is left to the gateway UI.
