import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Conversation, TenantKey, TokenMapping, User
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


@pytest.fixture
def key_provider(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


def _setup(key_provider):
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}", email="doc@example.com", role="doctor"
        )
        document_id = DocumentRepository(session).create(
            tenant_id,
            user.id,
            filename="x.pdf",
            content_type="application/pdf",
            byte_size=1,
            raw_storage_path="/data/documents/x/y/original.pdf",
        ).id
        dek_id = session.execute(
            sa.select(TenantKey.id).where(TenantKey.tenant_id == tenant_id)
        ).scalar_one()

    return tenant_id, document_id, dek_id


def test_check_constraint_rejects_conversation_scope_with_document_id_set(key_provider):
    tenant_id, document_id, dek_id = _setup(key_provider)
    with pytest.raises(IntegrityError, match="ck_token_mappings_scope_consistency"):
        with tenant_scoped_session(tenant_id) as session:
            session.add(
                TokenMapping(
                    tenant_id=tenant_id,
                    scope_type="conversation",
                    conversation_id=uuid.uuid4(),
                    document_id=document_id,
                    token="PATIENT_AAAAAAAAAA",
                    entity_type="PATIENT",
                    encrypted_value=b"x" * 28,
                    dek_id=dek_id,
                )
            )


def test_check_constraint_rejects_document_scope_with_no_document_id(key_provider):
    tenant_id, document_id, dek_id = _setup(key_provider)
    with pytest.raises(IntegrityError, match="ck_token_mappings_scope_consistency"):
        with tenant_scoped_session(tenant_id) as session:
            session.add(
                TokenMapping(
                    tenant_id=tenant_id,
                    scope_type="document",
                    conversation_id=None,
                    document_id=None,
                    token="PATIENT_AAAAAAAAAA",
                    entity_type="PATIENT",
                    encrypted_value=b"x" * 28,
                    dek_id=dek_id,
                )
            )


def test_check_constraint_rejects_document_scope_with_conversation_id_set(key_provider):
    """Mirror of the conversation-scope case above: scope_type='document' with
    conversation_id also populated must be rejected by the CHECK constraint, even
    though document_id is correctly set too."""
    tenant_id, document_id, dek_id = _setup(key_provider)
    with pytest.raises(IntegrityError, match="ck_token_mappings_scope_consistency"):
        with tenant_scoped_session(tenant_id) as session:
            session.add(
                TokenMapping(
                    tenant_id=tenant_id,
                    scope_type="document",
                    conversation_id=uuid.uuid4(),
                    document_id=document_id,
                    token="PATIENT_AAAAAAAAAA",
                    entity_type="PATIENT",
                    encrypted_value=b"x" * 28,
                    dek_id=dek_id,
                )
            )


def test_check_constraint_rejects_invalid_scope_type_discriminator(key_provider):
    """scope_type is a free-standing String column, not a Postgres ENUM, so the
    CHECK constraint itself is what has to reject a value outside
    {'conversation', 'document'} -- neither disjunct of its OR condition can match
    a scope_type that is neither, even with both conversation_id and document_id
    left NULL."""
    tenant_id, document_id, dek_id = _setup(key_provider)
    with pytest.raises(IntegrityError, match="ck_token_mappings_scope_consistency"):
        with tenant_scoped_session(tenant_id) as session:
            session.add(
                TokenMapping(
                    tenant_id=tenant_id,
                    scope_type="patient",
                    conversation_id=None,
                    document_id=None,
                    token="PATIENT_AAAAAAAAAA",
                    entity_type="PATIENT",
                    encrypted_value=b"x" * 28,
                    dek_id=dek_id,
                )
            )


def test_same_token_string_allowed_across_a_conversation_and_a_document_scope(key_provider):
    """The two partial unique indexes are scoped independently -- the same token
    string colliding across a conversation and a document is not a uniqueness
    violation, since resolution always requires both scope_type and scope_id."""
    tenant_id, document_id, dek_id = _setup(key_provider)
    conversation_id = uuid.uuid4()

    with tenant_scoped_session(tenant_id) as session:
        user_id = session.execute(sa.select(User.id).limit(1)).scalar_one()
        session.add(Conversation(id=conversation_id, tenant_id=tenant_id, user_id=user_id))
        session.flush()
        session.add(
            TokenMapping(
                tenant_id=tenant_id,
                scope_type="conversation",
                conversation_id=conversation_id,
                document_id=None,
                token="PATIENT_SAMETOKEN01",
                entity_type="PATIENT",
                encrypted_value=b"x" * 28,
                dek_id=dek_id,
            )
        )
        session.add(
            TokenMapping(
                tenant_id=tenant_id,
                scope_type="document",
                conversation_id=None,
                document_id=document_id,
                token="PATIENT_SAMETOKEN01",
                entity_type="PATIENT",
                encrypted_value=b"y" * 28,
                dek_id=dek_id,
            )
        )

    with tenant_scoped_session(tenant_id) as session:
        count = session.execute(
            sa.select(sa.func.count())
            .select_from(TokenMapping)
            .where(TokenMapping.tenant_id == tenant_id, TokenMapping.token == "PATIENT_SAMETOKEN01")
        ).scalar_one()
        assert count == 2


def test_duplicate_token_within_same_document_scope_is_rejected(key_provider):
    tenant_id, document_id, dek_id = _setup(key_provider)
    with tenant_scoped_session(tenant_id) as session:
        session.add(
            TokenMapping(
                tenant_id=tenant_id,
                scope_type="document",
                conversation_id=None,
                document_id=document_id,
                token="PATIENT_DUPDUP0001",
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
                    scope_type="document",
                    conversation_id=None,
                    document_id=document_id,
                    token="PATIENT_DUPDUP0001",
                    entity_type="PATIENT",
                    encrypted_value=b"y" * 28,
                    dek_id=dek_id,
                )
            )
