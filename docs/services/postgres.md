# Postgres

PostgreSQL database, the default Ignition historian and SQL store.

This is a stub. The full reference is written in Phase 8 (documentation site).

- Image: `postgres:18.1` (override with `POSTGRES_IMAGE` in `.env`).
- Kind: database.
- Network when split: backend.
- Provides: sql-database, postgres-compatible.
