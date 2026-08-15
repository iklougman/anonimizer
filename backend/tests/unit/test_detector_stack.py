import pytest

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack

GAZETTEER = frozenset({"charité universitätsmedizin berlin"})


class _FakeDetector:
    def __init__(self, layer_name, spans):
        self.layer_name = layer_name
        self._spans = spans
        self.claimed_at_call = None

    def detect(self, text, claimed):
        self.claimed_at_call = list(claimed)
        return [s for s in self._spans if not any(
            s.start < c.end and c.start < s.end for c in claimed
        )]


class _FakeRefiner:
    layer_name = "custom"

    def __init__(self):
        self.spans_at_call = None

    def refine(self, text, spans):
        self.spans_at_call = list(spans)
        return list(spans)


def test_presidio_layer_sees_the_regex_layers_spans_as_claimed():
    regex_span = Span(0, 10, "DATE", 1.0, "regex")
    regex = _FakeDetector("regex", [regex_span])
    presidio = _FakeDetector("presidio", [Span(0, 10, "PERSON", 0.85, "presidio")])
    refiner = _FakeRefiner()

    DetectorStack(regex, presidio, refiner).detect("0123456789 text")

    assert presidio.claimed_at_call == [regex_span]


def test_regex_wins_an_overlap_regardless_of_confidence():
    """Precedence is fixed, not confidence-based: even a 0.99-confidence Presidio
    span loses to the lower-numbered layer."""
    regex_span = Span(0, 10, "DATE", 1.0, "regex")
    regex = _FakeDetector("regex", [regex_span])
    presidio = _FakeDetector("presidio", [Span(2, 8, "PERSON", 0.99, "presidio")])

    spans = DetectorStack(regex, presidio, _FakeRefiner()).detect("0123456789 text")

    assert spans == [regex_span]


def test_the_refiner_receives_the_combined_span_set():
    regex_span = Span(0, 10, "DATE", 1.0, "regex")
    presidio_span = Span(11, 15, "PERSON", 0.85, "presidio")
    refiner = _FakeRefiner()

    DetectorStack(
        _FakeDetector("regex", [regex_span]),
        _FakeDetector("presidio", [presidio_span]),
        refiner,
    ).detect("0123456789 Anna")

    assert refiner.spans_at_call == [regex_span, presidio_span]


def test_result_is_sorted_by_offset():
    refiner = _FakeRefiner()
    spans = DetectorStack(
        _FakeDetector("regex", [Span(20, 30, "DATE", 1.0, "regex")]),
        _FakeDetector("presidio", [Span(0, 5, "PERSON", 0.85, "presidio")]),
        refiner,
    ).detect("x" * 40)
    assert [s.start for s in spans] == [0, 20]


def test_pre_refine_sort_makes_the_patient_tiebreak_correct():
    """CustomRecognizers' patient tie-break (Task 5) picks the surface that appears
    earliest in the text when counts are tied, but it determines "earliest" from the
    order of the span list it is handed -- it does not consult text offsets itself.
    If DetectorStack handed it a list that was out of offset order, the tie-break
    would silently promote the wrong PERSON to PATIENT.

    Regex here "finds" the name that occurs *later* in the text; Presidio "finds"
    the name that occurs *earlier*. Layer 1's output is concatenated before layer
    2's, so the raw regex+presidio list is out of offset order -- and only the
    intermediate `spans.sort()` in `DetectorStack.detect` (stack.py) fixes that
    before CustomRecognizers ever sees it. Without that sort, "Peter Klein" (which
    appears second) would win the tie instead of "Anna Weber" (which appears
    first).
    """
    text = "Anna Weber wurde untersucht. Peter Klein wartete daneben."
    anna_start = text.index("Anna Weber")
    anna_end = anna_start + len("Anna Weber")
    peter_start = text.index("Peter Klein")
    peter_end = peter_start + len("Peter Klein")

    peter_span = Span(peter_start, peter_end, "PERSON", 0.9, "regex")
    anna_span = Span(anna_start, anna_end, "PERSON", 0.9, "presidio")

    # Layer 1 (regex) reports the later-occurring name; layer 2 (Presidio) reports
    # the earlier-occurring one -- so concatenating regex-then-Presidio output
    # yields [peter_span, anna_span], which is out of offset order.
    regex = _FakeDetector("regex", [peter_span])
    presidio = _FakeDetector("presidio", [anna_span])

    stack = DetectorStack(regex, presidio, CustomRecognizers(GAZETTEER))
    result = stack.detect(text)

    by_surface = {text[s.start : s.end]: s.entity_type for s in result}
    assert by_surface["Anna Weber"] == "PATIENT"
    assert by_surface["Peter Klein"] == "PERSON"


@pytest.fixture(scope="module")
def real_stack():
    return DetectorStack(
        RegexDetector(), PresidioDetector(), CustomRecognizers(GAZETTEER)
    )


def test_end_to_end_precedence_on_a_real_clinical_sentence(real_stack):
    text = (
        "Patient Lukas Berger, Versichertennummer A123456789, wurde am 12.03.2024 "
        "im Universitätsklinikum Heidelberg von Dr. Anna Schmitt aufgenommen."
    )
    by_type = {}
    for span in real_stack.detect(text):
        by_type.setdefault(span.entity_type, set()).add(text[span.start : span.end])

    assert by_type["INSURANCE_NUMBER"] == {"A123456789"}
    assert by_type["DATE"] == {"12.03.2024"}
    assert "Lukas Berger" in by_type["PATIENT"]
    assert "Anna Schmitt" in by_type["DOCTOR"]
    assert "Universitätsklinikum Heidelberg" in by_type["HOSPITAL"]
    assert "ORGANIZATION" not in by_type


def test_the_stack_never_returns_overlapping_spans(real_stack):
    text = (
        "Elena Fischer ist 34 Jahre alt und kommt aus Leipzig. Sie ist erreichbar "
        "unter elena.fischer@example.com oder 0341 4455667."
    )
    spans = real_stack.detect(text)
    for earlier, later in zip(spans, spans[1:]):
        assert earlier.end <= later.start
