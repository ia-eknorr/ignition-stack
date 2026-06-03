# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-03

First public release. `ignition-stack` generates self-contained Docker Compose
projects for Ignition 8.3 SCADA demos and sales-engineering engagements: pick an
architecture, answer a few prompts, and `docker compose up` brings a gateway to
RUNNING with no manual UI steps.

### Added

- **`init` command** that writes a self-contained project (`docker-compose.yaml`,
  `.env`, bootstrap script, and a gateway resources tree) and boots it with
  env-driven commissioning, so first boot needs no UI.
- **Architecture profiles** `standalone`, `scaleout`, `hub-and-spoke`, and
  `mcp-n8n`, selectable with `--profile` or through an interactive wizard, with
  per-role Ignition Edge selection and a RAM-based advisory that gates oversized
  hub-and-spoke stacks behind `--force`.
- **Service catalog** covering Postgres, MySQL, MariaDB, and MongoDB; the
  Chariot, HiveMQ, EMQX, and RabbitMQ MQTT brokers; Kafka; Keycloak; OPC-UA and
  Modbus simulators; and n8n. A dependency resolver wires implicit needs such as
  Keycloak to a database and MySQL to its JDBC driver.
- **Module and driver catalog** (`modules.yaml`) with `modules list`,
  `modules validate`, and `modules download`, including checksum verification and
  an offline cache.
- **Compose generation engine** that preserves YAML anchors, supports an opt-in
  frontend/backend network split, and merges per-gateway environment overrides.
- **Lifecycle support**: one-shot and SE-demo (`--keep-cli`) modes, a generated
  `Makefile`, `reset`, `switch-profile`, project-scoped `wipe`, and a
  `POST-SETUP.md` that lists only the steps that cannot be pre-seeded.
- **Shell completion** for bash, zsh, fish, and PowerShell via
  `--install-completion`, including dynamic completion of profile names, edge
  roles, and catalog entry names.
- **Documentation site** built with Docusaurus, including a CLI reference
  generated from the live command tree and the Ignition 8.3 seeding matrix.
- **Cross-platform CI** running on macOS, Linux, and Windows, a Linux
  `docker compose up` smoke that waits for the gateway to reach RUNNING, and
  automated deployment of the documentation site to GitHub Pages.

### Distribution

- Installable with `pipx install ignition-stack` from PyPI, or from source with
  `pipx install git+https://github.com/ia-eknorr/ignition-stack.git`.
- Releases publish to PyPI automatically through GitHub Actions using Trusted
  Publishing (OIDC), with no stored API token.

[0.1.0]: https://github.com/ia-eknorr/ignition-stack/releases/tag/v0.1.0
