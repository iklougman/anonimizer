from app.privacy_gateway.detectors.base import Span, is_claimed, normalize, spans_overlap
from app.privacy_gateway.detectors.regex_detector import RegexDetector


def _surfaces(text, spans, entity_type):
    return {text[s.start : s.end] for s in spans if s.entity_type == entity_type}


def test_spans_overlap_is_half_open():
    assert spans_overlap(Span(0, 5, "DATE", 1.0, "regex"), Span(4, 9, "PHONE", 1.0, "regex"))
    assert not spans_overlap(Span(0, 5, "DATE", 1.0, "regex"), Span(5, 9, "PHONE", 1.0, "regex"))


def test_is_claimed_checks_every_existing_span():
    claimed = [Span(10, 20, "EMAIL", 1.0, "regex")]
    assert is_claimed(Span(15, 25, "PHONE", 1.0, "regex"), claimed)
    assert not is_claimed(Span(20, 25, "PHONE", 1.0, "regex"), claimed)


def test_normalize_collapses_case_punctuation_and_whitespace():
    assert normalize("  Klinikum   Nürnberg, AöR ") == "klinikum nürnberg aör"


def test_detects_insurance_number():
    text = "Die Versichertennummer lautet B987654321."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "INSURANCE_NUMBER") == {"B987654321"}


def test_detects_patient_number_value_only_not_the_label():
    text = "Patientennummer: 4471029. Fallnummer: 8830145."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "PATIENT_NUMBER") == {"4471029", "8830145"}


def test_detects_german_phone_formats():
    text = "Erreichbar unter 0941 5551234 oder +49 30 1234567."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "PHONE") == {"0941 5551234", "+49 30 1234567"}


def test_detects_email_without_swallowing_the_sentence_period():
    text = "Rückfragen an s.vogel@example.de."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "EMAIL") == {"s.vogel@example.de"}


def test_detects_german_dates():
    text = "Aufnahme am 12.03.2024, Entlassung am 28.02.2024."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "DATE") == {"12.03.2024", "28.02.2024"}


def test_detects_age_in_all_german_forms():
    text = "Ein 7-jähriger, eine 52-jährige, ein 41-jährigen und jemand 67 Jahre alt."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "AGE") == {
        "7-jähriger",
        "52-jährige",
        "41-jährigen",
        "67 Jahre alt",
    }


def test_every_regex_span_has_confidence_one_and_the_regex_layer_name():
    text = "A123456789 am 12.03.2024, 0941 5551234, a@b.de, 67 Jahre alt."
    spans = RegexDetector().detect(text, [])
    assert spans
    assert all(span.confidence == 1.0 for span in spans)
    assert all(span.source_layer == "regex" for span in spans)


def test_a_date_is_never_swallowed_by_the_permissive_phone_pattern():
    text = "Telefonisch unter +49 30 1234567 am 03.01.2025."
    spans = RegexDetector().detect(text, [])
    assert _surfaces(text, spans, "DATE") == {"03.01.2025"}
    assert _surfaces(text, spans, "PHONE") == {"+49 30 1234567"}


def test_already_claimed_offsets_are_skipped():
    text = "Aufnahme am 12.03.2024."
    claimed = [Span(12, 22, "DATE", 1.0, "some-higher-layer")]
    spans = RegexDetector().detect(text, claimed)
    assert spans == []


def test_spans_are_returned_in_ascending_offset_order():
    text = "Patientennummer: 4471029, Aufnahme am 12.03.2024, Kontakt a@b.de."
    spans = RegexDetector().detect(text, [])
    assert [s.start for s in spans] == sorted(s.start for s in spans)
