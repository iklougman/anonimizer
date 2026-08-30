"""Admin API: 403 without admin permissions, branch/user/matrix CRUD, and the
lock-out guards on PATCH /api/admin/users."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user, get_keycloak_admin_client
from app.auth.permissions import ALL_PERMISSIONS, DEFAULT_PERMISSIONS, Permission
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.keycloak_admin.client import KeycloakAdminConflictError, KeycloakAdminError
from app.main import app
from app.models import Tenant


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_keycloak_admin_client, None)


def _create_tenant() -> uuid.UUID:
    with SessionLocal() as session:
        tenant = Tenant(name="Admin Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30)
        session.add(tenant)
        session.commit()
        return tenant.id


def _create_user(tenant_id, role="super_admin", is_active=True) -> uuid.UUID:
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id,
            keycloak_subject=f"sub-{uuid.uuid4()}",
            email=f"{uuid.uuid4()}@example.com",
            role=role,
            is_active=is_active,
        )
        return user.id


def _client_as(tenant_id, user_id, role, permissions=None) -> TestClient:
    resolved = permissions if permissions is not None else (
        ALL_PERMISSIONS if role == "super_admin" else DEFAULT_PERMISSIONS[role]
    )
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        branch_id=None,
        email="admin@example.com",
        permissions=resolved,
    )
    return TestClient(app)


# --- Permission gating -------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/admin/branches"),
        ("POST", "/api/admin/branches"),
        ("GET", "/api/admin/users"),
        ("POST", "/api/admin/users"),
        ("GET", "/api/admin/permissions"),
        ("PUT", "/api/admin/permissions"),
    ],
)
def test_doctor_and_staff_get_403_on_every_admin_route(method, path):
    tenant_id = _create_tenant()
    for role in ("doctor", "staff"):
        user_id = _create_user(tenant_id, role=role)
        client = _client_as(tenant_id, user_id, role)
        response = client.request(method, path, json={})
        assert response.status_code == 403, f"{role} {method} {path}"


# --- Branches ------------------------------------------------------------


def test_branch_crud_roundtrip():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    client = _client_as(tenant_id, admin_id, "super_admin")

    created = client.post("/api/admin/branches", json={"name": "Nord"})
    assert created.status_code == 201
    branch_id = created.json()["id"]
    assert created.json()["user_count"] == 0

    listed = client.get("/api/admin/branches").json()
    assert [b["name"] for b in listed] == ["Nord"]

    renamed = client.patch(f"/api/admin/branches/{branch_id}", json={"name": "Ost"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Ost"

    deleted = client.delete(f"/api/admin/branches/{branch_id}")
    assert deleted.status_code == 204
    assert client.get("/api/admin/branches").json() == []


def test_branch_delete_conflicts_when_users_assigned():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    client = _client_as(tenant_id, admin_id, "super_admin")

    branch_id = client.post("/api/admin/branches", json={"name": "Nord"}).json()["id"]
    with tenant_scoped_session(tenant_id) as session:
        UserRepository(session).create(
            tenant_id,
            keycloak_subject=f"sub-{uuid.uuid4()}",
            email="staff@example.com",
            role="staff",
            branch_id=uuid.UUID(branch_id),
        )

    response = client.delete(f"/api/admin/branches/{branch_id}")
    assert response.status_code == 409


# --- Users: link mode and provisioning -----------------------------------


def test_create_user_link_mode_requires_no_keycloak_client():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    client = _client_as(tenant_id, admin_id, "super_admin")

    response = client.post(
        "/api/admin/users",
        json={"email": "doc@example.com", "role": "doctor", "keycloak_subject": "external-sub-1"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "doc@example.com"
    assert body["invite_email_sent"] is False


def test_create_user_without_link_or_admin_client_is_501():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    app.dependency_overrides[get_keycloak_admin_client] = lambda: None
    client = _client_as(tenant_id, admin_id, "super_admin")

    response = client.post("/api/admin/users", json={"email": "doc@example.com", "role": "doctor"})

    assert response.status_code == 501


class _FakeAdminClient:
    def __init__(self, subject="new-subject", create_error=None):
        self.subject = subject
        self.create_error = create_error
        self.deleted = []
        self.invited = []
        self.logout_user_calls = []

    def create_user(self, email, first_name, last_name, tenant_id):
        if self.create_error is not None:
            raise self.create_error
        return self.subject

    def send_invite(self, subject):
        self.invited.append(subject)

    def delete_user(self, subject):
        self.deleted.append(subject)

    def set_enabled(self, subject, enabled):
        pass

    def logout_user(self, subject):
        self.logout_user_calls.append(subject)


def test_create_user_provisioning_mode_uses_admin_client():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    fake = _FakeAdminClient(subject="prov-sub-1")
    app.dependency_overrides[get_keycloak_admin_client] = lambda: fake
    client = _client_as(tenant_id, admin_id, "super_admin")

    response = client.post("/api/admin/users", json={"email": "new@example.com", "role": "staff"})

    assert response.status_code == 201
    assert response.json()["invite_email_sent"] is True
    assert fake.invited == ["prov-sub-1"]


def test_create_user_provisioning_conflict_maps_to_409():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    fake = _FakeAdminClient(create_error=KeycloakAdminConflictError("exists"))
    app.dependency_overrides[get_keycloak_admin_client] = lambda: fake
    client = _client_as(tenant_id, admin_id, "super_admin")

    response = client.post("/api/admin/users", json={"email": "dup@example.com", "role": "staff"})

    assert response.status_code == 409


def test_create_user_provisioning_transport_error_maps_to_502():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    fake = _FakeAdminClient(create_error=KeycloakAdminError("down"))
    app.dependency_overrides[get_keycloak_admin_client] = lambda: fake
    client = _client_as(tenant_id, admin_id, "super_admin")

    response = client.post("/api/admin/users", json={"email": "x@example.com", "role": "staff"})

    assert response.status_code == 502


def test_create_user_db_conflict_compensates_keycloak_create():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    fake = _FakeAdminClient(subject="dup-subject")
    app.dependency_overrides[get_keycloak_admin_client] = lambda: fake
    client = _client_as(tenant_id, admin_id, "super_admin")

    # Link-mode create first, occupying "dup-subject" for this tenant so the
    # provisioning-mode create below collides on the (tenant_id, subject)
    # uniqueness constraint.
    with tenant_scoped_session(tenant_id) as session:
        UserRepository(session).create(
            tenant_id, keycloak_subject="dup-subject", email="first@example.com", role="staff"
        )

    response = client.post("/api/admin/users", json={"email": "second@example.com", "role": "staff"})

    assert response.status_code == 409
    assert fake.deleted == ["dup-subject"]


# --- Lock-out guards -------------------------------------------------


def test_cannot_deactivate_the_last_active_super_admin():
    tenant_id = _create_tenant()
    solo_admin = _create_user(tenant_id, role="super_admin")
    other_admin = _create_user(tenant_id, role="super_admin")
    client = _client_as(tenant_id, other_admin, "super_admin")

    with tenant_scoped_session(tenant_id) as session:
        UserRepository(session).update(tenant_id, other_admin, is_active=False)

    response = client.patch(f"/api/admin/users/{solo_admin}", json={"is_active": False})
    assert response.status_code == 409


def test_cannot_deactivate_own_account():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id, role="super_admin")
    _create_user(tenant_id, role="super_admin")  # another admin exists
    client = _client_as(tenant_id, admin_id, "super_admin")

    response = client.patch(f"/api/admin/users/{admin_id}", json={"is_active": False})
    assert response.status_code == 409


def test_can_deactivate_a_non_admin_user():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id, role="super_admin")
    staff_id = _create_user(tenant_id, role="staff")
    client = _client_as(tenant_id, admin_id, "super_admin")

    response = client.patch(f"/api/admin/users/{staff_id}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_deactivating_a_user_calls_logout_user():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id, role="super_admin")
    staff_id = _create_user(tenant_id, role="staff")
    fake = _FakeAdminClient()
    app.dependency_overrides[get_keycloak_admin_client] = lambda: fake
    client = _client_as(tenant_id, admin_id, "super_admin")

    with tenant_scoped_session(tenant_id) as session:
        staff_keycloak_subject = UserRepository(session).get(tenant_id, staff_id).keycloak_subject

    response = client.patch(f"/api/admin/users/{staff_id}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert fake.logout_user_calls == [staff_keycloak_subject]


def test_reactivating_a_user_does_not_call_logout_user():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id, role="super_admin")
    staff_id = _create_user(tenant_id, role="staff", is_active=False)
    fake = _FakeAdminClient()
    app.dependency_overrides[get_keycloak_admin_client] = lambda: fake
    client = _client_as(tenant_id, admin_id, "super_admin")

    response = client.patch(f"/api/admin/users/{staff_id}", json={"is_active": True})
    assert response.status_code == 200
    assert response.json()["is_active"] is True
    assert fake.logout_user_calls == []


def test_branch_id_can_be_explicitly_cleared():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id, role="super_admin")
    client = _client_as(tenant_id, admin_id, "super_admin")

    branch_id = client.post("/api/admin/branches", json={"name": "Nord"}).json()["id"]
    staff_id = _create_user(tenant_id, role="staff")
    with tenant_scoped_session(tenant_id) as session:
        UserRepository(session).update(tenant_id, staff_id, branch_id=uuid.UUID(branch_id))

    response = client.patch(f"/api/admin/users/{staff_id}", json={"branch_id": None})
    assert response.status_code == 200
    assert response.json()["branch_id"] is None


# --- Permission matrix -----------------------------------------------


def test_permission_matrix_get_reflects_code_defaults_when_empty():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    client = _client_as(tenant_id, admin_id, "super_admin")

    response = client.get("/api/admin/permissions")
    assert response.status_code == 200
    body = response.json()
    assert body["matrix"]["doctor"]["conversations:read:branch"] is False
    assert body["matrix"]["staff"]["conversations:read:all"] is False


def test_permission_matrix_put_round_trips_and_affects_resolution():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    client = _client_as(tenant_id, admin_id, "super_admin")

    put_response = client.put(
        "/api/admin/permissions",
        json={"matrix": {"doctor": {"conversations:read:branch": True}}},
    )
    assert put_response.status_code == 200
    assert put_response.json()["matrix"]["doctor"]["conversations:read:branch"] is True

    from app.auth.permissions import resolve_permissions

    with tenant_scoped_session(tenant_id) as session:
        resolved = resolve_permissions(session, tenant_id, "doctor")
    assert Permission.CONVERSATIONS_READ_BRANCH in resolved


def test_permission_matrix_put_rejects_unknown_role():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    client = _client_as(tenant_id, admin_id, "super_admin")

    response = client.put(
        "/api/admin/permissions",
        json={"matrix": {"super_admin": {"conversations:read:all": False}}},
    )
    assert response.status_code == 422


def test_permission_matrix_put_rejects_unknown_permission():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    client = _client_as(tenant_id, admin_id, "super_admin")

    response = client.put(
        "/api/admin/permissions",
        json={"matrix": {"doctor": {"admin:users:manage": True}}},
    )
    assert response.status_code == 422
