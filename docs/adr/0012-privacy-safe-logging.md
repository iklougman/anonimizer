# 0012 — Privacy-Safe Logging

**Status:** Accepted

## Context

Logs, error traces, and telemetry are a common accidental leak channel that
bypasses every other control in the system — a raw patient name in a log
line defeats pseudonymization even if the vault and LLM boundary are
flawless.

## Decision

The observability layer emits typed, structured security events (event
type, entity type, token, conversation ID, timestamp) as its *only* logging
primitive for anything touching request/response content. There is no
free-text log statement anywhere in `privacy_gateway/`, `llm_gateway/`, or
`api/` that includes request/response bodies, original values, or the token
mapping. This is enforced by the event schema itself having no field to put
a raw value in — not by best-effort redaction of arbitrary log strings.

## Alternatives Considered

- **Free-text logging with regex-based redaction:** rejected — redaction
  patterns lag behind the actual entity taxonomy (exactly the same recall
  problem as detection itself, ADR-0006/0007) and a missed pattern silently
  leaks. A schema with no raw-value field can't leak what it has no slot
  for.
- **Log everything to a restricted-access log store:** rejected — "restrict
  access to the leak" is weaker than "don't create the leak," and
  restricted access still fails the moment any log aggregation/APM tool is
  added later.

## Consequences

Debugging a specific pipeline failure from logs alone is harder than with
free-text logs — this trade-off is deliberate and offset by the dev-only
privacy debugger (ADR-0018) for local investigation.

## Security Implications

Directly addresses the "log leakage / accidental telemetry / debugging
traces" threat — third-party telemetry/APM integrations added later inherit
this safety by construction, since they only ever see the structured events.

## Privacy Implications

Satisfies rule #4 ("never log sensitive values") and rule #12 ("keep the
identity mapping outside the LLM context") as a structural guarantee rather
than a policy to remember.

## Reversibility

Low — retrofitting free-text logging back in would require re-auditing
every log call site for leak risk; the schema-based approach is meant to be
permanent.
