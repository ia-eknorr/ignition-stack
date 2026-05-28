# How generation works

`ignition-stack init` resolves a project configuration into a writable tree on disk. The compose generation engine sits at the heart of that pipeline.

## The pipeline

1. The CLI builds a `ProjectConfig` (name, gateways, database, network topology, modules).
2. The writer copies the static asset tree (`scripts/docker-bootstrap.sh`, per-gateway `services/<name>/` resource directories).
3. The compose engine renders `docker-compose.yaml` from a static anchor header, per-service Jinja2 fragments, and a footer that declares volumes (and networks when the split is opted into).
4. The writer renders `.env` from the resolved config, including per-gateway HTTP-port keys for multi-gateway projects.

## Why a hybrid engine

A pure Jinja2 approach makes nested YAML indentation a debugging nightmare. A pure dict-builder loses the hand-written feel maintainers need to read and tweak the generated compose file. The hybrid engine reads a static header that holds the YAML anchors (`x-ignition-common`, `x-ignition-environment`), renders each service as a Jinja2 fragment, concatenates them under `services:`, then round-trips the whole document through ruamel.yaml.

Anchors survive because the entire document is parsed in one context: the alias `*ignition-common` in a gateway fragment resolves to the anchor `&ignition-common` defined earlier in the same string.

## Network topology

By default every service shares the implicit project bridge (no per-service `networks:` block). Opting into `network_split=True` declares two top-level networks - `frontend` and `backend` - and routes services:

- `db` and brokers: `backend` only.
- Ignition gateways: both networks (they need DB reach AND user-facing UI exposure).
- `bootstrap-*` init containers: no network membership; they only write into the data volume.

## Per-gateway env overrides

The `x-ignition-environment` anchor carries the project-wide defaults (`ACCEPT_IGNITION_EULA: "Y"`, `IGNITION_EDITION: standard`, admin credentials, TZ). A gateway that needs a different value (e.g. Edge for a frontend role) inserts an override key after the merge-key reference:

```yaml
environment:
  <<: *ignition-environment
  IGNITION_EDITION: edge
```

Modules attached to a gateway append `GATEWAY_MODULES_ENABLED`, `ACCEPT_MODULE_LICENSES`, and `ACCEPT_MODULE_CERTS` as comma-separated fully-qualified module identifiers. The engine looks each module up in `modules.yaml` and rejects unknown slugs at render time.

## Golden tests

Every supported combination has a golden snapshot under `tests/golden/<profile>/`. Engine changes that affect output text fail the test with a unified diff; an intentional change is committed by re-running the suite with `UPDATE_GOLDENS=1`.

Phase 4 ships two goldens:

- `standalone-postgres/` - the Phase 2 walking skeleton (regression contract).
- `scaleout-skeleton/` - two gateways (frontend + backend), network split, frontend running Edge with the `mqtt-engine` module attached.

Phase 5 will add per-service goldens as the service catalog lands.
