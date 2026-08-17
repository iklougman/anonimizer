from __future__ import annotations

import decimal
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMCompletion:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: decimal.Decimal


class LLMProviderError(Exception):
    """A provider adapter's request failed or returned an unexpected response
    shape. The chat API maps this to a single HTTP 502 regardless of which
    adapter is configured."""


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, prompt: str) -> LLMCompletion: ...
