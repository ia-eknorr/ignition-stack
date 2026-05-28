# OPC-UA Simulation Server (Microsoft OPC-PLC)

An anonymous, unsecured OPC-UA server with simulated nodes, listening on `50000`
(host port `${OPCUA_SIM_PORT}`).

Endpoint from another container: `opc.tcp://opcua-sim:50000`. From the host:
`opc.tcp://localhost:${OPCUA_SIM_PORT}`.

In the Ignition gateway, add an OPC-UA connection to that endpoint with security
policy `None` and anonymous authentication. The server auto-accepts client
certificates (`--autoaccept`) so no certificate exchange is needed.
