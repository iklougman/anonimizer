import uuid

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.message_repository import MessageRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def _create_tenant(key_provider):
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic",
            keycloak_realm=f"realm-{uuid.uuid4()}",
            retention_days=30,
        )
        session.commit()
        return tenant.id


def test_user_conversation_message_round_trip(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))

    tenant_id = _create_tenant(key_provider)

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        )
        conversation = ConversationRepository(session).create(tenant_id, user.id)
        message = MessageRepository(session).create(
            tenant_id, conversation.id, role="user", sanitized_content="Hallo [PATIENT_AB12C]"
        )
        message_id = message.id
        conversation_id = conversation.id

    with tenant_scoped_session(tenant_id) as session:
        messages = MessageRepository(session).list_for_conversation(tenant_id, conversation_id)
        assert len(messages) == 1
        assert messages[0].id == message_id


def test_conversations_do_not_cross_tenants(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))

    tenant_a = _create_tenant(key_provider)
    tenant_b = _create_tenant(key_provider)

    with tenant_scoped_session(tenant_a) as session:
        user_a = UserRepository(session).create(
            tenant_a, keycloak_subject="sub-a", email="a@example.com", role="doctor"
        )
        ConversationRepository(session).create(tenant_a, user_a.id)
        user_a_id = user_a.id

    with tenant_scoped_session(tenant_b) as session:
        conversations = ConversationRepository(session).list_for_user(tenant_b, user_a_id)
        assert conversations == []
