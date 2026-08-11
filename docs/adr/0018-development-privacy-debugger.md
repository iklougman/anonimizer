# 0018 — Development Privacy Debugger

**Status:** Accepted

## Context

Privacy-safe logging (ADR-0012) deliberately makes production debugging of
pipeline decisions harder, by design. Developers still need a way to
inspect what the detection/pseudonymization pipeline actually did for a
given message during local development, without reintroducing a raw-value
leak channel into production.

## Decision

A UI panel (design doc, per the project brief's "Privacy Analysis" mockup)
showing detected entities, their categories, anonymization coverage, and
computed risk level — driven by a backend endpoint that is **disabled by
default** and only enabled via an explicit development-mode configuration
flag that must not be settable in a production deployment profile. Even
when enabled, the panel shows structured detection metadata (entity types,
counts, risk level, what was sent externally), not raw mapped values,
unless a further explicit secure-development-mode setting is also enabled.

## Alternatives Considered

- **No debugger, rely on the `privacy_invariants/` test suite alone for
  visibility:** rejected — tests validate known cases; developers
  investigating an unexpected detection miss on ad hoc input need
  interactive visibility that a fixed test suite can't provide.
- **Always-on debugger, gated only by a UI toggle (no server-side flag):**
  rejected — a client-side-only gate is not a security boundary; the
  server must refuse to serve debug data at all when not in development
  mode, not merely hide it in the UI.

## Consequences

Two distinct flags exist (debugger enabled vs. raw-value display enabled)
rather than one — deliberate, so "I want to see what got detected" and "I
want to see the actual sensitive values" are separately, explicitly opted
into.

## Security Implications

A misconfigured production deployment that accidentally leaves the
development flag set is a real risk this ADR cannot fully eliminate by
itself — it must be paired with deployment-config validation (e.g. a startup
check that refuses to boot with the debug flag set when
`ENVIRONMENT=production`), which is implementation-plan scope, not just
this ADR's.

## Privacy Implications

Directly implements the project brief's requirement that this feature be
disabled by default and never show raw mappings outside an explicit secure
development mode.

## Reversibility

High — purely additive tooling with no effect on the core pipeline's
behavior.
