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


@dataclass(frozen=True)
class ChatMessage:
    """One turn in the message list sent to a provider.

    The orchestrator (app/api/chat.py) builds the full list including the
    TOKEN_PRESERVATION_SYSTEM_PROMPT as the leading ``system`` message and the
    sanitized conversation history as ``user``/``assistant`` messages.
    Adapters pass the list through verbatim to their provider's native
    message-list API.
    """

    role: str  # "system" | "user" | "assistant"
    content: str


class LLMProviderError(Exception):
    """A provider adapter's request failed or returned an unexpected response
    shape. The chat API maps this to a single HTTP 502 regardless of which
    adapter is configured."""


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, messages: list[ChatMessage]) -> LLMCompletion: ...


# Prepended by the chat orchestrator (app/api/chat.py) as the leading ``system``
# ChatMessage in every provider call. The privacy pipeline replaces real patient
# data with opaque tokens before the prompt ever reaches a provider; without this
# instruction the model has no way to know that, and left to its own devices it
# will "helpfully" invent, translate, or paraphrase a token instead of copying it
# back verbatim -- which the output guard then fails closed on
# (LeakageDetectedError). This does not replace that guard -- it exists to reduce
# how often the guard has to reject a reply.
#
# Deliberately no concrete example token (no literal "PATIENT_A1B2C3D4E5"-shaped
# string) anywhere in this text: an earlier version had one, and a model asked to
# "reproduce tokens exactly" occasionally reproduced *that* one -- a syntactically
# valid but never-issued token, which the guard correctly rejects
# (UnresolvedTokenError) but which was self-inflicted, not a real leak. Describing
# the shape in words instead removes the string a model could copy.
TOKEN_PRESERVATION_SYSTEM_PROMPT = (
    "The user's message may contain placeholder tokens standing in for real "
    "patient data that has been redacted before reaching you. A token looks like "
    "an uppercase category name joined by an underscore to a 10-character "
    "uppercase hexadecimal code (for instance, a redacted patient name or a "
    "redacted date). Treat every token that literally appears in the user's "
    "message as an opaque identifier: reproduce it back exactly, character for "
    "character, wherever it belongs in your reply. Never invent, guess, "
    "translate, reformat, or paraphrase a token; never write out a real name, "
    "date, location, or other personal detail; and never write any token-shaped "
    "text that was not literally present in the user's message -- you do not "
    "have access to real patient data, only the tokens actually given to you."
)

# Lower temperature makes the model likelier to reproduce a token verbatim
# rather than "creatively" rephrasing around it -- a smaller lever than the
# system prompt above, but a free one to pull at the same time. Applied by
# OllamaProvider only: some OpenAI models (e.g. gpt-5-nano) reject any
# temperature other than their own default with a 400, and there is no value
# that works across OpenAI's whole lineup, so OpenAIProvider omits it entirely
# and relies on the system prompt alone.
SANITIZED_PROMPT_TEMPERATURE = 0.0
