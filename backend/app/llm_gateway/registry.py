from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.llm_gateway.ollama_provider import OllamaProvider
from app.llm_gateway.openai_provider import OpenAIProvider
from app.llm_gateway.provider import LLMProvider
from app.llm_gateway.stub_provider import StubProvider


@lru_cache(maxsize=1)
def get_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "ollama":
        return OllamaProvider(base_url=settings.ollama_base_url, model=settings.ollama_model)
    if settings.llm_provider == "stub":
        return StubProvider()
    # Settings' model validator guarantees openai_api_key is set whenever
    # llm_provider == "openai" -- fail-closed at startup, not at first request.
    return OpenAIProvider(api_key=settings.openai_api_key, model=settings.openai_model)
