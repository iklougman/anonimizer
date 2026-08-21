import decimal
import json

import httpx
import pytest

from app.llm_gateway.openai_provider import OpenAIProvider, OpenAIProviderError
from app.llm_gateway.provider import (
    ChatMessage,
    LLMProviderError,
    TOKEN_PRESERVATION_SYSTEM_PROMPT,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _single_user_prompt(prompt: str) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=TOKEN_PRESERVATION_SYSTEM_PROMPT),
        ChatMessage(role="user", content=prompt),
    ]


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

    completion = provider.complete(_single_user_prompt("Hallo"))

    assert completion.text == "Guten Tag."
    assert completion.tokens_in == 1000
    assert completion.tokens_out == 1000
    # 1000 prompt tokens @ $0.00015/1K + 1000 completion tokens @ $0.0006/1K
    assert completion.cost_usd == decimal.Decimal("0.00015") + decimal.Decimal("0.0006")


def test_sends_the_configured_model_and_the_message_list_verbatim():
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
    provider.complete(_single_user_prompt("Hallo"))

    assert captured["body"] == {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": TOKEN_PRESERVATION_SYSTEM_PROMPT},
            {"role": "user", "content": "Hallo"},
        ],
    }


def test_passes_a_multi_message_history_through_verbatim():
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
    provider.complete(
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="erste Frage."),
            ChatMessage(role="assistant", content="erste Antwort."),
            ChatMessage(role="user", content="zweite Frage."),
        ]
    )

    assert captured["body"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "erste Frage."},
        {"role": "assistant", "content": "erste Antwort."},
        {"role": "user", "content": "zweite Frage."},
    ]


def test_gpt_5_nano_cost_is_computed_correctly():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
            },
        )

    provider = OpenAIProvider(
        api_key="sk-test", model="gpt-5-nano", http_client=_client(handler)
    )
    completion = provider.complete(_single_user_prompt("Hallo"))
    # 1000 prompt tokens @ $0.00005/1K + 1000 completion tokens @ $0.0004/1K
    assert completion.cost_usd == decimal.Decimal("0.00005") + decimal.Decimal("0.0004")


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
    completion = provider.complete(_single_user_prompt("Hallo"))
    assert completion.cost_usd == decimal.Decimal("0")


def test_transport_error_raises_openai_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini", http_client=_client(handler))
    with pytest.raises(OpenAIProviderError):
        provider.complete(_single_user_prompt("Hallo"))


def test_http_status_error_message_includes_the_response_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "Unsupported value: 'temperature'...", "code": "unsupported_value"}},
        )

    provider = OpenAIProvider(api_key="sk-test", model="gpt-5-nano", http_client=_client(handler))
    with pytest.raises(OpenAIProviderError, match="unsupported_value"):
        provider.complete(_single_user_prompt("Hallo"))


def test_missing_expected_fields_raises_openai_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini", http_client=_client(handler))
    with pytest.raises(OpenAIProviderError):
        provider.complete(_single_user_prompt("Hallo"))


def test_openai_provider_error_is_an_llm_provider_error():
    assert issubclass(OpenAIProviderError, LLMProviderError)
