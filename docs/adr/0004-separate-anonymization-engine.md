# 0004 — NeMo Is Not the Anonymization Engine

**Status:** Accepted

## Context

NeMo Guardrails ships with some PII-handling actions, which could tempt using
it as the anonymization system directly. Doing so would couple the
privacy-critical pseudonymization/deanonymization logic to a
policy-orchestration framework not designed as a security boundary, and
would risk the identity mapping passing through NeMo's flow execution
context.

## Decision

The anonymization/tokenization system (`privacy_gateway/`) is a fully
separate component from NeMo Guardrails. NeMo may call into the gateway's
detection/pseudonymization functions as an action, but the Token Vault and
the identity mapping are never exposed to NeMo's runtime, and NeMo never
performs pseudonymization or deanonymization itself.

## Alternatives Considered

- **Use NeMo's built-in PII actions as the sole detection/anonymization
  layer:** rejected — ties the core security guarantee to a
  general-purpose orchestration framework's roadmap and internals, and
  blurs the "identity mapping must never reach anything LLM-adjacent" line
  from the project brief.

## Consequences

Some integration glue is required to call gateway functions as NeMo actions,
rather than getting anonymization "for free" from NeMo. Accepted — the
separation is the point.

## Security Implications

Keeps the identity mapping inside a structurally isolated package
(`privacy_gateway/`, ADR-0001) with its own import boundary, independent of
NeMo's dependency surface and configuration.

## Privacy Implications

Ensures the mapping's access path has exactly one implementation to audit,
not two overlapping ones (gateway and NeMo) that could drift.

## Reversibility

High — this is an integration-boundary decision, not a data-model one.
