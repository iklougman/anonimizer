from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

from app.privacy_gateway.detectors.base import Span, normalize

LAYER_NAME = "custom"

# The four suffixes named verbatim in the design spec (§2) and the master spec (§5).
HOSPITAL_SUFFIX_PATTERN = re.compile(
    r"\b(?:Universitätsklinikum|Klinikum|Krankenhaus|MVZ)\b"
)

TITLE_PREFIX_PATTERN = re.compile(r"(?:Prof\.|Dr\.|PD|Priv\.-Doz\.|med\.)\s*$")
TITLE_LOOKBEHIND_CHARS = 25

TREATING_DOCTOR_PATTERN = re.compile(r"behandelnde[rn]?\s+(?:Arzt|Ärztin)", re.IGNORECASE)
TREATING_DOCTOR_PROXIMITY_CHARS = 60


class CustomRecognizers:
    """Layer 3: refine, don't rescan (design spec §2).

    Operates only on spans layer 2 already tagged PERSON or ORGANIZATION, and never
    produces a span offset that layers 1 and 2 did not produce.
    """

    layer_name = LAYER_NAME

    def __init__(self, hospital_names: frozenset[str]) -> None:
        self._hospital_names = hospital_names

    def refine(self, text: str, spans: Sequence[Span]) -> list[Span]:
        result: list[Span] = []
        unlabeled_person_indices: list[int] = []

        for span in spans:
            if span.entity_type == "ORGANIZATION":
                if self._is_hospital(text[span.start : span.end]):
                    result.append(_retag(span, "HOSPITAL"))
                # A non-hospital ORGANIZATION is not part of the entity taxonomy at
                # all — layer 2 emits it purely so this layer can look for hospitals
                # in it — so it is dropped rather than carried into risk scoring.
                continue
            if span.entity_type == "PERSON":
                if self._is_doctor(text, span):
                    result.append(_retag(span, "DOCTOR"))
                else:
                    unlabeled_person_indices.append(len(result))
                    result.append(span)
                continue
            result.append(span)

        patient_surface = _patient_surface(text, result, unlabeled_person_indices)
        for index in unlabeled_person_indices:
            span = result[index]
            surface = text[span.start : span.end]
            result[index] = _retag(
                span, "PATIENT" if surface == patient_surface else "PERSON"
            )
        return sorted(result)

    def _is_hospital(self, surface: str) -> bool:
        if HOSPITAL_SUFFIX_PATTERN.search(surface):
            return True
        return normalize(surface) in self._hospital_names

    @staticmethod
    def _is_doctor(text: str, span: Span) -> bool:
        prefix = text[max(0, span.start - TITLE_LOOKBEHIND_CHARS) : span.start]
        if TITLE_PREFIX_PATTERN.search(prefix):
            return True
        window_start = max(0, span.start - TREATING_DOCTOR_PROXIMITY_CHARS)
        return TREATING_DOCTOR_PATTERN.search(text, window_start, span.start) is not None


def _retag(span: Span, entity_type: str) -> Span:
    return Span(span.start, span.end, entity_type, span.confidence, LAYER_NAME)


def _patient_surface(text: str, spans: list[Span], indices: list[int]) -> str | None:
    """Design spec §2: "the first/most-referenced remaining unlabeled PERSON span in
    the message promoted to PATIENT". Most occurrences of the same exact surface
    string wins; ties break toward the surface that appears earliest.

    Matching is by exact surface string, which is the documented MVP coreference
    limitation: "Hans Müller" and a later "Herr Müller" are different surfaces and
    therefore different entities.
    """
    if not indices:
        return None
    surfaces = [text[spans[i].start : spans[i].end] for i in indices]
    counts = Counter(surfaces)
    # Built in reverse so the earliest position for each surface is what survives.
    first_position = {
        surface: position for position, surface in reversed(list(enumerate(surfaces)))
    }
    return min(counts, key=lambda surface: (-counts[surface], first_position[surface]))
