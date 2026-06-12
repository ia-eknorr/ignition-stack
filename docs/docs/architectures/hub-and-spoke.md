---
title: Hub-and-spoke architecture
description: One central hub gateway plus N Edge spoke gateways, with a RAM advisory on the spoke count.
---

# Hub-and-spoke architecture

One central hub gateway plus N spoke gateways. Spokes are lightweight and deployed close to the data, so they default to the Edge edition; the hub aggregates them. The spoke count drives a RAM advisory so a large demo does not quietly outgrow the host.

## What you get

| Gateway | Role | HTTP port | Edition |
| --- | --- | --- | --- |
| `hub` | hub | 9088 | standard |
| `spoke-1` | spoke | 9089 | Edge by default |
| `spoke-2` | spoke | 9090 | Edge by default |
| `spoke-N` | spoke | 9088 + N | Edge by default |

| | |
| --- | --- |
| Gateways | 1 hub + N spokes |
| Default database | [Postgres](../services/postgres.md) |
| Network split | off (hub-and-spoke is typically single-tier) |
| Edition | spokes run Edge by default; the hub is always standard |

Each spoke gets its own host port, stepping up from 9089, so you can open any gateway directly from your laptop.

## The RAM advisory

A gateway costs roughly 1.5 GB of RAM, so spoke count is the knob most likely to overcommit a demo machine. The advisory has three tiers:

- **green** (1-4 spokes): proceed without prompting.
- **yellow** (5-8 spokes): the wizard asks for confirmation; the non-interactive path proceeds.
- **red** (9 or more spokes): the wizard asks you to acknowledge the cost; the non-interactive path refuses unless you pass `--force`.

The wizard surfaces the estimate against available memory so the cost is visible at decision time. If you decline a yellow or red prompt, the wizard steps the count back down to 4 (the top of green) rather than stranding you.

## Run it

```sh
ignition-stack create demo --arch hub-and-spoke --spokes 3
ignition-stack create big-demo --arch hub-and-spoke --spokes 12 --force
```

The second command crosses the red tier, so `--force` is required to proceed non-interactively.

## Gateway network

Each spoke is joined to the hub over Ignition's Gateway Network, and the link auto-forms on first boot with no UI approval. Every spoke opens a plain (non-SSL, port 8088) outgoing connection to the `hub`, and each gateway runs an `Unrestricted` incoming policy so the connection is accepted on sight. The generated `POST-SETUP.md` carries a *verification* readout — naming both ends of each link — rather than a manual approval step.

Plain transport is a deliberate demo-only default. For a cross-host or production deployment, switch to SSL on port 8060 with approved certificates, which reintroduces a one-time cert-approval step; the [redundancy guide](../guides/redundancy.md#security-plain-transport-is-demo-only) walks through that variant.

## Editions

Spokes default to Edge; pass `--edge-role none` to run them as standard instead. The hub is always standard and cannot be made Edge: the spokes aggregate _into_ it, and Edge is a leaf edition that can't be an aggregation target.

## When to use it

Use hub-and-spoke to show a central gateway aggregating many edge sites: remote Edge gateways feeding a hub. Keep the spoke count realistic for the host running the demo, and let the advisory be the guardrail.
