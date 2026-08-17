from app.config import Settings
from app.llm_gateway.ollama_provider import OllamaProvider
from app.llm_gateway.openai_provider import OpenAIProvider
from app.llm_gateway.registry import get_provider


def _base_env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")


def test_defaults_to_ollama_provider(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    get_provider.cache_clear()
    from app.config import get_settings

    get_settings.cache_clear()
    provider = get_provider()
    assert isinstance(provider, OllamaProvider)
    assert provider.model == Settings(_env_file=None).ollama_model


def test_selects_openai_provider_when_configured(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_provider.cache_clear()
    from app.config import get_settings

    get_settings.cache_clear()
    provider = get_provider()
    assert isinstance(provider, OpenAIProvider)
