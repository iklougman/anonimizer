from __future__ import annotations

import decimal

import httpx

from app.llm_gateway.provider import (
    ChatMessage,
    LLMCompletion,
    LLMProviderError,
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

    def complete(self, messages: list[ChatMessage]) -> LLMCompletion:
        try:
            # No "temperature" override here: some models (e.g. gpt-5-nano) reject
            # any value other than their default (1) with a 400, and there is no
            # single value that works across OpenAI's whole model lineup. The
            # leading system message (TOKEN_PRESERVATION_SYSTEM_PROMPT, prepended
            # by the orchestrator) is the portable lever; temperature is only
            # applied where a provider is known to support it (see OllamaProvider).
            response = self._http_client.post(
                f"{self._base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": m.role, "content": m.content} for m in messages
                    ],
                },
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OpenAIProviderError(
                f"openai request failed: {exc}; response body: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OpenAIProviderError(f"openai request failed: {exc}") from exc

        body = response.json()
        try:
            text = body["choices"][0]["message"]["content"]
            tokens_in = body["usage"]["prompt_tokens"]
            tokens_out = body["usage"]["completion_tokens"]
        except (KeyError, IndexError) as exc:
            raise OpenAIProviderError(f"openai response missing expected fields: {body}") from exc

        price_in, price_out = _PRICING_PER_1K_TOKENS.get(
            self.model, (decimal.Decimal("0"), decimal.Decimal("0"))
        )
        cost_usd = (tokens_in * price_in + tokens_out * price_out) / decimal.Decimal("1000")

        return LLMCompletion(text=text, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd)
