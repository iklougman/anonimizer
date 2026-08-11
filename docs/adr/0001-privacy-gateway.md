# 0001 — Why a Privacy Gateway Exists

**Status:** Accepted

## Context

This system sends German doctors' free-text clinical input to LLMs,
including external providers. That text routinely contains direct
identifiers, quasi-identifiers, and medical information about real (or, in
this research context, synthetic) patients. Without an intermediary, every
LLM call is a potential data export of sensitive personal and health data.

## Decision

Introduce a dedicated Privacy Gateway component sitting between the chat API
and the LLM Gateway. It is the single mandatory choke point through which
all user input passes before reaching any model, and through which all model
output passes before reaching the user. No code path may call an
`LLMProvider` directly without going through the gateway first.

## Alternatives Considered

- **No gateway, sanitize inline in the API handler:** rejected — scatters
  privacy-critical logic across route handlers, making it easy to add a new
  endpoint that forgets to sanitize.
- **Sanitize client-side (in the browser):** rejected — the client cannot be
  trusted to enforce a server-side security invariant, and client-side PII
  detection models would need to ship to every browser.

## Consequences

All LLM calls incur the latency and complexity of the detection →
pseudonymization → risk-scoring pipeline, even for content that turns out to
contain no sensitive data. This is accepted per engineering rule #14 ("do
not optimize for minimum latency at the expense of privacy").

## Security Implications

Centralizes the security-critical logic in one auditable place instead of
distributing trust across every call site.

## Privacy Implications

This is the component that makes every other privacy guarantee in the system
possible; its correctness is the system's primary risk surface.

## Reversibility

Low cost to extend (new detectors, new rails); high cost to remove — removing
the gateway removes the entire privacy guarantee of the system.
