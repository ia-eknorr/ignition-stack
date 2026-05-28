---
title: Services
description: The ignition-stack service catalog, how services reach a stack, and the capability model behind them.
---

# Services

A service is a container the generated stack can run alongside the Ignition gateways: a database, an MQTT broker, an identity provider, a simulator, or an automation engine. Each one is a self-contained entry in the catalog under `templates/services/<name>/`, carrying its own manifest, compose fragment, and seed assets. Adding or changing a service is a data change, not a code change; see [Adding a service](../maintainers/adding-a-service.md).

## The catalog

| Service | Kind | Image | Provides |
| --- | --- | --- | --- |
| [Postgres](./postgres.md) | database | `postgres:18.1` | `sql-database`, `postgres-compatible` |
| [MySQL](./mysql.md) | database | `mysql:9` | `sql-database`, `mysql-compatible` |
| [MariaDB](./mariadb.md) | database | `mariadb:11` | `sql-database`, `mysql-compatible` |
| [MongoDB](./mongo.md) | database | `mongo:7` | `document-store` |
| [Keycloak](./keycloak.md) | idp | `quay.io/keycloak/keycloak:26.6.2` | `oidc-idp` |
| [Chariot](./chariot.md) | mqtt-broker | `cirruslink/chariot:3.0.0` | `mqtt-broker` |
| [HiveMQ](./hivemq.md) | mqtt-broker | `hivemq/hivemq-ce:2025.5` | `mqtt-broker` |
| [EMQX](./emqx.md) | mqtt-broker | `emqx/emqx:6.2.0` | `mqtt-broker` |
| [RabbitMQ](./rabbitmq.md) | mqtt-broker | `rabbitmq:4.3.1-management` | `mqtt-broker` |
| [Kafka](./kafka.md) | streaming | `confluentinc/cp-kafka:8.2.1` | `kafka-broker` |
| [OPC-UA simulator](./opcua-sim.md) | simulator | `mcr.microsoft.com/iotedge/opc-plc:2.9.9` | `opcua-server` |
| [Modbus simulator](./modbus-sim.md) | simulator | `oitc/modbus-server:2.2.0` | `modbus-server` |
| [n8n](./n8n.md) | automation | `n8nio/n8n:2.23.0` | `automation` |

Every image is pinned and overridable through an `.env` key (for example `POSTGRES_IMAGE`); each service page lists its own.

## How services reach a stack

- **Databases** are chosen at the wizard's database prompt: Postgres (the default), MySQL, MariaDB, MongoDB, or none. The non-interactive `--profile` path uses Postgres.
- **n8n** is bundled automatically by the [mcp-n8n profile](../profiles/mcp-n8n.md).
- **Dependencies** are pulled in by the resolver. A service that `requires` a capability gets it satisfied automatically: Keycloak requires `sql-database`, so it brings in Postgres (and a dedicated `keycloak` logical database) when none is present.

The brokers, the Kafka broker, the simulators, and Keycloak are full catalog entries: each ships a manifest, a compose fragment, seed assets, and a golden snapshot under `tests/golden/services/`. They are composed into a stack through the project's resolved `services` list, which the [generation engine](../architecture/how-generation-works.md) and resolver act on.

## Capabilities and dependencies

Services describe themselves with two capability lists in their manifest:

- **`provides`** — the capabilities a service offers (Postgres provides `sql-database`).
- **`requires`** — the capabilities it needs from the rest of the stack (Keycloak requires `sql-database`).

The resolver matches `requires` against `provides` and adds whatever is missing, so a dependency never has to be selected by hand. It also applies one imperative coupling: selecting MySQL attaches the `mysql-jdbc` driver to every gateway, because Ignition ships no MySQL driver built in.

## Seeding and post-setup

Most services run the moment their container is up, but the gateway-side connection often cannot be fully baked into the seed. Postgres is the exception: it file-seeds a working gateway `db-connection` and its secret. The other services declare the connections they defer to `POST-SETUP.md`, and each service page lists them. The [seeding matrix](../reference/seeding-matrix.md) is the underlying contract for what is file-seedable on Ignition 8.3 and what is not.
