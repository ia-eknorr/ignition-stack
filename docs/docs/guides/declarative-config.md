---
title: Dump, edit, and rebuild a stack
description: Use init --dry-run to dump a stack's resolved configuration, edit it by hand, and rebuild it with init -f for a fully declarative workflow.
---

# Dump, edit, and rebuild a stack

A stack is fully described by its resolved [configuration record](../concepts/configuration-record.md). `init` can print that config instead of building (`--dry-run`) and build from a saved copy instead of a profile (`-f`). Together they make generation declarative: dump what a profile would build, hand-edit the parts you want to change, and rebuild from the file.

## Dump the resolved config

`--dry-run` resolves the config and prints it to stdout without writing any files. It shows the full build input - project name, every gateway with its ports and edition, the database, the selected services, and the network layout - after the resolver has expanded implicit dependencies.

```sh
ignition-stack init demo --profile scaleout --dry-run > arch.yml
```

The default format is YAML, ordered to read top-down (`name` first). Use `--output-format json` for JSON:

```sh
ignition-stack init demo --profile scaleout --dry-run --output-format json > arch.json
```

`--dry-run` writes nothing to disk - not even the project directory - so it is safe to run anywhere just to inspect what a set of flags would produce.

## Edit and rebuild

Open the dumped file and change what you need - bump a gateway's `memory_mb`, add a service, flip `network_split`, rename a gateway. Then build from it with `-f`:

```sh
ignition-stack init demo -f arch.yml
```

The file is run through the same resolver and writer as a profile build, so a project built from a profile and one built from that profile's dump are byte-identical. The project name argument wins over the file's `name`, so you can stamp out the same topology under different names:

```sh
ignition-stack init customer-b -f arch.yml
```

`-f` is mutually exclusive with `--profile` (the file already specifies the whole topology) and with the wizard (it never prompts). Combining `-f` with `--profile` is an error.

## Validation

The file is validated against the same schema the wizard and profiles produce. An unknown field, a bad enum, or a malformed document fails with a readable message and a non-zero exit - never a traceback:

```text
$ ignition-stack init demo -f broken.yml
error: invalid config in 'broken.yml':
  - database.kind: unsupported database kind 'oracle'; supported: mariadb, mongo, mysql, postgres
```

## Authoring from scratch

Because the format is the schema, an external tool - an architecture builder, a script, a templating step - can emit a config file and hand it to `ignition-stack -f` to materialize a stack, without driving the wizard. Dump a profile first to see the shape, then treat that as your starting template.
