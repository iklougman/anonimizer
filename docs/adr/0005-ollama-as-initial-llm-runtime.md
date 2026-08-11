# 0005 — Ollama as Initial LLM Runtime

**Status:** Accepted

## Context

The system needs an LLM runtime to validate the pipeline end-to-end without
incurring external egress or cost on every development iteration, and
without a cloud dependency for local-only development.

## Decision

Ollama is the first `LLMProvider` adapter implemented, used for local
development, the utility-baseline comparison in the benchmark (design doc
§8), and as the "no egress" reference point in tests that assert nothing
sensitive leaves the machine.

## Alternatives Considered

- **Start with only an external provider:** rejected — every dev iteration
  and CI run would incur cost/latency/network dependency, and the "no
  egress" reference case for the leakage tests would not exist.
- **A different local runtime (vLLM, llama.cpp server):** viable
  alternatives; Ollama chosen for developer ergonomics and Docker Compose
  fit, not a hard technical requirement — swappable via `LLMProvider`.

## Consequences

Local model quality is lower than frontier external models, which is
expected and acceptable — Ollama's role is pipeline validation and the
egress-free reference point, not production-quality clinical answers.

## Security Implications

Ollama running fully local means zero network egress for that code path,
making it the natural control case for automated "no data left the machine"
tests.

## Privacy Implications

None of the pseudonymization guarantees depend on Ollama specifically — the
same pipeline runs regardless of provider (ADR-0016).

## Reversibility

High — swapping or adding runtimes is an adapter-level change.
