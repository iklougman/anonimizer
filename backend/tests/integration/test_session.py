import uuid

import sqlalchemy as sa

from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Tenant, User


def _create_tenant(name: str) -> uuid.UUID:
    with SessionLocal() as session:
        tenant = Tenant(name=name, keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30)
        session.add(tenant)
        session.commit()
        return tenant.id


def test_tenant_scoped_session_sets_and_scopes_tenant_context():
    tenant_id = _create_tenant("Tenant A")

    with tenant_scoped_session(tenant_id) as session:
        user = User(
            tenant_id=tenant_id,
            keycloak_subject="subject-1",
            email="doc@example.com",
            role="doctor",
        )
        session.add(user)

    with tenant_scoped_session(tenant_id) as session:
        rows = session.execute(sa.select(User).where(User.tenant_id == tenant_id)).scalars().all()
        assert len(rows) == 1


def test_no_tenant_context_returns_zero_rows():
    tenant_id = _create_tenant("Tenant B")

    with tenant_scoped_session(tenant_id) as session:
        user = User(
            tenant_id=tenant_id,
            keycloak_subject="subject-2",
            email="doc2@example.com",
            role="doctor",
        )
        session.add(user)

    with SessionLocal() as session:
        rows = session.execute(sa.select(User).where(User.tenant_id == tenant_id)).scalars().all()
        assert rows == []


def test_wrong_tenant_context_returns_zero_rows():
    tenant_a = _create_tenant("Tenant C")
    tenant_b = _create_tenant("Tenant D")

    with tenant_scoped_session(tenant_a) as session:
        user = User(
            tenant_id=tenant_a,
            keycloak_subject="subject-3",
            email="doc3@example.com",
            role="doctor",
        )
        session.add(user)

    with tenant_scoped_session(tenant_b) as session:
        rows = session.execute(sa.select(User).where(User.tenant_id == tenant_a)).scalars().all()
        assert rows == []
