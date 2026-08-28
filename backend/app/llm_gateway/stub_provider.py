from __future__ import annotations

import decimal
from collections.abc import Iterator

from app.llm_gateway.provider import ChatMessage, StreamDelta, StreamUsage


class StubProvider:
    """Deterministic, echo-only LLMProvider for e2e/CI use (never production
    -- see Settings._forbid_stub_provider_in_production).

    Echoes the last user message verbatim, word-chunked so it exercises the
    same multi-delta SSE assembly path (app/api/chat.py's SentenceBuffer) a
    real streaming provider does. Because it is a pure echo, it cannot
    invent, translate, or drop a token -- it automatically satisfies the
    same token-preservation contract TOKEN_PRESERVATION_SYSTEM_PROMPT asks
    real models for, which is exactly what an e2e chat round-trip test needs
    to assert: if the rendered reply (after deanonymize()) still contains the
    original raw values, pseudonymize -> LLM -> deanonymize round-tripped
    correctly through opaque tokens.
    """

    name = "stub"
    model = "stub-echo-v1"

    def stream(self, messages: list[ChatMessage]) -> Iterator[StreamDelta | StreamUsage]:
        last_user_content = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        words = last_user_content.split(" ")
        for index, word in enumerate(words):
            text = word if index == 0 else f" {word}"
            yield StreamDelta(text=text)
        yield StreamUsage(
            tokens_in=len(last_user_content.split()),
            tokens_out=len(words),
            cost_usd=decimal.Decimal(0),
        )
