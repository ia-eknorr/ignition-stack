---
title: The wizard
description: An architecture-first wizard picks one of Ignition's system architectures, layers services on top, and can tweak into a per-gateway composer.
---

# The wizard

Run `ignition-stack init <name>` with no `--arch` and the wizard opens architecture-first: the first question is which [system architecture](../architectures/index.md) you want.

```text
[1/11] Architecture
? Architecture?
> basic — one gateway
  scale-out — frontend/backend tiers
  hub-and-spoke — central hub, edge spokes
```

Each prompt shapes that architecture; later prompts add services, IIoT, and exposure; the summary writes the project or hands it to a composer for finer edits.

## The step machine

The wizard is a list of steps, walked in order. Steps that don't apply to your architecture are skipped (no spoke count unless you picked hub-and-spoke), so the count of steps shrinks and grows with your earlier answers.

A breadcrumb header sits above every prompt: `[N/M] step › step › current`, where `N/M` is the position in the *applicable* steps and the trail lists what you have answered. A long trail elides its middle.

**Back navigation.** Select prompts carry a dim `← Back` choice; confirms render as `Yes / No / ← Back`. Choosing Back returns to the previous applicable step and re-asks from there forward, replaying your prior answers as defaults. An answer that is no longer legal for a changed earlier choice (an Edge role that the new architecture doesn't offer) falls back to that step's default. The first prompt (Architecture) has no Back — there is nowhere earlier to go.

## The questions

In order; single-gateway architectures skip the count and network-split steps:

1. **Architecture** — one of the [system architectures](../architectures/index.md). Default `basic`.
2. **Count** — `Spoke gateway count?` (hub-and-spoke) or `Frontend gateway count?` (scale-out).
3. **Database** — Postgres (default), MySQL, MariaDB, MongoDB, or None.
4. **Edge edition** — `Run the Edge edition on which role?` The default follows the architecture (hub-and-spoke proposes spokes; the rest propose none).
5. **Network split** — `Split frontend/backend onto separate Docker networks?` (multi-gateway only).
6. **Redundancy** — `Enable redundancy for the <role> gateway?` Default no. See [redundancy](./redundancy.md).
7. **IIoT** — `Add IIoT (MQTT/Sparkplug)?` Default no; accepting opens a broker select (default `chariot`). See [the IIoT overlay](./iiot-overlay.md).
8. **Modules** — `Customize the enabled gateway modules?` Decline to take the curated default set; accept to open a checkbox. See [disable built-in modules](./disable-builtins.md).
9. **Exposure** — `Expose gateways via` host ports (default) or a reverse proxy. See [the reverse proxy guide](./reverse-proxy.md).
10. **Services** — add catalog services, attached to gateways or left flat (below).
11. **Summary** — recap plus the generate / preview / tweak / cancel gate (below).

## The services stage

After exposure, the wizard loops `Add a service?` over the built stack. Each `Yes` runs the shared add flow:

1. **Pick** a catalog service (`Which service?`), grouped by kind.
2. **Place** it (`Where should '<service>' go?`): attach to gateway(s), or leave it flat. A single eligible gateway auto-attaches; multiple gateways get a checkbox. Databases and other `never_on_edge` services drop Edge gateways from the attach targets.
3. **Follow-ups** that the service needs: a broker asks its attachment role (Transmission / Engine / consumer); adding n8n offers the [MCP module drop-in](../services/n8n.md).

A **flat** service runs as a spare container with no gateway attachment and no seeded connection — useful for an external subscriber or a self-contained tool. The summary tags flat instances `(flat)`.

This stage and the composer share one action implementation, so attaching a service here and attaching it in the composer behave identically.

## The summary

The summary recaps the resolved stack and gates the write:

```text
? Ready to generate?
> Generate the project
  Preview the resolved config (dry-run)
  Tweak per-gateway services in the composer
  Cancel
```

- **Generate** writes the project.
- **Preview** dumps the resolved config as YAML (the same output as `init --dry-run`) and returns to the gate, so you can inspect before committing.
- **Tweak** opens the composer with everything you chose already in place.
- **Cancel** aborts without writing. Back returns to the last question.

## The composer

The composer opens on the built, resolved stack and lets you express shapes the linear flow cannot: attaching services to individual gateways, sharing one instance across gateways, per-block env overrides, and fine-grained IIoT wiring.

It prints the current composition before every action — a gateway table (edition, role, attachments, third-party modules, enabled-built-in count) and a service registry table. Registry rows with no attachment are labeled `stack-level`; that includes infrastructure a service pulled in for itself, like the Postgres instance Keycloak's requirement adds.

```text
? Composer action?
  Add a service (attach to gateways, or leave it flat)
  Share an existing instance with another gateway
  Add a flat service (no gateway attachment)
  Remove an attachment
  Set a gateway's enabled modules
  Set a gateway's edition (standard / edge)
  Set environment-variable overrides on a gateway or service
  Add or remove IIoT (MQTT/Sparkplug)
  Rename an instance
> Done — review and generate
```

- **Add a service** is the same add flow as the services stage: pick, place (attach-or-flat), wire follow-ups. Attaching a singleton that already exists reuses it instead of duplicating.
- **Share an existing instance** attaches another gateway to a running instance — two gateways on one Keycloak or one broker. Targets exclude already-attached and Edge-blocked gateways. Sharing a broker asks the attachment role and installs the matching Cirrus module.
- **Set a gateway's enabled modules** is the per-gateway version of the [module step](./disable-builtins.md): the checkbox pre-checks the curated set plus the JDBC driver for *that gateway's* database (or its current set, if already customized).
- **Set environment-variable overrides** layers `KEY=VALUE` lines onto a gateway's compose `environment:` or a service instance's `.env`, collected until a blank line.
- **Add or remove IIoT** applies or removes the same overlay the IIoT prompt uses.

Every mutation is validated immediately against the full rule set (one database per gateway, distinct database kinds, singletons, the Edge invariant). A rejected edit prints one `error:` line and returns to the menu with your composition untouched.

**Done** shows the final composition and asks generate / preview / keep editing / cancel.

## A sample composer session

A heterogeneous stack: the hub runs EMQX (Engine) and Keycloak, `spoke-1` shares that Keycloak and gets its own Mongo, and `spoke-2` publishes over MQTT but holds no database. Pick hub-and-spoke with no database, then **Tweak**:

```text
? Architecture?                  hub-and-spoke
? Spoke gateway count?           2
? Database?                      None
? Run the Edge edition on which role?  All spokes run Edge
? Split frontend/backend onto separate Docker networks?  No
? Enable redundancy for the hub gateway?  No
? Add IIoT (MQTT/Sparkplug)?     No
? Customize the enabled gateway modules?  No
? Expose gateways via            Host ports
? Add a service?                 No
? Ready to generate?             Tweak per-gateway services in the composer

? Composer action?  Set a gateway's edition (standard / edge)
? Set edition on which gateway?  spoke-1
? Edition for 'spoke-1'?         Standard

? Composer action?  Add a service (attach to gateways, or leave it flat)
? Which service?                 emqx [mqtt-broker] - EMQX MQTT broker ...
? Where should 'emqx' go?        Attach to gateway(s)
? Attach 'emqx' to which gateways?  hub
? Attachment role?               MQTT Engine (central gateway subscribes/aggregates)

? Composer action?  Share an existing instance with another gateway
? Share which instance?          emqx (emqx)
? Share with which gateway?      spoke-2
? Attachment role?               MQTT Transmission (edge gateway publishes Sparkplug)

? Composer action?  Add a service (attach to gateways, or leave it flat)
? Which service?                 keycloak [idp] - Keycloak identity provider ...
? Where should 'keycloak' go?    Attach to gateway(s)
? Attach 'keycloak' to which gateways?  hub, spoke-1

? Composer action?  Add a service (attach to gateways, or leave it flat)
? Which service?                 mongo [database] - MongoDB document store ...
? Where should 'mongo' go?       Attach to gateway(s)
? Attach 'mongo' to which gateways?  spoke-1

? Composer action?  Done — review and generate
? Generate this composition?     Generate the project
```

The registry ends with four instances: `emqx`, `keycloak`, `mongo`, and a stack-level `db` (Postgres) that Keycloak's requirement added for its own storage. The hub runs Engine, the Edge `spoke-2` runs Transmission, and `spoke-1` connects to the shared Keycloak plus its own Mongo.

## Round-tripping

A wizard- or composer-built stack saves like any other: `init --dry-run` dumps it, a [declarative config](./declarative-config.md) rebuilds it with `--from-file`, and the lifecycle record drives `reset` and `switch-arch`. The composer is an interactive editor for the same file an SE can version by hand.
