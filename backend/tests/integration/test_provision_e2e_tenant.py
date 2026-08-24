import decimal
import json
import subprocess
import sys
import uuid

import pytest
import sqlalchemy as sa

from app.config import get_settings
from app.db.repositories.audit_event_repository import AuditEventRepository
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.llm_request_repository import LLMRequestRepository
from app.db.repositories.message_repository import MessageRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Branch, Tenant, User


@pytest.fixture
def fake_keycloak_admin_client():
    """The script's own --fake-keycloak-client flag (passed in every
    _run_script call in this file) handles this in-process -- nothing to
    patch here across the subprocess boundary."""
    return None


def _run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/provision_e2e_tenant.py", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_create_provisions_tenant_branch_and_three_users(fake_keycloak_admin_client):
    result = _run_script("create", "--fake-keycloak-client")
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout)
    tenant_id = uuid.UUID(payload["tenant_id"])
    branch_id = uuid.UUID(payload["branch_id"])
    assert set(payload["users"].keys()) == {"super_admin", "doctor", "staff"}
    for role, user_info in payload["users"].items():
        assert user_info["email"]
        assert user_info["password"]
        assert user_info["keycloak_subject"]

    with tenant_scoped_session(tenant_id) as session:
        branch = session.get(Branch, branch_id)
        assert branch is not None
        assert branch.tenant_id == tenant_id

        users = session.execute(
            sa.select(User).where(User.tenant_id == tenant_id)
        ).scalars().all()
        assert len(users) == 3
        assert {u.role for u in users} == {"super_admin", "doctor", "staff"}
        assert all(u.branch_id == branch_id for u in users)

    # tenant_app_entitlements has FORCE ROW LEVEL SECURITY (migration 0007) and
    # app_runtime (SessionLocal's role) has no tenant context set here, so a
    # plain SessionLocal() query would always see zero rows regardless of
    # what's actually in the table. Read it back the same way
    # conftest.py::grant_app_entitlement writes it: a direct
    # get_settings().database_url connection (the migration-owner role, which
    # bypasses RLS), matching _grant_anonymization_entitlement in the script
    # itself.
    admin_engine = sa.create_engine(get_settings().database_url)
    with admin_engine.connect() as connection:
        entitlement = connection.execute(
            sa.text(
                "SELECT revoked_at FROM tenant_app_entitlements WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": str(tenant_id)},
        ).mappings().first()
    admin_engine.dispose()
    assert entitlement is not None
    assert entitlement["revoked_at"] is None

    _run_script("cleanup", "--tenant-id", str(tenant_id), "--fake-keycloak-client")


def test_cleanup_removes_tenant_and_all_its_rows(fake_keycloak_admin_client):
    result = _run_script("create", "--fake-keycloak-client")
    payload = json.loads(result.stdout)
    tenant_id = uuid.UUID(payload["tenant_id"])

    cleanup_result = _run_script("cleanup", "--tenant-id", str(tenant_id), "--fake-keycloak-client")
    assert cleanup_result.returncode == 0, cleanup_result.stderr

    with SessionLocal() as session:
        assert session.get(Tenant, tenant_id) is None


def test_cleanup_succeeds_for_a_tenant_with_real_chat_activity(fake_keycloak_admin_client):
    """Regression test for the review finding: cleanup() used to delete
    messages/conversations/users/branches/tenant_app_assignments but never
    touched llm_requests/audit_events/token_mappings, all of which carry hard
    (non-cascading) composite FKs to conversations -- so any tenant that had
    actually run through app/api/chat.py (llm_requests on every successful
    turn, audit_events on every leakage/unresolved-token error) made cleanup
    fail with a ForeignKeyViolation on `conversations`. This reproduces that
    exact shape via the real repositories chat.py itself uses, then asserts
    cleanup now exits 0 and the tenant is fully gone."""
    result = _run_script("create", "--fake-keycloak-client")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    tenant_id = uuid.UUID(payload["tenant_id"])
    doctor_user_id = uuid.UUID(payload["users"]["doctor"]["user_id"])

    with tenant_scoped_session(tenant_id) as session:
        conversation = ConversationRepository(session).create(tenant_id, doctor_user_id)
        conversation_id = conversation.id

        MessageRepository(session).create(
            tenant_id, conversation_id, role="user", sanitized_content="hello [PERSON_1]"
        )
        MessageRepository(session).create(
            tenant_id, conversation_id, role="assistant", sanitized_content="hi [PERSON_1], how can I help?"
        )

        LLMRequestRepository(session).create(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            provider="openai",
            model="gpt-4o",
            sanitized_prompt="hello [PERSON_1]",
            sanitized_response="hi [PERSON_1], how can I help?",
            tokens_in=5,
            tokens_out=8,
            cost_usd=decimal.Decimal("0.000123"),
            latency_ms=250,
        )

        AuditEventRepository(session).create(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            event_type="UnresolvedTokenError",
            entity_type="UNKNOWN",
            token="[PERSON_99]",
            actor=str(doctor_user_id),
        )

    cleanup_result = _run_script("cleanup", "--tenant-id", str(tenant_id), "--fake-keycloak-client")
    assert cleanup_result.returncode == 0, cleanup_result.stderr

    with SessionLocal() as session:
        assert session.get(Tenant, tenant_id) is None

    admin_engine = sa.create_engine(get_settings().database_url)
    with admin_engine.connect() as connection:
        for table in ("llm_requests", "audit_events", "conversations", "messages", "token_mappings"):
            remaining = connection.execute(
                sa.text(f"SELECT count(*) FROM {table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": str(tenant_id)},
            ).scalar_one()
            assert remaining == 0, f"{table} still has rows for cleaned-up tenant {tenant_id}"
    admin_engine.dispose()


def test_repeated_create_generates_distinct_tenants(fake_keycloak_admin_client):
    result_a = _run_script("create", "--fake-keycloak-client")
    result_b = _run_script("create", "--fake-keycloak-client")
    tenant_a = json.loads(result_a.stdout)["tenant_id"]
    tenant_b = json.loads(result_b.stdout)["tenant_id"]
    assert tenant_a != tenant_b

    _run_script("cleanup", "--tenant-id", tenant_a, "--fake-keycloak-client")
    _run_script("cleanup", "--tenant-id", tenant_b, "--fake-keycloak-client")
