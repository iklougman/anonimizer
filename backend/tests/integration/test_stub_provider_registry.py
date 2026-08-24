import pytest

from app.config import get_settings
from app.llm_gateway.registry import get_provider
from app.llm_gateway.stub_provider import StubProvider


@pytest.fixture
def stub_provider_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    get_settings.cache_clear()
    get_provider.cache_clear()
    yield
    get_settings.cache_clear()
    get_provider.cache_clear()


def test_get_provider_returns_stub_when_configured(stub_provider_env):
    provider = get_provider()
    assert isinstance(provider, StubProvider)


def test_stub_provider_forbidden_in_production(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="LLM_PROVIDER=stub is forbidden"):
            get_settings()
    finally:
        monkeypatch.setenv("ENVIRONMENT", "test")
        get_settings.cache_clear()
