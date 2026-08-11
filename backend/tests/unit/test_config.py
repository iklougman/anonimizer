import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_requires_environment_explicitly(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_defaults_are_secure(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    settings = Settings(_env_file=None)
    assert settings.debug is False
    assert settings.cors_allowed_origins == []


def test_settings_rejects_unknown_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
