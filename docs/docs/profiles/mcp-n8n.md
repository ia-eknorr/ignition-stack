---
title: MCP + n8n profile
description: One Ignition gateway plus n8n, scaffolded for the Early-Access Ignition MCP module.
---

# MCP + n8n profile

One Ignition gateway plus the [n8n](../services/n8n.md) workflow engine, scaffolded with a `modules/dropin/` directory for the Early-Access Ignition MCP module. The MCP module bridges Ignition and n8n; because it is gated behind an EA survey, this profile prepares the drop-in but does not download it.

## What you get

| | |
| --- | --- |
| Gateways | 1 (`gateway`) |
| HTTP port | 9088 |
| Services | [n8n](../services/n8n.md) (always bundled) |
| Default database | [Postgres](../services/postgres.md) |
| MCP module | manual drop-in into `modules/dropin/` |

The profile sets the `mcp_dropin` flag, which tells the generator to lay down a `modules/dropin/` directory with a README plus a matching entry in `POST-SETUP.md`. On boot, the bootstrap copies anything it finds in both `modules/cache/` and `modules/dropin/` into the gateway volume.

## Run it

```sh
ignition-stack init demo --profile mcp-n8n
```

Then complete the MCP module drop-in:

1. Request the module from the EA survey at [inductiveautomation.com/early-access](https://inductiveautomation.com/early-access).
2. Drop the resulting `.modl` into `modules/dropin/`.
3. `docker compose up -d`.

The generated `POST-SETUP.md` carries the same steps for the stack you produced.

## When to use it

Use this profile to demonstrate AI-driven automation against a live gateway: n8n orchestrates workflows, and the MCP module exposes the gateway to them. Everything but the EA-gated `.modl` is generated for you.
