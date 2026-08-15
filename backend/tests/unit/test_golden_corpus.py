import json
from collections import Counter
from pathlib import Path

import pytest

CORPUS_DIR = Path(__file__).resolve().parents[3] / "evaluation" / "golden_corpus"

ENTITY_TYPES = {
    "INSURANCE_NUMBER", "PATIENT_NUMBER", "PHONE", "EMAIL", "DATE", "AGE",
    "PERSON", "LOCATION", "HOSPITAL", "DOCTOR", "PATIENT",
}


def load_corpus():
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(CORPUS_DIR.glob("*.json"))]


def test_corpus_size_is_in_the_spec_range():
    notes = load_corpus()
    assert 20 <= len(notes) <= 25


def test_every_note_id_matches_its_filename():
    for path in sorted(CORPUS_DIR.glob("*.json")):
        assert json.loads(path.read_text(encoding="utf-8"))["id"] == path.stem


@pytest.mark.parametrize("note", load_corpus(), ids=lambda note: note["id"])
def test_every_annotation_offset_matches_its_surface_string(note):
    for entity in note["entities"]:
        assert note["text"][entity["start"] : entity["end"]] == entity["text"], (
            f"{note['id']}: {entity}"
        )


@pytest.mark.parametrize("note", load_corpus(), ids=lambda note: note["id"])
def test_every_annotation_uses_a_known_entity_type(note):
    for entity in note["entities"]:
        assert entity["entity_type"] in ENTITY_TYPES


@pytest.mark.parametrize("note", load_corpus(), ids=lambda note: note["id"])
def test_annotations_do_not_overlap(note):
    spans = sorted((e["start"], e["end"]) for e in note["entities"])
    for (_, earlier_end), (later_start, _) in zip(spans, spans[1:]):
        assert earlier_end <= later_start


@pytest.mark.parametrize("note", load_corpus(), ids=lambda note: note["id"])
def test_declared_rare_diseases_actually_appear_in_the_text(note):
    for disease in note["rare_diseases"]:
        assert disease in note["text"]


def test_every_entity_type_is_covered_at_least_twice():
    counts = Counter(
        entity["entity_type"] for note in load_corpus() for entity in note["entities"]
    )
    for entity_type in ENTITY_TYPES:
        assert counts[entity_type] >= 2, f"{entity_type} covered {counts[entity_type]} time(s)"


def test_exactly_one_note_is_the_high_risk_combination_case():
    rejected = [n for n in load_corpus() if n["expected_outcome"] == "high_risk_rejected"]
    assert len(rejected) == 1
    note = rejected[0]
    categories = {
        e["entity_type"] for e in note["entities"]
        if e["entity_type"] in {"AGE", "LOCATION", "DATE", "HOSPITAL"}
    }
    assert len(categories) >= 2
    assert note["rare_diseases"]


def test_only_the_high_risk_note_combines_a_rare_disease_with_two_quasi_categories():
    for note in load_corpus():
        if not note["rare_diseases"]:
            continue
        categories = {
            e["entity_type"] for e in note["entities"]
            if e["entity_type"] in {"AGE", "LOCATION", "DATE", "HOSPITAL"}
        }
        if len(categories) >= 2:
            assert note["expected_outcome"] == "high_risk_rejected", note["id"]


def test_the_coreference_limitation_case_exists():
    """One note refers to the same human three times under two distinct surfaces, so
    the exact-string-match limitation shows up in test output rather than passing
    silently."""
    note = json.loads((CORPUS_DIR / "note_012.json").read_text(encoding="utf-8"))
    surfaces = [e["text"] for e in note["entities"] if e["entity_type"] in {"PATIENT", "PERSON"}]
    assert len(surfaces) == 3
    assert len(set(surfaces)) == 2
