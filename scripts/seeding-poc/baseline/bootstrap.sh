#!/bin/bash
# Adapted from inductive-automation/template-ignition-project (master).
#
# Differences from the template:
#   - Copies template resources INTO /data/config/resources/ from a read-only
#     /template-source mount, instead of bind-mounting the resource trees
#     directly into the gateway. The gateway needs to write .resources/ cache
#     dirs inside those trees, which a :ro bind-mount blocks (FAULTED).
#   - Same deterministic gateway-network UUID + commissioning.json + sentinel.
set -e

DATA_DIR="/data"
TEMPLATE_SRC="/template-source"

if [ ! -f "${DATA_DIR}/.ignition-seed-complete" ]; then
  echo "Seeding data for gateway..."

  # Copy base ignition data into the persistent volume.
  cp -dpR /usr/local/bin/ignition/data/* "${DATA_DIR}/"

  # Layer the template's resources and projects on top.
  if [ -d "${TEMPLATE_SRC}/config/resources" ]; then
    echo "Copying resources from ${TEMPLATE_SRC}/config/resources -> ${DATA_DIR}/config/resources"
    mkdir -p "${DATA_DIR}/config/resources"
    cp -R "${TEMPLATE_SRC}/config/resources/." "${DATA_DIR}/config/resources/"
  fi
  if [ -d "${TEMPLATE_SRC}/projects" ]; then
    echo "Copying projects from ${TEMPLATE_SRC}/projects -> ${DATA_DIR}/projects"
    mkdir -p "${DATA_DIR}/projects"
    cp -R "${TEMPLATE_SRC}/projects/." "${DATA_DIR}/projects/"
  fi

  # Hand ownership of everything in /data to the ignition user (uid 2003)
  # so the gateway can write its .resources/ caches and any UI-driven changes.
  # Without this, cp -R from a host bind-mount lands files owned by root or
  # the host UID (typically 502 on macOS) and the gateway faults with
  # AccessDeniedException creating /data/projects/.resources at startup.
  chown -R 2003:2003 "${DATA_DIR}"

  # Generate a deterministic gateway-network UUID from GATEWAY_NAME so
  # the same project name always produces the same gateway identity.
  UUID=$(echo -n "${GATEWAY_NAME}" | md5sum | awk '{print $1}' | sed 's/\(........\)\(....\)\(....\)\(....\)\(............\)/\1-\2-\3-\4-\5/' | tr -d '[:space:]')
  mkdir -p "${DATA_DIR}/config/local/ignition/gateway-network"
  echo -n "${UUID}" > "${DATA_DIR}/config/local/ignition/gateway-network/uuid.txt"
  echo "Generated UUID for gateway: ${UUID}"

  # Note: the template-ignition-project writes commissioning.json={} here to
  # bypass the first-boot wizard, but doing so also skips processing of
  # GATEWAY_ADMIN_PASSWORD / ACCEPT_IGNITION_EULA / IGNITION_EDITION, leaving
  # the gateway with no admin user. The env-driven commissioning path needs
  # commissioning.json to NOT exist on first boot. The sentinel still prevents
  # this script from re-copying resources on container restart.
  touch "${DATA_DIR}/.ignition-seed-complete"

  echo "Seeding complete for gateway."
else
  echo "Gateway already seeded, skipping..."
fi

echo "Bootstrap completed successfully."
