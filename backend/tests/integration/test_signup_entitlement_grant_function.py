import uuid

import pytest
import sqlalchemy as sa

from app.config import get_settings
from app.db.repositories.tenant_repository import TenantRepository
from app.db.session import SessionLocal
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


@pytest.fixture
def test_tenant_id():
    """Provisions a bare tenant directly via TenantRepository -- the same
    setup as test_tenant_repository.py's tests -- but unlike that file, tears
    it back down afterward. These tests also write a tenant_app_entitlements
    row (that's the function under test), and TenantRepository.create()
    itself already inserts a tenant_keys row and a tenant-wide "anonymization"
    tenant_app_assignments row (base_app lookup in tenant_repository.py), so
    all three plus the tenants row itself need cleanup or repeated runs of
    this suite would leave orphaned rows and eventually collide on the
    uq_tenant_app_assignments_tenant_app_tenant_wide index.

    Cleanup mirrors scripts/provision_e2e_tenant.py's cleanup(): those three
    child tables plus `tenants` are deleted via a plain admin-engine
    connection (get_settings().database_url, the migration-owner credential),
    not through SessionLocal/app_runtime -- app_runtime is SELECT-only on
    tenant_app_entitlements by design (migration 0007), so it cannot delete
    that row itself, and the admin engine handles all four deletes uniformly
    the same way the script's own cleanup() does for tenant_app_entitlements/
    tenant_keys/tenants.
    """
    key_provider = FileSecretKeyProvider(get_settings().master_key_path)
    tenant_id = uuid.uuid4()
    with SessionLocal() as session:
        TenantRepository(session, key_provider).create(
            name="Test Signup Praxis",
            keycloak_realm=f"test-{tenant_id.hex[:8]}",
            retention_days=30,
            tenant_id=tenant_id,
        )
        session.commit()

    yield tenant_id

    engine = sa.create_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM tenant_app_entitlements WHERE tenant_id = :tid"),
            {"tid": str(tenant_id)},
        )
        connection.execute(
            sa.text("DELETE FROM tenant_app_assignments WHERE tenant_id = :tid"),
            {"tid": str(tenant_id)},
        )
        connection.execute(
            sa.text("DELETE FROM tenant_keys WHERE tenant_id = :tid"),
            {"tid": str(tenant_id)},
        )
        connection.execute(
            sa.text("DELETE FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    engine.dispose()


def test_grant_default_entitlement_on_signup_inserts_exactly_one_row(test_tenant_id):
    tenant_id = test_tenant_id

    with SessionLocal() as session:
        session.execute(
            sa.text("SELECT grant_default_entitlement_on_signup(:tid)"), {"tid": str(tenant_id)}
        )
        session.commit()

    # Verify via a direct migration-owner connection (this check, not the
    # function call itself, is allowed to use the elevated credential --
    # it's test-only verification, not request-serving code).
    engine = sa.create_engine(get_settings().database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT a.key FROM tenant_app_entitlements e JOIN apps a ON a.id = e.app_id "
                "WHERE e.tenant_id = :tid AND e.revoked_at IS NULL"
            ),
            {"tid": str(tenant_id)},
        ).fetchall()
    engine.dispose()
    assert [r[0] for r in rows] == ["anonymization"]


def test_grant_default_entitlement_on_signup_is_idempotent_on_reentitlement(test_tenant_id):
    # Calling it twice for the same tenant must not raise or duplicate the
    # row -- mirrors the ON CONFLICT ... DO UPDATE pattern used elsewhere
    # for this exact table (see provision_e2e_tenant.py's own grant SQL).
    tenant_id = test_tenant_id

    with SessionLocal() as session:
        session.execute(
            sa.text("SELECT grant_default_entitlement_on_signup(:tid)"), {"tid": str(tenant_id)}
        )
        session.commit()

    with SessionLocal() as session:
        session.execute(
            sa.text("SELECT grant_default_entitlement_on_signup(:tid)"), {"tid": str(tenant_id)}
        )
        session.commit()

    engine = sa.create_engine(get_settings().database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT a.key FROM tenant_app_entitlements e JOIN apps a ON a.id = e.app_id "
                "WHERE e.tenant_id = :tid AND e.revoked_at IS NULL"
            ),
            {"tid": str(tenant_id)},
        ).fetchall()
    engine.dispose()
    assert [r[0] for r in rows] == ["anonymization"]


def test_app_runtime_role_can_call_the_function_but_still_cannot_insert_directly():
    # Regression guard for the actual security property this migration
    # exists for: app_runtime has EXECUTE on the function (proven by the
    # two tests above using the normal app-scoped SessionLocal/app_runtime
    # connection) but a direct INSERT into tenant_app_entitlements through
    # that same connection must still fail. No tenant fixture needed here --
    # the INSERT is rejected by the role's SELECT-only grant (migration 0007)
    # before any foreign-key/RLS check on a real tenant_id would even run.
    tenant_id = uuid.uuid4()

    with SessionLocal() as session:
        with pytest.raises(sa.exc.DBAPIError):
            session.execute(
                sa.text(
                    "INSERT INTO tenant_app_entitlements (id, tenant_id, app_id, granted_by) "
                    "VALUES (gen_random_uuid(), :tid, (SELECT id FROM apps LIMIT 1), 'test')"
                ),
                {"tid": str(tenant_id)},
            )
            session.commit()
