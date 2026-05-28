---
title: MySQL
description: MySQL database; selecting it attaches the MySQL JDBC driver to every gateway.
---

# MySQL

MySQL is one of the four SQL databases the wizard offers. Ignition ships no MySQL driver in the box, so picking MySQL also pulls the connector down for you.

| | |
| --- | --- |
| Image | `mysql:9` (override with `MYSQL_IMAGE` in `.env`) |
| Catalog kind | `database` |
| Provides | `sql-database`, `mysql-compatible` |
| Requires | nothing |
| Network when split is on | `backend` |

## How it reaches a stack

Choose MySQL at the wizard's database prompt. Credentials come from the shared `.env` database section (`DB_USER`, `DB_PASSWORD`, `DB_HOST`), the same keys every database kind uses, defaulting to `ignition` / `ignition`.

When MySQL is the selected database, the [dependency resolver](../architecture/how-generation-works.md) attaches the `mysql-jdbc` driver to every gateway. The connector `.jar` lands in the gateway's `user-lib/jdbc/`, which is what makes the database reachable from Ignition. This is the one imperative coupling in the resolver; everything else is declarative capability matching.

## Seeding

MySQL ships the container and the JDBC driver, but it does not file-seed a gateway database connection. Create the connection in the gateway once the stack is up, pointing it at `db:3306` with the `.env` credentials.

## Post-setup

Configure the gateway's database connection in the UI after `docker compose up`.
