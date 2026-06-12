---
title: Disable built-in modules
description: Choose which shipped IA modules run — the wizard pre-selects a lean curated set, the CLI turns extras off — expressed as a friendly list the generator inverts into the gateway's module whitelist.
---

# Disable built-in modules

Every Ignition gateway ships with built-in IA modules — Vision, SFC, the PLC drivers, the JDBC drivers, and more. A demo usually needs only a few; turning the rest off slims the stack down. Two ways in, depending on how you start a stack:

- **The interactive wizard is opt-in.** It pre-selects a small curated set — Perspective, OPC-UA, SQL Bridge, the historian pair, Alarm Notification, Reporting, and the JDBC driver matching your database — and you add or remove from there. The common path is one keystroke: accept the lean default and move on.
- **The CLI is opt-out.** `--disable-builtin <slug>` (repeatable) turns modules off on top of the full set; pass none and every built-in stays on, so non-interactive architecture builds are unchanged.

Both land on the same stored shape — a `disable_builtins` list the generator inverts into the gateway's module whitelist — so the two paths never diverge.

## In the wizard

The module step pre-checks the curated default set. Decline the **Customize the enabled gateway modules?** prompt to take it as-is, or accept it to open a checkbox with the default set already selected:

- The default-enabled modules are checked; everything else is unchecked but one space-bar away.
- The **JDBC driver follows your database choice** — pick Postgres and the PostgreSQL driver is pre-checked, pick MariaDB or MySQL and the MariaDB driver is pre-checked (MySQL connects over the wire-compatible MariaDB driver; the catalog ships no MySQL-specific one). Mongo and "no database" pre-check no driver.

Whatever you leave unselected becomes `disable_builtins`. The summary screen lists the modules that will actually run.

## On the command line

Pass `--disable-builtin <slug>` to `create`, once per module:

```sh
ignition-stack create demo --disable-builtin vision --disable-builtin sfc
```

In a [declarative config](./declarative-config.md), the same intent lives as `disable_builtins` on a gateway, so `create --dry-run` shows exactly what is turned off and a saved config rebuilds it with `--from-file`.

Slugs are the friendly kebab names in the table below and tab-complete on the command line. An unknown slug is rejected up front — at config-construction time and at the wizard/CLI mutation path — with the full list of valid slugs, so a typo never silently slips through.

## How it works

The gateway exposes exactly one lever for this, the `GATEWAY_MODULES_ENABLED` environment variable, and it is a **strict whitelist**: set it and every module not listed is quarantined at boot. Handing that raw whitelist to users would be a footgun — to drop one module you would have to enumerate the couple dozen you want to keep, and forgetting one would silently quarantine it.

So `ignition-stack` takes a blocklist and inverts it internally. When you disable a module, the generator emits `GATEWAY_MODULES_ENABLED = (every other built-in) ∪ (any third-party modules you added)`. Folding in your added modules means disabling a built-in never quarantines a module you just installed. See the [seeding matrix](../reference/seeding-matrix.md#installing-third-party-modules) for the raw whitelist semantics this builds on.

A few consequences worth knowing:

- **Nothing disabled, nothing emitted.** If you disable no modules, the variable is omitted entirely, so an existing stack renders byte-for-byte unchanged.
- **Disabling everything emits an empty whitelist** (which quarantines all modules) rather than omitting the variable and silently re-enabling everything.
- **The complete built-in set is pinned** in `builtin_modules.yaml`. The inversion needs the full list — a stale one would quarantine forgotten modules — so an opt-in `smoke` test re-derives the set from a booted gateway and fails on drift.
- **Re-running keeps your choices.** `create -f` rebuilds from the recorded config, which includes the disabled set, so re-creating a stack does not silently bring Vision or SFC back.

## Verify it

After boot, the gateway logs the modules it loads and shows them in its module list. A disabled module is absent from that list (quarantined), while everything you kept reaches the running state. The behavior is verified against a live `inductiveautomation/ignition:8.3.6` gateway: booting with a Vision-disabled whitelist loads exactly the kept modules, Vision is absent, and no kept module faults on the quarantined one.

## Module slugs

The **Wizard default** column shows what the wizard pre-checks: `on` for the curated default set, `DB-driven` for the JDBC drivers (the one matching your database is pre-checked, the others are not), and `—` for the rest (off by default, addable on request).

| Slug | Module | Wizard default |
| --- | --- | --- |
| `alarm-notification` | Alarm Notification | on |
| `allen-bradley-driver` | Allen-Bradley Driver | — |
| `bacnet-driver` | BACnet Driver | — |
| `enterprise-administration` | Enterprise Administration | — |
| `event-streams` | Event Streams | — |
| `historian-core` | Historian Core | on |
| `kafka-connector` | Kafka Connector | — |
| `legacy-dnp3-driver` | Legacy DNP3 Driver | — |
| `logix-driver` | Logix Driver | — |
| `mariadb-jdbc-driver` | MariaDB JDBC Driver | DB-driven |
| `micro800-driver` | Micro800 Driver | — |
| `mitsubishi-driver` | Mitsubishi Driver | — |
| `modbus-driver` | Modbus Driver | — |
| `mssql-jdbc-driver` | MSSQL JDBC Driver | — |
| `omron-driver` | Omron Driver | — |
| `opc-ua` | OPC-UA | on |
| `perspective` | Perspective | on |
| `postgresql-jdbc-driver` | PostgreSQL JDBC Driver | DB-driven |
| `reporting` | Reporting | on |
| `sfc` | SFC | — |
| `siemens-drivers` | Siemens Drivers | — |
| `siemens-enhanced-driver` | Siemens Enhanced Driver | — |
| `sms-notification` | SMS Notification | — |
| `sql-bridge` | SQL Bridge | on |
| `sql-historian` | SQL Historian | on |
| `symbol-factory` | Symbol Factory | — |
| `udp-and-tcp-drivers` | UDP and TCP Drivers | — |
| `vision` | Vision | — |
| `webdev` | WebDev | — |

This list mirrors `builtin_modules.yaml`, the pinned set for the default Ignition image, where the `default_enabled` flag drives the wizard column. Disabling core modules other gateways depend on (for example `opc-ua`) can keep a gateway from reaching a working state; turn off only what the demo can do without.
