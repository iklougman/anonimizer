import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.tenant_resolver import AuthenticatedUser
from app.config import get_settings
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.message_repository import MessageRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.main import app
from app.privacy_gateway.pipeline import get_pipeline
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


@pytest.fixture
def scope():
    # Must use the same master key as the process-wide get_pipeline()'s TokenVault
    # (Settings.master_key_path, set up once in tests/conftest.py) -- a tenant's DEK
    # is wrapped with this key at creation, and get_pipeline()'s vault (used by the
    # conversations API routes) unwraps it with the same key at deanonymize() time.
    key_provider = FileSecretKeyProvider(get_settings().master_key_path)
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


@pytest.fixture
def client(scope):
    tenant_id, user_id = scope
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id, user_id=user_id, role="doctor"
    )
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def test_create_and_list_conversations(client):
    created = client.post("/api/conversations")
    assert created.status_code == 201
    body = created.json()
    assert body["title"] is None

    listed = client.get("/api/conversations")
    assert listed.status_code == 200
    ids = {c["id"] for c in listed.json()}
    assert body["id"] in ids


def test_get_messages_reconstructs_human_readable_text(scope, client):
    tenant_id, user_id = scope
    with tenant_scoped_session(tenant_id) as session:
        conversation_id = ConversationRepository(session).create(tenant_id, user_id).id

    sanitized = get_pipeline().sanitize(tenant_id, conversation_id, "Hallo, hier ist Anna Schmitt.")
    with tenant_scoped_session(tenant_id) as session:
        MessageRepository(session).create(
            tenant_id, conversation_id, role="user", sanitized_content=sanitized
        )

    response = client.get(f"/api/conversations/{conversation_id}/messages")
    assert response.status_code == 200
    messages = response.json()
    assert len(messages) == 1
    assert messages[0]["content"] == "Hallo, hier ist Anna Schmitt."


def test_messages_for_unknown_conversation_is_404(client):
    response = client.get(f"/api/conversations/{uuid.uuid4()}/messages")
    assert response.status_code == 404


def test_delete_soft_deletes_and_hides_the_conversation(client):
    created = client.post("/api/conversations").json()

    deleted = client.delete(f"/api/conversations/{created['id']}")
    assert deleted.status_code == 204

    listed = client.get("/api/conversations")
    assert created["id"] not in {c["id"] for c in listed.json()}


def test_conversations_do_not_cross_users(scope, client):
    tenant_id, _ = scope
    with tenant_scoped_session(tenant_id) as session:
        other_user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-2", email="other@example.com", role="doctor"
        )
        other_conversation_id = ConversationRepository(session).create(tenant_id, other_user.id).id

    response = client.get(f"/api/conversations/{other_conversation_id}/messages")
    assert response.status_code == 404
