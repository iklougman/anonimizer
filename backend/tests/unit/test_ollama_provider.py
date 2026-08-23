import decimal
import json

import httpx
import pytest

from app.llm_gateway.ollama_provider import OllamaProvider, OllamaProviderError
from app.llm_gateway.provider import (
    ChatMessage,
    LLMProviderError,
    SANITIZED_PROMPT_TEMPERATURE,
    StreamDelta,
    StreamUsage,
    TOKEN_PRESERVATION_SYSTEM_PROMPT,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _single_user_prompt(prompt: str) -> list[ChatMessage]:
    return [
        ChatMessage(role="system", content=TOKEN_PRESERVATION_SYSTEM_PROMPT),
        ChatMessage(role="user", content=prompt),
    ]


def _ndjson_response(*lines: dict) -> httpx.Response:
    body = "".join(json.dumps(line) + "\n" for line in lines)
    return httpx.Response(200, content=body.encode())


def _collect(items):
    items = list(items)
    deltas = [item.text for item in items if isinstance(item, StreamDelta)]
    usages = [item for item in items if isinstance(item, StreamUsage)]
    assert len(usages) == 1
    return deltas, usages[0]


def test_stream_yields_deltas_then_token_counts_from_the_done_line():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        return _ndjson_response(
            {"message": {"role": "assistant", "content": "Guten "}, "done": False},
            {"message": {"role": "assistant", "content": "Tag."}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 12, "eval_count": 4},
        )

    provider = OllamaProvider(base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler))
    deltas, usage = _collect(provider.stream(_single_user_prompt("Hallo")))

    assert deltas == ["Guten ", "Tag."]
    assert usage.tokens_in == 12
    assert usage.tokens_out == 4
    assert usage.cost_usd == decimal.Decimal("0")


def test_sends_stream_true_and_the_configured_model_with_messages_list():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ndjson_response({"message": {"role": "assistant", "content": "ok"}, "done": True})

    provider = OllamaProvider(base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler))
    list(provider.stream(_single_user_prompt("Hallo")))

    assert captured["body"] == {
        "model": "llama3.1",
        "messages": [
            {"role": "system", "content": TOKEN_PRESERVATION_SYSTEM_PROMPT},
            {"role": "user", "content": "Hallo"},
        ],
        "options": {"temperature": SANITIZED_PROMPT_TEMPERATURE},
        "stream": True,
    }


def test_passes_a_multi_message_history_through_verbatim():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ndjson_response({"message": {"role": "assistant", "content": "ok"}, "done": True})

    provider = OllamaProvider(base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler))
    list(
        provider.stream(
            [
                ChatMessage(role="system", content="sys"),
                ChatMessage(role="user", content="erste Frage."),
                ChatMessage(role="assistant", content="erste Antwort."),
                ChatMessage(role="user", content="zweite Frage."),
            ]
        )
    )

    assert captured["body"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "erste Frage."},
        {"role": "assistant", "content": "erste Antwort."},
        {"role": "user", "content": "zweite Frage."},
    ]


def test_missing_token_counts_default_to_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return _ndjson_response({"message": {"role": "assistant", "content": "ok"}, "done": True})

    provider = OllamaProvider(base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler))
    _, usage = _collect(provider.stream(_single_user_prompt("Hallo")))
    assert usage.tokens_in == 0
    assert usage.tokens_out == 0


def test_transport_error_raises_ollama_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OllamaProvider(base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler))
    with pytest.raises(OllamaProviderError):
        list(provider.stream(_single_user_prompt("Hallo")))


def test_ollama_provider_error_is_an_llm_provider_error():
    assert issubclass(OllamaProviderError, LLMProviderError)
