---
title: Scaleout profile
description: N frontend gateways + a backend networked together, with the network split on.
---

# Scaleout profile

One or more `frontend` gateways (user-facing UI and OPC-UA aggregation) plus a single `backend` (the database-connected workhorse), networked together through the gateway network, plus a shared SQL database. Every gateway runs the standard edition by default; the network split is on by default because separating the tiers is the whole point of the demo.

## What you get

With the default single frontend:

| Gateway | Role | HTTP port | Edition |
| --- | --- | --- | --- |
| `frontend` | frontend | 9088 | standard |
| `backend` | backend | 9089 | standard |

| | |
| --- | --- |
| Default database | [Postgres](../services/postgres.md) |
| Network split | on: `frontend` and `backend` networks |
| Reverse proxy | optional |

Every gateway joins both networks, so a frontend can reach the database the backend owns.

## Frontend count

`--frontends N` adds more frontends. A single frontend keeps the bare name `frontend`; two or more are numbered `frontend-1`, `frontend-2`, … with ports stepping up from 9088, and the backend takes the next free port.

```sh
ignition-stack init demo --profile scaleout --frontends 2
# frontend-1: 9088   frontend-2: 9089   backend: 9090
```

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

## Network split

The split is on by default. Force it off to put every gateway plus the database on a single shared network:

```sh
ignition-stack init demo --profile scaleout --no-network-split
```

## Edition choices

`--edge-role` controls which role (if any) runs Edge:

- `--edge-role none`: every gateway runs the standard edition (the default).
- `--edge-role frontend`: the frontends run Edge.
- `--edge-role backend`: the backend runs Edge.

## Reverse proxy

`--reverse-proxy traefik` scaffolds the [ia-eknorr/traefik-reverse-proxy](https://github.com/ia-eknorr/traefik-reverse-proxy) setup under `reverse-proxy/` (override the location with `--proxy-path`). Omit it for plain host-port mapping.

## When to use it

Use scaleout to demonstrate a multi-tier architecture: one or more frontends aggregating data and a backend owning the database and history. The gateway-network link between the tiers is the piece an SE typically confirms after boot; see the [seeding matrix](../reference/seeding-matrix.md) for what travels in the seed and what is left to the gateway UI.
