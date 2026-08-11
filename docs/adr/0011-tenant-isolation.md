# 0011 — Tenant Isolation

**Status:** Accepted

## Context

The MVP is full multi-tenant (organization = tenant) from the first
increment. A single missed `WHERE tenant_id = ...` clause in application
code would leak one clinic's patient data to another's — the most severe
class of bug this system can have, since it defeats every other privacy
control at once.

## Decision

Two independent, stacked enforcement layers:
1. **Application layer:** every repository method takes `tenant_id` as a
   mandatory first argument. There is no query method whose signature
   permits omitting it.
2. **Database layer:** Postgres Row-Level Security policies key every
   tenant-scoped table on `tenant_id`, set from the authenticated session
   context on each request. CI includes a schema-migration test asserting
   every tenant-scoped table has an RLS policy before merge.

A bug in layer 1 is caught by layer 2; neither layer alone is trusted as
sufficient.

## Alternatives Considered

- **Application-layer enforcement only:** rejected — a single missed filter
  in a new endpoint becomes a silent cross-tenant leak with no independent
  backstop.
- **Per-tenant database or schema:** rejected for MVP — operationally
  heavier (migrations, connection pooling, backup strategy all multiply per
  tenant) for a research platform's first increment; explicitly flagged in
  the design doc (§11, assumptions to validate) as worth revisiting only if
  a real multi-hospital pilot is contemplated.

## Consequences

Every new tenant-scoped table requires an RLS policy as part of its
migration, enforced by CI — this is deliberate friction, not an oversight.

## Security Implications

Directly mitigates the "cross-tenant access" and "database compromise via
application bug" threats in the threat model.

## Privacy Implications

Tenant isolation is a precondition for every other tenant-scoped guarantee
in the system (token scope, ADR-0009; encryption, ADR-0010) — if this fails,
those guarantees fail with it.

## Reversibility

Low — moving from RLS-based isolation to per-tenant databases later is a
significant migration, not a config change. This is why the "single
Postgres + RLS" choice was made explicitly rather than deferred.
