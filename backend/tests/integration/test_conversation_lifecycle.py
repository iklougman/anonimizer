import uuid

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def _create_tenant_and_user(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        )
        user_id = user.id
    return tenant_id, user_id


def test_new_conversation_has_no_title_and_is_not_deleted(tmp_path):
    tenant_id, user_id = _create_tenant_and_user(tmp_path)
    with tenant_scoped_session(tenant_id) as session:
        conversation = ConversationRepository(session).create(tenant_id, user_id)
        assert conversation.title is None
        assert conversation.deleted_at is None
        assert conversation.updated_at is not None


def test_set_title_updates_the_conversation(tmp_path):
    tenant_id, user_id = _create_tenant_and_user(tmp_path)
    with tenant_scoped_session(tenant_id) as session:
        conversation_id = ConversationRepository(session).create(tenant_id, user_id).id

    with tenant_scoped_session(tenant_id) as session:
        ConversationRepository(session).set_title(tenant_id, conversation_id, "Erste Anfrage")

    with tenant_scoped_session(tenant_id) as session:
        conversation = ConversationRepository(session).get(tenant_id, conversation_id)
        assert conversation.title == "Erste Anfrage"


def test_touch_updates_updated_at(tmp_path):
    tenant_id, user_id = _create_tenant_and_user(tmp_path)
    with tenant_scoped_session(tenant_id) as session:
        conversation = ConversationRepository(session).create(tenant_id, user_id)
        conversation_id = conversation.id
        original_updated_at = conversation.updated_at

    with tenant_scoped_session(tenant_id) as session:
        ConversationRepository(session).touch(tenant_id, conversation_id)

    with tenant_scoped_session(tenant_id) as session:
        conversation = ConversationRepository(session).get(tenant_id, conversation_id)
        assert conversation.updated_at >= original_updated_at


def test_soft_deleted_conversation_is_excluded_from_get_and_list(tmp_path):
    tenant_id, user_id = _create_tenant_and_user(tmp_path)
    with tenant_scoped_session(tenant_id) as session:
        conversation_id = ConversationRepository(session).create(tenant_id, user_id).id

    with tenant_scoped_session(tenant_id) as session:
        ConversationRepository(session).soft_delete(tenant_id, conversation_id)

    with tenant_scoped_session(tenant_id) as session:
        repo = ConversationRepository(session)
        assert repo.get(tenant_id, conversation_id) is None
        assert conversation_id not in {c.id for c in repo.list_for_user(tenant_id, user_id)}
