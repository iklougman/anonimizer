# evaluation/

`golden_corpus/` holds the hand-written synthetic German clinical notes described
in `docs/superpowers/specs/2026-08-11-privacy-gateway-mvp-design.md` §8 and
`docs/superpowers/specs/2026-08-15-detection-pseudonymization-pipeline-design.md` §7.

Every note is entirely synthetic. Names, numbers, dates and email addresses are
invented; hospital names are real public institutions from the Destatis
Krankenhausverzeichnis and are not associated with any real patient.

## Format

One JSON object per file:

| field | meaning |
| --- | --- |
| `id` | matches the filename stem |
| `text` | the note |
| `expected_outcome` | `sanitized`, or `high_risk_rejected` when `pipeline.sanitize()` must raise |
| `rare_diseases` | exact German ORDO labels present in `text` (may be empty) |
| `entities` | ground-truth spans: `entity_type`, `text`, `start`, `end`, with `text[start:end] == entity.text` |

`backend/tests/unit/test_golden_corpus.py` validates every one of those
invariants, so an offset typo fails immediately and loudly.

## Scope

This directory holds corpus **data** only. The scored benchmark that consumes it —
precision/recall/F1, leakage rate, latency, cost — is a separate later plan.
`backend/tests/privacy_invariants/test_pipeline_corpus.py` uses the corpus for
pass/fail assertions, not for scored reporting.
