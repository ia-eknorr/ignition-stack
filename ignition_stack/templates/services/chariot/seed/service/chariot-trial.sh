#!/bin/sh
#
# Start the Chariot MQTT broker's trial license.
#
# Chariot 3.0.0 will NOT open its MQTT listener without an active license -
# it logs "Not starting Chariot MQTT Server, license not active" and the
# Cirrus Transmission/Engine connections seeded into the gateways never
# connect. This one-shot init container waits for Chariot's web API, logs in
# with the admin credentials, and POSTs start-trial-timer so the listener
# comes up with no UI clicks. It is deliberately hand-readable (curl + the
# documented REST calls) so you can see exactly what it does.
#
# A real deployment replaces the trial: set LICENSE_TYPE=online (or floating)
# plus the matching license env on the chariot service and remove this init.
# There is no trial env var on the image, so the trial is REST-started here.
set -e

# The REST API is served at the web root (NOT under /api/v1 - that path
# returns 405; verified against cirruslink/chariot:3.0.0).
BASE="http://${CHARIOT_HOST:-chariot}:8080"
ADMIN_USER="${CHARIOT_ADMIN_USER:-admin}"
ADMIN_PASS="${CHARIOT_ADMIN_PASSWORD:-password}"
ACCEPT="Accept: application/json;api-version=1.0"

# Readiness: POST /login is the probe (GET is not allowed on it). A 200 means
# the default users are seeded and the API is up.
echo "chariot-trial: waiting for Chariot web API at ${BASE} ..."
i=0
until curl -fsS -o /dev/null -X POST "${BASE}/login" -u "${ADMIN_USER}:${ADMIN_PASS}" -H "${ACCEPT}" 2>/dev/null \
  || [ "$i" -ge 60 ]; do
  i=$((i + 1))
  sleep 2
done

# Log in (HTTP basic) and capture the bearer token from the JSON response.
# The field is `access_token` (verified live; there is no `token` field).
echo "chariot-trial: logging in as ${ADMIN_USER} ..."
TOKEN=$(curl -fsS -X POST "${BASE}/login" \
  -u "${ADMIN_USER}:${ADMIN_PASS}" \
  -H "${ACCEPT}" \
  | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

if [ -z "${TOKEN}" ]; then
  echo "chariot-trial: could not obtain a login token; is ADMIN_PASSWORD correct? Continuing without failing the stack."
  exit 0
fi

# Start the trial timer. Already-active or already-started is fine - this is
# idempotent enough that re-running it on a restart does no harm.
echo "chariot-trial: starting trial timer ..."
curl -fsS -X POST "${BASE}/license?action=start-trial-timer" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "${ACCEPT}" \
  || echo "chariot-trial: start-trial-timer returned non-zero (trial may already be active)."

echo "chariot-trial: done."
