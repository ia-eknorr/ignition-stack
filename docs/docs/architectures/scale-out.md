---
title: Scale-out architecture
description: N frontend gateways + a backend networked together, with the network split on.
---

# Scale-out architecture

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
ignition-stack create demo --arch scale-out --frontends 2
# frontend-1: 9088   frontend-2: 9089   backend: 9090
```

## Run it

```sh
ignition-stack create demo --arch scale-out
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
ignition-stack create demo --arch scale-out --no-network-split
```

The Docker network split is separate from the Gateway Network described below: one isolates container traffic, the other is Ignition's gateway-to-gateway link.

## Gateway network

The frontends and the backend are joined over Ignition's Gateway Network, and that link auto-forms on first boot with no UI approval. Each frontend opens a plain (non-SSL, port 8088) outgoing connection to the `backend`, and every gateway runs an `Unrestricted` incoming policy so the connection is accepted on sight. The generated `POST-SETUP.md` carries a *verification* readout — naming both ends of each link — rather than a manual approval step.

Plain transport is a deliberate demo-only default. For a cross-host or production deployment, switch to SSL on port 8060 with approved certificates, which reintroduces a one-time cert-approval step; the [redundancy guide](../guides/redundancy.md#security-plain-transport-is-demo-only) walks through that variant.

## Edition choices

`--edge-role` controls which role (if any) runs Edge:

- `--edge-role none`: every gateway runs the standard edition (the default).
- `--edge-role frontend`: the frontends run Edge.

The `backend` cannot run Edge. The frontends open a Gateway Network link _into_ the backend, and Edge is a leaf edition that can't be an aggregation target, so `--edge-role backend` is rejected at config time with that guidance. Edge frontends are fine — an `edge → standard` aggregation link is allowed.

## Redundancy

The `backend` is the single workhorse, so it is the role you pair for failover. Add `--redundant backend` to expand it into a master/backup pair that boots already paired; see [Add a redundant gateway](../guides/redundancy.md). The replicated `frontend` tier scales out horizontally instead, so it cannot be paired.

## Reverse proxy

`--reverse-proxy external` joins a Traefik proxy you already run; `--reverse-proxy scaffold` also lays down the [ia-eknorr/traefik-reverse-proxy](https://github.com/ia-eknorr/traefik-reverse-proxy) README under `reverse-proxy/` (override the location with `--proxy-path`). Omit it for plain host-port mapping. See the [reverse-proxy guide](../guides/reverse-proxy.md).

## When to use it

Use scale-out to demonstrate a multi-tier architecture: one or more frontends aggregating data and a backend owning the database and history. The gateway-network link between the tiers forms automatically on first boot; see the [seeding matrix](../reference/seeding-matrix.md) for what travels in the seed and what is left to the gateway UI.
