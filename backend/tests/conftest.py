import atexit
import os
import tempfile

import pytest
import sqlalchemy as sa

os.environ["ENVIRONMENT"] = "test"
os.environ["OUTPUT_GUARD_ENABLED"] = "true"
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://chatgpt_proxy:change-me@localhost:5432/chatgpt_proxy",
)
os.environ.setdefault("APP_RUNTIME_PASSWORD", "change-me-app-runtime")
os.environ.setdefault("APP_OPS_PASSWORD", "change-me-app-ops")
os.environ.setdefault("WARM_REFERENCE_DATA_ON_STARTUP", "false")

# Created at import time (not as a fixture) because MASTER_KEY_PATH must be set before
# `app.config` is imported below. `delete=False` is required so the file survives being
# closed here, so it is unlinked explicitly at interpreter exit instead of being leaked
# into the system temp directory on every test run.
_master_key_file = tempfile.NamedTemporaryFile(delete=False)
_master_key_file.write(os.urandom(32))
_master_key_file.close()
os.environ.setdefault("MASTER_KEY_PATH", _master_key_file.name)


@atexit.register
def _remove_master_key_file() -> None:
    try:
        os.unlink(_master_key_file.name)
    except OSError:
        pass

from app.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.auth.dependencies import get_keycloak_admin_client

    get_settings.cache_clear()
    get_keycloak_admin_client.cache_clear()
    yield
    get_settings.cache_clear()
    get_keycloak_admin_client.cache_clear()


@pytest.fixture(scope="session")
def db_engine():
    engine = sa.create_engine(get_settings().database_url)
    yield engine
    engine.dispose()


def grant_app_entitlement(tenant_id, app_key: str = "anonymization") -> None:
    """Test-only stand-in for the ops team granting an entitlement through the
    Django admin (see ops_admin/). Uses the plain admin DB connection
    (get_settings().database_url), not app_runtime -- app_runtime is
    deliberately read-only on tenant_app_entitlements in production (migration
    0007), so integration tests that exercise entitlement-gated endpoints
    (chat.py, conversations.py's create route) need this to make a freshly
    created test tenant behave like a real, ops-entitled one.
    """
    engine = sa.create_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO tenant_app_entitlements (id, tenant_id, app_id, granted_by) "
                "SELECT gen_random_uuid(), :tenant_id, id, 'test-fixture' FROM apps WHERE key = :app_key "
                "ON CONFLICT (tenant_id, app_id) DO UPDATE SET revoked_at = NULL"
            ),
            {"tenant_id": str(tenant_id), "app_key": app_key},
        )
    engine.dispose()


def revoke_app_entitlement(tenant_id, app_key: str = "anonymization") -> None:
    """Test-only counterpart to grant_app_entitlement(), simulating an ops
    revoke via the Django admin (set revoked_at, never deleted -- see
    catalog/admin.py in ops_admin/)."""
    engine = sa.create_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE tenant_app_entitlements SET revoked_at = now() "
                "WHERE tenant_id = :tenant_id AND app_id = (SELECT id FROM apps WHERE key = :app_key)"
            ),
            {"tenant_id": str(tenant_id), "app_key": app_key},
        )
    engine.dispose()
