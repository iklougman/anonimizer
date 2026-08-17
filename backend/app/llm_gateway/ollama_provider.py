from __future__ import annotations

import decimal

import httpx

from app.llm_gateway.provider import LLMCompletion, LLMProviderError


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

    def complete(self, prompt: str) -> LLMCompletion:
        try:
            response = self._http_client.post(
                f"{self._base_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaProviderError(f"ollama request failed: {exc}") from exc

        body = response.json()
        text = body.get("response")
        if text is None:
            raise OllamaProviderError(f"ollama response missing 'response' field: {body}")

        # Local inference has no metered $ cost; token counts are best-effort (some
        # models omit eval_count/prompt_eval_count), used only for the research
        # benchmark's utility metrics, never for billing.
        return LLMCompletion(
            text=text,
            tokens_in=body.get("prompt_eval_count", 0),
            tokens_out=body.get("eval_count", 0),
            cost_usd=decimal.Decimal("0"),
        )
