# Keycloak

Keycloak identity provider for gateway OIDC/SAML single sign-on.

This is a stub. The full reference is written in Phase 8 (documentation site).

- Image: `quay.io/keycloak/keycloak:26.6.2` (override with `KEYCLOAK_IMAGE` in `.env`).
- Kind: idp.
- Network when split: backend.
- Provides: oidc-idp.
- Requires: sql-database (the resolver adds a provider if none is selected).

Preset `.env` values (edit to taste):

- `KEYCLOAK_ADMIN_USER=admin`
- `KEYCLOAK_ADMIN_PASSWORD=admin`
- `KEYCLOAK_HTTP_PORT=8081`

Steps that cannot be file-seeded and land in `POST-SETUP.md`:

- `identity-provider`: Keycloak generates the OIDC client secret at runtime, so it cannot be file-seeded into the gateway's identity-provider config. Create (or open) the ignition-gateway client in Keycloak, copy its secret, and paste it into the gateway's IdP configuration.
