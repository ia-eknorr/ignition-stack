---
title: MariaDB
description: MariaDB database (MySQL-compatible), driver built into Ignition.
---

# MariaDB

MariaDB is a drop-in MySQL-compatible database. Unlike MySQL, Ignition ships a MariaDB driver configuration built in, so no external JDBC driver is attached when you pick it.

| | |
| --- | --- |
| Image | `mariadb:11` (override with `MARIADB_IMAGE` in `.env`) |
| Catalog kind | `database` |
| Provides | `sql-database`, `mysql-compatible` |
| Requires | nothing |
| Network when split is on | `backend` |

## How it reaches a stack

Choose MariaDB at the wizard's database prompt. Credentials come from the shared `.env` database section (`DB_USER`, `DB_PASSWORD`, `DB_HOST`), defaulting to `ignition` / `ignition`.

Because the gateway already carries a MariaDB driver, the resolver attaches no extra connector, which keeps the stack a little lighter than the MySQL equivalent.

## Seeding

MariaDB ships the container but does not file-seed a gateway database connection. Create the connection in the gateway once the stack is up, pointing it at `db:3306` with the `.env` credentials.

## Post-setup

Configure the gateway's database connection in the UI after `docker compose up`.
