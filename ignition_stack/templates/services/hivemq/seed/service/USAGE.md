# HiveMQ CE

Anonymous MQTT broker listening on `1883` (host port `${HIVEMQ_MQTT_PORT}`).

From another container on the stack, connect to `tcp://hivemq:1883`. From the
host, use `tcp://localhost:${HIVEMQ_MQTT_PORT}`.

Quick check with the Mosquitto CLI:

```bash
mosquitto_sub -h localhost -p ${HIVEMQ_MQTT_PORT} -t 'demo/#' &
mosquitto_pub -h localhost -p ${HIVEMQ_MQTT_PORT} -t 'demo/hello' -m 'from ignition-stack'
```

To bridge an Ignition gateway to this broker, add the Cirrus Link MQTT Engine
module and point its server endpoint at `tcp://hivemq:1883` (see POST-SETUP.md).
