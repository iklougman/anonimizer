import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Literal

import sqlalchemy as sa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from app.db.session import tenant_scoped_session
from app.models import TenantKey, TokenMapping
from app.privacy_gateway.token_vault.key_provider import KeyProvider

_NONCE_LENGTH = 12

ScopeType = Literal["conversation", "document"]


def _associated_data(tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, token: str) -> bytes:
    """AES-GCM associated data binding a ciphertext to the exact row it belongs to.

    The DEK is per-tenant, not per-row, so without AAD a ciphertext blob would be
    interchangeable between any two `token_mappings` rows of the same tenant —
    copying one scope's `encrypted_value` into another scope's row would still
    decrypt cleanly, defeating ADR-0009's scoping at the storage layer. Binding the
    AAD to `(tenant_id, scope_type, scope_id, token)` makes any such move fail
    with `cryptography.exceptions.InvalidTag`.

    `scope_type` is bound in addition to `scope_id` (not just `f"{scope_id}"`) so
    that a document-scoped mapping's ciphertext cannot decrypt under a
    conversation-scoped lookup even in the practically-impossible case of a
    document UUID colliding with a conversation UUID — defense in depth beyond
    what the schema's CHECK constraint and partial unique indexes already
    guarantee. See docs/adr/0023-token-vault-scope-generalization.md.
    """
    return f"{tenant_id}:{scope_type}:{scope_id}:{token}".encode()


class TokenVault:
    def __init__(self, key_provider: KeyProvider) -> None:
        self.key_provider = key_provider

    def create_mapping(
        self,
        tenant_id: uuid.UUID,
        scope_type: ScopeType,
        scope_id: uuid.UUID,
        entity_type: str,
        original_value: str,
    ) -> str:
        token = f"{entity_type}_{secrets.token_hex(5).upper()}"

        with tenant_scoped_session(tenant_id) as session:
            dek_row = self._active_dek_row(session, tenant_id)
            raw_dek = self.key_provider.unwrap_dek(dek_row.wrapped_dek)

            nonce = os.urandom(_NONCE_LENGTH)
            aad = _associated_data(tenant_id, scope_type, scope_id, token)
            ciphertext = AESGCM(raw_dek).encrypt(nonce, original_value.encode("utf-8"), aad)

            mapping = TokenMapping(
                tenant_id=tenant_id,
                scope_type=scope_type,
                conversation_id=scope_id if scope_type == "conversation" else None,
                document_id=scope_id if scope_type == "document" else None,
                token=token,
                entity_type=entity_type,
                encrypted_value=nonce + ciphertext,
                dek_id=dek_row.id,
            )
            session.add(mapping)

        return token

    def resolve_token(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, token: str
    ) -> str | None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, scope_type, scope_id, token)
            if mapping is None:
                return None

            dek_row = session.get(TenantKey, mapping.dek_id)
            if dek_row is None:
                raise RuntimeError(
                    f"token_mappings row {mapping.id} references missing tenant_keys row "
                    f"{mapping.dek_id}; its encrypted_value cannot be decrypted"
                )
            raw_dek = self.key_provider.unwrap_dek(dek_row.wrapped_dek)

            nonce = mapping.encrypted_value[:_NONCE_LENGTH]
            ciphertext = mapping.encrypted_value[_NONCE_LENGTH:]
            aad = _associated_data(tenant_id, scope_type, scope_id, token)
            # An InvalidTag here means the ciphertext does not belong to this row —
            # it must propagate, never be swallowed into a None/plaintext result.
            return AESGCM(raw_dek).decrypt(nonce, ciphertext, aad).decode("utf-8")

    def resolve_tokens(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, tokens: list[str]
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for token in tokens:
            value = self.resolve_token(tenant_id, scope_type, scope_id, token)
            if value is not None:
                resolved[token] = value
        return resolved

    def delete_mapping(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, token: str
    ) -> None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, scope_type, scope_id, token)
            if mapping is not None:
                session.delete(mapping)

    def expire_mapping(
        self, tenant_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID, token: str
    ) -> None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, scope_type, scope_id, token)
            if mapping is not None:
                mapping.deleted_at = datetime.now(timezone.utc)

    def _find_mapping(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        scope_type: ScopeType,
        scope_id: uuid.UUID,
        token: str,
    ) -> TokenMapping | None:
        now = datetime.now(timezone.utc)
        if scope_type == "conversation":
            scope_column = TokenMapping.conversation_id
        elif scope_type == "document":
            scope_column = TokenMapping.document_id
        else:
            raise ValueError(f"unsupported scope_type: {scope_type!r}")
        stmt = sa.select(TokenMapping).where(
            TokenMapping.tenant_id == tenant_id,
            TokenMapping.scope_type == scope_type,
            scope_column == scope_id,
            TokenMapping.token == token,
            TokenMapping.deleted_at.is_(None),
            # Fail-safe by construction: an expired-but-not-yet-reaped mapping is
            # unresolvable even before any retention job (ADR-0019) exists to delete it.
            (TokenMapping.expires_at.is_(None)) | (TokenMapping.expires_at > now),
        )
        return session.execute(stmt).scalar_one_or_none()

    def _active_dek_row(self, session: Session, tenant_id: uuid.UUID) -> TenantKey:
        stmt = (
            sa.select(TenantKey)
            .where(TenantKey.tenant_id == tenant_id)
            .order_by(TenantKey.key_version.desc())
            .limit(1)
        )
        return session.execute(stmt).scalar_one()
