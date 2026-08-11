# 0021 — Authentication and Tenant Identity (Keycloak/OIDC)

**Status:** Accepted

## Context

The MVP was scoped as full multi-tenant from the first increment (a
deliberate expansion of the original phased plan, decided during MVP
design). This requires a real authentication and tenant-identity mechanism,
not a placeholder — tenant isolation (ADR-0011) depends on the request
context reliably knowing which tenant a doctor belongs to.

## Decision

Keycloak, self-hosted via Docker Compose, with one realm per tenant. A
tenant is a clinic/hospital organization; doctors are users belonging to
exactly one tenant. Authentication uses standard OIDC; the API validates the
JWT and extracts `tenant_id` + `user_id`, which are then re-validated
against the Postgres RLS session variable on every request rather than
trusted transitively from the token claim alone (design doc §3, §4).
Keycloak being unreachable is fail-closed (ADR-0020): no session is issued,
and there is no fallback authentication path.

## Alternatives Considered

- **Local email+password auth (no external IdP):** the original lower-scope
  recommendation for MVP; superseded when the project was scoped to full
  multi-tenancy with realm-per-tenant identity from the start — Keycloak's
  realm model maps directly onto the tenant model, which local auth would
  have to reimplement.
- **Generic bring-your-own-OIDC (no bundled IdP in Docker Compose):**
  considered — lower integration burden, defers a real IdP to whoever
  deploys the system. Not chosen for MVP because it would leave
  local/dev environments without a working end-to-end auth flow to test
  against, undermining the E2E test in design doc §9 (Keycloak login → ...).
- **SSO/OIDC against a cloud identity provider (Auth0, Azure AD) instead of
  self-hosted Keycloak:** rejected — violates the "prefer open-source and
  self-hostable, no unnecessary cloud dependencies" project direction and
  rule #15.

## Consequences

Local development requires running Keycloak (via Docker Compose) even for
single-developer work — accepted, since it's the only way to exercise the
real multi-tenant auth path the design depends on, and it's one more
container in an already-Compose-based stack.

## Security Implications

Fail-closed auth prevents the failure mode where an IdP outage becomes a
security bypass rather than a mere availability problem; JWT re-validation
against RLS session state prevents blind trust in token claims.

## Privacy Implications

Tenant identity is the input every other tenant-scoped privacy control
(ADR-0009, 0010, 0011) depends on; getting this wrong undermines all of
them simultaneously.

## Reversibility

Medium — Keycloak is a bundled component, not embedded in application
logic; a generic OIDC client swap to a different IdP is a configuration
change, but the realm-per-tenant *model* is assumed elsewhere and would need
re-validating against a different IdP's multi-tenancy primitives.
