---
title: Hub-and-spoke profile
description: One central hub gateway plus N Edge spoke gateways, with a RAM advisory on the spoke count.
---

# Hub-and-spoke profile

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
| Edition | spokes run Edge by default; override with `--edge-role` |

Each spoke gets its own host port, stepping up from 9089, so you can open any gateway directly from your laptop.

## The RAM advisory

A gateway costs roughly 1.5 GB of RAM, so spoke count is the knob most likely to overcommit a demo machine. The advisory has three tiers:

- **green** (1-4 spokes) — proceed without prompting.
- **yellow** (5-8 spokes) — the wizard asks for confirmation; the non-interactive path proceeds.
- **red** (9 or more spokes) — the wizard asks you to acknowledge the cost; the non-interactive path refuses unless you pass `--force`.

The wizard surfaces the estimate against available memory so the cost is visible at decision time. If you decline a yellow or red prompt, the wizard steps the count back down to 4 (the top of green) rather than stranding you.

## Run it

```sh
ignition-stack init demo --profile hub-and-spoke --spokes 3
ignition-stack init big-demo --profile hub-and-spoke --spokes 12 --force
```

The second command crosses the red tier, so `--force` is required to proceed non-interactively.

## When to use it

Use hub-and-spoke to show a central gateway aggregating many edge sites: remote Edge gateways feeding a hub. Keep the spoke count realistic for the host running the demo, and let the advisory be the guardrail.
