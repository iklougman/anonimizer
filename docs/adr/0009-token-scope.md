# 0009 — Token Scope Strategy

**Status:** Accepted

## Context

Tokens must not be predictable or globally reusable — a global `[NAME_1]`
scheme would let an attacker (or the LLM provider, across requests) build a
cross-conversation identity graph purely from token reuse patterns, even
without ever seeing a resolved value.

## Decision

Tokens are opaque (`[TYPE_XXXXX]` with a cryptographically random suffix via
`secrets.token_hex`, never sequential) and scoped to
`(tenant_id, conversation_id)`. The same original value appearing twice in
the same conversation gets the same token (exact-string-match determinism,
per design doc §5); the same value in a different conversation gets an
unrelated token.

## Alternatives Considered

- **Global token reuse across a tenant's entire history:** rejected —
  explicitly disallowed by the project brief; would let an LLM (or anyone
  with output access across conversations) correlate patients across visits
  purely from token identity, defeating the point of pseudonymization.
- **Sequential/counter-based tokens:** rejected — leaks entity count and
  ordering to the LLM, and makes tokens guessable.
- **Per-patient scope (persisting a patient-level token across
  conversations) instead of per-conversation:** considered for a future
  phase (would let a doctor's follow-up conversations about the same patient
  stay linked) but rejected for MVP as unnecessary complexity — not
  requested, and re-linking conversations by patient identity is itself a
  re-identification risk surface that needs its own design.

## Consequences

A patient discussed across two separate conversations gets two unrelated
token sets in MVP; this is a known, accepted limitation, not an oversight.

## Security Implications

Conversation-scoped tokens bound the blast radius of a single leaked or
misresolved token to one conversation, not a patient's entire history.

## Privacy Implications

Directly implements the project brief's requirement to avoid global token
reuse and predictable schemes.

## Reversibility

Medium — moving to patient-level scope later is a schema and design change
(a new scoping dimension), not a full rearchitecture, since the
`TokenVault` interface already parameterizes scope.
