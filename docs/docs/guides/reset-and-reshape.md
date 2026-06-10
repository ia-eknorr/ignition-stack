---
title: Reset and reshape a stack
description: Use ignition-stack reset and switch-profile to return a project to a clean baseline or move it to a different architecture profile.
---

# Reset and reshape a stack

Every generated project records how it was built in its [configuration record](../concepts/configuration-record.md), which lets you regenerate or reshape it without re-walking the wizard. Both commands here read that record, so they work on any project this CLI generated.

## Reset to a clean baseline

`reset` regenerates the project from its recorded config. It is the command to reach for between customer sessions: it returns the on-disk tree to exactly what `init` produced, so a demo someone left in a messy state comes back clean.

```sh
ignition-stack reset -C ./demo
```

It reads `.ignition-stack/config.json`, clears the generated tree, and re-runs generation. The record round-trips exactly, so the result is byte-for-byte identical to the original project. The `.ignition-stack/` record and the `modules/cache/` survive the clear, so pinned module downloads are not re-fetched.

`reset` rewrites files; it does not touch running containers. To clear runtime state too, pair it with a [wipe](./teardown.md):

```sh
ignition-stack wipe -C ./demo    # remove containers and volumes
ignition-stack reset -C ./demo   # regenerate the tree
cd demo && docker compose up -d  # bring the clean stack back up
```

## Reshape to a different profile

`switch-profile` moves the project to a different [architecture profile](../profiles/index.md) while keeping the choices that are not profile-specific. The recorded database, services, reverse-proxy, and edge intent carry over to the new profile; the gateway count and network layout change to match it.

```sh
ignition-stack switch-profile scaleout -C ./demo
```

It regenerates in place and re-records the result, so the reshaped project can be reset or switched again. A gateway dropped by the reshape is removed cleanly on the next `up` because the generated teardown uses `--remove-orphans`.

Redundancy is the exception to "choices carry over". The [paired role](./redundancy.md) is profile-specific - standalone pairs `gateway`, scaleout pairs `backend`, hub-and-spoke pairs `hub` - so a reshape can only keep it when the target profile hosts a gateway with that same role. When it can't (for example a redundant `standalone` reshaped to `scaleout`, which has no `gateway`), `switch-profile` drops the redundancy, prints an advisory, and completes the reshape as a non-redundant stack rather than failing. Re-apply `--redundant` on a topology that has a pairable role to bring it back.

### What carries across a reshape

The reshape carries the whole [service registry](./declarative-config.md#heterogeneous-stacks), not just the profile-level choices:

- **Service instances** survive, including custom ids and per-instance overrides (image, env, database credentials). A second database of a different kind comes along too.
- **Attachments re-map by role.** A gateway's attachments move to the gateway that plays the matching role in the new topology, matched by name first, then by role counterpart (`hub` <-> `backend`, `spoke` <-> `frontend`), then by role class (central roles versus edge-side roles). So a database the old `backend` owned lands on the new `hub`.
- **The IIoT pipeline** re-applies natively. If the stack had a broker with Transmission/Engine wiring, the overlay re-runs in the new topology: the edge-side role publishes (Transmission), the central role aggregates (Engine). Switching hub-and-spoke to scaleout moves the spokes' Transmission onto the frontends and the hub's Engine onto the backend.

When the new topology can't host something, `switch-profile` **drops it with a `note` advisory** and completes the reshape rather than failing:

- An attachment whose source gateway has no counterpart in the new topology is dropped.
- An attachment that would put a database on an Edge gateway, or give a gateway a second database connection, is dropped (those invariants always hold in the result).

The advisory names exactly what was dropped, so you can re-attach it by hand in a [config file](./declarative-config.md) if the new shape should keep it. The reshaped result always re-records cleanly and stays reshapeable.

A typical reshape loop:

```sh
ignition-stack switch-profile hub-and-spoke -C ./demo
cd demo && docker compose up -d
# ...demo the new shape, then move on
ignition-stack switch-profile standalone
```

## When to use which

- **`reset`** keeps the same shape and returns it to a known-clean baseline.
- **`switch-profile`** changes the shape, carrying your database and services across.

Both leave the `.ignition-stack/` record in place, so the project stays reshapeable no matter how many times you reset or switch it.
