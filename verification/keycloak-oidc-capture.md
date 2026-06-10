# Keycloak-OIDC capture: zero-touch gateway SSO seeding

Hands-on findings from the Phase 5 Keycloak-OIDC seeding spike (issue #43).
Verified **2026-06-10** against `inductiveautomation/ignition:8.3.6` +
`quay.io/keycloak/keycloak:26.6.2` on an isolated Docker network, driving the
gateway UI by hand to capture what it writes, then replaying the captured files
on a wiped stack. This is a research note, not a CLI harness.

## TL;DR

**The Keycloak -> Ignition OIDC connection seeds fully zero-touch.** A fresh
gateway boots with the `keycloak` identity provider already configured, and an
OIDC Test Login as the seeded demo user succeeds with no UI configuration step.
Two pieces make it work:

1. **A fixed demo client secret** baked into both ends: the Keycloak realm
   import pins `secret: "ignition-oidc-demo-secret"` on the `ignition-gateway`
   client, and the gateway's seeded identity-provider config embeds the matching
   secret as a JWE blob.
2. **A pinned Keycloak frontend hostname** (`KC_HOSTNAME=http://localhost:<port>`
   + `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`) so the OIDC issuer and browser-facing
   URLs are deterministic regardless of which host hit Keycloak.

## What was captured

Configured the OIDC IdP once through the gateway UI (Platform -> Security ->
Identity Providers -> Create), choosing **Embedded** client secret. The gateway
wrote:

- `data/config/resources/dev/ignition/identity-provider/keycloak/config.json`
  — `profile.type=oidc`, the endpoint URLs, and **the client secret as an
  embedded JWE blob inside this file** (`clientSecret.type=Embedded`).
- `.../identity-provider/keycloak/resource.json` — uuid + enabled flag.

Critically, the embedded secret lives **inside the IdP's own config.json**, not
in the shared `internal-secret-provider`. The `internal-secret-provider/config.json`
(which Postgres seeds with the db-password) was **never touched** by IdP
creation. This is the secret-provider collision-avoidance strategy: Keycloak and
Postgres write disjoint files, so a gateway attached to both gets both secrets
with no last-write-wins clobber.

The captured config was then cleaned into the template seed at
`templates/services/keycloak/seed/gateway-resources/.../identity-provider/keycloak/`
(moved to `core/` mode to match the db-connection precedent), with two manual
corrections the capture surfaced (see below).

## The dual-hostname / issuer problem (the crux)

Keycloak derives the OIDC issuer and advertised endpoint URLs from the request
host. The browser reaches Keycloak at `localhost:<port>`; the gateway reaches it
at `keycloak:8080` over the Docker network. Without intervention these disagree,
and the gateway rejects the ID token (`iss` mismatch).

Fix: pin `KC_HOSTNAME=http://localhost:<KEYCLOAK_HTTP_PORT>` so the **issuer is
deterministic** (`http://localhost:<port>/realms/ignition`) from every vantage
point, and set `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` so the gateway's
back-channel calls to `keycloak:8080` still work. The seeded IdP config then
splits endpoints by channel:

- **Browser-facing** (302 redirect): `authorizationEndpoint`, `endSessionEndpoint`,
  and `providerId` (issuer) use `http://localhost:<port>`.
- **Gateway back-channel** (server-to-server): `tokenEndpoint`,
  `jsonWebKeysEndpoint`, `userInfoEndpoint` use `http://keycloak:8080`.

Two corrections the live capture forced (the UI defaults were wrong for this
flow):

1. **`allowedIdTokenSigningAlgorithms: ["RS256"]`** — left empty, the gateway
   permits no algorithms and rejects Keycloak's RS256-signed token
   (`'RS256' is not a permitted algorithm`).
2. **`providerId` set to the `localhost:<port>` issuer** (not the back-channel
   host) so it matches the `iss` claim Keycloak stamps from `KC_HOSTNAME`.

Also mapped `userName` to `preferred_username` (the UI defaulted it to `sub`, a
UUID) so logged-in users show a readable name.

## Zero-touch replay result

Generated a fresh stack with the CLI (`services=["keycloak"]`), wiped all
volumes, booted, and **without any UI configuration**:

- The gateway listed `keycloak` as a configured OpenID Connect 1.0 provider on
  first boot (from the seed).
- Ran the provider's **Test Login** and signed in as the realm's demo user
  (`demo` / `demo`). The test panel returned the full claim set:
  `iss=http://localhost:8081/realms/ignition`, `aud=ignition-gateway`,
  `preferred_username=demo`, `realm_access.roles=[ignition-admin]`, plus a
  resolved `userInfo`. Gateway log showed the token/JWKS/userInfo fetches
  succeeding over the back-channel with no errors.

This proves the **embedded client-secret JWE blob is portable**: it was captured
from one gateway's keystore and decrypted + authenticated cleanly on a different
freshly-initialised gateway, exactly as the seeding matrix documents for the
db-connection `internal-secret-provider` blob. There is no per-gateway
encryption key for the embedded IdP secret either.

## Demo-posture caveats (documented, not hardened)

- The client secret is a **fixed demo value** shipped to every user, matching the
  project's existing `admin`/`password` and `ignition`/`ignition` posture.
  Per-project secret generation is future hardening, not built here.
- The stack uses **plain HTTP**; the gateway logs insecure-URL warnings. Fine for
  an isolated demo, not for production. Front Keycloak with HTTPS for real use.
- The seeded IdP URLs are pinned to the default `KEYCLOAK_HTTP_PORT=8081`. If you
  change that port in `.env`, update the `authorizationEndpoint` /
  `endSessionEndpoint` / `providerId` in the seeded
  `identity-provider/keycloak/config.json` to match (`KC_HOSTNAME` tracks the
  `.env` port automatically; the static seed does not).
