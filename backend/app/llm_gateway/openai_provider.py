from __future__ import annotations

import decimal
import json
from collections.abc import Iterator

import httpx

from app.llm_gateway.provider import (
    ChatMessage,
    LLMProviderError,
    StreamDelta,
    StreamUsage,
)

# Per-1K-token USD pricing, for the research benchmark's cost metric (master design
# doc §8) only -- not billing-accurate, and not kept in sync with OpenAI's price
# changes automatically. Unknown models cost 0 rather than raising, so a model
# rename never blocks a chat response over a pricing lookup.
_PRICING_PER_1K_TOKENS: dict[str, tuple[decimal.Decimal, decimal.Decimal]] = {
    "gpt-5-nano": (decimal.Decimal("0.00005"), decimal.Decimal("0.0004")),
    "gpt-4o-mini": (decimal.Decimal("0.00015"), decimal.Decimal("0.0006")),
    "gpt-4o": (decimal.Decimal("0.0025"), decimal.Decimal("0.01")),
}


class OpenAIProviderError(LLMProviderError):
    """The OpenAI HTTP call failed or returned an unexpected shape."""


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        http_client: httpx.Client | None = None,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http_client = http_client if http_client is not None else httpx.Client(timeout=120.0)

    def stream(self, messages: list[ChatMessage]) -> Iterator[StreamDelta | StreamUsage]:
        # No "temperature" override here: some models (e.g. gpt-5-nano) reject
        # any value other than their default (1) with a 400, and there is no
        # single value that works across OpenAI's whole model lineup. The
        # leading system message (TOKEN_PRESERVATION_SYSTEM_PROMPT, prepended
        # by the orchestrator) is the portable lever; temperature is only
        # applied where a provider is known to support it (see OllamaProvider).
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            # Without this, OpenAI never emits the usage-only trailing chunk and
            # tokens_in/tokens_out silently stay 0 -- same "unknown model costs 0"
            # fallback as today, not a new failure mode.
            "stream_options": {"include_usage": True},
        }
        tokens_in = tokens_out = 0
        try:
            with self._http_client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ")
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    usage = chunk.get("usage")
                    if usage is not None:
                        tokens_in = usage.get("prompt_tokens", 0)
                        tokens_out = usage.get("completion_tokens", 0)
                    choices = chunk.get("choices") or []
                    if choices:
                        delta = (choices[0].get("delta") or {}).get("content")
                        if delta:
                            yield StreamDelta(text=delta)
        except httpx.HTTPStatusError as exc:
            raise OpenAIProviderError(
                f"openai request failed: {exc}; response body: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OpenAIProviderError(f"openai request failed: {exc}") from exc

        price_in, price_out = _PRICING_PER_1K_TOKENS.get(
            self.model, (decimal.Decimal("0"), decimal.Decimal("0"))
        )
        cost_usd = (tokens_in * price_in + tokens_out * price_out) / decimal.Decimal("1000")
        yield StreamUsage(tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd)
