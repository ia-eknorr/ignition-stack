---
title: CLI reference
description: Every ignition-stack command, argument, and option, generated from the live Typer app.
---

# CLI reference

:::info Generated page
This page is generated from the Typer command tree by `docs/gen_cli_reference.py`.
Do not edit it by hand. Regenerate it with `npm run gen:cli` (or
`uv run python docs/gen_cli_reference.py`); a CI drift check fails if it falls
out of sync with the CLI.
:::

Generate ready-to-run Docker Compose stacks for Ignition 8.3 SCADA demos.

## Global options

**Options**

- `--version` (flag): Show ignition-stack version and exit.
- `--install-completion` (flag): Install completion for the current shell.
- `--show-completion` (flag): Show completion for the current shell, to copy it or customize the installation.

## `ignition-stack init`

Generate a new Ignition stack at ``<output-dir>/<name>``.

With ``--from-file``, builds from a saved config file. With ``--profile``,
runs non-interactively from the named profile and its flags. With neither,
walks the interactive wizard. ``--dry-run`` resolves the config and prints
it instead of writing anything.

```text
ignition-stack init [OPTIONS] NAME
```

**Arguments**

- `NAME` (required): Project name. Becomes the directory, the compose project, and the gateway name.

**Options**

- `--profile`, `-p` `TEXT`: Architecture profile to materialize (skips the wizard): - hub-and-spoke: Central hub gateway + N Edge spoke gateways. Spoke count > 8 needs --force. - mcp-n8n: One Ignition gateway + n8n + manual MCP (EA) module drop-in. - scaleout: Frontend + backend Ignition gateways via gateway network + Postgres. - standalone: One full Ignition 8.3 gateway + Postgres. The default starter stack.
- `--spokes` `INTEGER` (default `3`): Spoke gateway count for the hub-and-spoke profile (ignored otherwise).
- `--frontends` `INTEGER` (default `1`): Frontend gateway count for the scaleout profile (ignored otherwise).
- `--network-split`, `--no-network-split` (flag): Force the frontend/backend network split on or off. Default follows the profile (scaleout splits, hub-and-spoke does not).
- `--reverse-proxy` `TEXT`: Scaffold a reverse proxy of the given kind ('traefik'). Lays down a README + POST-SETUP entry at --proxy-path. Omit for plain host-port mapping.
- `--proxy-path` `TEXT` (default `reverse-proxy`): Relative directory the reverse-proxy scaffold lives in (with --reverse-proxy).
- `--force` (flag): Bypass the hub-and-spoke red-tier RAM advisory.
- `--edge-role` `TEXT`: Gateway role that runs the Ignition Edge edition. Scaleout runs all gateways standard by default; hub-and-spoke defaults its spokes to Edge. Pass 'none' to disable the profile's edge default; pass a role name ('frontend', 'hub', 'gateway', ...) to opt that specific role in.
- `--redundant` `TEXT`: Make a single gateway role redundant, expanding it into a master + backup pair (e.g. 'backend' for scaleout, 'hub' for hub-and-spoke, 'gateway' for standalone). Frontends and spokes are replicated, not paired, and are rejected.
- `--disable-builtin` `TEXT` (default `[]`): Built-in IA module to turn off on every gateway (repeatable), e.g. --disable-builtin vision --disable-builtin sfc. Emits a GATEWAY_MODULES_ENABLED whitelist of everything else. Slugs tab-complete; an unknown slug is rejected with the full valid list.
- `--iiot`, `--no-iiot` (flag): Overlay an MQTT/Sparkplug IIoT pipeline: add a broker and wire the Cirrus Link Transmission/Engine modules across the gateways by role (spokes/frontends transmit, hub/backend run Engine; a single gateway runs both). Defaults the broker to 'chariot'.
- `--iiot-broker` `TEXT`: MQTT broker slug the IIoT overlay wires to (implies --iiot). Must be a catalog 'mqtt-broker' kind (e.g. 'chariot', 'emqx', 'hivemq'). Defaults to 'chariot' when --iiot is given without this flag.
- `--from-file`, `-f` `PATH`: Build from a saved config file (YAML or JSON, as dumped by --dry-run) instead of a profile or the wizard. Mutually exclusive with --profile. The project name argument overrides the file's name.
- `--dry-run` (flag): Resolve the config and print it (see --output-format) without writing any files. The dump is the full build input; redirect it to a file, edit it, and rebuild with --from-file.
- `--output-format` `TEXT`: Format for the --dry-run dump: 'yaml' (default) or 'json'.
- `--output-dir`, `-o` `PATH`: Parent directory the project is written into. Defaults to the current directory.

## `ignition-stack modules`

Inspect and prepare the module + driver catalog.

```text
ignition-stack modules COMMAND [ARGS]...
```

### `ignition-stack modules download`

Materialise selected catalog entries into the host-side cache.

```text
ignition-stack modules download [OPTIONS] [NAMES...]
```

**Arguments**

- `NAMES...` (optional): Entries to download. Omit to download every non-manual entry.

**Options**

- `--catalog` `PATH`: Path to a modules.yaml. Defaults to the bundled catalog.
- `--ignition-version` `TEXT`: Filter to entries verified for this exact version.
- `--cache-dir` `PATH` (default `modules/cache`): Destination directory for cached artifacts.
- `--offline` (flag): No network calls. Fails if any selected entry is missing from the cache.

### `ignition-stack modules list`

Show every catalog entry as a table.

```text
ignition-stack modules list [OPTIONS]
```

**Options**

- `--catalog` `PATH`: Path to a modules.yaml. Defaults to the bundled catalog.
- `--ignition-version` `TEXT`: Filter to entries verified for this exact version.

### `ignition-stack modules validate`

Confirm schema integrity, pinned shas, and (optionally) URL reachability.

```text
ignition-stack modules validate [OPTIONS]
```

**Options**

- `--catalog` `PATH`: Path to a modules.yaml. Defaults to the bundled catalog.
- `--skip-network` (flag): Only validate the schema; skip URL reachability.

## `ignition-stack reset`

Regenerate a project from its recorded config.

Reads ``.ignition-stack/config.json``, clears the generated tree (keeping the
record and the modules cache), and re-runs generation. Works on any project
generated by this CLI; a directory without a record can't be reset.

```text
ignition-stack reset [OPTIONS]
```

**Options**

- `--project-dir`, `-C` `PATH` (default `.`): The generated project to reset. Defaults to the current directory.

## `ignition-stack switch-profile`

Reshape a project under a different architecture profile.

Carries the recorded database, services, reverse-proxy, and edge intent over
to the new profile, then regenerates in place and re-records the result.

```text
ignition-stack switch-profile [OPTIONS] PROFILE
```

**Arguments**

- `PROFILE` (required): Architecture profile to switch this stack to.

**Options**

- `--project-dir`, `-C` `PATH` (default `.`): The generated project to reshape. Defaults to the current directory.

## `ignition-stack wipe`

Remove only this project's containers, networks, and volumes.

Runs ``docker compose -p <project> down -v --remove-orphans``; the ``-p``
pin scopes the teardown to resources labelled with this compose project, so
unrelated Docker resources on the host are never touched.

```text
ignition-stack wipe [OPTIONS]
```

**Options**

- `--project-dir`, `-C` `PATH` (default `.`): The generated project to wipe. Defaults to the current directory.
- `--dry-run` (flag): Print the scoped teardown command without running it.
