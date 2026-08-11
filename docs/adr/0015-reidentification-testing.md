# 0015 — Re-identification Testing

**Status:** Accepted (lightweight in MVP; full scope is Phase 5)

## Context

Removing direct identifiers is not sufficient — quasi-identifier
combinations (age + city + rare disease + date, per the project brief) can
uniquely re-identify a patient even when no name is ever sent to the LLM.
"Name removed" must not be treated as "safe" anywhere in this system's
design or documentation.

## Decision

Two tiers, explicitly separated in scope:
- **MVP (lightweight):** the deterministic risk-scoring rule (design doc §5)
  that escalates when ≥2 quasi-identifier categories co-occur with a
  rare-disease/procedure mention is tested against fixed combination test
  cases in the golden corpus (design doc §8) — this validates that the
  *risk engine itself* behaves correctly, not that the system is immune to
  a real re-identification attack.
- **Phase 5 (full):** an explicit attacker model (attacker has anonymized
  text, public information, internet search, population knowledge) tested
  against realistic combinations, per the project brief. Not attempted in
  MVP — doing this properly requires a much larger corpus and adversarial
  methodology than a first increment can responsibly claim to validate.

## Alternatives Considered

- **Claim MVP's risk-scoring rule as sufficient re-identification
  protection:** rejected — it's a heuristic on a hand-crafted corpus, not a
  formal guarantee (k-anonymity or otherwise), and must not be
  overstated as one. Stated explicitly as a residual risk in the threat
  model.
- **Skip re-identification testing entirely until Phase 5:** rejected — even
  a lightweight correctness check on the risk engine now is better than
  discovering the escalation rule doesn't actually fire once real usage
  begins.

## Consequences

MVP ships with a documented, bounded claim ("the risk engine fires on our
test combinations") rather than an unbounded one ("the system prevents
re-identification") — this distinction must be preserved in any user-facing
or stakeholder-facing description of the MVP.

## Security Implications

Prevents the specific failure mode named in the brief: treating direct-
identifier removal as sufficient privacy protection.

## Privacy Implications

Sets the expectation, from the first ADR onward, that privacy claims in this
project are always scoped to what's actually been tested — not inferred
from what the architecture is designed to do.

## Reversibility

High — expanding from lightweight to full re-identification testing in
Phase 5 is additive, not a redesign of the MVP risk-scoring mechanism.
