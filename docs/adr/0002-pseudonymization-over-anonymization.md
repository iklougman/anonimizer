# 0002 — Pseudonymization, Not Anonymization

**Status:** Accepted

## Context

The system replaces sensitive values with tokens and later resolves them
back to the original values for the doctor. This is a reversible
transformation, not an irreversible one. Terminology matters here: calling
reversible tokenization "anonymization" is both technically wrong (GDPR
Recital 26 draws this distinction explicitly) and dangerous, because it
invites treating a reversible mapping as if it carried the reduced legal
weight of true anonymization.

## Decision

Use "pseudonymization" for this transformation, everywhere: code, docs, UI,
ADRs, logs. "Anonymization" is reserved for contexts where re-identification
is genuinely impossible (e.g. aggregate benchmark statistics with no stored
mapping). The identity mapping (the Token Vault) is the thing that makes this
pseudonymization rather than anonymization, and it is treated as sensitive
data in its own right.

## Alternatives Considered

- **True anonymization (no mapping stored, irreversible):** rejected as the
  primary mode — doctors need the reconstructed, readable answer, and the
  project's explicit goal is to compare strategies, which requires knowing
  ground truth.
- **Loose terminology ("anonymization" used informally):** rejected per the
  project's explicit terminology requirement; imprecise language here is a
  compliance risk, not just a style issue.

## Consequences

Every place that stores or transmits a token implicitly depends on the Token
Vault's security (ADR-0008); the system's privacy guarantee is only as strong
as that vault, not as strong as "the data is gone."

## Security Implications

The mapping is a high-value target — its compromise re-identifies every
tokenized value it covers. This drives the encryption (ADR-0010) and
isolation (ADR-0008, ADR-0011) decisions.

## Privacy Implications

Legally and practically, pseudonymized data is still personal data under
GDPR. This system must never be described, marketed, or relied upon as
producing anonymized (out-of-scope) data.

## Reversibility

By design, reversible at the data level (that's the point). The terminology
decision itself is not something to reverse without re-auditing every
downstream claim about this system's compliance posture.
