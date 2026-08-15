from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers

GAZETTEER = frozenset(
    {"charité universitätsmedizin berlin", "katharinen hospiz am park"}
)


def _refine(text, spans):
    return CustomRecognizers(GAZETTEER).refine(text, spans)


def _typed(text, spans, entity_type):
    return {text[s.start : s.end] for s in spans if s.entity_type == entity_type}


def test_organization_with_a_spec_suffix_becomes_hospital():
    text = "Aufnahme im Universitätsklinikum Heidelberg."
    spans = [Span(12, 43, "ORGANIZATION", 0.85, "presidio")]
    assert _typed(text, _refine(text, spans), "HOSPITAL") == {
        "Universitätsklinikum Heidelberg"
    }


def test_all_four_spec_suffixes_are_recognised():
    for surface in (
        "Universitätsklinikum Freiburg",
        "Klinikum Nürnberg",
        "Krankenhaus Sankt Elisabeth",
        "MVZ Gesundheitszentrum Ost",
    ):
        spans = [Span(0, len(surface), "ORGANIZATION", 0.85, "presidio")]
        assert _typed(surface, _refine(surface, spans), "HOSPITAL") == {surface}


def test_organization_in_the_gazetteer_becomes_hospital_without_a_suffix():
    text = "Verlegung in die Charité Universitätsmedizin Berlin."
    spans = [Span(17, 51, "ORGANIZATION", 0.85, "presidio")]
    assert _typed(text, _refine(text, spans), "HOSPITAL") == {
        "Charité Universitätsmedizin Berlin"
    }


def test_organization_that_is_neither_is_dropped_entirely():
    """Layer 2 only emits ORGANIZATION so this layer can look for hospitals in it;
    an ORGANIZATION that is not a hospital is not part of the entity taxonomy."""
    text = "Der Bericht ging an die Siemens AG."
    spans = [Span(24, 34, "ORGANIZATION", 0.85, "presidio")]
    refined = _refine(text, spans)
    assert refined == []


def test_person_with_a_dr_prefix_becomes_doctor():
    text = "Untersucht von Dr. Miriam Falk."
    spans = [Span(19, 30, "PERSON", 0.85, "presidio")]
    assert _typed(text, _refine(text, spans), "DOCTOR") == {"Miriam Falk"}


def test_person_with_a_prof_prefix_becomes_doctor():
    text = "Prof. Ulrike Brandt empfiehlt eine Kontrolle."
    spans = [Span(6, 19, "PERSON", 0.85, "presidio")]
    assert _typed(text, _refine(text, spans), "DOCTOR") == {"Ulrike Brandt"}


def test_person_near_behandelnder_arzt_becomes_doctor():
    text = "Behandelnder Arzt ist Stefan Roth."
    spans = [Span(22, 33, "PERSON", 0.85, "presidio")]
    assert _typed(text, _refine(text, spans), "DOCTOR") == {"Stefan Roth"}


def test_the_only_remaining_person_becomes_patient():
    text = "Herr Tobias Lang wurde von Dr. Miriam Falk untersucht."
    spans = [
        Span(5, 16, "PERSON", 0.85, "presidio"),
        Span(31, 42, "PERSON", 0.85, "presidio"),
    ]
    refined = _refine(text, spans)
    assert _typed(text, refined, "PATIENT") == {"Tobias Lang"}
    assert _typed(text, refined, "DOCTOR") == {"Miriam Falk"}


def test_most_referenced_person_wins_the_patient_label():
    text = "Lea Sommer stellte sich vor. Begleitet wurde Lea Sommer von Ingrid Sommer."
    spans = [
        Span(0, 10, "PERSON", 0.85, "presidio"),
        Span(45, 55, "PERSON", 0.85, "presidio"),
        Span(60, 73, "PERSON", 0.85, "presidio"),
    ]
    refined = _refine(text, spans)
    assert _typed(text, refined, "PATIENT") == {"Lea Sommer"}
    assert _typed(text, refined, "PERSON") == {"Ingrid Sommer"}


def test_ties_break_toward_the_earliest_mention():
    text = "Anna Weiss und Bernd Klein waren anwesend."
    spans = [
        Span(0, 10, "PERSON", 0.85, "presidio"),
        Span(15, 26, "PERSON", 0.85, "presidio"),
    ]
    refined = _refine(text, spans)
    assert _typed(text, refined, "PATIENT") == {"Anna Weiss"}
    assert _typed(text, refined, "PERSON") == {"Bernd Klein"}


def test_non_person_non_organization_spans_pass_through_untouched():
    text = "Aufnahme am 12.03.2024."
    original = Span(12, 22, "DATE", 1.0, "regex")
    assert _refine(text, [original]) == [original]


def test_retagged_spans_carry_the_custom_layer_name_and_original_offsets():
    text = "Untersucht von Dr. Miriam Falk."
    refined = _refine(text, [Span(19, 30, "PERSON", 0.85, "presidio")])
    assert refined == [Span(19, 30, "DOCTOR", 0.85, "custom")]


def test_this_layer_never_invents_new_spans():
    text = "Universitätsklinikum Heidelberg behandelte Lukas Berger am 12.03.2024."
    refined = _refine(text, [])
    assert refined == []
