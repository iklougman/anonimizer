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


def test_app_runtime_pooled_connection_reused_after_committed_tenant_context_still_returns_zero_rows(
    db_engine,
):
    """Regression test for a pooled-connection GUC-reset gap.

    Postgres does not revert a custom GUC like ``app.current_tenant_id`` to
    NULL/unset once a transaction that called ``set_config(..., true)`` on it
    commits -- it reverts to the empty string ''. SQLAlchemy's connection
    pool reuses the same physical connection across separate logical
    connections/requests, so a later request that never sets a tenant
    context at all can inherit '' left over from an earlier request that
    did. ``''::uuid`` raises ``InvalidTextRepresentation`` unless the RLS
    policy guards against it with ``nullif(..., '')`` before the cast.

    ``pool_size=1, max_overflow=0`` forces both connections below to be the
    exact same physical Postgres connection.
    """
    tenant_id = _create_tenant_and_user(db_engine)
    app_engine = sa.create_engine(
        get_settings().app_database_url, pool_size=1, max_overflow=0
    )
    try:
        # "Request" 1: sets tenant context via set_config(..., true) and
        # commits, exactly like tenant_scoped_session would.
        with app_engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            rows = conn.execute(sa.select(User).where(User.tenant_id == tenant_id)).fetchall()
            assert len(rows) == 1

        # "Request" 2: reuses the same pooled physical connection (pool_size=1
        # guarantees it) but sets no tenant context at all. Must return zero
        # rows, not raise InvalidTextRepresentation from casting '' to uuid.
        with app_engine.connect() as conn:
            rows = conn.execute(sa.select(User).where(User.tenant_id == tenant_id)).fetchall()
            assert rows == []
    finally:
        app_engine.dispose()
