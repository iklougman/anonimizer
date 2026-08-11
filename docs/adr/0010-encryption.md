# 0010 — Encryption Strategy

**Status:** Accepted

## Context

The Token Vault's `encrypted_value` column holds the actual sensitive
original values. Database compromise (backup leak, misconfigured access,
insider threat) must not directly expose them, and a single global key
should not let compromise of one tenant's data expose another's.

## Decision

Envelope encryption: each tenant has a Data Encryption Key (DEK), generated
per tenant and stored wrapped (encrypted) by a master key in `tenant_keys`.
`token_mappings.encrypted_value` is AES-GCM under the tenant's DEK. The
master key itself is sourced from a mounted secret (Docker secret / file at
runtime), never committed to git, never logged, and never stored in the
database it protects. Access to it is mediated by a `KeyProvider` interface.

## Alternatives Considered

- **Single global encryption key for all tenants:** rejected — a single key
  compromise decrypts every tenant's data at once; envelope encryption with
  per-tenant DEKs contains the blast radius.
- **Application-level plaintext + rely solely on Postgres disk encryption:**
  rejected — protects only against physical disk theft, not against a
  logical DB compromise (stolen credentials, SQL injection, misconfigured
  replica) which is a more realistic threat for a networked service.
- **Full external KMS/HSM from day one:** deferred, not rejected — the
  `KeyProvider` interface exists specifically so this is a drop-in
  replacement later (see design doc §10, "dedicated secrets vault" under
  Out-of-MVP-scope) without touching call sites.

## Consequences

Losing the master key makes all tenant data permanently unrecoverable; this
is treated as an acceptable, explicit trade-off (favors fail-closed data
loss over fail-open exposure) and must be operationally documented, not
silently risked.

## Security Implications

A full Postgres dump, even with credentials, is useless without the master
key; per-tenant DEK wrapping means revoking one tenant's key (e.g. on
offboarding, feeding ADR-0019's retention/crypto-shredding mechanism) does
not affect any other tenant.

## Privacy Implications

This is what makes the "delete a tenant's DEK to render their historical
messages permanently unreadable" retention mechanism (design doc §4,
ADR-0019) possible.

## Reversibility

Medium — swapping the `KeyProvider` implementation is low-cost; changing the
envelope-encryption *scheme* itself (e.g. per-conversation DEKs instead of
per-tenant) requires a data migration.
