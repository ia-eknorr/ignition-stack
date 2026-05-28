#!/bin/bash
# Postgres runs every *.sh in /docker-entrypoint-initdb.d once, on first init.
# Creates each database named in POSTGRES_EXTRA_DATABASES (comma-separated)
# beyond the default one Postgres creates for POSTGRES_USER. The resolver sets
# this to "keycloak" when Keycloak is selected against this database.
set -e

if [ -z "${EXTRA_DATABASES:-}" ]; then
  exit 0
fi

for db in $(echo "${EXTRA_DATABASES}" | tr ',' ' '); do
  echo "Ensuring database '${db}' exists..."
  if ! psql -tAc "SELECT 1 FROM pg_database WHERE datname = '${db}'" --username "${POSTGRES_USER}" | grep -q 1; then
    psql --username "${POSTGRES_USER}" -c "CREATE DATABASE \"${db}\""
  fi
done
