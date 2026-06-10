---
title: The two-track wizard
description: Quick picks a profile and answers a few questions; Custom composes services per gateway on a topology preset. Both tracks produce the same project.
---

# The two-track wizard

Run `ignition-stack init <name>` with no `--profile` flag and the wizard opens with one question:

```text
? How do you want to build?
> Pick a profile and answer a few questions (recommended)
  Compose per-gateway services on a topology
```

- **Quick** walks the linear profile flow: profile, database, edition, redundancy, modules, proxy, summary. The common path is a handful of keystrokes, and every prompt has a sensible default.
- **Custom** starts from a topology preset and opens a composer where you attach services to individual gateways, share one instance across gateways, and wire the MQTT pipeline: the heterogeneous shapes a flat profile cannot express.

Both tracks produce the same kind of project and the same saved [configuration record](../concepts/configuration-record.md). Quick's output is not sealed either: its summary offers a **tweak** action that hands the built stack to the Custom composer pre-filled, so you can take the easy on-ramp and still end up anywhere the composer can go.

## The Quick track

The prompts, in order (single-gateway profiles skip the count and network questions):

1. **Architecture profile**: one of the [four presets](../profiles/index.md). Default `standalone`.
2. **Count**: spoke count for hub-and-spoke, frontend count for scaleout.
3. **Database**: Postgres (default), MySQL, MariaDB, MongoDB, or none.
4. **Edge edition**: which role (if any) runs Edge.
5. **Network split**: frontend/backend networks for the multi-gateway profiles.
6. **Redundancy**: pair the workhorse gateway with a backup node. Default off.
7. **IIoT**: `Wire an MQTT pipeline (Cirrus Transmission/Engine + broker)?` Default no. Accepting opens a broker select (default `chariot`, listing every catalog MQTT broker) and overlays the pipeline by role: Transmission on edge-side gateways, Engine on the central one.
8. **Modules**: the curated default set plus the JDBC driver matching your database, pre-checked. See [disable built-in modules](./disable-builtins.md).
9. **Reverse proxy**: external, install Traefik, or skip.
10. **Summary**: a recap plus a three-way choice:

```text
? Ready to generate?
> Generate the project
  Tweak per-gateway services in the custom composer
  Cancel
```

**Generate** writes the project. **Tweak** opens the composer with everything you just chose already in place. **Cancel** aborts without writing anything.

## The Custom track

Custom asks only the topology questions (preset, counts, Edge role, network split, redundancy), builds a bare gateway skeleton with no database, and drops into the composer. The `mcp-n8n` preset is absent on purpose: it is standalone plus an n8n attachment, which the composer expresses directly.

The composer prints the current composition before every action: a gateway table (edition, role, attachments, third-party modules, enabled built-in count) and a service registry table. Registry rows with no gateway attachment are labeled `stack-level`; that includes infrastructure a service pulled in for itself, like the Postgres instance Keycloak's requirement adds, which no gateway needs to connect to.

```text
? Composer action?
  Add a service to a gateway
  Share an existing instance with another gateway
  Add a stack-level service (no gateway attachment)
  Remove an attachment
  Set a gateway's enabled modules
  Set a gateway's edition (standard / edge)
  Wire or unwire the MQTT (IIoT) pipeline
  Rename an instance
> Done - review and generate
```

What each action does:

- **Add a service to a gateway** picks a gateway, then a catalog service grouped by kind. Databases are included here; this is where multi-database stacks get built. On an Edge gateway, services that cannot run against Edge (every database) are filtered out of the list with a note, instead of erroring after selection. If the service is a singleton that already exists (a database kind, Keycloak, a broker), the composer offers to attach the gateway to the existing instance instead of creating a duplicate.
- **Share an existing instance** attaches another gateway to an instance that already runs, which is how two gateways use the same Keycloak or the same broker. Targets are filtered to gateways not already attached and not Edge-blocked. Sharing a broker asks for the attachment role (`mqtt-transmission`, `mqtt-engine`, or plain consumer) and installs the matching Cirrus module on that gateway.
- **Set a gateway's enabled modules** is the per-gateway version of the [module step](./disable-builtins.md): the checkbox pre-checks the curated default set plus the JDBC driver for the database *this gateway* attaches to (or the gateway's current set, if you already customized it). The checkbox governs built-ins only; third-party modules such as the Cirrus pair or a JDBC `.jar` are managed by the resolver and shown in their own table column.
- **Wire or unwire the MQTT (IIoT) pipeline** applies or removes the same overlay the Quick track's IIoT prompt uses: one broker instance, Transmission attachments on edge-side roles, Engine on central roles.

Every mutation is validated immediately against the full rule set (one database per gateway, distinct database kinds, singletons, the Edge invariant). A rejected edit prints one `error:` line and returns to the menu with your composition untouched.

**Done** shows the final composition and asks generate / keep editing / cancel.

## A sample composer session

The heterogeneous stack from the feature request: gateway 1 runs EMQX and Keycloak, gateway 2 shares the same Keycloak and has its own database, and an Edge spoke publishes over MQTT but never touches a database.

```text
? How do you want to build?      Compose per-gateway services on a topology
? Starting topology (preset)?    hub-and-spoke
? Spoke gateway count?           2
? Run the Edge edition on which role?  All spokes run Edge
? Split frontend/backend onto separate Docker networks?  No
? Make the hub gateway redundant ...?  No

? Composer action?  Set a gateway's edition (standard / edge)
? Set edition on which gateway?  spoke-1
? Edition for 'spoke-1'?         Standard

? Composer action?  Add a service to a gateway
? Add a service to which gateway?  hub
? Which service?                 emqx [mqtt-broker] - EMQX MQTT broker ...
? Attachment role?               MQTT Engine (central gateway subscribes/aggregates)

? Composer action?  Share an existing instance with another gateway
? Share which instance?          emqx (emqx)
? Share with which gateway?      spoke-2
? Attachment role?               MQTT Transmission (edge gateway publishes Sparkplug)

? Composer action?  Add a service to a gateway
? Add a service to which gateway?  hub
? Which service?                 keycloak [idp] - Keycloak identity provider ...

? Composer action?  Share an existing instance with another gateway
? Share which instance?          keycloak (keycloak)
? Share with which gateway?      spoke-1

? Composer action?  Add a service to a gateway
? Add a service to which gateway?  spoke-1
? Which service?                 mongo [database] - MongoDB document store ...

? Composer action?  Done - review and generate
? Generate this composition?     Generate the project
```

The registry ends up with four instances: `emqx`, `keycloak`, `mongo`, and a stack-level `db` (Postgres) that Keycloak's requirement added for its own storage, attached to no gateway. The hub runs Engine, the Edge spoke runs Transmission, and `spoke-1` connects to the shared Keycloak plus its own Mongo.

## Round-tripping

A composer-built stack is saved like any other: `init --dry-run` dumps it, the [declarative config](./declarative-config.md) file rebuilds it with `--from-file`, and the lifecycle record drives `reset` and `switch-profile`. The composer is an interactive editor for the same file an SE can version by hand.
