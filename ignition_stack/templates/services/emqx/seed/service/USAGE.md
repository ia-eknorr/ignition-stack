# EMQX

MQTT broker on `1883` (host port `${EMQX_MQTT_PORT}`) with a dashboard on
`18083` (host port `${EMQX_DASHBOARD_PORT}`).

- Dashboard: http://localhost:${EMQX_DASHBOARD_PORT} (user `admin`, password
  `${EMQX_DASHBOARD_PASSWORD}`).
- MQTT from another container: `tcp://emqx:1883`.

To bridge an Ignition gateway, add the Cirrus Link MQTT Engine module and point
its server endpoint at `tcp://emqx:1883` (see POST-SETUP.md).
