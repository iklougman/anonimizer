import decimal
import json

import httpx
import pytest

from app.llm_gateway.openai_provider import OpenAIProvider, OpenAIProviderError
from app.llm_gateway.provider import LLMProviderError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_complete_returns_text_tokens_and_a_computed_cost():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Guten Tag."}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
            },
        )

    provider = OpenAIProvider(
        api_key="sk-test", model="gpt-4o-mini", http_client=_client(handler)
    )

    completion = provider.complete("Hallo")

    assert completion.text == "Guten Tag."
    assert completion.tokens_in == 1000
    assert completion.tokens_out == 1000
    # 1000 prompt tokens @ $0.00015/1K + 1000 completion tokens @ $0.0006/1K
    assert completion.cost_usd == decimal.Decimal("0.00015") + decimal.Decimal("0.0006")


def test_sends_the_configured_model_and_prompt_as_a_user_message():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    provider = OpenAIProvider(
        api_key="sk-test", model="gpt-4o-mini", http_client=_client(handler)
    )
    provider.complete("Hallo")

    assert captured["body"] == {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Hallo"}],
    }


def test_unknown_model_costs_zero_rather_than_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
            },
        )

    provider = OpenAIProvider(
        api_key="sk-test", model="some-future-model", http_client=_client(handler)
    )
    completion = provider.complete("Hallo")
    assert completion.cost_usd == decimal.Decimal("0")


def test_transport_error_raises_openai_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini", http_client=_client(handler))
    with pytest.raises(OpenAIProviderError):
        provider.complete("Hallo")


def test_missing_expected_fields_raises_openai_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini", http_client=_client(handler))
    with pytest.raises(OpenAIProviderError):
        provider.complete("Hallo")


def test_openai_provider_error_is_an_llm_provider_error():
    assert issubclass(OpenAIProviderError, LLMProviderError)
