# 0022 — Include an External Provider (OpenAI) in the MVP

**Status:** Accepted

## Context

The original phased plan deferred external LLM providers to Phase 4,
keeping Phase 1/MVP local-only via Ollama. But the system's core value
proposition — "the external LLM provider must never receive the identity
mapping" — is never actually exercised by a local-only runtime: nothing
leaves the machine at all with Ollama, so the boundary claim (ADR-0013)
would be structurally true but empirically untested until Phase 4, several
increments after the concept is supposedly validated.

## Decision

Implement both `OllamaProvider` and `OpenAIProvider` in the MVP (ADR-0016).
Only synthetic/fabricated data (per the project's data-handling decision for
this build) is ever sent to OpenAI, but the full pipeline — sanitize → send
externally → validate output → deanonymize — is real, not simulated or
mocked, and is what the `privacy_invariants/` test suite's captured-request
assertions (design doc §9) actually exercise.

## Alternatives Considered

- **Stay with the original Phase 1 scope (Ollama only, defer external
  providers to Phase 4):** rejected during MVP design — would mean the
  project's headline invariant is unverified by real egress for the first
  several increments of the project, which undercuts the project's own
  stated purpose as an evaluation platform for privacy vs. utility trade-offs
  against real external models.
- **Mock/simulate an external call instead of using a real provider:**
  rejected — a mock can't demonstrate that data was actually sanitized
  before serialization onto the wire; only a real HTTP call, with the
  request body captured and asserted on, proves the boundary holds under
  real conditions (network layer included).

## Consequences

MVP incurs real OpenAI API cost and requires a provisioned API key even for
research/dev use — accepted, and mitigated by using only synthetic data
(never real or real-derived patient data) for all external calls, keeping
actual sensitivity of any exposure at zero even in the worst case of a
pipeline bug.

## Security Implications

This is the decision that makes ADR-0013's boundary claim testable against a
real external system rather than only a local one, closing the gap between
"designed to prevent exposure" and "demonstrated to prevent exposure."

## Privacy Implications

Because only synthetic data is used, this decision does not introduce real
patient-data risk — it exists purely to validate the mechanism, not to
process real clinical workloads externally during MVP development.

## Reversibility

High — this changes *when* an already-planned provider (OpenAI, originally
Phase 4) is added, not *whether* it's added; no architectural rework is
implied, only resequencing.
