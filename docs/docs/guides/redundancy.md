---
title: Add a redundant gateway
description: Make a gateway role redundant so generation emits a master/backup pair that boots already paired over the Gateway Network.
---

# Add a redundant gateway

Ignition redundancy is a two-node arrangement: a **master** runs the system and a **backup** stays in sync and takes over if the master fails. Mark one gateway role redundant and `ignition-stack` expands it into an explicit master/backup pair, seeds each node's role, and wires the backup to the master so the pair comes up **already paired** - no UI clicks.

## Make a role redundant

Pass `--redundant <role>` to `init`, naming the single workhorse role to pair:

```sh
ignition-stack init demo --arch scale-out --redundant backend
ignition-stack init demo --arch hub-and-spoke --redundant hub
ignition-stack init demo --arch basic --redundant gateway
```

The wizard asks the same question (`Enable redundancy for the <role> gateway?`) for the eligible architectures.

Redundancy applies to a **single** gateway. Replicated tiers - scale-out frontends, hub-and-spoke spokes - scale out horizontally rather than failing over, so `--redundant frontend` and `--redundant spoke` are rejected. (Ignition has no active/active or N-way redundancy; it is master/backup only.)

Because the paired role is architecture-specific, redundancy does not always survive a [reshape](./reset-and-reshape.md): `switch-arch` drops it (with an advisory) when the target architecture has no gateway to pair, rather than failing the reshape.

## What gets generated

A role marked redundant resolves into two gateways. For `--redundant backend`:

| Node | Name | Role | Redundancy |
| --- | --- | --- | --- |
| master | `backend` | `master` | seeds `redundancy.xml` with `noderole=Master` |
| backup | `backend-backup` | `backup` | seeds `redundancy.xml` with `noderole=Backup`, points at the master |

Both nodes are visible in the dumped config (`init --dry-run`), each with its own port, volume, and network membership, so the [declarative artifact](declarative-config.md) shows exactly what runs. Generation adds, per node:

- A **pre-seeded `data/redundancy.xml`** that sets the redundancy role. Nothing sets the role via an environment variable, so this seed file is what actually forms the pair. The bootstrap drops it into the data volume on first boot.
- An **open incoming Gateway Network policy** (`Unrestricted`, no SSL) so the plain redundancy link is accepted and auto-approved.
- On the backup, a Gateway Network connection pointed at the master's service name on the **plain port 8088**.

The pair uses a plain (non-SSL) link on 8088 deliberately: it sidesteps the Gateway Network certificate-approval handshake that the SSL path (8060) would otherwise force into a manual UI step. See the [`redundancy` row of the seeding matrix](../reference/seeding-matrix.md) and `verification/redundancy-spike/` for the hands-on findings this is built on.

## Verify pairing

Bring the stack up and, within about a minute of both nodes reaching RUNNING, open the master gateway:

```sh
cd demo && docker compose up -d
```

On the master, go to **Config → System → Redundancy**. You should see **PEER CONNECTED: Yes** and **Sync Status: Good**; the backup pulls a one-time full config sync from the master. The generated `POST-SETUP.md` repeats this as a verification step - it is a check, not a manual procedure.

## Edge redundancy is Edge-to-Edge only

An Edge gateway can only fail over to another Edge gateway. Marking an Edge role redundant yields two Edge nodes; a mixed standard-master / Edge-backup pair is rejected with a clear error. Both nodes always inherit the role's edition.

## Licensing

Both nodes need a license. A discounted **backup-only** license forces the node into Backup mode (the mode is locked while it is applied). Unlicensed gateways run in a 2-hour trial - enough to demonstrate pairing, but failover longevity needs licenses. Licensing is a deployment concern; the generator does not manage it.

## Security: plain transport is demo-only

The generated pair uses a **plain, non-SSL** redundancy link with an `Unrestricted` Gateway Network policy. Redundancy replicates the entire gateway - projects, tags, and secrets - over that link, and the gateway itself logs `Require SSL setting is disabled, rendering Gateway Network connections insecure`. This is safe only on an isolated demo network.

For any cross-host or production deployment, switch to **SSL on port 8060 with approved certificates**. That reintroduces a one-time manual step: the backup's outbound SSL connection fails with `certificate_unknown` until you approve the master's Gateway Network server certificate at **Config → Network → Gateway Network → Connections → Outgoing → Remote Certificates → Approve**, then let the connection reconnect.
