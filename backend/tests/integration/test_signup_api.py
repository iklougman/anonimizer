"""POST /api/signup: public tenant self-onboarding.

Exercises the endpoint against a real Postgres (tenant + super_admin user
creation, the grant_default_entitlement_on_signup() grant, Keycloak
compensation on a DB failure, and per-IP rate limiting) with a fake Keycloak
admin client standing in for the real Keycloak Admin API -- the same
dependency-override pattern test_admin_api.py uses for
get_keycloak_admin_client.

Every tenant created by these tests is torn down directly, matching
scripts/provision_e2e_tenant.py's cleanup()'s FK-safe delete order (child
rows before the tenants row itself), scoped down to only the tables this
endpoint actually writes -- a freshly signed-up tenant has no branches,
conversations, or messages, so those steps are omitted rather than blindly
copied from that script's more general cleanup().
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.api.rate_limit import _reset_for_tests
from app.auth.dependencies import get_keycloak_admin_client
from app.config import get_settings
from app.db.session import tenant_scoped_session
from app.keycloak_admin.client import KeycloakAdminError
from app.main import app


class FakeKeycloakAdminClientForSignup:
    def __init__(self):
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.sent_actions: list[tuple[str, list[str]]] = []
        self._next_subject_n = 0

    def create_user(self, *, email, first_name, last_name, tenant_id, required_actions=None):
        self._next_subject_n += 1
        subject = f"fake-subject-{self._next_subject_n}"
        self.created.append(
            {"email": email, "tenant_id": tenant_id, "required_actions": required_actions, "subject": subject}
        )
        return subject

    def set_password(self, subject, password, temporary=False):
        pass

    def send_required_actions_email(self, subject, actions):
        self.sent_actions.append((subject, actions))

    def delete_user(self, subject):
        self.deleted.append(subject)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    # check_rate_limit's in-memory store is keyed by IP and shared process-wide
    # (app/api/rate_limit.py) -- TestClient requests all carry the same fake
    # client host, so without resetting between tests, quota used by one test
    # would bleed into the next and break both the happy-path tests and the
    # dedicated rate-limit test below.
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
def fake_admin_client():
    return FakeKeycloakAdminClientForSignup()


@pytest.fixture
def client(fake_admin_client):
    app.dependency_overrides[get_keycloak_admin_client] = lambda: fake_admin_client
    yield TestClient(app)
    app.dependency_overrides.clear()


def _signup_body(email: str | None = None) -> dict:
    return {
        "practice_name": "Test Praxis GmbH",
        "first_name": "Anna",
        "last_name": "Schmitt",
        "email": email or f"signup-{uuid.uuid4().hex[:8]}@example.test",
        "password": "correct-horse-battery-staple",
    }


def _cleanup_tenant(tenant_id: uuid.UUID) -> None:
    """Direct DB cleanup matching provision_e2e_tenant.py's cleanup()'s
    FK-safe delete order (users, then tenant_app_assignments, before the
    tenant_app_entitlements/tenant_keys/tenants deletes), restricted to the
    tables POST /api/signup actually writes."""
    with tenant_scoped_session(tenant_id) as session:
        session.execute(sa.text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        session.execute(
            sa.text("DELETE FROM tenant_app_assignments WHERE tenant_id = :tid"), {"tid": str(tenant_id)}
        )

    # tenant_app_entitlements/tenant_keys/tenants: same as
    # test_signup_entitlement_grant_function.py's test_tenant_id fixture --
    # app_runtime is SELECT-only on tenant_app_entitlements (migration 0007)
    # and cannot delete it, and TenantRepository/tenant creation itself has
    # no app_runtime-scoped delete path either, so this test-only teardown
    # uses the migration-owner connection the same way that fixture does.
    engine = sa.create_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM tenant_app_entitlements WHERE tenant_id = :tid"), {"tid": str(tenant_id)}
        )
        connection.execute(sa.text("DELETE FROM tenant_keys WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        connection.execute(sa.text("DELETE FROM tenants WHERE id = :tid"), {"tid": str(tenant_id)})
    engine.dispose()


@pytest.fixture
def created_tenant_ids():
    ids: list[uuid.UUID] = []
    yield ids
    for tenant_id in ids:
        _cleanup_tenant(tenant_id)


def test_signup_creates_a_tenant_and_super_admin_user(client, fake_admin_client, created_tenant_ids):
    response = client.post("/api/signup", json=_signup_body())
    assert response.status_code == 201
    body = response.json()
    assert "tenant_id" in body
    created_tenant_ids.append(uuid.UUID(body["tenant_id"]))

    assert fake_admin_client.created[0]["required_actions"] == ["VERIFY_EMAIL"]
    assert fake_admin_client.sent_actions[0][1] == ["VERIFY_EMAIL"]

    # The tenant and its super_admin user actually landed in the DB. `users`
    # carries a fail-closed RLS policy (migration 0003: `tenant_id =
    # nullif(current_setting('app.current_tenant_id', true), '')::uuid`), so
    # a plain SessionLocal() with no tenant context set can never see a row
    # here regardless of the WHERE clause -- this must go through
    # tenant_scoped_session, the same way every other reader of this table
    # does (e.g. get_db_session, provision_e2e_tenant.py's cleanup()).
    with tenant_scoped_session(uuid.UUID(body["tenant_id"])) as session:
        row = session.execute(
            sa.text("SELECT role FROM users WHERE tenant_id = :tid"), {"tid": body["tenant_id"]}
        ).fetchone()
        assert row[0] == "super_admin"


def test_signup_grants_the_default_entitlement(client, fake_admin_client, created_tenant_ids):
    response = client.post("/api/signup", json=_signup_body())
    assert response.status_code == 201
    tenant_id = response.json()["tenant_id"]
    created_tenant_ids.append(uuid.UUID(tenant_id))

    # Same query shape as Task 4's test_signup_entitlement_grant_function.py.
    engine = sa.create_engine(get_settings().database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT a.key FROM tenant_app_entitlements e JOIN apps a ON a.id = e.app_id "
                "WHERE e.tenant_id = :tid AND e.revoked_at IS NULL"
            ),
            {"tid": tenant_id},
        ).fetchall()
    engine.dispose()
    assert [r[0] for r in rows] == ["anonymization"]


def test_signup_returns_501_when_keycloak_admin_client_unconfigured():
    app.dependency_overrides[get_keycloak_admin_client] = lambda: None
    try:
        response = TestClient(app).post("/api/signup", json=_signup_body())
        assert response.status_code == 501
    finally:
        app.dependency_overrides.clear()


def test_signup_reports_verification_email_not_sent_on_keycloak_failure(
    client, fake_admin_client, created_tenant_ids
):
    def failing_send(subject, actions):
        raise KeycloakAdminError("smtp down")

    fake_admin_client.send_required_actions_email = failing_send

    response = client.post("/api/signup", json=_signup_body())
    assert response.status_code == 201
    body = response.json()
    created_tenant_ids.append(uuid.UUID(body["tenant_id"]))
    assert body["verification_email_sent"] is False


def test_signup_compensates_keycloak_user_on_db_failure(client, fake_admin_client, monkeypatch, created_tenant_ids):
    def failing_create(self, tenant_id, keycloak_subject, email, role, branch_id=None, is_active=True):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr("app.db.repositories.user_repository.UserRepository.create", failing_create)

    response = client.post("/api/signup", json=_signup_body())
    assert response.status_code == 500

    # The tenant/entitlement steps already committed before the simulated
    # failure, so they still need teardown even though the request itself
    # failed -- recover the tenant_id from what was sent to Keycloak, since
    # a 500 response carries no body to read it from.
    assert len(fake_admin_client.created) == 1
    tenant_id = uuid.UUID(fake_admin_client.created[0]["tenant_id"])
    created_tenant_ids.append(tenant_id)

    subject = fake_admin_client.created[0]["subject"]
    assert fake_admin_client.deleted == [subject]


def test_signup_is_rate_limited_per_ip(client, fake_admin_client, created_tenant_ids):
    limit = get_settings().signup_rate_limit_per_hour
    for i in range(limit):
        response = client.post("/api/signup", json=_signup_body(email=f"signup-rl-{i}-{uuid.uuid4().hex[:6]}@example.test"))
        assert response.status_code == 201, response.text
        created_tenant_ids.append(uuid.UUID(response.json()["tenant_id"]))

    response = client.post("/api/signup", json=_signup_body(email=f"signup-rl-overflow-{uuid.uuid4().hex[:6]}@example.test"))
    assert response.status_code == 429
