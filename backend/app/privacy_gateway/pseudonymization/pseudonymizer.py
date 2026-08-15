from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.privacy_gateway.detectors.base import Span
from app.privacy_gateway.token_vault.vault import TokenVault


class Pseudonymizer:
    """Replaces every tokenize-eligible span with a TokenVault token (design spec §4)."""

    def __init__(self, vault: TokenVault) -> None:
        self._vault = vault

    def apply(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        text: str,
        spans: Sequence[Span],
    ) -> str:
        # TokenVault.create_mapping mints a fresh random token on every call and
        # `encrypted_value` is not searchable, so exact-string-match determinism has
        # to live here. This cache gives it within one message; cross-message
        # determinism inside a conversation would need a deterministic (HMAC) index
        # column on token_mappings and is out of scope for this plan.
        issued: dict[tuple[str, str], str] = {}
        result = text
        # Descending start order so an earlier replacement never shifts the offsets
        # of a span that has not been processed yet (design spec §4).
        for span in sorted(spans, key=lambda item: item.start, reverse=True):
            original_value = text[span.start : span.end]
            key = (span.entity_type, original_value)
            token = issued.get(key)
            if token is None:
                token = self._vault.create_mapping(
                    tenant_id, conversation_id, span.entity_type, original_value
                )
                issued[key] = token
            result = result[: span.start] + token + result[span.end :]
        return result
