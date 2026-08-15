import pytest

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.detectors.presidio_detector import (
    SPACY_MODEL_NAME,
    SUPPORTED_ENTITIES,
    PresidioDetector,
    build_analyzer_engine,
)


@pytest.fixture(scope="module")
def detector():
    return PresidioDetector()


def _surfaces(text, spans, entity_type):
    return {text[s.start : s.end] for s in spans if s.entity_type == entity_type}


def test_uses_the_german_model_named_in_the_design_spec():
    assert SPACY_MODEL_NAME == "de_core_news_lg"
    assert SUPPORTED_ENTITIES == ["PERSON", "LOCATION", "ORGANIZATION", "DATE_TIME"]


def test_registry_holds_only_the_spacy_recognizer():
    """ADR-0001: Presidio's predefined recognizer set pulls in network-capable
    helpers (tldextract fetches a public-suffix list). Registering only the spaCy
    recognizer keeps the analyzer offline by construction."""
    engine = build_analyzer_engine()
    assert len(engine.registry.recognizers) == 1
    assert engine.registry.recognizers[0].name == "SpacyRecognizer"


def test_detects_person_and_location(detector):
    text = "Lukas Berger wurde in Heidelberg behandelt."
    spans = detector.detect(text, [])
    assert _surfaces(text, spans, "PERSON") == {"Lukas Berger"}
    assert _surfaces(text, spans, "LOCATION") == {"Heidelberg"}


def test_preserves_presidios_own_confidence_score(detector):
    spans = detector.detect("Lukas Berger wurde in Heidelberg behandelt.", [])
    assert spans
    for span in spans:
        assert 0.0 < span.confidence <= 1.0
        assert span.source_layer == "presidio"


def test_skips_text_already_claimed_by_a_higher_precedence_layer(detector):
    text = "Lukas Berger wurde in Heidelberg behandelt."
    claimed = [Span(0, 12, "PATIENT_NUMBER", 1.0, "regex")]
    spans = detector.detect(text, claimed)
    assert _surfaces(text, spans, "PERSON") == set()
    assert _surfaces(text, spans, "LOCATION") == {"Heidelberg"}


def test_emits_organization_for_a_clinic_name(detector):
    text = "Die Verlegung in das Universitätsklinikum Heidelberg erfolgte."
    spans = detector.detect(text, [])
    organizations = _surfaces(text, spans, "ORGANIZATION")
    assert any("Universitätsklinikum" in name for name in organizations)


def test_returns_spans_in_ascending_offset_order(detector):
    text = "Lukas Berger wurde in Heidelberg von Anna Schmitt behandelt."
    spans = detector.detect(text, [])
    assert [s.start for s in spans] == sorted(s.start for s in spans)
