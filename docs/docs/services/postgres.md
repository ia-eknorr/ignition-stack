---
title: Postgres
description: PostgreSQL, the default Ignition historian and SQL store, with a pre-seeded gateway connection.
---

# Postgres

PostgreSQL is the default database for every architecture and the wizard's default answer. It backs the Ignition historian and any SQL-driven demo, and it is the one service that arrives with its gateway connection already wired up.

| | |
| --- | --- |
| Image | `postgres:18.1` (override with `POSTGRES_IMAGE` in `.env`) |
| Catalog kind | `database` |
| Provides | `sql-database`, `postgres-compatible` |
| Requires | nothing |
| Network when split is on | `backend` |

## How it reaches a stack

The wizard offers Postgres as the default answer to its database prompt, and the non-interactive `ignition-stack create --arch <slug>` path uses it unless you pick another database. The dependency resolver also adds Postgres automatically when another service needs a SQL database (for example [Keycloak](./keycloak.md)).

Credentials live in the shared database section of the generated `.env` (`DB_USER`, `DB_PASSWORD`, `DB_HOST`), so every database kind speaks one credential vocabulary. The defaults are `ignition` / `ignition`.

## Seeding

Postgres is the only catalog database that seeds a working gateway connection. Its `seed/gateway-resources/` overlay ships a `db-connection` (config plus `resource.json`) and the `internal-secret-provider` that holds the connection password as a JWE blob, so the gateway boots with a `VALID` database connection and no manual step. This follows the `db-connection` row of the [seeding matrix](../reference/seeding-matrix.md), where both the config and the secret are file-seedable.

The other databases ship the container but leave the gateway-side connection to you to create in the UI once the stack is up.

## Post-setup

None. The connection is live on first boot.
