import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.exc import IntegrityError

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import TenantKey, TokenMapping
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import _NONCE_LENGTH, TokenVault


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


def _create_document(key_provider, tenant_id):
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}", email="doc2@example.com", role="doctor"
        )
        document_id = DocumentRepository(session).create(
            tenant_id,
            user.id,
            filename="x.pdf",
            content_type="application/pdf",
            byte_size=1,
            raw_storage_path="/data/documents/x/y/original.pdf",
        ).id
    return document_id


def test_create_and_resolve_round_trip(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Hans Müller")
    resolved = vault.resolve_token(tenant_id, "conversation", conversation_id, token)

    assert resolved == "Hans Müller"
    assert token.startswith("PATIENT_")


def test_resolve_fails_for_wrong_tenant(key_provider):
    tenant_a, conversation_a = _create_tenant_and_conversation(key_provider)
    tenant_b, _ = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_a, "conversation", conversation_a, "PATIENT", "Hans Müller")

    assert vault.resolve_token(tenant_b, "conversation", conversation_a, token) is None


def test_resolve_fails_for_wrong_conversation(key_provider):
    tenant_id, conversation_a = _create_tenant_and_conversation(key_provider)
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-2", email="doc2@example.com", role="doctor"
        )
        other_conversation = ConversationRepository(session).create(tenant_id, user.id)
        conversation_b = other_conversation.id

    vault = TokenVault(key_provider)
    token = vault.create_mapping(tenant_id, "conversation", conversation_a, "PATIENT", "Hans Müller")

    assert vault.resolve_token(tenant_id, "conversation", conversation_b, token) is None


def test_token_uniqueness_within_tenant_and_conversation(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)

    with tenant_scoped_session(tenant_id) as session:
        dek_id = session.execute(
            sa.select(TenantKey.id).where(TenantKey.tenant_id == tenant_id)
        ).scalar_one()

        session.add(
            TokenMapping(
                tenant_id=tenant_id,
                scope_type="conversation",
                conversation_id=conversation_id,
                document_id=None,
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
                    scope_type="conversation",
                    conversation_id=conversation_id,
                    document_id=None,
                    token="PATIENT_AAAAA",
                    entity_type="PATIENT",
                    encrypted_value=b"y" * 28,
                    dek_id=dek_id,
                )
            )


def test_delete_mapping_removes_the_row(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Hans Müller")
    vault.delete_mapping(tenant_id, "conversation", conversation_id, token)

    assert vault.resolve_token(tenant_id, "conversation", conversation_id, token) is None

    with tenant_scoped_session(tenant_id) as session:
        mapping = session.execute(
            sa.select(TokenMapping).where(TokenMapping.token == token)
        ).scalar_one_or_none()
        assert mapping is None


def test_expire_mapping_soft_deletes(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Hans Müller")
    vault.expire_mapping(tenant_id, "conversation", conversation_id, token)

    assert vault.resolve_token(tenant_id, "conversation", conversation_id, token) is None

    with tenant_scoped_session(tenant_id) as session:
        mapping = session.execute(
            sa.select(TokenMapping).where(TokenMapping.token == token)
        ).scalar_one()
        assert mapping.deleted_at is not None


def test_ciphertext_is_bound_to_its_row_by_associated_data(key_provider):
    """A stored ciphertext must not decrypt under any other row's identity.

    The DEK is per-tenant, so without AES-GCM associated data the same blob would
    decrypt fine if it were moved into a different scope's row. The AAD binds it
    to `(tenant_id, scope_type, scope_id, token)`; a mismatch raises InvalidTag.
    """
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Hans Müller")

    with tenant_scoped_session(tenant_id) as session:
        mapping = session.execute(
            sa.select(TokenMapping).where(TokenMapping.token == token)
        ).scalar_one()
        blob = bytes(mapping.encrypted_value)
        dek_row = session.get(TenantKey, mapping.dek_id)
        raw_dek = key_provider.unwrap_dek(dek_row.wrapped_dek)

    nonce, ciphertext = blob[:_NONCE_LENGTH], blob[_NONCE_LENGTH:]

    correct_aad = f"{tenant_id}:conversation:{conversation_id}:{token}".encode()
    assert AESGCM(raw_dek).decrypt(nonce, ciphertext, correct_aad).decode() == "Hans Müller"

    with pytest.raises(InvalidTag):
        AESGCM(raw_dek).decrypt(
            nonce, ciphertext, f"{tenant_id}:conversation:{conversation_id}:PATIENT_00000".encode()
        )

    with pytest.raises(InvalidTag):
        AESGCM(raw_dek).decrypt(
            nonce, ciphertext, f"{tenant_id}:conversation:{uuid.uuid4()}:{token}".encode()
        )

    # Same tenant + same scope_id + same token, but scope_type='document' instead
    # of 'conversation' -- the new dimension this task adds.
    with pytest.raises(InvalidTag):
        AESGCM(raw_dek).decrypt(
            nonce, ciphertext, f"{tenant_id}:document:{conversation_id}:{token}".encode()
        )

    with pytest.raises(InvalidTag):
        AESGCM(raw_dek).decrypt(nonce, ciphertext, None)


def test_expired_mapping_is_not_resolvable(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Hans Müller")
    assert vault.resolve_token(tenant_id, "conversation", conversation_id, token) == "Hans Müller"

    with tenant_scoped_session(tenant_id) as session:
        mapping = session.execute(
            sa.select(TokenMapping).where(TokenMapping.token == token)
        ).scalar_one()
        mapping.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert vault.resolve_token(tenant_id, "conversation", conversation_id, token) is None


def test_document_scoped_create_and_resolve_round_trip(key_provider):
    tenant_id, _ = _create_tenant_and_conversation(key_provider)
    document_id = _create_document(key_provider, tenant_id)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "document", document_id, "PATIENT", "Anna Weber")
    resolved = vault.resolve_token(tenant_id, "document", document_id, token)

    assert resolved == "Anna Weber"


def test_document_scoped_token_does_not_resolve_under_another_documents_scope(key_provider):
    tenant_id, _ = _create_tenant_and_conversation(key_provider)
    document_a = _create_document(key_provider, tenant_id)
    document_b = _create_document(key_provider, tenant_id)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "document", document_a, "PATIENT", "Anna Weber")

    assert vault.resolve_token(tenant_id, "document", document_b, token) is None


def test_document_scoped_token_does_not_resolve_under_a_conversation_scope(key_provider):
    """The core cross-scope isolation guarantee this task exists to add: a token
    minted while anonymizing a document must be structurally unresolvable through
    the conversation-scoped lookup path a chat message would use, even if a
    document's UUID were ever equal to some conversation's UUID."""
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    document_id = _create_document(key_provider, tenant_id)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "document", document_id, "PATIENT", "Anna Weber")

    assert vault.resolve_token(tenant_id, "conversation", conversation_id, token) is None
    assert vault.resolve_token(tenant_id, "conversation", document_id, token) is None


def test_conversation_scoped_token_does_not_resolve_under_a_document_scope(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    document_id = _create_document(key_provider, tenant_id)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, "conversation", conversation_id, "PATIENT", "Anna Weber")

    assert vault.resolve_token(tenant_id, "document", document_id, token) is None
    assert vault.resolve_token(tenant_id, "document", conversation_id, token) is None
