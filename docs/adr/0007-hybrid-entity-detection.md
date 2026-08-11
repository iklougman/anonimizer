# 0007 — Hybrid Entity Detection

**Status:** Accepted

## Context

No single detection method covers the full entity taxonomy in the project
brief (direct identifiers, quasi-identifiers, medical/health information,
hospital/doctor names) at acceptable precision and recall. Fixed-format
identifiers need deterministic matching; names and locations need NER;
hospitals and clinical roles need domain-specific heuristics with no
standard NER class.

## Decision

Combine three layers in fixed precedence order — regex > Presidio custom
recognizers > generic NER — so that deterministic, high-confidence matches
always win span-overlap resolution, per design doc §5. This precedence
order (not a confidence score) is the tie-break rule, keeping merge behavior
reviewable and reproducible.

## Alternatives Considered

- **Single-method detection (e.g. NER only):** rejected — cannot reliably
  catch fixed-format identifiers like insurance numbers, and NER alone
  offers no clean way to add hospital/doctor recognition without
  fine-tuning a model, which is out of MVP scope.
- **Confidence-score-based merge instead of fixed precedence:** rejected for
  MVP — a probabilistic merge is harder to reason about and test
  deterministically; revisit if Phase 3's multi-strategy benchmark shows
  fixed precedence underperforms.

## Consequences

Adding a new detector requires an explicit precedence-order decision, not
just registration — this is intentional friction to prevent silent
detection-quality regressions.

## Security Implications

Deterministic merge behavior means detection output is reproducible and
testable; a flaky/probabilistic merge would make the `privacy_invariants/`
regression suite unreliable.

## Privacy Implications

Direct identifiers (regex layer) always win any overlap, meaning the
highest-risk entity types can never be silently downgraded to a lower-risk
type by a lower-precedence detector's classification.

## Reversibility

Medium — the pluggable strategy interface (`pseudonymization/strategies/`)
is scaffolded specifically so Phase 3's A–E strategy comparison doesn't
require rearchitecting this decision, only adding alternatives to benchmark
against it.
