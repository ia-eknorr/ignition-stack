# n8n

Workflow automation engine with a web editor on `5678`
(host port `${N8N_PORT}`): http://localhost:${N8N_PORT}.

On first visit, n8n walks you through creating the owner account. From a
workflow you can call the Ignition gateway's HTTP/WebDev endpoints at
`http://gateway:8088` to read tags or trigger logic.

In the mcp-n8n profile (Phase 6) this pairs with the Ignition MCP module so an
assistant can drive the gateway through n8n.
