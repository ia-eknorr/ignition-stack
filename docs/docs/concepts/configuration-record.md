---
title: The configuration record
description: Every generated project records its resolved configuration — the artifact you can pass back to create -f to recreate or clone a stack.
---

# The configuration record

Every `ignition-stack create` writes the usual runnable tree — `docker-compose.yaml`, `.env`, the seed tree, a `Makefile`, and `POST-SETUP.md` — plus one more file: `.ignition-stack/config.json`, the **resolved configuration** the stack was built from.

That record is a single source of truth. It holds the fully-expanded topology — every gateway, its ports and edition, the database, the selected services, the network layout — after the resolver has made implicit dependencies explicit. It is exactly what the generator consumed, so reproducing it reproduces the stack.

## Re-running and cloning with `create -f`

Pass the record back to `create` to recreate or clone the stack:

```sh
# Recreate the same stack (after wiping it, for example)
ignition-stack create demo -f demo/.ignition-stack/config.json

# Clone it under a new name — the positional name overrides the recorded one
ignition-stack create demo-copy -f demo/.ignition-stack/config.json
```

The positional name argument always overrides the name in the file, so the same config file can produce as many distinct compose projects as you need. Everything else — architecture, database, services, editions, ports — comes from the file.

Editing the file before re-running is a supported workflow. The format is the same YAML or JSON schema that `create --dry-run` dumps, so you can adjust any field and rebuild from the edited copy.

## The declarative dump/build loop

`create --dry-run` dumps the resolved config to stdout without writing anything to disk:

```sh
ignition-stack create demo --arch scale-out --dry-run > arch.yml
```

That dump is byte-identical to the `.ignition-stack/config.json` the generator would write, so you can inspect it, version it, and rebuild from it:

```sh
ignition-stack create demo -f arch.yml
```

See [Declarative config](../guides/declarative-config.md) for the full dump/edit/rebuild guide.

## Why it is always written

The record is small, lives in a dot-directory out of the way of the hand-readable files, and costs nothing to ignore if you don't need it. Making it unconditional means every project is re-runnable and every project can be dumped — there is no mode to choose at `create` time and no project that is stuck without a record.

`make wipe` does not need the record — it scopes teardown to the compose project name baked into the `Makefile` — so a project stays tearable even if you delete `.ignition-stack/`.
