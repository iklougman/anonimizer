from __future__ import annotations

import re
import uuid

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.token_vault.vault import TokenVault

# ADR-0009's token format: entity type + secrets.token_hex(5).upper(). The design
# spec's `[TYPE_XXXXX]` notation is shorthand for this shape — there are no literal
# square brackets in a token.
TOKEN_PATTERN = re.compile(r"\b[A-Z][A-Z_]*_[0-9A-F]{10}\b")


class LeakageDetectedError(Exception):
    """The LLM's raw output contains detected PII that is not a legitimate token.

    Design spec §5 step 1: fail-closed — raise, do not return partial output. No
    audit_events row is written here; audit_events repository access is out of scope
    per the data-model plan, so this typed exception is what the caller logs.
    """


class UnresolvedTokenError(Exception):
    """A token-shaped substring in the LLM output did not resolve for this
    (tenant_id, conversation_id).

    Design spec §5 step 4: a token the LLM could not have legitimately produced —
    another tenant's, another conversation's, or fabricated by prompt injection.
    Raise rather than returning it opaque or silently dropping it.
    """


class OutputGuard:
    def __init__(self, detector_stack: DetectorStack, vault: TokenVault) -> None:
        self._detector_stack = detector_stack
        self._vault = vault

    def restore(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, llm_output: str
    ) -> str:
        token_bounds = [
            (match.start(), match.end()) for match in TOKEN_PATTERN.finditer(llm_output)
        ]

        # Step 1: re-run the full detector stack; anything detected outside a token is
        # raw-looking PII the LLM produced or leaked.
        leaked = [
            span
            for span in self._detector_stack.detect(llm_output)
            if not _inside_a_token(llm_output, span, token_bounds)
        ]
        if leaked:
            raise LeakageDetectedError(
                "raw-looking PII in LLM output: "
                + ", ".join(
                    f"{span.entity_type} at [{span.start}:{span.end}]" for span in leaked
                )
                + "; the response is rejected rather than partially returned"
            )

        # Steps 2 and 3: extract the token-shaped substrings and let TokenVault apply
        # the (tenant_id, conversation_id) authorization scope.
        tokens = [llm_output[start:end] for start, end in token_bounds]
        resolved = self._vault.resolve_tokens(tenant_id, conversation_id, tokens)

        # Step 4: anything still unresolved is fail-closed.
        unresolved = sorted({token for token in tokens if token not in resolved})
        if unresolved:
            raise UnresolvedTokenError(
                "token(s) not issued for this tenant and conversation: "
                + ", ".join(unresolved)
                + "; the response is rejected rather than returned opaque"
            )

        restored = llm_output
        for start, end in reversed(token_bounds):
            restored = restored[:start] + resolved[llm_output[start:end]] + restored[end:]
        return restored


def _inside_a_token(text: str, span: Span, token_bounds: list[tuple[int, int]]) -> bool:
    """A detected span is "inside a token" if the token fully accounts for its
    alphanumeric content.

    Presidio's spaCy NER occasionally sweeps adjacent punctuation (most commonly a
    sentence-final period right after a token) into a PERSON span, which
    CustomRecognizers then promotes to PATIENT. A token itself is always pure
    `[A-Z_0-9]` (see TOKEN_PATTERN) — it never contains punctuation — so trimming
    leading/trailing non-alphanumeric characters off the detected span before the
    containment check discards that NER artifact without weakening the check: any
    span whose real (word) content extends beyond the token is still leakage.
    """
    trimmed_start, trimmed_end = _trim_punctuation(text, span.start, span.end)
    return any(
        start <= trimmed_start and trimmed_end <= end for start, end in token_bounds
    )


def _trim_punctuation(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and not (text[start].isalnum() or text[start] == "_"):
        start += 1
    while end > start and not (text[end - 1].isalnum() or text[end - 1] == "_"):
        end -= 1
    return start, end
