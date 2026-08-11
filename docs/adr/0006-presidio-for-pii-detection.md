# 0006 — Presidio for PII Detection

**Status:** Accepted

## Context

The system needs a detection layer for generic PERSON/LOCATION/DATE-type
entities in German text, on top of the deterministic regex layer that
handles fixed-format German identifiers.

## Decision

Use Microsoft Presidio, configured with a German spaCy NLP engine
(`de_core_news_lg` initially), as the second detection layer. Presidio's
recognizer-registry architecture is also the mechanism used for the custom
hospital/doctor recognizers (ADR-0007).

## Alternatives Considered

- **Build a bespoke NER pipeline directly on spaCy without Presidio:**
  rejected for MVP — Presidio already provides recognizer composition,
  span-conflict resolution primitives, and an analyzer/anonymizer split that
  matches this project's detect-then-tokenize architecture; reimplementing
  that scaffolding has no benefit here.
- **Commercial PII detection API:** rejected — introduces an external
  dependency and, worse, a second vendor that would see raw sensitive text,
  contradicting the minimal-external-exposure principle.

## Consequences

Presidio's out-of-box recognizers are US/English-shaped; German-specific
identifiers (Versichertennummer, Patientennummer) and entity types with no
standard NER class (hospital, doctor role) require custom recognizers —
this is treated as expected, necessary MVP work, not a shortcoming of the
choice (see design doc §5 and ADR-0007).

## Security Implications

Presidio runs entirely within the Privacy Gateway process; no text is sent
to an external service for detection.

## Privacy Implications

Detection quality directly bounds pseudonymization completeness — recall
gaps here are the main channel by which sensitive data could reach an LLM
provider. This is why Presidio's German-language recall is explicitly listed
as an assumption to validate (design doc §11) rather than assumed adequate.

## Reversibility

Medium — Presidio is one detection layer among several (ADR-0007); replacing
it means reimplementing the analyzer/recognizer interface the pipeline
depends on, but doesn't touch tokenization, vault, or output-guard logic.
