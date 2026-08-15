from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from app.privacy_gateway.detectors.base import Span, normalize

# Design spec §3 lists INSURANCE_NUMBER / PATIENT_NUMBER / PHONE / EMAIL explicitly.
# PERSON / DOCTOR / PATIENT are added here: the spec's own catch-all would otherwise
# leave patient names as untokenized "medical content", which contradicts §7's
# corpus-wide invariant that no raw PII string survives sanitize(), and ADR-0009's
# PATIENT_ token examples. Names are always tokenized, never escalation inputs.
DIRECT_IDENTIFIER_TYPES = frozenset(
    {"INSURANCE_NUMBER", "PATIENT_NUMBER", "PHONE", "EMAIL", "PERSON", "DOCTOR", "PATIENT"}
)

# Design spec §3, matching §5's own list verbatim: "city, hospital, exact date, age".
# DATE_TIME is layer 2's name for the same category as layer 1's DATE.
QUASI_IDENTIFIER_TYPES = frozenset({"LOCATION", "HOSPITAL", "DATE", "AGE", "DATE_TIME"})
_QUASI_CATEGORY_ALIASES = {"DATE_TIME": "DATE"}

# Presidio's own common default. Deliberately a module constant and not a Settings
# field: a fail-closed threshold that an environment variable can relax is not
# fail-closed.
CONFIDENCE_THRESHOLD = 0.4

QUASI_IDENTIFIER_ESCALATION_THRESHOLD = 2

# The longest ORDO German label is well under this; capping the n-gram window keeps
# rare-disease lookup linear in message length.
MAX_DISEASE_NAME_WORDS = 8


class RiskClassification(Enum):
    DIRECT_IDENTIFIER = "direct_identifier"
    QUASI_IDENTIFIER = "quasi_identifier"
    MEDICAL_CONTENT = "medical_content"


class HighRiskMessageError(Exception):
    """Two or more quasi-identifier categories co-occur with a rare-disease mention.

    Design spec §3: the whole message is rejected — sanitize() raises rather than
    returning partially-sanitized text. There is no human-review or override
    workflow yet, so this is the only possible outcome.
    """


class LowConfidenceSpanError(Exception):
    """A detected span scored below CONFIDENCE_THRESHOLD.

    Design spec §3: it blocks the whole message — fail-closed, not partial.
    """


@dataclass(frozen=True)
class RiskAssessment:
    tokenize: tuple[Span, ...]
    quasi_identifier_categories: frozenset[str]
    rare_diseases: tuple[str, ...]


class RiskScorer:
    """Deterministic, not ML (design spec §3)."""

    def __init__(self, rare_disease_names: frozenset[str]) -> None:
        self._rare_disease_names = rare_disease_names

    def classify(self, span: Span) -> RiskClassification:
        if span.entity_type in DIRECT_IDENTIFIER_TYPES:
            return RiskClassification.DIRECT_IDENTIFIER
        if span.entity_type in QUASI_IDENTIFIER_TYPES:
            return RiskClassification.QUASI_IDENTIFIER
        return RiskClassification.MEDICAL_CONTENT

    def find_rare_diseases(self, text: str) -> tuple[str, ...]:
        """Whole-word n-gram lookup against the ORDO-derived name list.

        Design spec §3 says "substring/lemma match against the message's
        medical-content spans", but no detector layer produces medical-content
        spans, so the whole message is scanned. Matching is on normalized whole-word
        n-grams rather than raw substrings, so a name like "Thymom" cannot fire on a
        longer unrelated word.
        """
        words = normalize(text).split()
        found: list[str] = []
        for start in range(len(words)):
            for length in range(1, MAX_DISEASE_NAME_WORDS + 1):
                if start + length > len(words):
                    break
                candidate = " ".join(words[start : start + length])
                if candidate in self._rare_disease_names and candidate not in found:
                    found.append(candidate)
        return tuple(found)

    def score(self, text: str, spans: Sequence[Span]) -> RiskAssessment:
        for span in spans:
            if span.confidence < CONFIDENCE_THRESHOLD:
                raise LowConfidenceSpanError(
                    f"span {span.entity_type} at [{span.start}:{span.end}] from layer "
                    f"{span.source_layer!r} has confidence {span.confidence}, below the "
                    f"{CONFIDENCE_THRESHOLD} threshold; the whole message is rejected "
                    "rather than partially sanitized"
                )

        categories = frozenset(
            _QUASI_CATEGORY_ALIASES.get(span.entity_type, span.entity_type)
            for span in spans
            if self.classify(span) is RiskClassification.QUASI_IDENTIFIER
        )
        rare_diseases = self.find_rare_diseases(text)

        if len(categories) >= QUASI_IDENTIFIER_ESCALATION_THRESHOLD and rare_diseases:
            raise HighRiskMessageError(
                f"{len(categories)} quasi-identifier categories "
                f"({', '.join(sorted(categories))}) co-occur with rare-disease "
                f"mention(s) ({', '.join(rare_diseases)}); the whole message is "
                "rejected rather than partially sanitized"
            )

        tokenize = tuple(
            span
            for span in spans
            if self.classify(span) is not RiskClassification.MEDICAL_CONTENT
        )
        return RiskAssessment(
            tokenize=tokenize,
            quasi_identifier_categories=categories,
            rare_diseases=rare_diseases,
        )
