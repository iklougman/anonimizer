import pytest

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.risk_scoring.scorer import (
    CONFIDENCE_THRESHOLD,
    QUASI_IDENTIFIER_ESCALATION_THRESHOLD,
    HighRiskMessageError,
    LowConfidenceSpanError,
    RiskClassification,
    RiskScorer,
)

DISEASES = frozenset({"zystische fibrose", "huntington-krankheit"})


@pytest.fixture
def scorer():
    return RiskScorer(DISEASES)


def _span(entity_type, start=0, end=5, confidence=1.0):
    return Span(start, end, entity_type, confidence, "test")


def test_threshold_is_exactly_the_spec_value():
    assert CONFIDENCE_THRESHOLD == 0.4
    assert QUASI_IDENTIFIER_ESCALATION_THRESHOLD == 2


@pytest.mark.parametrize(
    "entity_type",
    ["INSURANCE_NUMBER", "PATIENT_NUMBER", "PHONE", "EMAIL", "PERSON", "DOCTOR", "PATIENT"],
)
def test_direct_identifiers(scorer, entity_type):
    assert scorer.classify(_span(entity_type)) is RiskClassification.DIRECT_IDENTIFIER


@pytest.mark.parametrize("entity_type", ["LOCATION", "HOSPITAL", "DATE", "AGE", "DATE_TIME"])
def test_quasi_identifiers(scorer, entity_type):
    assert scorer.classify(_span(entity_type)) is RiskClassification.QUASI_IDENTIFIER


def test_anything_else_is_medical_content(scorer):
    assert scorer.classify(_span("DIAGNOSIS")) is RiskClassification.MEDICAL_CONTENT


def test_finds_a_rare_disease_by_whole_word_ngram(scorer):
    found = scorer.find_rare_diseases("Diagnose Zystische Fibrose seit 2019.")
    assert found == ("zystische fibrose",)


def test_rare_disease_matching_is_case_and_punctuation_insensitive(scorer):
    assert scorer.find_rare_diseases("… ZYSTISCHE  FIBROSE!") == ("zystische fibrose",)


def test_rare_disease_matching_does_not_fire_on_a_partial_word(scorer):
    assert scorer.find_rare_diseases("Fallnummer 8830145 dokumentiert.") == ()


def test_two_quasi_categories_plus_rare_disease_escalates_to_high(scorer):
    text = "Der 19-jährige Patient aus Tübingen hat Zystische Fibrose."
    spans = [_span("AGE", 4, 14), _span("LOCATION", 27, 35)]
    with pytest.raises(HighRiskMessageError, match="quasi-identifier"):
        scorer.score(text, spans)


def test_one_quasi_category_plus_rare_disease_does_not_escalate(scorer):
    text = "Der 41-jährige Patient hat eine Huntington-Krankheit."
    spans = [_span("AGE", 4, 14)]
    assessment = scorer.score(text, spans)
    assert assessment.rare_diseases == ("huntington-krankheit",)
    assert assessment.quasi_identifier_categories == frozenset({"AGE"})


def test_two_quasi_categories_without_a_rare_disease_does_not_escalate(scorer):
    text = "Der 19-jährige Patient aus Tübingen wurde entlassen."
    spans = [_span("AGE", 4, 14), _span("LOCATION", 27, 35)]
    assessment = scorer.score(text, spans)
    assert assessment.rare_diseases == ()
    assert len(assessment.quasi_identifier_categories) == 2


def test_repeats_of_one_quasi_category_are_still_one_category(scorer):
    text = "Aus Tübingen nach Kiel verlegt, Diagnose Zystische Fibrose."
    spans = [_span("LOCATION", 4, 12), _span("LOCATION", 18, 22)]
    assessment = scorer.score(text, spans)
    assert assessment.quasi_identifier_categories == frozenset({"LOCATION"})


def test_date_and_date_time_count_as_one_category(scorer):
    """A regex DATE and a spaCy DATE_TIME are the same quasi-identifier category and
    must not on their own satisfy the two-category escalation rule."""
    text = "Am 11.05.2024, im Mai, Diagnose Zystische Fibrose."
    spans = [_span("DATE", 3, 13), _span("DATE_TIME", 18, 21)]
    assessment = scorer.score(text, spans)
    assert assessment.quasi_identifier_categories == frozenset({"DATE"})


def test_a_low_confidence_span_blocks_the_whole_message(scorer):
    spans = [_span("PERSON", 0, 5, confidence=0.39), _span("DATE", 10, 20)]
    with pytest.raises(LowConfidenceSpanError, match="0.4"):
        scorer.score("some text with a name and a date here", spans)


def test_a_span_exactly_at_the_threshold_is_accepted(scorer):
    spans = [_span("PERSON", 0, 5, confidence=0.4)]
    assert scorer.score("Anna wurde untersucht.", spans).tokenize == tuple(spans)


def test_confidence_is_checked_before_escalation(scorer):
    """Fail-closed ordering: a message that is both low-confidence and high-risk
    reports the low-confidence reason, and never gets as far as partial handling."""
    text = "Der 19-jährige Patient aus Tübingen hat Zystische Fibrose."
    spans = [_span("AGE", 4, 14), _span("LOCATION", 27, 35, confidence=0.2)]
    with pytest.raises(LowConfidenceSpanError):
        scorer.score(text, spans)


def test_direct_and_quasi_identifiers_are_tokenize_eligible_medical_content_is_not(scorer):
    spans = [_span("PATIENT", 0, 5), _span("LOCATION", 6, 11), _span("DIAGNOSIS", 12, 20)]
    assessment = scorer.score("Anna Kiel Migraene ok", spans)
    assert [s.entity_type for s in assessment.tokenize] == ["PATIENT", "LOCATION"]


def test_scoring_a_message_with_no_spans_is_not_an_error(scorer):
    assessment = scorer.score("Keine sensiblen Angaben.", [])
    assert assessment.tokenize == ()
    assert assessment.quasi_identifier_categories == frozenset()
