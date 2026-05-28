#!/bin/bash
# Vendored from ignition-stack Phase 1 (scripts/seeding-poc/baseline/bootstrap.sh).
#
# This script runs once per gateway data volume. It:
#   1. Copies the base /usr/local/bin/ignition/data into the persistent
#      named volume so the gateway can write its .resources/ caches.
#   2. Layers the project's resources and projects on top from a read-only
#      /template-source mount. We copy (not :ro bind-mount) because the
#      gateway needs to write .resources/ cache dirs inside those trees
#      and an :ro bind blocks that.
#   3. chown -R 2003:2003 /data so the gateway user owns everything. Without
#      this, cp from a host bind-mount lands files owned by root or the
#      host UID and the gateway faults with AccessDeniedException at start.
#   4. Writes a deterministic gateway-network UUID derived from GATEWAY_NAME
#      so the same project name always produces the same gateway identity.
#
# NOTE on commissioning: we deliberately do NOT write commissioning.json={}.
# Doing so bypasses env-driven commissioning, which means
# ACCEPT_IGNITION_EULA / GATEWAY_ADMIN_PASSWORD / IGNITION_EDITION are
# ignored and the gateway boots with no admin user. The sentinel file alone
# prevents this script from re-seeding on container restart.
set -e

DATA_DIR="/data"
TEMPLATE_SRC="/template-source"

if [ ! -f "${DATA_DIR}/.ignition-seed-complete" ]; then
  echo "Seeding data for gateway..."

  # Copy base ignition data into the persistent volume.
  cp -dpR /usr/local/bin/ignition/data/* "${DATA_DIR}/"

  # Layer the project's resources and projects on top.
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

  # Drop any cached third-party .modl files into the gateway's modules
  # directory. The compose engine mounts <project>/modules/cache to
  # /modules-cache:ro for any gateway that lists modules; gateways
  # without modules skip this block harmlessly. The companion env vars
  # (GATEWAY_MODULES_ENABLED + ACCEPT_MODULE_* on the gateway service)
  # tell Ignition to auto-load them on boot.
  if [ -d "/modules-cache" ]; then
    echo "Dropping cached modules from /modules-cache -> ${DATA_DIR}/user-lib/modules"
    mkdir -p "${DATA_DIR}/user-lib/modules"
    cp /modules-cache/*.modl "${DATA_DIR}/user-lib/modules/" 2>/dev/null || true
  fi

  # Hand ownership of everything in /data to the ignition user (uid 2003)
  # so the gateway can write its .resources/ caches and any UI-driven changes.
  chown -R 2003:2003 "${DATA_DIR}"

  # Deterministic gateway-network UUID derived from GATEWAY_NAME.
  UUID=$(echo -n "${GATEWAY_NAME}" | md5sum | awk '{print $1}' | sed 's/\(........\)\(....\)\(....\)\(....\)\(............\)/\1-\2-\3-\4-\5/' | tr -d '[:space:]')
  mkdir -p "${DATA_DIR}/config/local/ignition/gateway-network"
  echo -n "${UUID}" > "${DATA_DIR}/config/local/ignition/gateway-network/uuid.txt"
  echo "Generated UUID for gateway: ${UUID}"

  touch "${DATA_DIR}/.ignition-seed-complete"

  echo "Seeding complete for gateway."
else
  echo "Gateway already seeded, skipping..."
fi

echo "Bootstrap completed successfully."
