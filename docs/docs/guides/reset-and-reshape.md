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
