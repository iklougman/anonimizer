from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import SpacyRecognizer

from app.privacy_gateway.detectors.base import Span, is_claimed

LAYER_NAME = "presidio"
SPACY_MODEL_NAME = "de_core_news_lg"

# de_core_news_lg's NER labels are PER, LOC, ORG and MISC; Presidio maps
# PER -> PERSON, LOC -> LOCATION, ORG -> ORGANIZATION and drops MISC. DATE_TIME is
# listed because the design spec names it as a layer-2 output, but no German spaCy
# model emits DATE entities — in practice all date coverage comes from layer 1's
# regex. ORGANIZATION exists purely as input to the layer-3 HOSPITAL refinement.
SUPPORTED_ENTITIES = ["PERSON", "LOCATION", "ORGANIZATION", "DATE_TIME"]


@lru_cache(maxsize=1)
def build_analyzer_engine() -> AnalyzerEngine:
    """One analyzer (and one loaded spaCy model) per process.

    The registry is populated with the spaCy recognizer *before* AnalyzerEngine sees
    it, because AnalyzerEngine calls `load_predefined_recognizers()` on an empty
    registry — and several predefined recognizers reach for the network (ADR-0001).
    """
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "de", "model_name": SPACY_MODEL_NAME}],
        }
    )
    registry = RecognizerRegistry(supported_languages=["de"])
    registry.add_recognizer(
        SpacyRecognizer(supported_language="de", supported_entities=SUPPORTED_ENTITIES)
    )
    return AnalyzerEngine(
        nlp_engine=provider.create_engine(),
        registry=registry,
        supported_languages=["de"],
    )


class PresidioDetector:
    """Layer 2: the German-NER layer (design spec §2). Presidio's own per-entity
    confidence score is preserved rather than overwritten."""

    layer_name = LAYER_NAME

    def __init__(self, analyzer: AnalyzerEngine | None = None) -> None:
        self._analyzer = analyzer if analyzer is not None else build_analyzer_engine()

    def detect(self, text: str, claimed: Sequence[Span]) -> list[Span]:
        results = self._analyzer.analyze(
            text=text, language="de", entities=SUPPORTED_ENTITIES
        )
        seen: list[Span] = list(claimed)
        produced: list[Span] = []
        for result in sorted(results, key=lambda item: (item.start, item.end)):
            span = Span(
                result.start,
                result.end,
                result.entity_type,
                float(result.score),
                self.layer_name,
            )
            if is_claimed(span, seen):
                continue
            seen.append(span)
            produced.append(span)
        return produced
