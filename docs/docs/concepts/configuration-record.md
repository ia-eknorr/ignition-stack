---
title: The configuration record
description: Every generated project records its resolved configuration, the single artifact that powers reset, switch-profile, and the declarative dump/build loop.
---

# The configuration record

Every `ignition-stack init` runs one generation pass and writes the usual runnable tree - `docker-compose.yaml`, `.env`, the seed tree, a `Makefile`, and `POST-SETUP.md` - plus one more file: `.ignition-stack/config.json`, the **resolved configuration** the stack was built from.

That record is a single source of truth. It holds the fully-expanded topology - every gateway, its ports and edition, the database, the selected services, the network layout - after the resolver has made implicit dependencies explicit. It is exactly what the generator consumed, so reproducing it reproduces the stack.

## What the record powers

- **[`reset`](../guides/reset-and-reshape.md#reset-to-a-clean-baseline)** reads the record, clears the generated tree (keeping `.ignition-stack/` and `modules/cache/` so pinned downloads survive), and re-runs generation. The record round-trips exactly, so a reset reproduces the original project byte-for-byte.
- **[`switch-profile`](../guides/reset-and-reshape.md#reshape-to-a-different-profile)** reads the record, carries its database, services, reverse-proxy, and edge intent over to a different architecture profile, regenerates in place, and re-records the result.
- **The [declarative dump/build loop](../guides/declarative-config.md)** is the same artifact made portable: `init --dry-run` prints the resolved config to stdout, and `init -f <file>` builds from a saved copy. The record on disk and the file you dump are the same schema, so anything you can reset you can also dump, edit, and rebuild.

## Why it is always written

The record is small, lives in a dot-directory out of the way of the hand-readable files, and costs nothing to ignore if you don't need it. Making it unconditional means every project is reshapeable and every project can be dumped - there is no mode to choose at `init` time and no project that is stuck because it forgot to record itself.

`make wipe` does not need the record - it scopes itself to the compose project name, falling back to `COMPOSE_PROJECT_NAME` in `.env` if the record was removed - so a project stays tearable even if you delete `.ignition-stack/`.
