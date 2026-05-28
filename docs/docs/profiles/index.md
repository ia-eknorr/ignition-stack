---
title: Architecture profiles
description: The four canned stack shapes ignition-stack can generate, and how to pick one.
---

# Architecture profiles

A profile is the high-level shape of a stack: how many gateways, in what roles, on what network layout. You pick one at the start of the wizard, or name it directly with `ignition-stack init <name> --profile <slug>`. Every profile resolves to the same kind of project; they differ only in the gateways and topology they set up.

## The four profiles

| Profile | Gateways | Network split | Edge default | Use it for |
| --- | --- | --- | --- | --- |
| [standalone](./standalone.md) | 1 | off | none | The default starter stack: one gateway + a database |
| [scaleout](./scaleout.md) | 2 (frontend + backend) | on | frontend | A two-tier frontend/backend demo |
| [hub-and-spoke](./hub-and-spoke.md) | 1 hub + N spokes | off | spokes | A central hub aggregating many edge sites |
| [mcp-n8n](./mcp-n8n.md) | 1 + n8n | off | none | AI-driven automation with the EA MCP module |

Every profile defaults its database to [Postgres](../services/postgres.md) and offers the optional reverse-proxy scaffold.

## Choosing a profile

- Start with **standalone** if you want one gateway and a working historian. It is the lightest stack and the baseline everything else extends.
- Choose **scaleout** to show a frontend/backend split across the gateway network, the canonical multi-gateway field shape.
- Choose **hub-and-spoke** to show one hub aggregating many Edge spokes. Watch the [RAM advisory](./hub-and-spoke.md#the-ram-advisory) as the spoke count grows.
- Choose **mcp-n8n** for an n8n automation demo wired to the gateway through the Early-Access MCP module.

## Lifecycle

The profile you pick is recorded with the stack when you use SE-demo mode (`--keep-cli`). `ignition-stack switch-profile <slug>` then reshapes the same stack under a different profile, carrying your database, services, reverse-proxy, and edge choices across. See [lifecycle modes](../lifecycle/modes.md).
