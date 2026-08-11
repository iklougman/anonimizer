# 0008 — Isolated Token Vault

**Status:** Accepted

## Context

The identity mapping (token → original value) is the single most sensitive
piece of state in the system — its compromise re-identifies every
pseudonymized value it covers. It must never be reachable from the LLM
Gateway, NeMo Guardrails, or any code path that touches external network
calls.

## Decision

Implement a `TokenVault` abstraction (`create_mapping`, `resolve_token`,
`resolve_tokens`, `delete_mapping`, `expire_mapping`) as the only interface
through which the mapping is ever read or written. MVP's concrete
implementation is Postgres with envelope encryption (ADR-0010); the
abstraction exists specifically so a dedicated secrets vault (HashiCorp
Vault, KMS-backed) can replace it later without touching call sites.

## Alternatives Considered

- **Store the mapping alongside `messages`/general application data:**
  rejected — collapses the security boundary between "safe to have wide
  read access" and "must be tightly controlled," making it easy for an
  unrelated feature to accidentally query the mapping.
- **Skip the abstraction, call Postgres directly wherever needed:** rejected
  — per rule #13, every privacy component must be replaceable; a leaky
  abstraction here would lock the MVP's storage choice into the architecture
  permanently.

## Consequences

Every read of an original value must go through `resolve_token(s)`, which
enforces the authorization check (ADR-0014) — there is no lower-level escape
hatch by design.

## Security Implications

`privacy_gateway/` (which owns the vault) never imports anything
network-capable (ADR-0001) — the vault is structurally unreachable from any
code path that could exfiltrate its contents to an external service.

## Privacy Implications

This is the component ADR-0002 depends on to make pseudonymization possible
at all; its interface is the enforcement point for tenant/conversation
scoping (ADR-0009, ADR-0011).

## Reversibility

High for the implementation (Postgres → dedicated vault is an adapter swap);
low for the interface contract itself, since the whole pipeline is written
against it.
