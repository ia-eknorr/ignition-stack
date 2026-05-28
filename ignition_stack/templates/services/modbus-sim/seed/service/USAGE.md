# Modbus TCP Simulation Server

Serves simulated Modbus registers over TCP on `5020` (host port
`${MODBUS_SIM_PORT}`).

Endpoint from another container: `modbus-sim:5020`. From the host:
`localhost:${MODBUS_SIM_PORT}`.

In the Ignition gateway, add a Modbus TCP device pointing at `modbus-sim:5020`
(unit ID 1) and map the holding/input registers you want to poll.
