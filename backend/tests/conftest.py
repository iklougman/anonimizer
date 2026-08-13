import os
import tempfile

import pytest

os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://chatgpt_proxy:change-me@localhost:5432/chatgpt_proxy",
)

_master_key_file = tempfile.NamedTemporaryFile(delete=False)
_master_key_file.write(os.urandom(32))
_master_key_file.close()
os.environ.setdefault("MASTER_KEY_PATH", _master_key_file.name)

from app.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
