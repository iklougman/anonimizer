import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.permissions import ALL_PERMISSIONS, DEFAULT_PERMISSIONS
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.branch_repository import BranchRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.main import app
from app.models import Tenant


@pytest.fixture
def scope():
    with SessionLocal() as session:
        tenant = Tenant(name="Me Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30)
        session.add(tenant)
        session.commit()
        tenant_id = tenant.id
    with tenant_scoped_session(tenant_id) as session:
        branch = BranchRepository(session).create(tenant_id, "Hauptstandort")
        branch_id = branch.id
    return tenant_id, branch_id


def _client_for(user: AuthenticatedUser) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup_override():
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_me_returns_identity_role_branch_and_permissions(scope):
    tenant_id, branch_id = scope
    user_id = uuid.uuid4()
    client = _client_for(
        AuthenticatedUser(
            tenant_id=tenant_id,
            user_id=user_id,
            role="staff",
            branch_id=branch_id,
            email="empfang@example.com",
            permissions=DEFAULT_PERMISSIONS["staff"],
        )
    )

    response = client.get("/api/me")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(user_id)
    assert body["tenant_id"] == str(tenant_id)
    assert body["tenant_name"] == "Me Clinic"
    assert body["email"] == "empfang@example.com"
    assert body["role"] == "staff"
    assert body["branch_id"] == str(branch_id)
    assert body["branch_name"] == "Hauptstandort"
    assert body["permissions"] == sorted(str(p) for p in DEFAULT_PERMISSIONS["staff"])


def test_me_without_branch_returns_null_branch_fields(scope):
    tenant_id, _ = scope
    client = _client_for(
        AuthenticatedUser(
            tenant_id=tenant_id,
            user_id=uuid.uuid4(),
            role="super_admin",
            branch_id=None,
            email="chef@example.com",
            permissions=ALL_PERMISSIONS,
        )
    )

    response = client.get("/api/me")

    assert response.status_code == 200
    body = response.json()
    assert body["branch_id"] is None
    assert body["branch_name"] is None
    assert set(body["permissions"]) == {str(p) for p in ALL_PERMISSIONS}
