# 0016 — LLM Provider Abstraction

**Status:** Accepted

## Context

The system must support multiple LLM backends (Ollama, OpenAI in MVP;
Anthropic, Azure, others later) without coupling the Privacy Gateway or any
call site to a specific provider's API shape, and without requiring the
privacy pipeline to change when a provider is added.

## Decision

Define an `LLMProvider` interface (send a pseudonymized prompt, receive a
raw completion) with one adapter per provider. MVP implements
`OllamaProvider` and `OpenAIProvider`. The Privacy Gateway and its callers
depend only on this interface, never on a provider SDK directly.

## Alternatives Considered

- **Use LiteLLM or a similar gateway proxy for multi-provider routing:**
  considered, not adopted for MVP — with only two providers, a thin internal
  interface is simpler to reason about and audit than an additional proxy
  layer/service; revisit in Phase 4 if the provider count and routing needs
  (load balancing, fallback, cost-based routing) grow enough to justify it,
  per the project's "use a model gateway only if it simplifies multi-provider
  routing" guidance.
- **Couple the pipeline directly to each provider's SDK:** rejected — would
  require touching pipeline code every time a provider is added or changed,
  violating rule #13 (every component replaceable).

## Consequences

Provider-specific features (e.g. a capability only OpenAI's API exposes)
either get abstracted into the common interface if broadly useful, or
handled via provider-specific configuration — not by leaking provider
detection into pipeline logic.

## Security Implications

Provides the single choke point (ADR-0013) through which pseudonymized text
reaches any external network call, regardless of which provider is
configured.

## Privacy Implications

Makes provider-specific data-handling policies (e.g. "this provider is
approved for external test data, this one isn't") a configuration concern
enforceable at the adapter boundary, not scattered through call sites.

## Reversibility

High — this is precisely the abstraction designed to make provider changes
low-cost.
