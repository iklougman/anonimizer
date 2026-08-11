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


def test_settings_ignores_unrelated_env_vars(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("POSTGRES_USER", "chatgpt_proxy")
    monkeypatch.setenv("POSTGRES_PASSWORD", "change-me")
    monkeypatch.setenv("POSTGRES_DB", "chatgpt_proxy")
    monkeypatch.setenv("KEYCLOAK_ADMIN", "admin")
    monkeypatch.setenv("KEYCLOAK_ADMIN_PASSWORD", "change-me")
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
