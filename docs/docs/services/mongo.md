---
title: MongoDB
description: MongoDB document store for NoSQL demos.
---

# MongoDB

MongoDB is the document-store option, for automation and streaming demos that want a NoSQL backend alongside Ignition. It is not a SQL database, so it does not satisfy services that require one (for example [Keycloak](./keycloak.md)).

| | |
| --- | --- |
| Image | `mongo:7` (override with `MONGO_IMAGE` in `.env`) |
| Catalog kind | `database` |
| Provides | `document-store` |
| Requires | nothing |
| Network when split is on | `backend` |

## How it reaches a stack

Choose MongoDB at the wizard's database prompt. Credentials come from the shared `.env` database section, defaulting to `ignition` / `ignition`.

Because Mongo provides `document-store` rather than `sql-database`, the resolver will not select it to satisfy a SQL dependency. If a stack needs both Mongo and a SQL-backed service, pick a SQL database and add Mongo as a separate service.

## Seeding

MongoDB ships the container only. Ignition reaches MongoDB through application logic or a third-party module rather than a built-in database connection, so there is no gateway connection to file-seed.

## Post-setup

Wire the gateway to MongoDB through whatever module or scripting your demo uses, once the container is up.
