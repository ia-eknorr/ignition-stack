---
title: Keycloak
description: Keycloak identity provider for gateway OIDC/SAML single sign-on.
---

# Keycloak

Keycloak is an OIDC/SAML identity provider for demonstrating gateway single sign-on. It is the catalog's one service that depends on another: it needs a SQL database to store its realm.

| | |
| --- | --- |
| Image | `quay.io/keycloak/keycloak:26.6.2` (override with `KEYCLOAK_IMAGE` in `.env`) |
| Catalog kind | `idp` |
| Provides | `oidc-idp` |
| Requires | `sql-database` |
| Network when split is on | `backend` |

## Dependency resolution

Keycloak declares `requires: [sql-database]`. When it is added to a stack with no SQL database, the [resolver](../architecture/how-generation-works.md) adds [Postgres](./postgres.md) and appends a dedicated `keycloak` logical database so Keycloak's tables stay separate from any Ignition schema. If a SQL database is already present, Keycloak reuses it.

## Preset credentials and ports

The generated `.env` ships these defaults. Change them before exposing the stack anywhere shared.

- `KEYCLOAK_ADMIN_USER=admin` — Keycloak admin console user.
- `KEYCLOAK_ADMIN_PASSWORD=admin` — admin console password.
- `KEYCLOAK_HTTP_PORT=8081` — host port for the Keycloak console (8081 keeps it clear of the gateway's 9088).

## Seeding

Keycloak's own realm can be seeded from `seed/service/`, but the gateway side cannot be fully file-seeded, because the OIDC client secret does not exist until Keycloak generates it at runtime. The gateway's identity-provider config is therefore left to post-setup.

## Post-setup

After `docker compose up`, link the gateway to Keycloak:

- **`identity-provider`** — Keycloak generates the OIDC client secret at runtime, so it cannot be file-seeded into the gateway's identity-provider config. Create (or open) the `ignition-gateway` client in Keycloak, copy its secret, and paste it into the gateway's identity-provider configuration.

The generated `POST-SETUP.md` carries this step for the stack you produced.
