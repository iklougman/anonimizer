import openpyxl  # noqa: F401
import rdflib  # noqa: F401
import spacy
from presidio_analyzer import AnalyzerEngine  # noqa: F401

from app.config import DEFAULT_PATIENT_NUMBER_PATTERN, Settings


def test_german_spacy_model_is_installed_at_build_time():
    """ADR-0001 / design spec §6: the model is installed during the image build,
    never downloaded at runtime from inside privacy_gateway."""
    assert spacy.util.is_package("de_core_news_lg")


def test_german_spacy_model_tags_persons_and_locations():
    nlp = spacy.load("de_core_news_lg")
    doc = nlp("Lukas Berger wurde in Heidelberg behandelt.")
    labels = {ent.label_ for ent in doc.ents}
    assert "PER" in labels
    assert "LOC" in labels


def test_settings_default_patient_number_pattern_matches_german_id_labels(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.delenv("PATIENT_NUMBER_PATTERN", raising=False)
    settings = Settings(_env_file=None)
    assert settings.patient_number_pattern == DEFAULT_PATIENT_NUMBER_PATTERN


def test_patient_number_pattern_is_overridable(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.setenv("PATIENT_NUMBER_PATTERN", r"(?P<value>\d{4})")
    settings = Settings(_env_file=None)
    assert settings.patient_number_pattern == r"(?P<value>\d{4})"


def test_warm_reference_data_on_startup_defaults_to_true(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.delenv("WARM_REFERENCE_DATA_ON_STARTUP", raising=False)
    settings = Settings(_env_file=None)
    assert settings.warm_reference_data_on_startup is True
