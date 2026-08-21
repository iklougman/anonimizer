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
