"""Adjustable conversation visibility: own-only by default, widened by the
permission matrix, never widened for writes/creates.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.permissions import ALL_PERMISSIONS, DEFAULT_PERMISSIONS, Permission
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.audit_event_repository import AuditEventRepository
from app.db.repositories.branch_repository import BranchRepository
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.main import app
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from tests.conftest import grant_app_entitlement


@pytest.fixture(autouse=True)
def _cleanup_override():
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _key_provider(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


def _create_tenant(tmp_path, name="Visibility Clinic"):
    key_provider = _key_provider(tmp_path)
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name=name, keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        grant_app_entitlement(tenant.id)
        return tenant.id


def _create_user(tenant_id, role="doctor", branch_id=None, email=None):
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id,
            keycloak_subject=f"sub-{uuid.uuid4()}",
            email=email or f"{uuid.uuid4()}@example.com",
            role=role,
            branch_id=branch_id,
        )
        return user.id


def _create_branch(tenant_id, name):
    with tenant_scoped_session(tenant_id) as session:
        return BranchRepository(session).create(tenant_id, name).id


def _create_conversation(tenant_id, user_id):
    with tenant_scoped_session(tenant_id) as session:
        return ConversationRepository(session).create(tenant_id, user_id).id


def _client_as(tenant_id, user_id, role, branch_id=None, permissions=None):
    resolved_permissions = (
        permissions
        if permissions is not None
        else (ALL_PERMISSIONS if role == "super_admin" else DEFAULT_PERMISSIONS[role])
    )
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        branch_id=branch_id,
        email="viewer@example.com",
        permissions=resolved_permissions,
    )
    return TestClient(app)


def test_default_visibility_is_own_conversations_only(tmp_path):
    tenant_id = _create_tenant(tmp_path)
    alice = _create_user(tenant_id)
    bob = _create_user(tenant_id)
    alice_conversation = _create_conversation(tenant_id, alice)
    _create_conversation(tenant_id, bob)

    client = _client_as(tenant_id, alice, "doctor")
    listed = client.get("/api/conversations").json()

    assert [c["id"] for c in listed] == [str(alice_conversation)]
    assert listed[0]["is_own"] is True


def test_branch_grant_widens_visibility_to_same_branch_only(tmp_path):
    tenant_id = _create_tenant(tmp_path)
    branch_north = _create_branch(tenant_id, "Nord")
    branch_south = _create_branch(tenant_id, "Sued")

    alice = _create_user(tenant_id, branch_id=branch_north)
    colleague = _create_user(tenant_id, branch_id=branch_north)
    stranger = _create_user(tenant_id, branch_id=branch_south)

    alice_conv = _create_conversation(tenant_id, alice)
    colleague_conv = _create_conversation(tenant_id, colleague)
    _create_conversation(tenant_id, stranger)

    client = _client_as(
        tenant_id,
        alice,
        "doctor",
        branch_id=branch_north,
        permissions=frozenset(
            {Permission.CONVERSATIONS_CREATE, Permission.CONVERSATIONS_READ_BRANCH}
        ),
    )
    listed = {c["id"] for c in client.get("/api/conversations").json()}

    assert listed == {str(alice_conv), str(colleague_conv)}


def test_null_branch_praxis_single_degenerates_branch_grant_to_tenant_wide(tmp_path):
    """A praxis with no branches: every user's branch_id is NULL, so a
    'read:branch' grant there means "see everyone" -- the correct behavior
    for a single-location praxis, not an accidental lock-out."""
    tenant_id = _create_tenant(tmp_path)
    alice = _create_user(tenant_id, branch_id=None)
    bob = _create_user(tenant_id, branch_id=None)

    alice_conv = _create_conversation(tenant_id, alice)
    bob_conv = _create_conversation(tenant_id, bob)

    client = _client_as(
        tenant_id,
        alice,
        "doctor",
        branch_id=None,
        permissions=frozenset(
            {Permission.CONVERSATIONS_CREATE, Permission.CONVERSATIONS_READ_BRANCH}
        ),
    )
    listed = {c["id"] for c in client.get("/api/conversations").json()}

    assert listed == {str(alice_conv), str(bob_conv)}


def test_read_all_grant_sees_every_conversation_in_tenant(tmp_path):
    tenant_id = _create_tenant(tmp_path)
    alice = _create_user(tenant_id)
    bob = _create_user(tenant_id)
    carol = _create_user(tenant_id)

    for owner in (alice, bob, carol):
        _create_conversation(tenant_id, owner)

    client = _client_as(tenant_id, alice, "super_admin")
    listed = client.get("/api/conversations").json()

    assert len(listed) == 3
    owned_flags = {c["is_own"] for c in listed}
    assert owned_flags == {True, False}


def test_non_owner_cannot_send_a_message_even_with_read_all(tmp_path):
    tenant_id = _create_tenant(tmp_path)
    alice = _create_user(tenant_id)
    bob_conv_owner = _create_user(tenant_id)
    conversation_id = _create_conversation(tenant_id, bob_conv_owner)

    client = _client_as(tenant_id, alice, "super_admin")
    response = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "hallo"}
    )

    assert response.status_code == 404


def test_non_owner_without_delete_any_cannot_delete(tmp_path):
    tenant_id = _create_tenant(tmp_path)
    alice = _create_user(tenant_id)
    bob = _create_user(tenant_id)
    conversation_id = _create_conversation(tenant_id, bob)

    client = _client_as(
        tenant_id,
        alice,
        "doctor",
        permissions=frozenset(
            {Permission.CONVERSATIONS_CREATE, Permission.CONVERSATIONS_READ_ALL}
        ),
    )
    response = client.delete(f"/api/conversations/{conversation_id}")

    assert response.status_code == 404


def test_super_admin_can_delete_any_conversation(tmp_path):
    tenant_id = _create_tenant(tmp_path)
    alice = _create_user(tenant_id)
    bob = _create_user(tenant_id)
    conversation_id = _create_conversation(tenant_id, bob)

    client = _client_as(tenant_id, alice, "super_admin")
    response = client.delete(f"/api/conversations/{conversation_id}")

    assert response.status_code == 204


def test_shared_read_writes_an_audit_event(tmp_path):
    tenant_id = _create_tenant(tmp_path)
    alice = _create_user(tenant_id)
    bob = _create_user(tenant_id)
    conversation_id = _create_conversation(tenant_id, bob)

    client = _client_as(tenant_id, alice, "super_admin")
    response = client.get(f"/api/conversations/{conversation_id}/messages")
    assert response.status_code == 200

    with tenant_scoped_session(tenant_id) as session:
        # No repository read method exists for audit_events yet; go direct.
        import sqlalchemy as sa

        from app.models import AuditEvent

        rows = session.execute(
            sa.select(AuditEvent).where(AuditEvent.conversation_id == conversation_id)
        ).scalars().all()
        assert any(r.event_type == "SharedConversationRead" for r in rows)


def test_own_read_does_not_write_an_audit_event(tmp_path):
    tenant_id = _create_tenant(tmp_path)
    alice = _create_user(tenant_id)
    conversation_id = _create_conversation(tenant_id, alice)

    client = _client_as(tenant_id, alice, "doctor")
    response = client.get(f"/api/conversations/{conversation_id}/messages")
    assert response.status_code == 200

    with tenant_scoped_session(tenant_id) as session:
        import sqlalchemy as sa

        from app.models import AuditEvent

        rows = session.execute(
            sa.select(AuditEvent).where(AuditEvent.conversation_id == conversation_id)
        ).scalars().all()
        assert rows == []


def test_read_all_does_not_leak_across_tenants_even_when_granted(tmp_path):
    tenant_a = _create_tenant(tmp_path, name="Tenant A")
    tenant_b = _create_tenant(tmp_path, name="Tenant B")
    a_user = _create_user(tenant_a)
    b_user = _create_user(tenant_b)
    _create_conversation(tenant_b, b_user)

    # A super_admin in tenant A holds CONVERSATIONS_READ_ALL, but RLS scopes
    # every query to tenant A regardless of what the permission says.
    client = _client_as(tenant_a, a_user, "super_admin")
    listed = client.get("/api/conversations").json()

    assert listed == []


def test_user_without_create_permission_cannot_create_a_conversation(tmp_path):
    tenant_id = _create_tenant(tmp_path)
    user_id = _create_user(tenant_id)

    client = _client_as(tenant_id, user_id, "doctor", permissions=frozenset())
    response = client.post("/api/conversations")

    assert response.status_code == 403
