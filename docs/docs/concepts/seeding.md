---
title: Seeding and post-setup
description: How ignition-stack pre-seeds gateway configuration from the filesystem on Ignition 8.3, and what it defers to POST-SETUP.md.
---

# Seeding and post-setup

Ignition 8.3 reads its configuration from a resource tree on disk when a gateway first boots. `ignition-stack` leans on that: instead of leaving you to click through the commissioning UI, it writes the connection, provider, and module configuration into the seed tree so the gateway comes up already wired. Whatever the gateway cannot read from the filesystem lands in `POST-SETUP.md` as an explicit manual step.

The goal is a stack that reaches `RUNNING` with no UI prompts. A generated project tells you, up front, exactly which connections are live and which ones still need a hand.

## The file-config resource tree

Each gateway carries a `services/<name>/` directory whose `config/resources/` tree is mounted into the gateway's data volume on first boot. The gateway reads it the same way it reads anything an operator configured through the UI: a database connection, an identity provider, a tag provider, and gateway security settings are all just files under `config/resources/<mode>/`.

A service can ship its own slice of that tree. When you add Postgres, its catalog entry overlays a `db-connection` resource (plus the secret provider that holds its password) onto every gateway, so the gateway boots with a `VALID` connection to a database it has never been told about by hand.

## The config and secret split

Most connections have two parts, and they seed differently:

- **Config** is the non-sensitive shape of the connection: the JDBC URL, the port, the username, the driver. This is plain JSON in the resource tree and is almost always file-seedable.
- **Secret** is the sensitive value: a database password, an OIDC client secret. Ignition 8.3 stores secrets through an `internal-secret-provider` as encrypted (JWE) blobs, referenced from the config by name rather than inlined.

Because a secret is referenced rather than embedded, the generator can seed the config part of a connection even when it cannot produce the secret part. The Postgres connection seeds both halves because its password is a value the stack itself chooses. A connection whose secret only exists after a human creates it elsewhere (a third-party broker account, an external OIDC client) gets its config seeded and its secret deferred.

## What lands in POST-SETUP.md

`POST-SETUP.md` is generated alongside the stack and lists only the steps that could not be pre-seeded. A standalone Postgres stack has nothing to defer, so its `POST-SETUP.md` says exactly that. A stack with an MQTT broker or Keycloak carries the handful of UI steps those services still require, and each [service page](../services/index.md) spells out which connections it defers.

This keeps the manual surface honest: the file is empty when nothing is manual, and when it is non-empty, every line is a step the tooling genuinely could not take for you.

## The seeding contract

Which connection types are file-seedable on Ignition 8.3, and which require UI entry, is recorded in the [seeding matrix](../reference/seeding-matrix.md). The service catalog reads that contract to decide what each service can promise and what it has to defer. Database connections, JDBC drivers, identity providers, OPC-UA server config, tag providers, security settings, and module installs are all file-seedable; per-link gateway-network approval is the main case that still needs a UI touch.

For where seeding sits in the larger generation flow, see [How generation works](./how-generation-works.md).
