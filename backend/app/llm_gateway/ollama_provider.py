from __future__ import annotations

import decimal

import httpx

from app.llm_gateway.provider import (
    ChatMessage,
    LLMCompletion,
    LLMProviderError,
    SANITIZED_PROMPT_TEMPERATURE,
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

    def complete(self, messages: list[ChatMessage]) -> LLMCompletion:
        try:
            response = self._http_client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": m.role, "content": m.content} for m in messages
                    ],
                    "options": {"temperature": SANITIZED_PROMPT_TEMPERATURE},
                    "stream": False,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaProviderError(f"ollama request failed: {exc}") from exc

        body = response.json()
        # /api/chat returns {"message": {"role": "assistant", "content": "..."}}.
        text = (body.get("message") or {}).get("content")
        if text is None:
            raise OllamaProviderError(f"ollama response missing message.content: {body}")

        # Local inference has no metered $ cost; token counts are best-effort (some
        # models omit eval_count/prompt_eval_count), used only for the research
        # benchmark's utility metrics, never for billing.
        return LLMCompletion(
            text=text,
            tokens_in=body.get("prompt_eval_count", 0),
            tokens_out=body.get("eval_count", 0),
            cost_usd=decimal.Decimal(0),
        )
