"""GET /api/apps: the non-admin "what can I use" view -- every role sees it,
unlike GET /api/admin/apps (admin:apps:manage only), and the response never
carries entitlement/assignment plumbing."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.permissions import DEFAULT_PERMISSIONS
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.app_entitlement_repository import AppEntitlementRepository
from app.db.repositories.branch_repository import BranchRepository
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
        tenant = Tenant(name="Apps API Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30)
        session.add(tenant)
        session.commit()
        return tenant.id


def _create_user(tenant_id, role="doctor", branch_id=None) -> uuid.UUID:
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id,
            keycloak_subject=f"sub-{uuid.uuid4()}",
            email=f"{uuid.uuid4()}@example.com",
            role=role,
            branch_id=branch_id,
        )
        return user.id


def _create_branch(tenant_id, name: str) -> uuid.UUID:
    with tenant_scoped_session(tenant_id) as session:
        return BranchRepository(session).create(tenant_id, name).id


def _client_as(tenant_id, user_id, role, branch_id=None) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        branch_id=branch_id,
        email="user@example.com",
        permissions=DEFAULT_PERMISSIONS.get(role, frozenset()),
    )
    return TestClient(app)


def _enable_tenant_wide(tenant_id, app_key="anonymization"):
    with tenant_scoped_session(tenant_id) as session:
        repo = AppEntitlementRepository(session)
        app_row = repo.get_by_key(app_key)
        repo.upsert_assignment(tenant_id, app_row.id, branch_id=None, is_enabled=True, assigned_by=None)


def test_doctor_sees_the_entitled_and_enabled_app():
    tenant_id = _create_tenant()
    user_id = _create_user(tenant_id, role="doctor")
    grant_app_entitlement(tenant_id)
    _enable_tenant_wide(tenant_id)

    response = _client_as(tenant_id, user_id, "doctor").get("/api/apps")
    assert response.status_code == 200
    body = response.json()
    assert [a["key"] for a in body] == ["anonymization"]
    assert set(body[0].keys()) == {"key", "name", "description"}


def test_staff_role_also_sees_available_apps_not_just_admin_roles():
    tenant_id = _create_tenant()
    user_id = _create_user(tenant_id, role="staff")
    grant_app_entitlement(tenant_id)
    _enable_tenant_wide(tenant_id)

    response = _client_as(tenant_id, user_id, "staff").get("/api/apps")
    assert response.status_code == 200
    assert [a["key"] for a in response.json()] == ["anonymization"]


def test_entitled_but_not_enabled_is_invisible():
    tenant_id = _create_tenant()
    user_id = _create_user(tenant_id, role="doctor")
    grant_app_entitlement(tenant_id)
    # No assignment row created -- entitled at the ops layer, but the tenant's
    # own admin has not turned it on yet.

    response = _client_as(tenant_id, user_id, "doctor").get("/api/apps")
    assert response.status_code == 200
    assert response.json() == []


def test_not_entitled_at_all_is_invisible():
    tenant_id = _create_tenant()
    user_id = _create_user(tenant_id, role="doctor")
    # No grant_app_entitlement() call at all.

    response = _client_as(tenant_id, user_id, "doctor").get("/api/apps")
    assert response.status_code == 200
    assert response.json() == []


def test_branch_specific_assignment_is_only_visible_to_that_branch():
    tenant_id = _create_tenant()
    branch_a = _create_branch(tenant_id, "Filiale A")
    branch_b = _create_branch(tenant_id, "Filiale B")
    grant_app_entitlement(tenant_id)

    with tenant_scoped_session(tenant_id) as session:
        repo = AppEntitlementRepository(session)
        app_row = repo.get_by_key("anonymization")
        repo.upsert_assignment(tenant_id, app_row.id, branch_id=branch_a, is_enabled=True, assigned_by=None)

    user_a = _create_user(tenant_id, role="doctor", branch_id=branch_a)
    user_b = _create_user(tenant_id, role="doctor", branch_id=branch_b)

    response_a = _client_as(tenant_id, user_a, "doctor", branch_id=branch_a).get("/api/apps")
    assert [a["key"] for a in response_a.json()] == ["anonymization"]

    response_b = _client_as(tenant_id, user_b, "doctor", branch_id=branch_b).get("/api/apps")
    assert response_b.json() == []


def test_tenant_wide_and_branch_specific_rows_for_the_same_app_are_not_duplicated():
    tenant_id = _create_tenant()
    branch_a = _create_branch(tenant_id, "Filiale A")
    grant_app_entitlement(tenant_id)
    _enable_tenant_wide(tenant_id)

    with tenant_scoped_session(tenant_id) as session:
        repo = AppEntitlementRepository(session)
        app_row = repo.get_by_key("anonymization")
        repo.upsert_assignment(tenant_id, app_row.id, branch_id=branch_a, is_enabled=True, assigned_by=None)

    user_id = _create_user(tenant_id, role="doctor", branch_id=branch_a)
    response = _client_as(tenant_id, user_id, "doctor", branch_id=branch_a).get("/api/apps")
    assert [a["key"] for a in response.json()] == ["anonymization"]
