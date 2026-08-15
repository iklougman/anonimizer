from __future__ import annotations

from app.privacy_gateway.detectors.base import Detector, Span, SpanRefiner


class DetectorStack:
    """The three detection layers in fixed precedence order (design spec §2).

    Regex > Presidio+spaCy > custom recognizers. Overlap is resolved by that order
    alone — confidence scores are never compared to decide which layer wins, so the
    result is deterministic for a given input.
    """

    def __init__(
        self,
        regex_detector: Detector,
        presidio_detector: Detector,
        refiner: SpanRefiner,
    ) -> None:
        self._regex_detector = regex_detector
        self._presidio_detector = presidio_detector
        self._refiner = refiner

    def detect(self, text: str) -> list[Span]:
        spans: list[Span] = list(self._regex_detector.detect(text, []))
        spans.extend(self._presidio_detector.detect(text, spans))
        spans.sort()
        return sorted(self._refiner.refine(text, spans))
