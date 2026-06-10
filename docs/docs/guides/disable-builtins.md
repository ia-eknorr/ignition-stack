---
title: Disable built-in modules
description: Turn off shipped IA modules like Vision or SFC to slim a stack down, expressed as a friendly blocklist that the generator inverts into the gateway's module whitelist.
---

# Disable built-in modules

Every Ignition gateway ships with a set of built-in IA modules — Vision, SFC, the PLC drivers, the JDBC drivers, and more. A demo often needs only a few of them, and turning the rest off slims the stack down. `ignition-stack` lets you name the modules to **disable** and does the rest.

## Turn modules off

Pass `--disable-builtin <slug>` to `init`, once per module:

```sh
ignition-stack init demo --disable-builtin vision --disable-builtin sfc
```

The interactive wizard offers the same choice as an opt-in multi-select. In a [declarative config](./declarative-config.md), the same intent lives as `disable_builtins` on a gateway, so `init --dry-run` shows exactly what is turned off and a saved config rebuilds it with `--from-file`.

Slugs are the friendly kebab names in the table below and tab-complete on the command line. An unknown slug is rejected up front — at config-construction time and at the wizard/CLI mutation path — with the full list of valid slugs, so a typo never silently slips through.

## How it works

The gateway exposes exactly one lever for this, the `GATEWAY_MODULES_ENABLED` environment variable, and it is a **strict whitelist**: set it and every module not listed is quarantined at boot. Handing that raw whitelist to users would be a footgun — to drop one module you would have to enumerate the couple dozen you want to keep, and forgetting one would silently quarantine it.

So `ignition-stack` takes a blocklist and inverts it internally. When you disable a module, the generator emits `GATEWAY_MODULES_ENABLED = (every other built-in) ∪ (any third-party modules you added)`. Folding in your added modules means disabling a built-in never quarantines a module you just installed. See the [seeding matrix](../reference/seeding-matrix.md#installing-third-party-modules) for the raw whitelist semantics this builds on.

A few consequences worth knowing:

- **Nothing disabled, nothing emitted.** If you disable no modules, the variable is omitted entirely, so an existing stack renders byte-for-byte unchanged.
- **Disabling everything emits an empty whitelist** (which quarantines all modules) rather than omitting the variable and silently re-enabling everything.
- **The complete built-in set is pinned** in `builtin_modules.yaml`. The inversion needs the full list — a stale one would quarantine forgotten modules — so an opt-in `smoke` test re-derives the set from a booted gateway and fails on drift.
- **A reshape keeps your choices.** `switch-profile` carries the disabled set across, so changing profiles does not silently bring Vision or SFC back.

## Verify it

After boot, the gateway logs the modules it loads and shows them in its module list. A disabled module is absent from that list (quarantined), while everything you kept reaches the running state. The behavior is verified against a live `inductiveautomation/ignition:8.3.6` gateway: booting with a Vision-disabled whitelist loads exactly the kept modules, Vision is absent, and no kept module faults on the quarantined one.

## Module slugs

| Slug | Module |
| --- | --- |
| `alarm-notification` | Alarm Notification |
| `allen-bradley-driver` | Allen-Bradley Driver |
| `bacnet-driver` | BACnet Driver |
| `enterprise-administration` | Enterprise Administration |
| `event-streams` | Event Streams |
| `historian-core` | Historian Core |
| `kafka-connector` | Kafka Connector |
| `legacy-dnp3-driver` | Legacy DNP3 Driver |
| `logix-driver` | Logix Driver |
| `mariadb-jdbc-driver` | MariaDB JDBC Driver |
| `micro800-driver` | Micro800 Driver |
| `mitsubishi-driver` | Mitsubishi Driver |
| `modbus-driver` | Modbus Driver |
| `mssql-jdbc-driver` | MSSQL JDBC Driver |
| `omron-driver` | Omron Driver |
| `opc-ua` | OPC-UA |
| `perspective` | Perspective |
| `postgresql-jdbc-driver` | PostgreSQL JDBC Driver |
| `reporting` | Reporting |
| `sfc` | SFC |
| `siemens-drivers` | Siemens Drivers |
| `siemens-enhanced-driver` | Siemens Enhanced Driver |
| `sms-notification` | SMS Notification |
| `sql-bridge` | SQL Bridge |
| `sql-historian` | SQL Historian |
| `symbol-factory` | Symbol Factory |
| `udp-and-tcp-drivers` | UDP and TCP Drivers |
| `vision` | Vision |
| `webdev` | WebDev |

This list is generated from `builtin_modules.yaml`, the pinned set for the default Ignition image. Disabling core modules other gateways depend on (for example `opc-ua`) can keep a gateway from reaching a working state; turn off only what the demo can do without.
