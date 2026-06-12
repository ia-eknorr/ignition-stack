---
title: Architectures
description: The system architectures ignition-stack can generate, mirroring Ignition's documented Basic, Scale Out, and Hub and Spoke shapes.
---

# Architectures

An architecture is the high-level shape of a stack: how many gateways, in what roles, on what network layout. The slugs mirror [Ignition's documented system architectures](https://docs.inductiveautomation.com/docs/8.3/system-architectures) — Basic, Scale Out, Hub and Spoke. You pick one at the start of the wizard, or name it directly with `ignition-stack create <name> --arch <slug>`. Every architecture resolves to the same kind of project; they differ only in the gateways and topology they set up. Architectures are editable presets, not forks: the [wizard's composer](../guides/wizard.md) lets you attach services per gateway from one as a starting point.

## The architectures

| Architecture | Gateways | Network split | Edge default | Use it for |
| --- | --- | --- | --- | --- |
| [basic](./basic.md) | 1 | off | none | The default starter stack: one gateway + a database |
| [scale-out](./scale-out.md) | 2 (frontend + backend) | on | none | A two-tier frontend/backend demo |
| [hub-and-spoke](./hub-and-spoke.md) | 1 hub + N spokes | off | spokes | A central hub aggregating many edge sites |

Every architecture defaults its database to [Postgres](../services/postgres.md) and offers the optional reverse-proxy scaffold.

## Choosing an architecture

- Start with **basic** if you want one gateway and a working historian. It is the lightest stack and the baseline everything else extends.
- Choose **scale-out** to show a frontend/backend split across the gateway network, the canonical multi-gateway field shape.
- Choose **hub-and-spoke** to show one hub aggregating many Edge spokes. Watch the [RAM advisory](./hub-and-spoke.md#the-ram-advisory) as the spoke count grows.

## Configuration record

The architecture you pick is saved in the stack's [configuration record](../concepts/configuration-record.md). Pass that record back to `ignition-stack create <name> -f` to recreate or clone the stack — the positional name overrides the recorded one, so the same file can produce a differently-named project.
