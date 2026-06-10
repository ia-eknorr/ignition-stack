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

Keycloak declares `requires: [sql-database]`. When it is added to a stack with no SQL database, the [resolver](../concepts/how-generation-works.md) adds [Postgres](./postgres.md) and appends a dedicated `keycloak` logical database so Keycloak's tables stay separate from any Ignition schema. If a SQL database is already present, Keycloak reuses it.

## Preset credentials and ports

The generated `.env` ships these defaults. Change them before exposing the stack anywhere shared.

- `KEYCLOAK_ADMIN_USER=admin`: Keycloak admin console user.
- `KEYCLOAK_ADMIN_PASSWORD=admin`: admin console password.
- `KEYCLOAK_HTTP_PORT=8081`: host port for the Keycloak console (8081 keeps it clear of the gateway's 9088).

## Seeding

The Keycloak OIDC connection is seeded end to end, so a fresh gateway boots with single sign-on already configured and no manual step.

Two halves cooperate:

- **The realm** (`seed/service/import/ignition-realm.json`) ships the `ignition` realm with the `ignition-gateway` client carrying a **fixed demo client secret** (`ignition-oidc-demo-secret`), an `ignition-admin` realm role, and a demo user **`demo` / `demo`** holding that role. Keycloak imports it on first boot with `--import-realm`.
- **The gateway side** (`seed/gateway-resources/`) overlays an OIDC identity provider at `config/resources/core/ignition/identity-provider/keycloak/config.json` (`profile.type=oidc`). The matching client secret rides **inside that file** as an embedded JWE blob, the same file-seeding the [seeding matrix](../reference/seeding-matrix.md) verifies for `db-connection`. Because the secret is embedded in the IdP config rather than in the shared `internal-secret-provider`, it never collides with the Postgres db-password secret on a gateway attached to both.

To keep the OIDC issuer and redirect URLs consistent across the browser and the gateway's back-channel, the Keycloak container pins its frontend hostname (`KC_HOSTNAME=http://localhost:${KEYCLOAK_HTTP_PORT}`, with `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`). Browser-facing endpoints (authorize, logout, issuer) use the published `localhost:8081`; the gateway reaches the token, JWKS, and userinfo endpoints over the Docker network at `keycloak:8080`.

This is a demo posture: the client secret is a fixed value shipped to every user, matching the bundled `admin` / `ignition` credentials. Rotate it in Keycloak and re-seed for any real deployment, and front Keycloak with HTTPS. If you change `KEYCLOAK_HTTP_PORT`, update the `authorizationEndpoint` / `endSessionEndpoint` / `providerId` URLs in the seeded `identity-provider/keycloak/config.json` to match.

## Post-setup

There is no manual configuration step. After `docker compose up`, open the gateway's **Config → Security → Identity Providers**, find the seeded `keycloak` provider, and run **Test Login** signing in as `demo` / `demo` to confirm the round-trip. The generated `POST-SETUP.md` frames this as a verification, not a procedure.
