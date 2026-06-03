---
title: Seeding matrix
description: Which Ignition 8.3 connection types are file-seedable from the resource tree and which still require manual entry in the gateway UI.
---

# Seeding matrix

This matrix records which Ignition 8.3 connection types you can provision from the filesystem and environment, versus which still require manual entry in the gateway UI. The service catalog and the wizard read it to decide what each service can seed and what it has to defer to `POST-SETUP.md`. For how seeding fits into generation, see [Seeding and post-setup](../concepts/seeding.md).

The matrix has two verdict columns because the *config* and *secret* parts of a connection often split: the config (URL, port, username) is usually file-seedable, but the secret (password, API key) may need a separate mechanism. Recording them separately lets the catalog seed the config part while post-setup covers the secret part.

Verdicts were verified against a live `inductiveautomation/ignition:8.3.6` gateway booted from a file-seeded reference project (`inductive-automation/template-ignition-project`).

## How the resource tree is laid out

- Resources live under `data/config/resources/{external,core,local,<mode>}`, with `<mode>` chosen by the `-Dignition.config.mode=<mode>` JVM arg. The reference template uses `dev`, which inherits from `core`, which inherits from `external`.
- The path template is `data/config/resources/<mode>/<plugin-id>/<subsystem>/<item-name>/{config.json,resource.json}`, where `<plugin-id>` is `ignition` for platform subsystems and `com.inductiveautomation.<module>` (or `com.cirruslink.<module>`) for module-owned subsystems.
- `resource.json` carries `attributes.uuid`, `attributes.lastModification`, and a `lastModificationSignature` blob. The gateway accepts these as written. A JWE-encrypted secret from one gateway's `internal-secret-provider` decodes cleanly on a fresh gateway, which means there is no per-gateway encryption key: any secret referenced through `{"providerName": "internal-secret-provider", ...}` is file-seedable as a JWE blob lifted from a working seed.

## Installing third-party modules

Placing a `.modl` on disk is necessary but not sufficient. The full install contract for an unlicensed third-party module on Ignition 8.3 is three cooperating environment variables:

| Env var | Role | Required? | Format |
| --- | --- | --- | --- |
| `ACCEPT_MODULE_LICENSES` | Auto-accept the module's third-party EULA at commission time | Yes, for any unlicensed third-party module | Comma-delimited fully-qualified module identifiers |
| `ACCEPT_MODULE_CERTS` | Auto-trust the module's signing certificate at commission time | Yes, for any third-party module whose cert the gateway doesn't already trust | Comma-delimited fully-qualified module identifiers |
| `GATEWAY_MODULES_ENABLED` | Whitelist of allowed modules (restricts uninstall). If set, anything not listed is quarantined, including built-in IA modules | No (omit unless you want strict whitelisting) | Comma-delimited fully-qualified module identifiers |

The fully-qualified identifier comes from the `.modl`'s `module.xml` `<id>` element, for example `com.cirruslink.mqtt.engine.gateway` for MQTT Engine. The accept-vars take identifiers, never in-container paths.

What each combination does, verified on 8.3.6 with the MQTT Engine `.modl`:

- **All three vars set** to the module identifier: the module appears in Running Modules with status `ACTIVE`. The built-in IA modules (OPC-UA, SQL Historian, and the rest) move to Quarantined because the whitelist excludes them. The third-party module works; quarantining the built-ins is the cost of setting the whitelist.
- **Only `GATEWAY_MODULES_ENABLED` set**, accept-vars unset: the gateway boots into `RUNNING` with details `COMMISSIONING`, and the `/welcome` wizard shows the module as `NOT TRUSTED` and `NOT ACCEPTED`. This is what happens when you place a `.modl` without the accept-vars.
- **Accept-vars set, `GATEWAY_MODULES_ENABLED` empty**: the modules table is empty, because an empty whitelist allows nothing.

The recommended setup, and what `ignition-stack` generates:

1. Place each `.modl` at `/usr/local/bin/ignition/user-lib/modules/`.
2. Emit `ACCEPT_MODULE_LICENSES` and `ACCEPT_MODULE_CERTS` listing every bundled third-party module's identifier.
3. Omit `GATEWAY_MODULES_ENABLED` so the built-in IA modules keep running. Enumerate every required built-in identifier alongside the third-party ones only if you genuinely need strict whitelisting.

Each `modules.yaml` catalog entry exposes its fully-qualified identifier so the compose engine can build the two accept-var lists deterministically.

## Matrix

Verdict legend: `yes` = file-seeding works without UI touch; `partial` = some aspects work, some don't (the fallback column says where the partial-ness lives); `no` = manual UI entry required.

| connection-type | file-seedable-config | file-seedable-secret | path-template / manual fallback |
| --- | --- | --- | --- |
| `db-connection` | yes | yes | Config: `resources/<mode>/ignition/database-connection/<name>/{config.json,resource.json}`. Secret: referenced from config via `{"providerName":"internal-secret-provider","secretName":"..."}` and stored as a JWE blob in `resources/<mode>/ignition/secret-provider/internal-secret-provider/config.json` under `settings.secrets.<name>.ciphertext`. The template's JWE values decoded cleanly on a fresh gateway, so this is fully file-seedable (connection reads `VALID` on boot). |
| `jdbc-driver` | yes | n/a | Driver metadata: `resources/<mode>/ignition/database-driver/<Name>/config.json` (classname, URL format, default translator, default validation query). The `.jar` itself must be placed in `user-lib/jdbc/` via bootstrap copy or a derived Dockerfile; the template ships configs for PostgreSQL, MySQL, MariaDB, MSSQL, Oracle, SQLite. |
| `database-translator` | yes | n/a | `resources/<mode>/ignition/database-translator/<TYPE>/config.json`. Built-ins (POSTGRES, MYSQL, MSSQL, ORACLE, SQLITE, GENERIC) ship with the gateway; the template overlays an ORACLE entry, confirming custom translators are file-seedable the same way. |
| `opc-ua-connection` | yes | yes (via secret-provider) | Outbound OPC-UA client connections to external servers. Path: `resources/<mode>/com.inductiveautomation.opcua/connection/<name>/{config.json,resource.json}`. Secrets (usernames/passwords for the remote server) follow the same `internal-secret-provider` indirection as `db-connection`. A fresh gateway lists no connections by default, matching the template carrying none. |
| `opc-ua server-config` | yes | n/a | `resources/<mode>/com.inductiveautomation.opcua/{server-config,access-control,one-time}/config.json`. Anonymous-access, bind ports, security policies, and access-control rules are all file-seedable. |
| `identity-provider` | yes | yes (via secret-provider for OIDC/SAML client secrets) | `resources/<mode>/ignition/identity-provider/<name>/config.json` with `profile.type` controlling the provider kind. The template ships `default` (`type=internal`); the gateway also auto-generates a `temp` provider for password reset. Adding an OIDC or SAML provider means a new directory at the same path with `profile.type=oidc` or `saml` and the type-specific fields; client secrets reference the internal-secret-provider the same way `db-connection` does. |
| `security-levels` | yes | n/a | `resources/<mode>/ignition/security-levels/{config.json,resource.json}` is a single document describing the security-level tree. |
| `security-zones` | yes | n/a | `resources/<mode>/ignition/security-zone/<name>/{config.json,resource.json}`. The template ships a `Default` zone. |
| `secret-provider` | yes | yes | `resources/<mode>/ignition/secret-provider/<provider-name>/config.json` carries the provider profile (`type: internal`) and the `settings.secrets.*` map of JWE-encrypted values. The template's `internal-secret-provider` reads as `Enabled=true Type=Internal` on a fresh gateway. |
| `gateway-network-link` | partial | n/a | Own UUID: file-seedable at `data/config/local/ignition/gateway-network/uuid.txt` (the template's bootstrap writes a deterministic MD5-based UUID there before the gateway starts). Outbound peer-link settings: file-seedable under `resources/<mode>/ignition/gateway-network-settings/` and `resources/<mode>/ignition/gateway-network-proxy-rules/`. Per-link approval state is the part most likely to need UI confirmation when a remote gateway initiates a handshake. |
| `redundancy` | yes | n/a | Master/backup pairing is fully file-seedable on 8.3. The redundancy *mode* lives in `data/redundancy.xml` (a Java-properties XML in the data root, **not** under `resources/`; created only once redundancy is configured). Seed `redundancy.noderole=Master` on the master and `=Backup` + `redundancy.gan.host=<master>` on the backup. Use a **plain** redundancy connection (`redundancy.gan.enableSsl=false`, `redundancy.gan.port=8088`) plus an open incoming Gateway Network policy (`securityPolicy=Unrestricted`, `requireSSL=false` — env-settable as `GATEWAY_NETWORK_SECURITYPOLICY`/`GATEWAY_NETWORK_REQUIRESSL` or file-seeded in `gateway-network-settings/config.json`) and the pair comes up paired with **no UI**. The only manual fallback is the SSL variant: a default SSL connection (8060) needs the master's GAN server certificate approved on the backup at `Config ▸ Network ▸ Gateway Network ▸ Connections ▸ Outgoing ▸ Remote Certificates ▸ Approve`. Do not pass `-n` to the backup container (it adopts the master's system name on pairing). The `yes` verdict assumes the demo's single isolated Docker network: plain/no-SSL/`Unrestricted` is insecure on a shared or cross-host network (the gateway logs `Require SSL setting is disabled, rendering Gateway Network connections insecure`), and redundancy replicates the full system backup over that link — use SSL (8060) + approved certs there, which reintroduces the manual cert-approval step. Verified hands-on against two `inductiveautomation/ignition:8.3.6` containers; see `verification/redundancy-spike/`. |
| `theme-asset` | yes | n/a | `resources/<mode>/com.inductiveautomation.perspective/themes/<name>/{config.json,resource.json}`. The template ships `dark-cool`, `dark-warm`, `light-cool`, `light-warm`, and the gateway reads them on boot. |
| `tag-provider` | yes | n/a | `resources/<mode>/ignition/tag-provider/<name>/{config.json,resource.json}`. Realtime providers (memory/expression) are fully file-seedable; database-backed historical providers are file-seedable in config but depend on the corresponding DB connection being valid. |
| `alarm-pipeline` | yes (gateway-level) | n/a | Gateway-level alarm settings live at `resources/<mode>/ignition/general-alarm-settings/config.json` (file-seedable). Per-project alarm pipelines live under each project's `com.inductiveautomation.alarm-notification/alarm-pipeline/<name>/` directory inside `data/projects/<project>/` and are file-seedable the same way project resources are. |
| `project-resource` | yes | n/a | Projects mount to `data/projects/<project>/` and the template's `services/ignition/projects/` bind-mounts cleanly. Resource format: `<project>/com.inductiveautomation.<module>/<resource-type>/<name>/{resource.json,data.bin,...}`. The project tree is read on boot. |
| `historian-provider` | yes | n/a | `resources/<mode>/com.inductiveautomation.historian/historian-provider/<name>/{config.json,resource.json}`. The template ships a `db`-backed historian provider that reads as configured on a fresh gateway. |
| `jvm-arg` | yes | n/a | JVM args go on the gateway container `command:` line after the standalone `--` separator. Example from the template: `-Dignition.config.mode=${DEPLOYMENT_MODE:-dev}`. No env-var wrapper is needed; the compose `command:` is the seam. |
| `env-var` | yes | n/a | Standard container env. Confirmed: `ACCEPT_IGNITION_EULA=Y`, `GATEWAY_ADMIN_USERNAME` (defaults to `admin`), `GATEWAY_ADMIN_PASSWORD`, `IGNITION_EDITION=standard|edge|maker`, `IGNITION_LICENSE_KEY`, `IGNITION_ACTIVATION_TOKEN`, `IGNITION_ROOT_KEY_PASSWORD_FILE`, `GATEWAY_MODULES_ENABLED`, `ACCEPT_MODULE_LICENSES`, `ACCEPT_MODULE_CERTS`. The `<VAR>_FILE` secret-injection pattern exists for `IGNITION_ROOT_KEY_PASSWORD`; the JWE-stored `internal-secret-provider` entries make `<VAR>_FILE` less necessary for other secrets. |
| `secret-reference` | yes | yes | A `config.json` referencing a secret looks like `{"<field>": {"data": {"providerName": "<provider>", "secretName": "<name>"}, "type": "Referenced"}}`. The reference itself is file-seedable, and the secret value lives in the referenced provider's own file-seeded `config.json` as a JWE blob. Use a JWE value lifted from a working seed, or pre-encrypt your own. |
| `module-install` | yes | n/a | See [Installing third-party modules](#installing-third-party-modules). Placing `.modl` files under `/usr/local/bin/ignition/user-lib/modules/` plus setting `ACCEPT_MODULE_LICENSES` and `ACCEPT_MODULE_CERTS` to the modules' fully-qualified identifiers activates them at boot. Omit `GATEWAY_MODULES_ENABLED` unless you intend to restrict built-ins. |
