# 0013 — External LLM Provider Boundary

**Status:** Accepted

## Context

The project's single most important invariant: an external LLM provider
must never receive the identity mapping, and must never receive raw
direct/quasi-identifiers. This must be a testable, enforced boundary, not a
convention developers are trusted to uphold.

## Decision

The boundary is enforced structurally, not just procedurally:
`privacy_gateway/` never imports anything network-capable (ADR-0001), so the
component holding the mapping has no code path capable of making an
outbound call. Separately, every `LLMProvider` adapter (ADR-0016) only ever
receives the pseudonymized representation produced by the pipeline — there
is no code path that constructs an LLM request from anything other than the
gateway's sanitized output. This is made automatically testable: the
`privacy_invariants/` suite captures the actual request body sent to
`OpenAIProvider` in tests and asserts zero raw golden-corpus PII strings
appear in it (design doc §9).

## Alternatives Considered

- **Trust developers to always call the gateway before the LLM Gateway:**
  rejected — a convention with no structural enforcement will eventually be
  violated by a new feature under time pressure.
- **Runtime-only checks (scan the outbound request just before sending, no
  structural import boundary):** kept as a secondary layer (this is
  effectively what the leakage scan does on the output side, ADR-0014) but
  not relied upon alone for the *inbound* side, where structural prevention
  is stronger than runtime detection.

## Consequences

Any future feature needing both LLM access and privacy-gateway
functionality must go through the gateway's public interface; it cannot
reach into `token_vault/` internals directly, even from within the same
codebase.

## Security Implications

This ADR is the concrete implementation of the project's core stated
invariant; the threat model's "compromised LLM provider" section depends on
it holding.

## Privacy Implications

Makes the "Allowed: `[PATIENT_7F82A]` / Forbidden: `Hans Müller`" boundary
from the project brief an automatically-testable property of the codebase,
not a manual review checklist item.

## Reversibility

Low — this is the foundational invariant; changing it would mean redesigning
the entire gateway/LLM Gateway relationship.
