import os
import secrets
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from app.db.session import tenant_scoped_session
from app.models import TenantKey, TokenMapping
from app.privacy_gateway.token_vault.key_provider import KeyProvider

_NONCE_LENGTH = 12


class TokenVault:
    def __init__(self, key_provider: KeyProvider) -> None:
        self.key_provider = key_provider

    def create_mapping(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        entity_type: str,
        original_value: str,
    ) -> str:
        token = f"{entity_type}_{secrets.token_hex(5).upper()}"

        with tenant_scoped_session(tenant_id) as session:
            dek_row = self._active_dek_row(session, tenant_id)
            raw_dek = self.key_provider.unwrap_dek(dek_row.wrapped_dek)

            nonce = os.urandom(_NONCE_LENGTH)
            ciphertext = AESGCM(raw_dek).encrypt(nonce, original_value.encode("utf-8"), None)

            mapping = TokenMapping(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                token=token,
                entity_type=entity_type,
                encrypted_value=nonce + ciphertext,
                dek_id=dek_row.id,
            )
            session.add(mapping)

        return token

    def resolve_token(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, token: str
    ) -> str | None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, conversation_id, token)
            if mapping is None:
                return None

            dek_row = session.get(TenantKey, mapping.dek_id)
            raw_dek = self.key_provider.unwrap_dek(dek_row.wrapped_dek)

            nonce = mapping.encrypted_value[:_NONCE_LENGTH]
            ciphertext = mapping.encrypted_value[_NONCE_LENGTH:]
            return AESGCM(raw_dek).decrypt(nonce, ciphertext, None).decode("utf-8")

    def resolve_tokens(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, tokens: list[str]
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for token in tokens:
            value = self.resolve_token(tenant_id, conversation_id, token)
            if value is not None:
                resolved[token] = value
        return resolved

    def delete_mapping(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, token: str) -> None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, conversation_id, token)
            if mapping is not None:
                session.delete(mapping)

    def expire_mapping(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, token: str) -> None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, conversation_id, token)
            if mapping is not None:
                mapping.deleted_at = datetime.now(timezone.utc)

    def _find_mapping(
        self, session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID, token: str
    ) -> TokenMapping | None:
        stmt = sa.select(TokenMapping).where(
            TokenMapping.tenant_id == tenant_id,
            TokenMapping.conversation_id == conversation_id,
            TokenMapping.token == token,
            TokenMapping.deleted_at.is_(None),
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
