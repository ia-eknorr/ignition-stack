---
title: Adding a service
description: The templates/services/<name>/ contract for adding a new catalog service without touching engine code.
---

# Adding a service

A service is data, not code. The compose engine, the `.env` writer, and the seed copier all discover services by walking `templates/services/`, so adding one means dropping a new directory in. You never edit the engine to add a broker, a database, or a simulator.

## The directory contract

Create `ignition_stack/templates/services/<name>/` with up to three pieces:

```
templates/services/<name>/
  manifest.yaml          # required: declarative metadata
  compose.yaml.j2        # required: the service's compose fragment
  seed/                  # optional: files copied into the project
    service/             #   mounted into the service's own container
    gateway-resources/   #   overlaid onto every gateway's resource tree
```

`<name>` is the catalog slug. It must start with a lowercase letter and contain only lowercase letters, digits, and hyphens, and it doubles as the compose service key and the directory name. The slug in the path must match the `name` field in the manifest, or the loader rejects it.

## 1. Write the manifest

`manifest.yaml` is the metadata the engine and resolver need without parsing the fragment. It is validated against the `ServiceManifest` schema (`ignition_stack/services/manifest.py`), which forbids unknown keys.

| Field | Required | Purpose |
| --- | --- | --- |
| `name` | yes | Catalog slug; must match the directory name. |
| `kind` | yes | One of `database`, `mqtt-broker`, `idp`, `simulator`, `streaming`, `automation`. Drives the order services render in. |
| `summary` | no | One-line description used in docs and generated headers. |
| `image` | yes | Default `image:tag`. Pin it. |
| `image_env` | yes | The `.env` key that overrides the image (uppercase, e.g. `HIVEMQ_IMAGE`). |
| `network` | no | `frontend` or `backend` (default). Which network the service joins when the split is on. |
| `provides` | no | Capability tags this service satisfies (e.g. `mqtt-broker`). |
| `requires` | no | Capability tags this service needs; the resolver auto-adds a provider. |
| `env` | no | Preset `.env` keys mapped to default values this service contributes. |
| `seeds_gateway_resources` | no | `true` if the service ships `seed/gateway-resources/`. |
| `post_setup` | no | List of `{connection, reason}` items the service cannot pre-seed. |

A minimal broker manifest:

```yaml
name: my-broker
kind: mqtt-broker
summary: My MQTT broker for demos.
image: vendor/my-broker:1.2.3
image_env: MY_BROKER_IMAGE
network: backend
provides:
  - mqtt-broker
requires: []
env:
  MY_BROKER_MQTT_PORT: "1888"
seeds_gateway_resources: false
post_setup:
  - connection: mqtt-engine-connection
    reason: >-
      Linking a gateway needs the Cirrus Link MQTT module plus an MQTT
      server endpoint, configured once the stack is up.
```

## 2. Write the compose fragment

`compose.yaml.j2` renders into the `services:` block of the generated `docker-compose.yaml`. It must emit a single service keyed by `{{ name }}`. The engine renders it with this context:

| Variable | What it is |
| --- | --- |
| `name` | The service slug (use it as the service key and `hostname`). |
| `image_ref` | `${<IMAGE_ENV>}`, already wrapped for compose interpolation. |
| `container_name_ref` | `<name>-${COMPOSE_PROJECT_NAME}`, the conventional container name. |
| `networks` | A list: the manifest's network when the split is on, empty otherwise. |
| `depends_on` | Service dependencies the resolver computed. |
| `db_host`, `db_kind` | The database service name and kind, or `None` for a gateway-only stack. |

A fragment matching the manifest above:

```jinja
{{ name }}:
  image: {{ image_ref }}
  hostname: {{ name }}
  container_name: {{ container_name_ref }}
  ports:
    - "${MY_BROKER_MQTT_PORT}:1888"
{%- if networks %}
  networks:
{%- for net in networks %}
    - {{ net }}
{%- endfor %}
{%- endif %}
```

Reference `.env` values with `${KEY}` directly. The manifest never duplicates the compose body; it only carries metadata the engine cannot read from the YAML.

## 3. Add seed assets (optional)

Two seed directories, each copied only when present:

- **`seed/service/`** is copied into the project at `services/<name>/` and is yours to mount into the service's own container (a database initdb script, a Keycloak realm export, a broker config). Mount it from the fragment with a `volumes:` entry, as Postgres does for its `initdb/`.
- **`seed/gateway-resources/`** is overlaid onto every gateway's `config/resources/` tree. Set `seeds_gateway_resources: true` when you ship this. It is how a service pre-seeds a file-seedable gateway connection. Postgres uses it to ship a working `db-connection` plus the `internal-secret-provider` that holds its password. What is file-seedable on Ignition 8.3 is governed by the [seeding matrix](../reference/seeding-matrix.md); anything that cannot be seeded belongs in `post_setup` instead.

## 4. Capabilities and dependencies

The resolver matches `requires` against `provides` across the catalog and auto-adds whatever is missing, so a dependency is never selected by hand. Declaring `requires: [sql-database]` is exactly how Keycloak pulls in a database. See [how generation works](../architecture/how-generation-works.md) for the resolver in full.

## 5. Wire it into tests and docs

- **Golden snapshot.** Add a minimal snapshot under `tests/golden/services/<name>/` and regenerate with `UPDATE_GOLDENS=1`. The catalog smoke test asserts every service has one.
- **Docs.** Add a page at `docs/docs/services/<name>.md`, list it in the [services index](../services/index.md) table, and add its id to the Services category in `docs/sidebars.ts`.

Once the directory and the golden are in place, the service is selectable wherever the project's `services` list is populated, with no engine change.
