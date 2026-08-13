import uuid

import sqlalchemy as sa

from app.config import get_settings
from app.models import Tenant, User


def _create_tenant_and_user(db_engine) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    with db_engine.begin() as conn:
        conn.execute(
            sa.insert(Tenant).values(
                id=tenant_id,
                name="Clinic",
                keycloak_realm=f"realm-{tenant_id}",
                retention_days=30,
            )
        )
        conn.execute(
            sa.insert(User).values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                keycloak_subject="sub",
                email="doc@example.com",
                role="doctor",
            )
        )
    return tenant_id


def test_app_runtime_role_is_not_superuser_and_cannot_bypass_rls(db_engine):
    with db_engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'app_runtime'")
        ).one()
        assert row.rolsuper is False
        assert row.rolbypassrls is False


def test_app_runtime_query_with_no_tenant_context_returns_zero_rows_not_an_error(db_engine):
    tenant_id = _create_tenant_and_user(db_engine)
    app_engine = sa.create_engine(get_settings().app_database_url)
    try:
        with app_engine.connect() as conn:
            rows = conn.execute(sa.select(User).where(User.tenant_id == tenant_id)).fetchall()
            assert rows == []
    finally:
        app_engine.dispose()


def test_app_runtime_query_with_correct_tenant_context_returns_the_row(db_engine):
    tenant_id = _create_tenant_and_user(db_engine)
    app_engine = sa.create_engine(get_settings().app_database_url)
    try:
        with app_engine.connect() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            rows = conn.execute(sa.select(User).where(User.tenant_id == tenant_id)).fetchall()
            assert len(rows) == 1
    finally:
        app_engine.dispose()
