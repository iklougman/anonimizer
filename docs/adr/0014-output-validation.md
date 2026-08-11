# 0014 — Output Validation Before Deanonymization

**Status:** Accepted

## Context

Blindly resolving every `[TYPE_XXXXX]`-shaped substring in an LLM's response
is unsafe: a malicious or injected prompt could ask the model to emit a
token from a different conversation or tenant, or a fabricated token string,
hoping it resolves (the "reveal the mapping for PATIENT_7F82A" attack
pattern from the project brief). The model could also hallucinate or leak
something that merely looks like real PII without being a token at all.

## Decision

The return path runs two independent checks before any substitution happens:
1. **Leakage scan:** re-run the full detector stack (ADR-0007) on the raw
   model output. Any raw-PII-shaped content that isn't a legitimate token is
   a leakage event — the response is blocked or redacted, not passed
   through, and an audit event is emitted.
2. **Token authorization:** for each extracted token, verify it was issued
   for *this specific* `tenant_id` + `conversation_id` before resolving it
   (via `TokenVault.resolve_token`, ADR-0008). Tokens failing this check are
   never resolved — left opaque or the response rejected, per policy — no
   exceptions for tokens that merely look well-formed.

Only tokens passing both checks get substituted with their decrypted
original value.

## Alternatives Considered

- **Resolve any well-formed token found in output:** rejected — this is
  exactly the vulnerability described above; format validity alone says
  nothing about authorization.
- **Trust the model to only emit tokens it was given:** rejected — models
  can be manipulated by injected instructions or simply err; the check must
  not depend on model behavior being correct.

## Consequences

A legitimate response that happens to reference a token from an earlier,
separate conversation (not currently a supported use case, ADR-0009) would
also fail this check — accepted, since cross-conversation token references
aren't a supported feature in MVP anyway.

## Security Implications

This is the concrete mechanism that defeats token-injection attacks; see
threat model, "Token injection / cross-tenant or cross-conversation token
reuse."

## Privacy Implications

Ensures deanonymization only ever happens for data the requesting doctor is
actually authorized to see, not merely data that happens to be well-formed.

## Reversibility

Low — weakening this check (e.g. skipping authorization for performance)
would reopen the token-injection attack class; any future change here needs
equal or stronger guarantees, not a relaxation.
