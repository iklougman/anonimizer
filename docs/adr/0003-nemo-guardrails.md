# 0003 — NeMo Guardrails as Policy/Orchestration Layer

**Status:** Accepted (Phase 2 — not in MVP runtime, interface stubbed)

## Context

Beyond entity-level pseudonymization, the system needs topic restrictions,
prompt-injection detection, output validation, and a place to plug in
specialized safety models, without hand-rolling all of that logic.

## Decision

Use NVIDIA NeMo Guardrails for input rails, output rails, topic/policy
restrictions, and prompt-injection detection. It sits alongside, not inside,
the Privacy Gateway's detection pipeline. MVP scaffolds the `guardrails/`
integration point but does not wire up live rails — that's Phase 2.

## Alternatives Considered

- **Hand-rolled rail logic:** rejected — reinvents a well-scoped problem
  NeMo already solves, and would grow into an unmaintained parallel system
  over time.
- **Rely on the LLM provider's own moderation/safety layer:** rejected — it
  runs on provider infrastructure, after the sensitive data has already
  left the boundary, too late to matter for this system's core invariant.

## Consequences

Adds an external dependency and its own configuration surface (Colang
flows). Phase 2 scope, not MVP — the MVP's leakage/authorization checks
(ADR-0014) must stand on their own without relying on NeMo being present.

## Security Implications

NeMo rails are defense-in-depth for prompt injection, not the sole
mitigation — see ADR-0004 and the threat model's prompt-injection section:
the token-authorization check must hold even if a rail is bypassed.

## Privacy Implications

NeMo must never be given access to the identity mapping or asked to perform
the pseudonymization/deanonymization itself (ADR-0004).

## Reversibility

High — NeMo is an orchestration layer around the gateway, not embedded in
the pseudonymization core. Swapping it for another guardrail framework
later does not require changing the Token Vault or detection pipeline.
