# Mysql

MySQL database; pulls in the MySQL JDBC driver for the gateway.

This is a stub. The full reference is written in Phase 8 (documentation site).

- Image: `mysql:9` (override with `MYSQL_IMAGE` in `.env`).
- Kind: database.
- Network when split: backend.
- Provides: sql-database, mysql-compatible.
