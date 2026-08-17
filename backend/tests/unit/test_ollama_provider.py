import decimal
import json

import httpx
import pytest

from app.llm_gateway.ollama_provider import OllamaProvider, OllamaProviderError
from app.llm_gateway.provider import LLMProviderError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_complete_returns_the_response_text_and_token_counts():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        return httpx.Response(
            200,
            json={"response": "Guten Tag.", "prompt_eval_count": 12, "eval_count": 4},
        )

    provider = OllamaProvider(
        base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler)
    )

    completion = provider.complete("Hallo")

    assert completion.text == "Guten Tag."
    assert completion.tokens_in == 12
    assert completion.tokens_out == 4
    assert completion.cost_usd == decimal.Decimal("0")


def test_sends_stream_false_and_the_configured_model():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    provider = OllamaProvider(
        base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler)
    )
    provider.complete("Hallo")

    assert captured["body"] == {"model": "llama3.1", "prompt": "Hallo", "stream": False}


def test_missing_token_counts_default_to_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": "ok"})

    provider = OllamaProvider(
        base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler)
    )
    completion = provider.complete("Hallo")
    assert completion.tokens_in == 0
    assert completion.tokens_out == 0


def test_transport_error_raises_ollama_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OllamaProvider(
        base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler)
    )
    with pytest.raises(OllamaProviderError):
        provider.complete("Hallo")


def test_missing_response_field_raises_ollama_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = OllamaProvider(
        base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler)
    )
    with pytest.raises(OllamaProviderError):
        provider.complete("Hallo")


def test_ollama_provider_error_is_an_llm_provider_error():
    assert issubclass(OllamaProviderError, LLMProviderError)
