---
title: Dump, edit, and rebuild a stack
description: Use init --dry-run to dump a stack's resolved configuration, edit it by hand, and rebuild it with init -f for a fully declarative workflow.
---

# Dump, edit, and rebuild a stack

A stack is fully described by its resolved [configuration record](../concepts/configuration-record.md). `init --dry-run` prints that config instead of building; `init -f` builds from a saved copy instead of an architecture. Together they make generation declarative: dump what an architecture would build, hand-edit it, and rebuild from the file.

## Dump the resolved config

`--dry-run` resolves the config and prints it to stdout without writing any files. It shows the full build input - project name, every gateway with its ports and edition, the database, the selected services, and the network layout - after the resolver has expanded implicit dependencies.

```sh
ignition-stack create demo --arch scale-out --dry-run > arch.yml
```

The default format is YAML, ordered to read top-down (`name` first). Use `--output-format json` for JSON:

```sh
ignition-stack create demo --arch scale-out --dry-run --output-format json > arch.json
```

`--dry-run` writes nothing to disk - not even the project directory - so it is safe to run anywhere just to inspect what a set of flags would produce.

## Edit and rebuild

Open the dumped file and change what you need - bump a gateway's `memory_mb`, add a service, flip `network_split`, rename a gateway. Then build from it with `-f`:

```sh
ignition-stack create demo -f arch.yml
```

The file is run through the same resolver and writer as an architecture build, so a project built from an architecture and one built from that architecture's dump are byte-identical. The project name argument wins over the file's `name`, so you can stamp out the same topology under different names:

```sh
ignition-stack create customer-b -f arch.yml
```

`-f` is mutually exclusive with `--arch` (the file already specifies the whole topology) and with the wizard (it never prompts). Combining `-f` with `--arch` is an error.

## Validation

The file is validated against the same schema the wizard and architectures produce. An unknown field, a bad enum, or a malformed document fails with a readable message and a non-zero exit - never a traceback:

```text
$ ignition-stack create demo -f broken.yml
error: invalid config in 'broken.yml':
  - database.kind: unsupported database kind 'oracle'; supported: mariadb, mongo, mysql, postgres
```

## Heterogeneous stacks

An architecture gives every gateway the same services. The config file goes further: it carries a stack-level **service registry** (`service_instances`) and **per-gateway attachments** (each gateway's `services:` list), so different gateways can use different services, share one instance, or hold none at all.

Each entry in `service_instances` is an addressable service keyed by `id` and backed by a catalog `service` slug. Each entry in a gateway's `services:` list names the instance it attaches to and the `role` it plays (`consumer` by default; `mqtt-transmission` / `mqtt-engine` for the IIoT pipeline). Sharing is just two gateways naming the same instance `id`.

This stack runs a shared Keycloak across two standard gateways, gives the second its own Mongo, and runs an Edge gateway that publishes to a broker while holding no database:

```yaml
name: plant
database: null
gateways:
  - name: gw1
    role: hub
    http_port: 9088
    services:
      - instance: emqx
      - instance: keycloak
  - name: gw2
    role: hub
    http_port: 9089
    services:
      - instance: keycloak     # the same instance gw1 uses
      - instance: mongo1
  - name: gw3
    role: spoke
    ignition_edition: edge
    http_port: 9090
    services:
      - instance: emqx
        role: mqtt-transmission # publishes Sparkplug; no database
service_instances:
  - id: emqx
    service: emqx
  - id: keycloak
    service: keycloak
  - id: mongo1
    service: mongo
```

Two kinds of dependency resolve at different levels, which is what lets `gw3` use the broker without a database:

- **Service to service.** Keycloak requires a SQL database, so the resolver auto-adds a Postgres instance and gives Keycloak its logical schema there. That Postgres is a registry-level dependency: it backs Keycloak but attaches to **no** gateway. An Edge gateway can use Keycloak SSO while never holding a database connection.
- **Gateway to service.** A gateway only connects to a database when it explicitly attaches to one. `gw2` attaches to `mongo1`; `gw1` and `gw3` hold no database at all.

Two rules bound the database side of a heterogeneous stack: multiple databases are allowed only when their kinds differ (the auto-added Postgres and `gw2`'s Mongo are fine), and a gateway may attach to at most one database. An Edge gateway may never attach to a database (the resolver rejects it).

### Legacy shorthand still works

The flat `services: ["keycloak", "hivemq"]` list and single `database:` block from earlier versions are still accepted as input shorthand. The resolver lowers them into the registry: each becomes a `ServiceInstance` plus a `consumer` attachment on every gateway, and the legacy fields are cleared. So a dumped config (and the [configuration record](../concepts/configuration-record.md)) always shows the registry form, even when the input used the shorthand. Dump a legacy-style config with `--dry-run` to see exactly what it lowers to.

## Authoring from scratch

Because the format is the schema, an external tool - an architecture builder, a script, a templating step - can emit a config file and hand it to `ignition-stack -f` to materialize a stack, without driving the wizard. Dump an architecture first to see the shape, then treat that as your starting template.
