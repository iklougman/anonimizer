from __future__ import annotations

import re
from collections.abc import Sequence

from app.config import get_settings
from app.privacy_gateway.detectors.base import Span, is_claimed

LAYER_NAME = "regex"

EMAIL_PATTERN = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
# German Versichertennummer: one uppercase letter followed by nine digits.
INSURANCE_NUMBER_PATTERN = r"\b[A-Z]\d{9}\b"
DATE_PATTERN = r"\b\d{2}\.\d{2}\.\d{4}\b"
# Design spec §2 gives `\d{1,3}\s*(?:-jährig|jährige[rn]?|Jahre alt)`. The hyphenated
# alternatives are listed longest-first here so "81-jähriger" yields the full
# "81-jähriger" rather than stopping after "81-jährig" — Python alternation is
# first-match, not longest-match.
AGE_PATTERN = r"\d{1,3}\s*(?:-jährige[rn]?|-jährig|jährige[rn]?|Jahre alt)"
PHONE_PATTERN = r"(?:\+49[ ]?|0)\d{2,5}[ /-]?\d{3,9}(?:[ -]?\d{1,4})?"


class RegexDetector:
    """Layer 1: German-specific fixed-format identifiers, confidence always 1.0."""

    layer_name = LAYER_NAME

    def __init__(self, patient_number_pattern: str | None = None) -> None:
        if patient_number_pattern is None:
            patient_number_pattern = get_settings().patient_number_pattern
        # An ordered list, not a dict: EMAIL, INSURANCE_NUMBER, PATIENT_NUMBER and
        # DATE all contain digit runs that the deliberately permissive PHONE pattern
        # would otherwise swallow, so PHONE runs last and sees them already claimed.
        self._patterns: list[tuple[str, re.Pattern[str]]] = [
            ("EMAIL", re.compile(EMAIL_PATTERN)),
            ("INSURANCE_NUMBER", re.compile(INSURANCE_NUMBER_PATTERN)),
            ("PATIENT_NUMBER", re.compile(patient_number_pattern)),
            ("DATE", re.compile(DATE_PATTERN)),
            ("AGE", re.compile(AGE_PATTERN)),
            ("PHONE", re.compile(PHONE_PATTERN)),
        ]

    def detect(self, text: str, claimed: Sequence[Span]) -> list[Span]:
        seen: list[Span] = list(claimed)
        produced: list[Span] = []
        for entity_type, pattern in self._patterns:
            for match in pattern.finditer(text):
                start, end = self._match_bounds(match)
                span = Span(start, end, entity_type, 1.0, self.layer_name)
                if is_claimed(span, seen):
                    continue
                seen.append(span)
                produced.append(span)
        return sorted(produced)

    @staticmethod
    def _match_bounds(match: re.Match[str]) -> tuple[int, int]:
        """A pattern may mark the identifier itself with a named group `value`, so a
        label like "Patientennummer: " stays in the text and only the number is
        replaced by a token."""
        if "value" in match.re.groupindex:
            return match.start("value"), match.end("value")
        return match.start(), match.end()
