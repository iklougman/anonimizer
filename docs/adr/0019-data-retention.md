# 0019 — Data Retention

**Status:** Accepted

## Context

Persisted data (sanitized messages, token mappings, audit events) cannot be
retained indefinitely by default in a system handling health-adjacent data,
even pseudonymized health data — and retention must be tenant-configurable
since different organizations may have different policies.

## Decision

`tenants.retention_days` configures a per-tenant retention window.
`token_mappings` and `conversations` carry `expires_at`. Two retention
mechanisms work together:
1. **Explicit deletion:** `TokenVault.expire_mapping`/`delete_mapping`
   remove/mark mappings past their window.
2. **Crypto-shredding:** deleting a tenant's DEK (`tenant_keys`, ADR-0010)
   renders any remaining `encrypted_value` rows permanently undecryptable,
   even if the row deletion job hasn't run yet — a second, independent
   enforcement path for the same guarantee, mirroring the tenant-isolation
   defense-in-depth pattern (ADR-0011).

`messages.sanitized_content` (containing no raw sensitive values by design,
ADR-0002/§4 of the design doc) may be retained longer than the identity
mapping without reintroducing re-identification risk from storage alone —
though it remains subject to the same tenant-configured retention window
unless a tenant explicitly opts into longer retention for benchmark/research
purposes.

## Alternatives Considered

- **No configurable retention, fixed global window:** rejected — different
  clinics/organizations will have different legally-driven retention
  requirements; a single hardcoded window can't satisfy all of them and
  isn't this system's decision to make unilaterally.
- **Retention enforced only by a deletion job, no crypto-shredding:**
  rejected — a deletion job that fails to run (bug, backlog, missed
  schedule) leaves data retained past its window with no independent
  backstop; crypto-shredding via DEK deletion provides that backstop.

## Consequences

Retention is tenant-configured, not a single global constant — this must be
exposed as an actual admin-configurable setting in the implementation, not
a config file value only an operator can change.

## Security Implications

Crypto-shredding means "delete the DEK" is a strong, fast, independently
verifiable retention-enforcement action, not dependent on successfully
locating and deleting every row referencing that tenant.

## Privacy Implications

Directly addresses rule requiring configurable retention and deletion as
first-class TokenVault operations (ADR-0008), not an afterthought bolted on
later.

## Reversibility

Medium — the retention *mechanism* (expiry fields + crypto-shredding) is
foundational; the specific default window values are trivially
reconfigurable per tenant.
