# 0020 — Fail-Closed, Not Fail-Open

**Status:** Accepted

## Context

Every uncertain state in this system — a low-confidence detection, an
unreachable identity provider, an unauthorized token, an ambiguous risk
score — has two possible failure modes: silently proceed with the original
(possibly unsafe) behavior, or block and surface the uncertainty. For a
system whose core purpose is preventing sensitive data exposure, only one
of these is acceptable.

## Decision

Fail closed everywhere uncertainty exists, as a blanket policy applied
consistently across every component, not decided ad hoc per feature:
- Detection confidence below threshold → block the message, not send it
  through unsanitized (design doc §5).
- Keycloak unreachable → no session issued, no fallback auth (ADR-0021).
- Token fails authorization check → not resolved, response rejected or left
  opaque, not silently passed through (ADR-0014).
- Leakage scan finds raw-PII-shaped content in output → response blocked,
  not delivered (ADR-0014).
- Risk score escalates to HIGH on quasi-identifier combination → policy
  check required before sending, not sent by default (design doc §5).

The user-facing failure is an explicit message ("Sensitive information could
not be safely processed"), never a silent degradation to unsafe behavior.

## Alternatives Considered

- **Fail open with a warning:** rejected — a warning that appears alongside
  data that has already been sent to an external provider is too late; the
  exposure already happened by the time the warning is seen.
- **Configurable fail-open mode for convenience/latency:** rejected as a
  default and as anything easily reachable — if ever needed for a specific,
  deliberate controlled-test scenario (rule #5's "unless explicitly
  configured for a controlled test"), it must require explicit,
  non-default, auditable configuration, not a runtime toggle a developer
  could flip under time pressure.

## Consequences

Doctors will occasionally see a blocked message for input that was actually
safe (false positives in the fail-closed direction) — this is an accepted
utility cost, and is exactly what the benchmark's precision metric (design
doc §8) makes visible and trackable, not something to silently tune away by
weakening the fail-closed default.

## Security Implications

This ADR is the system-wide policy that every other ADR's "block on
uncertainty" behavior is an instance of, rather than each component
independently deciding its own uncertainty-handling policy.

## Privacy Implications

Directly implements the project's stated Critical Security Principle: when
uncertain whether sensitive information has been adequately protected, fail
closed, never send uncertain data to the external model.

## Reversibility

Low — this is a foundational safety posture; any component-level exception
to it requires its own explicit ADR justifying the deviation, not a quiet
local decision.
