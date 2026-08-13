import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import TenantKey, TokenMapping
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault


@pytest.fixture
def key_provider(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


def _create_tenant_and_conversation(key_provider):
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub", email="doc@example.com", role="doctor"
        )
        conversation = ConversationRepository(session).create(tenant_id, user.id)
        conversation_id = conversation.id

    return tenant_id, conversation_id


def test_create_and_resolve_round_trip(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, conversation_id, "PATIENT", "Hans Müller")
    resolved = vault.resolve_token(tenant_id, conversation_id, token)

    assert resolved == "Hans Müller"
    assert token.startswith("PATIENT_")


def test_resolve_fails_for_wrong_tenant(key_provider):
    tenant_a, conversation_a = _create_tenant_and_conversation(key_provider)
    tenant_b, _ = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_a, conversation_a, "PATIENT", "Hans Müller")

    assert vault.resolve_token(tenant_b, conversation_a, token) is None


def test_resolve_fails_for_wrong_conversation(key_provider):
    tenant_id, conversation_a = _create_tenant_and_conversation(key_provider)
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-2", email="doc2@example.com", role="doctor"
        )
        other_conversation = ConversationRepository(session).create(tenant_id, user.id)
        conversation_b = other_conversation.id

    vault = TokenVault(key_provider)
    token = vault.create_mapping(tenant_id, conversation_a, "PATIENT", "Hans Müller")

    assert vault.resolve_token(tenant_id, conversation_b, token) is None


def test_token_uniqueness_within_tenant_and_conversation(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)

    with tenant_scoped_session(tenant_id) as session:
        dek_id = session.execute(
            sa.select(TenantKey.id).where(TenantKey.tenant_id == tenant_id)
        ).scalar_one()

        session.add(
            TokenMapping(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                token="PATIENT_AAAAA",
                entity_type="PATIENT",
                encrypted_value=b"x" * 28,
                dek_id=dek_id,
            )
        )

    with pytest.raises(IntegrityError):
        with tenant_scoped_session(tenant_id) as session:
            session.add(
                TokenMapping(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    token="PATIENT_AAAAA",
                    entity_type="PATIENT",
                    encrypted_value=b"y" * 28,
                    dek_id=dek_id,
                )
            )


def test_delete_mapping_removes_the_row(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, conversation_id, "PATIENT", "Hans Müller")
    vault.delete_mapping(tenant_id, conversation_id, token)

    assert vault.resolve_token(tenant_id, conversation_id, token) is None

    with tenant_scoped_session(tenant_id) as session:
        mapping = session.execute(
            sa.select(TokenMapping).where(TokenMapping.token == token)
        ).scalar_one_or_none()
        assert mapping is None


def test_expire_mapping_soft_deletes(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, conversation_id, "PATIENT", "Hans Müller")
    vault.expire_mapping(tenant_id, conversation_id, token)

    assert vault.resolve_token(tenant_id, conversation_id, token) is None

    with tenant_scoped_session(tenant_id) as session:
        mapping = session.execute(
            sa.select(TokenMapping).where(TokenMapping.token == token)
        ).scalar_one()
        assert mapping.deleted_at is not None
