# Chariot MQTT Broker

Sparkplug-aware MQTT broker from Cirrus Link, the primary broker for Ignition
SCADA demos.

- Web UI: http://localhost:${CHARIOT_HTTP_PORT} (user `admin`, password
  `${CHARIOT_ADMIN_PASSWORD}`).
- MQTT from another container: `tcp://chariot:1883`. From the host:
  `tcp://localhost:${CHARIOT_MQTT_PORT}`.

The EULA is auto-accepted (`ACCEPT_EULA=true`) so the broker commissions itself
on first boot. To bridge an Ignition gateway, add the Cirrus Link MQTT
Transmission/Engine modules and point them at `tcp://chariot:1883`
(see POST-SETUP.md).
