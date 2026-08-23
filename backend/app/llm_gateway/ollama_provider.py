from __future__ import annotations

import decimal
import json
from collections.abc import Iterator

import httpx

from app.llm_gateway.provider import (
    ChatMessage,
    LLMProviderError,
    SANITIZED_PROMPT_TEMPERATURE,
    StreamDelta,
    StreamUsage,
)


class OllamaProviderError(LLMProviderError):
    """The Ollama HTTP call failed or returned an unexpected shape."""


class OllamaProvider:
    name = "ollama"

    def __init__(
        self, base_url: str, model: str, http_client: httpx.Client | None = None
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client if http_client is not None else httpx.Client(timeout=120.0)

    def stream(self, messages: list[ChatMessage]) -> Iterator[StreamDelta | StreamUsage]:
        tokens_in = tokens_out = 0
        try:
            with self._http_client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": m.role, "content": m.content} for m in messages
                    ],
                    "options": {"temperature": SANITIZED_PROMPT_TEMPERATURE},
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                # Ollama's /api/chat streams newline-delimited JSON objects, not
                # SSE `data:` framing (that's OpenAI's format -- see
                # OpenAIProvider.stream()).
                for line in response.iter_lines():
                    if not line:
                        continue
                    body = json.loads(line)
                    content = (body.get("message") or {}).get("content")
                    if content:
                        yield StreamDelta(text=content)
                    if body.get("done"):
                        # Local inference has no metered $ cost; token counts are
                        # best-effort (some models omit these fields), used only
                        # for the research benchmark's utility metrics, never
                        # for billing.
                        tokens_in = body.get("prompt_eval_count", 0)
                        tokens_out = body.get("eval_count", 0)
        except httpx.HTTPError as exc:
            raise OllamaProviderError(f"ollama request failed: {exc}") from exc

        yield StreamUsage(tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=decimal.Decimal(0))
