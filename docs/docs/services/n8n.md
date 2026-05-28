---
title: n8n
description: n8n workflow automation engine, bundled with the mcp-n8n profile.
---

# n8n

n8n is a workflow automation engine. It is the catalog's user-facing service, so when the network split is on it lands on the `frontend` network rather than the `backend`. It pairs with the Ignition MCP module in the [mcp-n8n profile](../profiles/mcp-n8n.md).

| | |
| --- | --- |
| Image | `n8nio/n8n:2.23.0` (override with `N8N_IMAGE` in `.env`) |
| Catalog kind | `automation` |
| Provides | `automation` |
| Requires | nothing |
| Network when split is on | `frontend` |

## How it reaches a stack

n8n is bundled automatically by the [mcp-n8n profile](../profiles/mcp-n8n.md), which inserts it ahead of any other services. That profile is the intended way to get an n8n + Ignition demo, with the MCP module bridging the two.

## Preset ports

- `N8N_PORT=5678` — host port for the n8n editor UI.

## Post-setup

None for n8n itself. The mcp-n8n profile adds a post-setup step for the Early-Access MCP module drop-in; see the [mcp-n8n profile](../profiles/mcp-n8n.md).
