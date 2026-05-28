#!/bin/bash
# MariaDB runs every *.sh in /docker-entrypoint-initdb.d once, on first init.
# Creates each database named in EXTRA_DATABASES (comma-separated) and grants
# the application user access. The resolver sets EXTRA_DATABASES to "keycloak"
# when Keycloak is selected against this database.
set -e

if [ -z "${EXTRA_DATABASES:-}" ]; then
  exit 0
fi

for db in $(echo "${EXTRA_DATABASES}" | tr ',' ' '); do
  echo "Ensuring database '${db}' exists..."
  mariadb -u root -p"${MARIADB_ROOT_PASSWORD}" <<-SQL
    CREATE DATABASE IF NOT EXISTS \`${db}\`;
    GRANT ALL PRIVILEGES ON \`${db}\`.* TO '${MARIADB_USER}'@'%';
    FLUSH PRIVILEGES;
SQL
done
