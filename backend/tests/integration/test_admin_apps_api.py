"""Admin apps API: 403 without admin:apps:manage, entitlement visibility, and
the "cannot enable what ops hasn't granted" 409 guard."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.permissions import ALL_PERMISSIONS, DEFAULT_PERMISSIONS
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.main import app
from app.models import Tenant
from tests.conftest import grant_app_entitlement


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _create_tenant() -> uuid.UUID:
    with SessionLocal() as session:
        tenant = Tenant(name="Apps Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30)
        session.add(tenant)
        session.commit()
        return tenant.id


def _create_user(tenant_id, role="super_admin") -> uuid.UUID:
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}", email=f"{uuid.uuid4()}@example.com", role=role
        )
        return user.id


def _client_as(tenant_id, user_id, role) -> TestClient:
    permissions = ALL_PERMISSIONS if role == "super_admin" else DEFAULT_PERMISSIONS[role]
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        branch_id=None,
        email="admin@example.com",
        permissions=permissions,
    )
    return TestClient(app)


@pytest.mark.parametrize("method,path", [("GET", "/api/admin/apps")])
def test_doctor_and_staff_get_403_on_apps_routes(method, path):
    tenant_id = _create_tenant()
    for role in ("doctor", "staff"):
        user_id = _create_user(tenant_id, role=role)
        client = _client_as(tenant_id, user_id, role)
        assert client.request(method, path).status_code == 403, f"{role} {method} {path}"


def test_list_apps_shows_anonymization_not_entitled_by_default():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    client = _client_as(tenant_id, admin_id, "super_admin")

    apps = client.get("/api/admin/apps").json()
    anonymization = next(a for a in apps if a["key"] == "anonymization")
    assert anonymization["is_entitled"] is False
    assert anonymization["assignments"] == []


def test_put_assignment_fails_409_when_not_entitled():
    tenant_id = _create_tenant()
    admin_id = _create_user(tenant_id)
    client = _client_as(tenant_id, admin_id, "super_admin")

    apps = client.get("/api/admin/apps").json()
    app_id = next(a for a in apps if a["key"] == "anonymization")["id"]

    response = client.put(f"/api/admin/apps/{app_id}/assignment", json={"branch_id": None, "is_enabled": True})
    assert response.status_code == 409


def test_put_assignment_succeeds_and_round_trips_when_entitled():
    tenant_id = _create_tenant()
    grant_app_entitlement(tenant_id)
    admin_id = _create_user(tenant_id)
    client = _client_as(tenant_id, admin_id, "super_admin")

    apps = client.get("/api/admin/apps").json()
    anonymization = next(a for a in apps if a["key"] == "anonymization")
    assert anonymization["is_entitled"] is True
    app_id = anonymization["id"]

    enabled = client.put(f"/api/admin/apps/{app_id}/assignment", json={"branch_id": None, "is_enabled": True})
    assert enabled.status_code == 200
    assert enabled.json() == {"branch_id": None, "branch_name": None, "is_enabled": True}

    apps_after = client.get("/api/admin/apps").json()
    anonymization_after = next(a for a in apps_after if a["key"] == "anonymization")
    assert anonymization_after["assignments"] == [{"branch_id": None, "branch_name": None, "is_enabled": True}]

    disabled = client.put(f"/api/admin/apps/{app_id}/assignment", json={"branch_id": None, "is_enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["is_enabled"] is False
