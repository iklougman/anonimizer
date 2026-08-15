from pathlib import Path

import pytest

from app.privacy_gateway.risk_scoring.reference_data import (
    DATA_DIR,
    KRANKENHAUSVERZEICHNIS_FILENAME,
    MIN_RARE_DISEASE_NAME_LENGTH,
    ORDO_FILENAME,
    load_hospital_names,
    load_rare_disease_names,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

reference_data = pytest.mark.skipif(
    not (DATA_DIR / ORDO_FILENAME).exists()
    or not (DATA_DIR / KRANKENHAUSVERZEICHNIS_FILENAME).exists(),
    reason="run `python scripts/fetch_reference_data.py` to enable",
)


def test_ordo_labels_are_loaded_normalized():
    names = load_rare_disease_names(str(FIXTURES / "ordo_sample.owl"))
    assert "zystische fibrose" in names
    assert "huntington-krankheit" in names
    assert "marfan-syndrom" in names


def test_short_ordo_labels_are_filtered_out():
    """ORDO contains 4-5 character labels like "Kuru", "Fall" and "Pest". Matching
    those would escalate almost every clinical note to HIGH risk."""
    assert MIN_RARE_DISEASE_NAME_LENGTH == 6
    names = load_rare_disease_names(str(FIXTURES / "ordo_sample.owl"))
    assert "kuru" not in names


def test_non_orphanet_subjects_are_ignored():
    names = load_rare_disease_names(str(FIXTURES / "ordo_sample.owl"))
    assert "experimentelle einheit" not in names


def test_hospital_names_come_from_the_khv_sheet_normalized():
    names = load_hospital_names(str(FIXTURES / "krankenhausverzeichnis_sample.xlsx"))
    assert "städtisches krankenhaus kiel gmbh" in names
    # `normalize()` (Task 2) keeps only [0-9A-Za-zÄÖÜäöüß-], so "Charité" normalizes
    # to "charit". Harmless for matching — message text goes through the same
    # function — but it means the stored key is the truncated form.
    assert "charit universitätsmedizin berlin" in names
    assert "katharinen hospiz am park" in names


def test_hospital_names_include_the_standortname_column():
    names = load_hospital_names(str(FIXTURES / "krankenhausverzeichnis_sample.xlsx"))
    assert "charit campus virchow klinikum" in names


def test_hospital_header_row_and_banner_rows_are_not_treated_as_names():
    names = load_hospital_names(str(FIXTURES / "krankenhausverzeichnis_sample.xlsx"))
    assert "kh_name" not in names
    assert "zurück zum inhalt" not in names


def test_missing_khv_sheet_is_a_loud_failure(tmp_path):
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.title = "Inhalt"
    path = tmp_path / "no_khv.xlsx"
    workbook.save(path)
    with pytest.raises(ValueError, match="KHV_"):
        load_hospital_names(str(path))


@reference_data
def test_real_ordo_release_contains_the_corpus_diseases():
    names = load_rare_disease_names(str(DATA_DIR / ORDO_FILENAME))
    assert len(names) > 10_000
    assert "zystische fibrose" in names
    assert "huntington-krankheit" in names


@reference_data
def test_real_krankenhausverzeichnis_contains_known_facilities():
    names = load_hospital_names(str(DATA_DIR / KRANKENHAUSVERZEICHNIS_FILENAME))
    assert len(names) > 3_000
    assert "universitätsklinikum heidelberg" in names
    assert "charit universitätsmedizin berlin" in names
