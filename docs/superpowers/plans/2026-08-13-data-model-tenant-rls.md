# Data Model & Tenant RLS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the full Postgres data model (all 8 tables from the
design spec), enforce tenant isolation with Postgres Row-Level Security
plus a mandatory-`tenant_id` repository layer, and implement the Token
Vault (encryption/decryption of sensitive values) end to end, so the
backend has a working, tested persistence layer ready for the pipeline and
auth plans to build on.

**Architecture:** SQLAlchemy 2.0 (sync) models + one Alembic migration
create the schema; a second Alembic migration enables and forces RLS with
a policy per tenant-scoped table; a `tenant_scoped_session(tenant_id)`
context manager sets the RLS session variable per unit of work;
repositories built on top of it take `tenant_id` as a mandatory first
argument; a `KeyProvider` (file-secret backed) wraps/unwraps per-tenant
DEKs, and a `TokenVault` uses it to AES-GCM encrypt/decrypt
`token_mappings.encrypted_value`, with authorization enforced by scoping
every lookup to `(tenant_id, conversation_id, token)`.

**Tech Stack:** SQLAlchemy 2.0 (sync), psycopg3, Alembic, `cryptography`
(AES-GCM + AES-KW), pytest against a real Postgres 16 (no SQLite — RLS
can't be tested without real Postgres).

## Global Constraints

- SQLAlchemy 2.0 sync (`Session`, not `AsyncSession`) — matches the
  existing sync FastAPI endpoints.
- Driver is `psycopg[binary]` (psycopg3); migrations run via Alembic with
  a sync `env.py`.
- New backend dependencies (main, not dev — the app needs them at
  runtime): `sqlalchemy>=2.0,<2.1`, `psycopg[binary]>=3.2,<4`,
  `alembic>=1.13,<2`, `cryptography>=43,<44`.
- Primary keys are application-generated `uuid.uuid4()`, Python-side
  default.
- `Settings.database_url: str`, `Settings.master_key_path: str`, and
  `Settings.app_runtime_password: str` are all required with no default —
  missing config crashes startup (fail-closed, ADR-0020), matching the
  existing `environment` field's pattern.
- **Two Postgres roles, not one** (added as Task 4b after Task 5 found
  the original single-role design didn't work — see the amended design
  spec §4, §8): `database_url` connects as the admin/superuser role and
  is used **only** for running migrations. The application itself —
  every repository, the Token Vault, `app/db/session.py` — connects as a
  separate restricted `app_runtime` role (`NOSUPERUSER NOBYPASSRLS`, DML
  grants only) via `Settings.app_database_url`, a property derived from
  `database_url` with the role swapped in. `FORCE ROW LEVEL SECURITY` on
  its own does nothing for a superuser connection — Postgres exempts
  superusers from RLS unconditionally, `FORCE` or not — so the restricted
  role is what makes `FORCE` (below) meaningful at all.
- Every tenant-scoped table (every table except `tenants`) gets
  `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`, `... FORCE ROW LEVEL
  SECURITY`, and a policy `USING (tenant_id =
  nullif(current_setting('app.current_tenant_id', true), '')::uuid)` —
  `missing_ok=true` alone only covers a connection that never set the GUC;
  `nullif(..., '')` is also required because Postgres reverts a
  session-local GUC to `''` (not unset) after it's been used once on a
  pooled connection, and `''::uuid` raises rather than filtering to zero
  rows. Both a session with no tenant context and a pooled connection
  carrying a stale empty GUC from an earlier request must fail closed the
  same way.
- Every repository method's first parameter is `tenant_id`, and every
  query includes it explicitly — redundant with RLS by design (ADR-0011:
  "neither layer alone is trusted as sufficient").
- `token_mappings` has a unique constraint on
  `(tenant_id, conversation_id, token)` (ADR-0009 — tokens are never
  reused across conversations).
- Tokens are `{ENTITY_TYPE}_{5-byte hex suffix}` via `secrets.token_hex`,
  never sequential (ADR-0009).
- `privacy_gateway/` (including `token_vault/`) never imports `httpx`,
  `requests`, `aiohttp`, `urllib3`, `socket`, `urllib`, `http`, `ftplib`,
  `smtplib`, or `telnetlib` — enforced by the existing import-linter
  contract in `backend/pyproject.toml` (ADR-0001).
- CI's backend job must run migrations against a real `postgres:16-alpine`
  service container before `pytest` (design doc §9, ADR-0011's CI
  requirement).
- Local dev assumes `docker compose up -d postgres` is running and
  reachable at `localhost:5432` with the credentials from `.env`
  (`chatgpt_proxy` / `change-me` / `chatgpt_proxy` by default, per
  `.env.example`).

---

## Task 1: Backend dependencies & fail-closed `database_url`/`master_key_path` settings

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/config.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings.database_url: str`, `Settings.master_key_path: str`
  (both required, no default), consumed by every later task's DB/vault
  code via `get_settings()`.

- [ ] **Step 1: Add DB/crypto dependencies to `backend/pyproject.toml`**

Change the `dependencies` list (in `[project]`) to:

```toml
dependencies = [
    "fastapi>=0.115,<0.116",
    "uvicorn[standard]>=0.32,<0.33",
    "pydantic>=2.9,<3",
    "pydantic-settings>=2.6,<3",
    "sqlalchemy>=2.0,<2.1",
    "psycopg[binary]>=3.2,<4",
    "alembic>=1.13,<2",
    "cryptography>=43,<44",
]
```

Leave `[project.optional-dependencies]`, `[build-system]`,
`[tool.setuptools.packages.find]`, `[tool.pytest.ini_options]`,
`[tool.ruff]`, and `[tool.importlinter]` sections unchanged.

- [ ] **Step 2: Install the new dependencies**

Run: `cd backend && pip install -e ".[dev]"`
Expected: install succeeds, `sqlalchemy`, `psycopg`, `alembic`, and
`cryptography` appear in `pip list`.

- [ ] **Step 3: Write the failing tests**

Replace `backend/tests/unit/test_config.py` in full with:

```python
import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_requires_environment_explicitly(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_requires_database_url_explicitly(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_requires_master_key_path_explicitly(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.delenv("MASTER_KEY_PATH", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_defaults_are_secure(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    settings = Settings(_env_file=None)
    assert settings.debug is False
    assert settings.cors_allowed_origins == []


def test_settings_rejects_unknown_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_ignores_unrelated_env_vars(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("POSTGRES_USER", "chatgpt_proxy")
    monkeypatch.setenv("POSTGRES_PASSWORD", "change-me")
    monkeypatch.setenv("POSTGRES_DB", "chatgpt_proxy")
    monkeypatch.setenv("KEYCLOAK_ADMIN", "admin")
    monkeypatch.setenv("KEYCLOAK_ADMIN_PASSWORD", "change-me")
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
```

- [ ] **Step 4: Run the tests to verify the new ones fail**

Run: `cd backend && pytest tests/unit/test_config.py -v`
Expected: `test_settings_requires_database_url_explicitly` and
`test_settings_requires_master_key_path_explicitly` FAIL (no
`ValidationError` raised, since the fields don't exist yet); the other
four pass unchanged.

- [ ] **Step 5: Add the fields to `backend/app/config.py`**

Replace the file in full with:

```python
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"]
    log_level: str = "INFO"
    debug: bool = False
    cors_allowed_origins: list[str] = Field(default_factory=list)
    database_url: str
    master_key_path: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 6: Run the tests to verify they all pass**

Run: `cd backend && pytest tests/unit/test_config.py -v`
Expected: PASS (6 passed).

- [ ] **Step 7: Update `backend/tests/conftest.py` so the rest of the suite has valid settings**

Replace the file in full with:

```python
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
```

This makes `DATABASE_URL` default to the credentials in `.env.example`
(overridable via a real env var, e.g. in CI) and generates a throwaway
master key file so any test importing `app.main` (which calls
`get_settings()` at module load) doesn't crash — tests that care about a
*specific* master key content use their own `tmp_path` fixture instead.

- [ ] **Step 8: Run the full backend test suite to confirm nothing else broke**

Run: `cd backend && pytest -v`
Expected: all tests pass (existing `test_health.py` and the updated
`test_config.py`).

- [ ] **Step 9: Commit**

```bash
git add backend/pyproject.toml backend/app/config.py backend/tests/conftest.py backend/tests/unit/test_config.py
git commit -m "feat: require database_url and master_key_path, add DB/crypto dependencies"
```

---

## Task 2: SQLAlchemy models for all 8 tables

**Files:**
- Create: `backend/app/models/base.py`
- Create: `backend/app/models/tenant.py`
- Create: `backend/app/models/user.py`
- Create: `backend/app/models/conversation.py`
- Create: `backend/app/models/message.py`
- Create: `backend/app/models/tenant_key.py`
- Create: `backend/app/models/token_mapping.py`
- Create: `backend/app/models/audit_event.py`
- Create: `backend/app/models/llm_request.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/unit/test_models.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Base` (declarative base) and ORM classes `Tenant`, `User`,
  `Conversation`, `Message`, `TenantKey`, `TokenMapping`, `AuditEvent`,
  `LLMRequest`, importable from `app.models`. Every model with a
  `tenant_id` column exposes it as `Mapped[uuid.UUID]`. Used by every
  later task in this plan.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/test_models.py`:

```python
from app.models import Base


def test_all_tables_registered():
    expected = {
        "tenants",
        "users",
        "conversations",
        "messages",
        "token_mappings",
        "tenant_keys",
        "audit_events",
        "llm_requests",
    }
    assert set(Base.metadata.tables.keys()) == expected


def test_tenant_scoped_tables_have_tenant_id_column():
    tenant_scoped = {
        "users",
        "conversations",
        "messages",
        "token_mappings",
        "tenant_keys",
        "audit_events",
        "llm_requests",
    }
    for table_name in tenant_scoped:
        table = Base.metadata.tables[table_name]
        assert "tenant_id" in table.columns, f"{table_name} missing tenant_id"


def test_token_mappings_unique_scope_constraint():
    table = Base.metadata.tables["token_mappings"]
    unique_cols = {
        tuple(col.name for col in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("tenant_id", "conversation_id", "token") in unique_cols
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'Base' from 'app.models'`.

- [ ] **Step 3: Create `backend/app/models/base.py`**

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

- [ ] **Step 4: Create `backend/app/models/tenant.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    keycloak_realm: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

- [ ] **Step 5: Create `backend/app/models/user.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "keycloak_subject", name="uq_users_tenant_keycloak_subject"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    keycloak_subject: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

- [ ] **Step 6: Create `backend/app/models/conversation.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 7: Create `backend/app/models/message.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    sanitized_content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

- [ ] **Step 8: Create `backend/app/models/tenant_key.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class TenantKey(Base):
    __tablename__ = "tenant_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "key_version", name="uq_tenant_keys_tenant_version"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

- [ ] **Step 9: Create `backend/app/models/token_mapping.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class TokenMapping(Base):
    __tablename__ = "token_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "conversation_id", "token", name="uq_token_mappings_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    token: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dek_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenant_keys.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 10: Create `backend/app/models/audit_event.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    token: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 11: Create `backend/app/models/llm_request.py`**

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class LLMRequest(Base):
    __tablename__ = "llm_requests"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    sanitized_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    sanitized_response: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

- [ ] **Step 12: Wire them all up in `backend/app/models/__init__.py`**

```python
from app.models.audit_event import AuditEvent
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.llm_request import LLMRequest
from app.models.message import Message
from app.models.tenant import Tenant
from app.models.tenant_key import TenantKey
from app.models.token_mapping import TokenMapping
from app.models.user import User

__all__ = [
    "AuditEvent",
    "Base",
    "Conversation",
    "LLMRequest",
    "Message",
    "Tenant",
    "TenantKey",
    "TokenMapping",
    "User",
]
```

- [ ] **Step 13: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_models.py -v`
Expected: PASS (3 passed).

- [ ] **Step 14: Commit**

```bash
git add backend/app/models
git commit -m "feat: add SQLAlchemy models for all 8 data-model tables"
```

---

## Task 3: Alembic setup & initial schema migration

**Files:**
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/alembic/versions/0001_initial_schema.py`

**Interfaces:**
- Consumes: `Base` (and all registered models) from Task 2;
  `get_settings().database_url` from Task 1.
- Produces: all 8 tables existing in the target Postgres database (no RLS
  yet — that's Task 4). `alembic upgrade head` / `alembic downgrade base`
  as the commands every later task's tests assume have already been run.

**Prerequisite:** `docker compose up -d postgres` running locally
(`localhost:5432`, credentials from `.env`).

- [ ] **Step 1: Create `backend/alembic.ini`**

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 2: Create `backend/alembic/env.py`**

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Create `backend/alembic/script.py.mako`**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Create `backend/alembic/versions/0001_initial_schema.py`**

```python
"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("keycloak_realm", sa.String(), nullable=False, unique=True),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("keycloak_subject", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "keycloak_subject", name="uq_users_tenant_keycloak_subject"),
    )

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("sanitized_content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "tenant_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "key_version", name="uq_tenant_keys_tenant_version"),
    )

    op.create_table(
        "token_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("encrypted_value", sa.LargeBinary(), nullable=False),
        sa.Column("dek_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenant_keys.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "conversation_id", "token", name="uq_token_mappings_scope"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "llm_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("sanitized_prompt", sa.Text(), nullable=False),
        sa.Column("sanitized_response", sa.Text(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("llm_requests")
    op.drop_table("audit_events")
    op.drop_table("token_mappings")
    op.drop_table("tenant_keys")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("users")
    op.drop_table("tenants")
```

- [ ] **Step 5: Apply the migration**

Run:
```bash
cd backend
export ENVIRONMENT=development
export DATABASE_URL=postgresql+psycopg://chatgpt_proxy:change-me@localhost:5432/chatgpt_proxy
export MASTER_KEY_PATH=/tmp/dev-master.key
head -c 32 /dev/urandom > /tmp/dev-master.key
alembic upgrade head
```
Expected: `Running upgrade -> 0001, initial schema` with no errors.

- [ ] **Step 6: Verify all 8 tables exist**

Run: `docker compose exec postgres psql -U chatgpt_proxy -d chatgpt_proxy -c '\dt'`
Expected: lists `tenants`, `users`, `conversations`, `messages`,
`tenant_keys`, `token_mappings`, `audit_events`, `llm_requests`.

- [ ] **Step 7: Verify the migration is reversible**

Run:
```bash
cd backend
alembic downgrade base
docker compose exec postgres psql -U chatgpt_proxy -d chatgpt_proxy -c '\dt'
alembic upgrade head
```
Expected: after `downgrade base`, `\dt` shows no application tables
(only `alembic_version`); after `upgrade head` again, all 8 tables are
back.

- [ ] **Step 8: Commit**

```bash
git add backend/alembic.ini backend/alembic/env.py backend/alembic/script.py.mako backend/alembic/versions/0001_initial_schema.py
git commit -m "feat: add Alembic and the initial schema migration"
```

---

## Task 4: Row-Level Security migration + CI schema-migration test

**Files:**
- Create: `backend/alembic/versions/0002_rls_policies.py`
- Create: `backend/tests/conftest.py` (modify — add `db_engine` fixture)
- Test: `backend/tests/privacy_invariants/test_rls.py`

**Interfaces:**
- Consumes: tables created by Task 3's migration.
- Produces: `db_engine` pytest fixture (session-scoped `sqlalchemy.Engine`
  bound to `get_settings().database_url`), reused by every later
  integration/privacy-invariant test that needs a raw connection.

**Prerequisite:** Task 3's migration applied (`alembic upgrade head`
already run).

- [ ] **Step 1: Add the `db_engine` fixture to `backend/tests/conftest.py`**

Add this import and fixture at the end of the existing file (after the
`_clear_settings_cache` fixture):

```python
import sqlalchemy as sa


@pytest.fixture(scope="session")
def db_engine():
    engine = sa.create_engine(get_settings().database_url)
    yield engine
    engine.dispose()
```

The full file is now:

```python
import os
import tempfile

import pytest
import sqlalchemy as sa

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


@pytest.fixture(scope="session")
def db_engine():
    engine = sa.create_engine(get_settings().database_url)
    yield engine
    engine.dispose()
```

- [ ] **Step 2: Write the failing test**

`backend/tests/privacy_invariants/test_rls.py`:

```python
import sqlalchemy as sa

TENANT_SCOPED_TABLES = [
    "users",
    "conversations",
    "messages",
    "tenant_keys",
    "token_mappings",
    "audit_events",
    "llm_requests",
]


def test_tenant_scoped_tables_have_rls_enabled_and_forced(db_engine):
    with db_engine.connect() as conn:
        for table in TENANT_SCOPED_TABLES:
            row = conn.execute(
                sa.text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = :table"
                ),
                {"table": table},
            ).one()
            assert row.relrowsecurity is True, f"{table} does not have RLS enabled"
            assert row.relforcerowsecurity is True, f"{table} does not have RLS forced"


def test_tenant_scoped_tables_have_a_policy(db_engine):
    with db_engine.connect() as conn:
        for table in TENANT_SCOPED_TABLES:
            count = conn.execute(
                sa.text("SELECT count(*) FROM pg_policies WHERE tablename = :table"),
                {"table": table},
            ).scalar_one()
            assert count >= 1, f"{table} has no RLS policy"


def test_tenants_table_has_no_rls(db_engine):
    with db_engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT relrowsecurity FROM pg_class WHERE relname = 'tenants'")
        ).one()
        assert row.relrowsecurity is False
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend && pytest tests/privacy_invariants/test_rls.py -v`
Expected: FAIL — `relrowsecurity` is `False` for every tenant-scoped table
(migration 0002 doesn't exist yet).

- [ ] **Step 4: Create `backend/alembic/versions/0002_rls_policies.py`**

```python
"""enable row level security on tenant-scoped tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-13

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_SCOPED_TABLES = [
    "users",
    "conversations",
    "messages",
    "tenant_keys",
    "token_mappings",
    "audit_events",
    "llm_requests",
]


def upgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid)
            """
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
```

- [ ] **Step 5: Apply the migration**

Run:
```bash
cd backend
export ENVIRONMENT=development
export DATABASE_URL=postgresql+psycopg://chatgpt_proxy:change-me@localhost:5432/chatgpt_proxy
export MASTER_KEY_PATH=/tmp/dev-master.key
alembic upgrade head
```
Expected: `Running upgrade 0001 -> 0002, enable row level security...`

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd backend && pytest tests/privacy_invariants/test_rls.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Commit**

```bash
git add backend/tests/conftest.py backend/alembic/versions/0002_rls_policies.py backend/tests/privacy_invariants/test_rls.py
git commit -m "feat: enable and force RLS on every tenant-scoped table"
```

---

## Task 4b: Restricted `app_runtime` Postgres role (fixes RLS bypass)

> **Why this task exists:** it was not in the original plan. Task 5's
> implementer found that RLS (Task 4) has no effect at all — the app's DB
> role (`chatgpt_proxy`, from `POSTGRES_USER`, per the official Postgres
> Docker image) is a **superuser**, and Postgres unconditionally exempts
> superusers from Row-Level Security; `FORCE ROW LEVEL SECURITY` cannot
> change that. See the amended design spec §4/§8 for the full writeup.
> This task provisions a second, restricted role for the application's
> own connections and fixes a related fail-closed gap in the RLS policy
> (missing `missing_ok=true` on `current_setting`). Task 5 is re-briefed
> below to consume this task's `app_database_url` instead of
> `database_url`.

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/tests/unit/test_config.py`
- Modify: `backend/tests/conftest.py`
- Create: `backend/alembic/versions/0003_app_runtime_role.py`
- Test: `backend/tests/privacy_invariants/test_app_runtime_role.py`

**Interfaces:**
- Consumes: `Settings.database_url` (Task 1), `db_engine` fixture (Task
  4), `Tenant`/`User` models (Task 2).
- Produces: `Settings.app_runtime_password: str` (required, no default)
  and `Settings.app_database_url` (a property — not a settings field —
  derived from `database_url` with the role and password swapped to
  `app_runtime`/`app_runtime_password`), and a Postgres role `app_runtime`
  (`NOSUPERUSER NOBYPASSRLS`, `SELECT`/`INSERT`/`UPDATE`/`DELETE` on all 8
  tables). Task 5's `app/db/session.py` connects as this role via
  `get_settings().app_database_url`.

- [ ] **Step 1: Write the failing tests for the new setting**

Replace `backend/tests/unit/test_config.py` in full with:

```python
import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_requires_environment_explicitly(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_requires_database_url_explicitly(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_requires_master_key_path_explicitly(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.delenv("MASTER_KEY_PATH", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_requires_app_runtime_password_explicitly(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.delenv("APP_RUNTIME_PASSWORD", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_defaults_are_secure(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    settings = Settings(_env_file=None)
    assert settings.debug is False
    assert settings.cors_allowed_origins == []


def test_settings_rejects_unknown_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_ignores_unrelated_env_vars(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.setenv("POSTGRES_USER", "chatgpt_proxy")
    monkeypatch.setenv("POSTGRES_PASSWORD", "change-me")
    monkeypatch.setenv("POSTGRES_DB", "chatgpt_proxy")
    monkeypatch.setenv("KEYCLOAK_ADMIN", "admin")
    monkeypatch.setenv("KEYCLOAK_ADMIN_PASSWORD", "change-me")
    settings = Settings(_env_file=None)
    assert settings.environment == "development"


def test_app_database_url_derives_from_database_url_with_app_runtime_credentials(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://admin_user:admin_pw@dbhost:5432/mydb")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    settings = Settings(_env_file=None)
    assert settings.app_database_url == "postgresql+psycopg://app_runtime:runtime-secret@dbhost:5432/mydb"
```

- [ ] **Step 2: Run the tests to verify the new ones fail**

Run: `cd backend && pytest tests/unit/test_config.py -v`
Expected: FAIL — `test_settings_requires_app_runtime_password_explicitly`
(no such field yet, so nothing raises) and
`test_app_database_url_derives_from_database_url_with_app_runtime_credentials`
(`AttributeError: 'Settings' object has no attribute 'app_database_url'`)
fail; the rest pass unchanged.

- [ ] **Step 3: Add the field and derived property to `backend/app/config.py`**

Replace the file in full with:

```python
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"]
    log_level: str = "INFO"
    debug: bool = False
    cors_allowed_origins: list[str] = Field(default_factory=list)
    database_url: str
    master_key_path: str
    app_runtime_password: str

    @property
    def app_database_url(self) -> str:
        url = make_url(self.database_url).set(
            username="app_runtime", password=self.app_runtime_password
        )
        return url.render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`render_as_string(hide_password=False)` is required — plain `str(url)`
masks the password as `***` (SQLAlchemy's default safe-printing
behavior), which would silently produce an unusable connection string.

- [ ] **Step 4: Run the tests to verify they all pass**

Run: `cd backend && pytest tests/unit/test_config.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Update `backend/tests/conftest.py` so the rest of the suite has a valid `app_runtime_password`**

Add this line after the existing `DATABASE_URL` `setdefault` call:

```python
os.environ.setdefault("APP_RUNTIME_PASSWORD", "change-me-app-runtime")
```

The full file is now:

```python
import os
import tempfile

import pytest
import sqlalchemy as sa

os.environ["ENVIRONMENT"] = "test"
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://chatgpt_proxy:change-me@localhost:5432/chatgpt_proxy",
)
os.environ.setdefault("APP_RUNTIME_PASSWORD", "change-me-app-runtime")

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


@pytest.fixture(scope="session")
def db_engine():
    engine = sa.create_engine(get_settings().database_url)
    yield engine
    engine.dispose()
```

- [ ] **Step 6: Run the full backend test suite to confirm nothing else broke**

Run: `cd backend && pytest -v`
Expected: all tests pass (this uses the default `change-me-app-runtime`
password everywhere except the two tests above, which set their own
value via `monkeypatch`).

- [ ] **Step 7: Write the failing test for the role and the RLS fail-closed fix**

`backend/tests/privacy_invariants/test_app_runtime_role.py` — 3 tests as
written below. **Amended after implementation:** the file ends up with **4**
tests. A fourth,
`test_app_runtime_pooled_connection_reused_after_committed_tenant_context_still_returns_zero_rows`,
was added during the fix round for the pooled-connection GUC bug (a reused
pooled connection carries a stale `''` rather than an unset GUC, so the
policy needed the `nullif(..., '')` wrapper — see design spec §4/§8) and was
never folded back into this task's text.

```python
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
```

- [ ] **Step 8: Run the test to verify it fails**

Run: `cd backend && pytest tests/privacy_invariants/test_app_runtime_role.py -v`
Expected: FAIL —
`test_app_runtime_role_is_not_superuser_and_cannot_bypass_rls` fails with
`sqlalchemy.exc.NoResultFound` (the `app_runtime` role doesn't exist
yet); the other two fail with an authentication error (no such role to
connect as).

- [ ] **Step 9: Create `backend/alembic/versions/0003_app_runtime_role.py`**

```python
"""provision restricted app_runtime role; fix RLS policy to fail closed on missing context

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-13

"""
from typing import Sequence, Union

from alembic import op

from app.config import get_settings

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALL_TABLES = [
    "tenants",
    "users",
    "conversations",
    "messages",
    "tenant_keys",
    "token_mappings",
    "audit_events",
    "llm_requests",
]

TENANT_SCOPED_TABLES = [
    "users",
    "conversations",
    "messages",
    "tenant_keys",
    "token_mappings",
    "audit_events",
    "llm_requests",
]


def upgrade() -> None:
    escaped_password = get_settings().app_runtime_password.replace("'", "''")

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_runtime') THEN
                CREATE ROLE app_runtime LOGIN PASSWORD '{escaped_password}'
                    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            ELSE
                ALTER ROLE app_runtime WITH PASSWORD '{escaped_password}';
            END IF;
        END
        $$;
        """
    )

    op.execute("GRANT USAGE ON SCHEMA public TO app_runtime")
    for table in ALL_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_runtime")

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
            """
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid)
            """
        )

    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM app_runtime")
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_runtime")
    op.execute("DROP ROLE IF EXISTS app_runtime")
```

`upgrade()` reads the password via `get_settings().app_runtime_password`
rather than a new raw env-var read, so there is exactly one place
(`Settings`) that knows how the app's credentials are assembled. The role
creation is idempotent (`IF NOT EXISTS` / `ALTER ROLE ... WITH PASSWORD`
on the else branch) so re-running `alembic upgrade head` against a
database that already has the role just refreshes its password rather
than erroring.

**`nullif(..., '')` matters, not just `missing_ok=true`, once connections
are pooled** (found by Task 5's implementer): after `set_config(...,
true)` has been called once on a physical connection and committed,
Postgres reverts that GUC to the empty string `''` for the rest of that
connection's life — not to unset/`NULL`. A pooled `SessionLocal` reuses
physical connections across requests, so a later request that opens no
tenant context can still inherit `''` from an earlier request on the same
connection, and `''::uuid` raises rather than filtering to zero rows.
`nullif(current_setting(...), '')` converts that leftover `''` to `NULL`
before the cast, so "no context" is zero rows on every request,
regardless of pooled connection history — this is a correction to what
this task originally shipped, not a new requirement.

- [ ] **Step 10: Apply the migration**

Run:
```bash
cd backend
export ENVIRONMENT=development
export DATABASE_URL=postgresql+psycopg://chatgpt_proxy:change-me@localhost:5432/chatgpt_proxy
export MASTER_KEY_PATH=/tmp/dev-master.key
export APP_RUNTIME_PASSWORD=change-me-app-runtime
alembic upgrade head
```
Expected: `Running upgrade 0002 -> 0003, provision restricted app_runtime role...`

- [ ] **Step 11: Run the test to verify it passes**

Run: `cd backend && pytest tests/privacy_invariants/test_app_runtime_role.py -v`
Expected: PASS (3 passed as originally planned; **4 passed** in the final
state, after the pooled-connection regression test described in Step 7 was
added during the fix round).

- [ ] **Step 12: Run the full backend test suite**

Run: `cd backend && pytest -v`
Expected: all tests pass (the pre-existing RLS tests from Task 4 only
check `pg_class`/`pg_policies` state, which migration 0003 doesn't
change — same policies exist, just with `missing_ok=true` added).

- [ ] **Step 13: Commit**

```bash
git add backend/app/config.py backend/tests/unit/test_config.py backend/tests/conftest.py backend/alembic/versions/0003_app_runtime_role.py backend/tests/privacy_invariants/test_app_runtime_role.py
git commit -m "fix: provision restricted app_runtime role so FORCE ROW LEVEL SECURITY actually applies"
```

---

## Task 5: Tenant-scoped session helper

**Files:**
- Create: `backend/app/db/session.py`
- Test: `backend/tests/integration/test_session.py`

**Interfaces:**
- Consumes: `get_settings().app_database_url` (Task 4b — the restricted
  `app_runtime` role; **not** `database_url`, which is the admin/migration
  connection and must never be used for the application's own queries),
  ORM models `Tenant`, `User` (Task 2), applied migrations (Tasks 3–4b).
- Produces: `engine` (`sqlalchemy.Engine`), `SessionLocal`
  (`sessionmaker`), and `tenant_scoped_session(tenant_id: uuid.UUID) ->
  ContextManager[Session]` from `app.db.session`. Every repository and
  the Token Vault (Tasks 7–9) open sessions exclusively through these.

- [ ] **Step 1: Write the failing test**

`backend/tests/integration/test_session.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/integration/test_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db.session'`.

- [ ] **Step 3: Create `backend/app/db/session.py`**

```python
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().app_database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def tenant_scoped_session(tenant_id: uuid.UUID) -> Iterator[Session]:
    with SessionLocal() as session:
        session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
```

Note: `engine` binds to `app_database_url` (the restricted `app_runtime`
role from Task 4b), not `database_url` (the admin/migration role) — this
is what makes `FORCE ROW LEVEL SECURITY` actually apply. `set_config(...,
true)` is the parameter-bindable equivalent of `SET LOCAL` — Postgres
does not accept bind parameters in a literal `SET LOCAL x = $1`
statement (it raises a syntax error), so this is the standard safe
substitute with identical transaction-scoping semantics (`is_local =
true`).

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/integration/test_session.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/session.py backend/tests/integration/test_session.py
git commit -m "feat: add tenant_scoped_session helper enforcing the RLS session variable"
```

---

## Task 6: KeyProvider (envelope encryption primitive)

**Files:**
- Create: `backend/app/privacy_gateway/token_vault/key_provider.py`
- Test: `backend/tests/unit/test_key_provider.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure crypto primitive).
- Produces: `KeyProvider` protocol (`wrap_dek(raw_dek: bytes) -> bytes`,
  `unwrap_dek(wrapped_dek: bytes) -> bytes`) and `FileSecretKeyProvider`
  (constructed with `master_key_path: str`). Used by `TenantRepository`
  (Task 7) and `TokenVault` (Task 9).

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/test_key_provider.py`:

```python
import os

import pytest
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap

from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


@pytest.fixture
def master_key_path(tmp_path):
    path = tmp_path / "master.key"
    path.write_bytes(os.urandom(32))
    return str(path)


def test_wrap_and_unwrap_round_trip(master_key_path):
    provider = FileSecretKeyProvider(master_key_path)
    raw_dek = os.urandom(32)

    wrapped = provider.wrap_dek(raw_dek)
    unwrapped = provider.unwrap_dek(wrapped)

    assert unwrapped == raw_dek
    assert wrapped != raw_dek


def test_unwrap_fails_under_a_different_master_key(tmp_path):
    key_a = tmp_path / "a.key"
    key_a.write_bytes(os.urandom(32))
    key_b = tmp_path / "b.key"
    key_b.write_bytes(os.urandom(32))

    provider_a = FileSecretKeyProvider(str(key_a))
    provider_b = FileSecretKeyProvider(str(key_b))

    raw_dek = os.urandom(32)
    wrapped = provider_a.wrap_dek(raw_dek)

    with pytest.raises(InvalidUnwrap):
        provider_b.unwrap_dek(wrapped)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_key_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named
'app.privacy_gateway.token_vault.key_provider'`.

- [ ] **Step 3: Create `backend/app/privacy_gateway/token_vault/key_provider.py`**

```python
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap


class KeyProvider(Protocol):
    def wrap_dek(self, raw_dek: bytes) -> bytes: ...
    def unwrap_dek(self, wrapped_dek: bytes) -> bytes: ...


class FileSecretKeyProvider:
    def __init__(self, master_key_path: str) -> None:
        self._master_key = Path(master_key_path).read_bytes()

    def wrap_dek(self, raw_dek: bytes) -> bytes:
        return aes_key_wrap(self._master_key, raw_dek)

    def unwrap_dek(self, wrapped_dek: bytes) -> bytes:
        return aes_key_unwrap(self._master_key, wrapped_dek)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_key_provider.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the import-linter contract to confirm `token_vault/` is still network-isolated**

Run: `cd backend && lint-imports`
Expected: `Contracts: 2 kept, 0 broken.`

- [ ] **Step 6: Commit**

```bash
git add backend/app/privacy_gateway/token_vault/key_provider.py backend/tests/unit/test_key_provider.py
git commit -m "feat: add file-secret-backed KeyProvider for DEK wrap/unwrap"
```

---

## Task 7: Repository base class & `TenantRepository`

**Files:**
- Create: `backend/app/db/repositories/__init__.py`
- Create: `backend/app/db/repositories/base.py`
- Create: `backend/app/db/repositories/tenant_repository.py`
- Test: `backend/tests/integration/test_tenant_repository.py`

**Interfaces:**
- Consumes: `SessionLocal` (Task 5), `Tenant`/`TenantKey` models (Task 2),
  `KeyProvider` (Task 6).
- Produces: `BaseRepository(session: Session)` — the pattern every other
  repository subclasses. `TenantRepository(session: Session, key_provider:
  KeyProvider)` with `.create(name: str, keycloak_realm: str,
  retention_days: int) -> Tenant` and `.get(tenant_id: uuid.UUID) ->
  Tenant | None`. `TenantRepository` does **not** subclass
  `BaseRepository` — tenant creation happens before any tenant context
  exists, so it takes a plain `SessionLocal()` session, not a
  `tenant_scoped_session`. Used by every later task's test fixtures to
  set up a tenant.

- [ ] **Step 1: Create `backend/app/db/repositories/__init__.py`**

```python
```

(Empty file — matches the existing convention of explicit `__init__.py`
per package.)

- [ ] **Step 2: Create `backend/app/db/repositories/base.py`**

```python
from sqlalchemy.orm import Session


class BaseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
```

- [ ] **Step 3: Write the failing test**

`backend/tests/integration/test_tenant_repository.py`:

```python
import uuid

import sqlalchemy as sa

from app.db.repositories.tenant_repository import TenantRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import TenantKey
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def test_create_tenant_provisions_a_dek(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))

    with SessionLocal() as session:
        repo = TenantRepository(session, key_provider)
        tenant = repo.create(
            name="Test Clinic",
            keycloak_realm=f"realm-{uuid.uuid4()}",
            retention_days=30,
        )
        session.commit()
        tenant_id = tenant.id

    assert tenant_id is not None

    with tenant_scoped_session(tenant_id) as session:
        tenant_key = session.execute(
            sa.select(TenantKey).where(TenantKey.tenant_id == tenant_id)
        ).scalar_one()
        assert tenant_key.key_version == 1
        assert key_provider.unwrap_dek(tenant_key.wrapped_dek) is not None


def test_get_returns_none_for_unknown_tenant():
    with SessionLocal() as session:
        repo = TenantRepository(session, key_provider=None)
        assert repo.get(uuid.uuid4()) is None
```

Reading `tenant_key` back through `tenant_scoped_session(tenant_id)`
rather than continuing on the same raw `SessionLocal()` after commit is
deliberate, not incidental — see the note after Step 5 for why.

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd backend && pytest tests/integration/test_tenant_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named
'app.db.repositories.tenant_repository'`.

- [ ] **Step 5: Create `backend/app/db/repositories/tenant_repository.py`**

```python
import os
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Tenant, TenantKey
from app.privacy_gateway.token_vault.key_provider import KeyProvider


class TenantRepository:
    def __init__(self, session: Session, key_provider: KeyProvider) -> None:
        self.session = session
        self.key_provider = key_provider

    def create(self, name: str, keycloak_realm: str, retention_days: int) -> Tenant:
        tenant = Tenant(name=name, keycloak_realm=keycloak_realm, retention_days=retention_days)
        self.session.add(tenant)
        self.session.flush()

        self.session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant.id)},
        )

        raw_dek = os.urandom(32)
        wrapped_dek = self.key_provider.wrap_dek(raw_dek)
        tenant_key = TenantKey(tenant_id=tenant.id, wrapped_dek=wrapped_dek, key_version=1)
        self.session.add(tenant_key)
        self.session.flush()

        return tenant

    def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return self.session.get(Tenant, tenant_id)
```

`tenant_keys` is RLS-protected (Task 4b) — `WITH CHECK` rejects the
`INSERT` unless `app.current_tenant_id` is set for this transaction, even
though `create()` uses a plain `SessionLocal()` (no
`tenant_scoped_session`, since the tenant doesn't exist yet when the
method starts). Once `tenant.id` is known (after `flush()`), setting the
GUC with `set_config(..., true)` — the same transaction-local form
`tenant_scoped_session` itself uses — satisfies the policy for the
`tenant_key` insert that follows, and unwinds automatically at
commit/rollback exactly like every other transaction-local use of this
GUC. No pool-level cleanup and no session-scoped (`false`) `set_config`
are needed: transaction-local is sufficient for the insert, and it means
this method never risks leaving a real tenant id set on a connection
returned to the pool. The one consequence is that a plain `SessionLocal()`
query on the *same* session *after* `commit()` no longer sees the new
`tenant_key` row (the GUC has already unwound) — callers that need to
read back what they just created should open a fresh
`tenant_scoped_session(tenant.id)` instead, as the test above does.

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd backend && pytest tests/integration/test_tenant_repository.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add backend/app/db/repositories/__init__.py backend/app/db/repositories/base.py backend/app/db/repositories/tenant_repository.py backend/tests/integration/test_tenant_repository.py
git commit -m "feat: add TenantRepository, provisioning a DEK on tenant creation"
```

---

## Task 8: `UserRepository`, `ConversationRepository`, `MessageRepository`

**Files:**
- Create: `backend/app/db/repositories/user_repository.py`
- Create: `backend/app/db/repositories/conversation_repository.py`
- Create: `backend/app/db/repositories/message_repository.py`
- Test: `backend/tests/integration/test_repositories.py`

**Interfaces:**
- Consumes: `BaseRepository`, `tenant_scoped_session` (Tasks 5, 7),
  `User`/`Conversation`/`Message` models (Task 2), `TenantRepository`
  (Task 7, for test fixtures).
- Produces: `UserRepository.create(tenant_id, keycloak_subject, email,
  role) -> User`, `.get_by_keycloak_subject(tenant_id,
  keycloak_subject) -> User | None`;
  `ConversationRepository.create(tenant_id, user_id) -> Conversation`,
  `.get(tenant_id, conversation_id) -> Conversation | None`,
  `.list_for_user(tenant_id, user_id) -> list[Conversation]`;
  `MessageRepository.create(tenant_id, conversation_id, role,
  sanitized_content) -> Message`, `.list_for_conversation(tenant_id,
  conversation_id) -> list[Message]`. Used by Task 9's `TokenVault` tests
  to set up conversations.

- [ ] **Step 1: Write the failing test**

`backend/tests/integration/test_repositories.py`:

```python
import uuid

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.message_repository import MessageRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def _create_tenant(key_provider):
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic",
            keycloak_realm=f"realm-{uuid.uuid4()}",
            retention_days=30,
        )
        session.commit()
        return tenant.id


def test_user_conversation_message_round_trip(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))

    tenant_id = _create_tenant(key_provider)

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        )
        conversation = ConversationRepository(session).create(tenant_id, user.id)
        message = MessageRepository(session).create(
            tenant_id, conversation.id, role="user", sanitized_content="Hallo [PATIENT_AB12C]"
        )
        message_id = message.id
        conversation_id = conversation.id

    with tenant_scoped_session(tenant_id) as session:
        messages = MessageRepository(session).list_for_conversation(tenant_id, conversation_id)
        assert len(messages) == 1
        assert messages[0].id == message_id


def test_conversations_do_not_cross_tenants(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))

    tenant_a = _create_tenant(key_provider)
    tenant_b = _create_tenant(key_provider)

    with tenant_scoped_session(tenant_a) as session:
        user_a = UserRepository(session).create(
            tenant_a, keycloak_subject="sub-a", email="a@example.com", role="doctor"
        )
        ConversationRepository(session).create(tenant_a, user_a.id)
        user_a_id = user_a.id

    with tenant_scoped_session(tenant_b) as session:
        conversations = ConversationRepository(session).list_for_user(tenant_b, user_a_id)
        assert conversations == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/integration/test_repositories.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named
'app.db.repositories.user_repository'`.

- [ ] **Step 3: Create `backend/app/db/repositories/user_repository.py`**

```python
import uuid

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import User


class UserRepository(BaseRepository):
    def create(self, tenant_id: uuid.UUID, keycloak_subject: str, email: str, role: str) -> User:
        user = User(tenant_id=tenant_id, keycloak_subject=keycloak_subject, email=email, role=role)
        self.session.add(user)
        self.session.flush()
        return user

    def get_by_keycloak_subject(self, tenant_id: uuid.UUID, keycloak_subject: str) -> User | None:
        stmt = sa.select(User).where(
            User.tenant_id == tenant_id,
            User.keycloak_subject == keycloak_subject,
        )
        return self.session.execute(stmt).scalar_one_or_none()
```

- [ ] **Step 4: Create `backend/app/db/repositories/conversation_repository.py`**

```python
import uuid

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import Conversation


class ConversationRepository(BaseRepository):
    def create(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Conversation:
        conversation = Conversation(tenant_id=tenant_id, user_id=user_id)
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def get(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation | None:
        stmt = sa.select(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.id == conversation_id,
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_user(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Conversation]:
        stmt = sa.select(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.user_id == user_id,
        )
        return list(self.session.execute(stmt).scalars().all())
```

- [ ] **Step 5: Create `backend/app/db/repositories/message_repository.py`**

```python
import uuid

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import Message


class MessageRepository(BaseRepository):
    def create(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, role: str, sanitized_content: str
    ) -> Message:
        message = Message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role=role,
            sanitized_content=sanitized_content,
        )
        self.session.add(message)
        self.session.flush()
        return message

    def list_for_conversation(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> list[Message]:
        stmt = sa.select(Message).where(
            Message.tenant_id == tenant_id,
            Message.conversation_id == conversation_id,
        )
        return list(self.session.execute(stmt).scalars().all())
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd backend && pytest tests/integration/test_repositories.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add backend/app/db/repositories/user_repository.py backend/app/db/repositories/conversation_repository.py backend/app/db/repositories/message_repository.py backend/tests/integration/test_repositories.py
git commit -m "feat: add User, Conversation, and Message repositories"
```

---

## Task 9: `TokenVault`

**Files:**
- Create: `backend/app/privacy_gateway/token_vault/vault.py`
- Test: `backend/tests/privacy_invariants/test_token_vault.py`

**Interfaces:**
- Consumes: `tenant_scoped_session` (Task 5), `KeyProvider` (Task 6),
  `TenantKey`/`TokenMapping` models (Task 2), `TenantRepository`,
  `UserRepository`, `ConversationRepository` (Tasks 7–8, for test
  fixtures).
- Produces: `TokenVault(key_provider: KeyProvider)` with
  `.create_mapping(tenant_id, conversation_id, entity_type: str,
  original_value: str) -> str` (returns the token),
  `.resolve_token(tenant_id, conversation_id, token: str) -> str | None`,
  `.resolve_tokens(tenant_id, conversation_id, tokens: list[str]) ->
  dict[str, str]`, `.delete_mapping(tenant_id, conversation_id, token) ->
  None`, `.expire_mapping(tenant_id, conversation_id, token) -> None`.
  This is the concrete implementation the future detection/pipeline plan
  calls into.

- [ ] **Step 1: Write the failing test**

`backend/tests/privacy_invariants/test_token_vault.py`:

```python
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import TenantKey, TokenMapping
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault


@pytest.fixture
def key_provider(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    return FileSecretKeyProvider(str(master_key_path))


def _create_tenant_and_conversation(key_provider):
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub", email="doc@example.com", role="doctor"
        )
        conversation = ConversationRepository(session).create(tenant_id, user.id)
        conversation_id = conversation.id

    return tenant_id, conversation_id


def test_create_and_resolve_round_trip(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, conversation_id, "PATIENT", "Hans Müller")
    resolved = vault.resolve_token(tenant_id, conversation_id, token)

    assert resolved == "Hans Müller"
    assert token.startswith("PATIENT_")


def test_resolve_fails_for_wrong_tenant(key_provider):
    tenant_a, conversation_a = _create_tenant_and_conversation(key_provider)
    tenant_b, _ = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_a, conversation_a, "PATIENT", "Hans Müller")

    assert vault.resolve_token(tenant_b, conversation_a, token) is None


def test_resolve_fails_for_wrong_conversation(key_provider):
    tenant_id, conversation_a = _create_tenant_and_conversation(key_provider)
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-2", email="doc2@example.com", role="doctor"
        )
        other_conversation = ConversationRepository(session).create(tenant_id, user.id)
        conversation_b = other_conversation.id

    vault = TokenVault(key_provider)
    token = vault.create_mapping(tenant_id, conversation_a, "PATIENT", "Hans Müller")

    assert vault.resolve_token(tenant_id, conversation_b, token) is None


def test_token_uniqueness_within_tenant_and_conversation(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)

    with tenant_scoped_session(tenant_id) as session:
        dek_id = session.execute(
            sa.select(TenantKey.id).where(TenantKey.tenant_id == tenant_id)
        ).scalar_one()

        session.add(
            TokenMapping(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                token="PATIENT_AAAAA",
                entity_type="PATIENT",
                encrypted_value=b"x" * 28,
                dek_id=dek_id,
            )
        )

    with pytest.raises(IntegrityError):
        with tenant_scoped_session(tenant_id) as session:
            session.add(
                TokenMapping(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    token="PATIENT_AAAAA",
                    entity_type="PATIENT",
                    encrypted_value=b"y" * 28,
                    dek_id=dek_id,
                )
            )


def test_delete_mapping_removes_the_row(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, conversation_id, "PATIENT", "Hans Müller")
    vault.delete_mapping(tenant_id, conversation_id, token)

    assert vault.resolve_token(tenant_id, conversation_id, token) is None


def test_expire_mapping_soft_deletes(key_provider):
    tenant_id, conversation_id = _create_tenant_and_conversation(key_provider)
    vault = TokenVault(key_provider)

    token = vault.create_mapping(tenant_id, conversation_id, "PATIENT", "Hans Müller")
    vault.expire_mapping(tenant_id, conversation_id, token)

    assert vault.resolve_token(tenant_id, conversation_id, token) is None

    with tenant_scoped_session(tenant_id) as session:
        mapping = session.execute(
            sa.select(TokenMapping).where(TokenMapping.token == token)
        ).scalar_one()
        assert mapping.deleted_at is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/privacy_invariants/test_token_vault.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named
'app.privacy_gateway.token_vault.vault'`.

- [ ] **Step 3: Create `backend/app/privacy_gateway/token_vault/vault.py`**

```python
import os
import secrets
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from app.db.session import tenant_scoped_session
from app.models import TenantKey, TokenMapping
from app.privacy_gateway.token_vault.key_provider import KeyProvider

_NONCE_LENGTH = 12


class TokenVault:
    def __init__(self, key_provider: KeyProvider) -> None:
        self.key_provider = key_provider

    def create_mapping(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        entity_type: str,
        original_value: str,
    ) -> str:
        token = f"{entity_type}_{secrets.token_hex(5).upper()}"

        with tenant_scoped_session(tenant_id) as session:
            dek_row = self._active_dek_row(session, tenant_id)
            raw_dek = self.key_provider.unwrap_dek(dek_row.wrapped_dek)

            nonce = os.urandom(_NONCE_LENGTH)
            ciphertext = AESGCM(raw_dek).encrypt(nonce, original_value.encode("utf-8"), None)

            mapping = TokenMapping(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                token=token,
                entity_type=entity_type,
                encrypted_value=nonce + ciphertext,
                dek_id=dek_row.id,
            )
            session.add(mapping)

        return token

    def resolve_token(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, token: str
    ) -> str | None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, conversation_id, token)
            if mapping is None:
                return None

            dek_row = session.get(TenantKey, mapping.dek_id)
            raw_dek = self.key_provider.unwrap_dek(dek_row.wrapped_dek)

            nonce = mapping.encrypted_value[:_NONCE_LENGTH]
            ciphertext = mapping.encrypted_value[_NONCE_LENGTH:]
            return AESGCM(raw_dek).decrypt(nonce, ciphertext, None).decode("utf-8")

    def resolve_tokens(
        self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, tokens: list[str]
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for token in tokens:
            value = self.resolve_token(tenant_id, conversation_id, token)
            if value is not None:
                resolved[token] = value
        return resolved

    def delete_mapping(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, token: str) -> None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, conversation_id, token)
            if mapping is not None:
                session.delete(mapping)

    def expire_mapping(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, token: str) -> None:
        with tenant_scoped_session(tenant_id) as session:
            mapping = self._find_mapping(session, tenant_id, conversation_id, token)
            if mapping is not None:
                mapping.deleted_at = datetime.now(timezone.utc)

    def _find_mapping(
        self, session: Session, tenant_id: uuid.UUID, conversation_id: uuid.UUID, token: str
    ) -> TokenMapping | None:
        stmt = sa.select(TokenMapping).where(
            TokenMapping.tenant_id == tenant_id,
            TokenMapping.conversation_id == conversation_id,
            TokenMapping.token == token,
            TokenMapping.deleted_at.is_(None),
        )
        return session.execute(stmt).scalar_one_or_none()

    def _active_dek_row(self, session: Session, tenant_id: uuid.UUID) -> TenantKey:
        stmt = (
            sa.select(TenantKey)
            .where(TenantKey.tenant_id == tenant_id)
            .order_by(TenantKey.key_version.desc())
            .limit(1)
        )
        return session.execute(stmt).scalar_one()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/privacy_invariants/test_token_vault.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Run the import-linter contract**

Run: `cd backend && lint-imports`
Expected: `Contracts: 2 kept, 0 broken.`

- [ ] **Step 6: Commit**

```bash
git add backend/app/privacy_gateway/token_vault/vault.py backend/tests/privacy_invariants/test_token_vault.py
git commit -m "feat: add TokenVault with AES-GCM encryption and tenant+conversation-scoped authorization"
```

---

## Task 10: CI — Postgres service container + migrations

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: everything from Tasks 1–9.

- [ ] **Step 1: Update the `backend` job in `.github/workflows/ci.yml`**

Replace the `backend` job with:

```yaml
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: chatgpt_proxy
          POSTGRES_PASSWORD: change-me
          POSTGRES_DB: chatgpt_proxy
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U chatgpt_proxy"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    env:
      ENVIRONMENT: test
      DATABASE_URL: postgresql+psycopg://chatgpt_proxy:change-me@localhost:5432/chatgpt_proxy
      MASTER_KEY_PATH: /tmp/ci-master.key
      APP_RUNTIME_PASSWORD: change-me-app-runtime
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: head -c 32 /dev/urandom > /tmp/ci-master.key
      - run: ruff check .
      - run: lint-imports
      - run: alembic upgrade head
      - run: pytest --cov=app
```

Leave the `frontend` and `compose` jobs unchanged.

- [ ] **Step 2: Run the same commands locally to confirm the workflow would pass**

Run:
```bash
cd backend
export ENVIRONMENT=test
export DATABASE_URL=postgresql+psycopg://chatgpt_proxy:change-me@localhost:5432/chatgpt_proxy
export MASTER_KEY_PATH=/tmp/ci-master.key
export APP_RUNTIME_PASSWORD=change-me-app-runtime
head -c 32 /dev/urandom > /tmp/ci-master.key
ruff check .
lint-imports
alembic upgrade head
pytest --cov=app
```
Expected: all commands exit 0 (assumes `docker compose up -d postgres`
is running against the same credentials — CI uses its own service
container instead).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run backend tests against a real Postgres service container"
```

---

## Task 11: Docker Compose & Dockerfile wiring

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.override.yml.example`
- Modify: `backend/Dockerfile`
- Modify: `.gitignore`
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: everything from Tasks 1–9 (the backend container must be able
  to run `alembic upgrade head` and start with valid `DATABASE_URL`/
  `MASTER_KEY_PATH`/`APP_RUNTIME_PASSWORD`).
- Produces: a `docker compose up --build` stack where the backend
  container migrates itself on startup and both health checks still pass
  — the same verification done manually for the scaffold plan, now with
  the data layer wired in.

- [ ] **Step 1: Add `secrets/` to `.gitignore`**

Add this line under the `# Env / local overrides` section (after
`docker-compose.override.yml`):

```
secrets/
```

- [ ] **Step 2: Update `docker-compose.yml`**

Add a top-level `secrets:` block (after `volumes:` at the end of the
file) and update the `backend` service:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 10

  keycloak:
    image: quay.io/keycloak/keycloak:24.0
    command: start-dev
    environment:
      KEYCLOAK_ADMIN: ${KEYCLOAK_ADMIN}
      KEYCLOAK_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD}
    ports:
      - "127.0.0.1:8080:8080"

  ollama:
    image: ollama/ollama:latest
    ports:
      - "127.0.0.1:11434:11434"
    volumes:
      - ollama_data:/root/.ollama

  backend:
    build: ./backend
    environment:
      ENVIRONMENT: ${ENVIRONMENT}
      LOG_LEVEL: ${LOG_LEVEL}
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      MASTER_KEY_PATH: /run/secrets/master_key
      APP_RUNTIME_PASSWORD: ${APP_RUNTIME_PASSWORD}
    secrets:
      - master_key
    ports:
      - "127.0.0.1:8000:8000"
    depends_on:
      postgres:
        condition: service_healthy

  frontend:
    build: ./frontend
    ports:
      - "127.0.0.1:3000:3000"
    depends_on:
      - backend

volumes:
  postgres_data:
  ollama_data:

secrets:
  master_key:
    file: ./secrets/master.key
```

- [ ] **Step 3: Update `docker-compose.override.yml.example`**

Keep the `frontend` block unchanged; update the `backend` block's
`command` so the dev override also migrates before serving with
`--reload`:

```yaml
services:
  backend:
    volumes:
      - ./backend/app:/app/app
      - ./backend/tests:/app/tests
    command: sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

  frontend:
    volumes:
      - ./frontend:/app
      - /app/node_modules
      - /app/.next
    command: npm run dev
```

- [ ] **Step 4: Update `backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

- [ ] **Step 5: Add `APP_RUNTIME_PASSWORD` to `.env.example`**

Add this line under the `# --- Postgres ---` section (after
`POSTGRES_DB=chatgpt_proxy`):

```
# Password for the restricted app_runtime Postgres role (not the admin
# POSTGRES_USER/PASSWORD above, which is only used for migrations).
APP_RUNTIME_PASSWORD=change-me-app-runtime
```

- [ ] **Step 6: Update `README.md`**

Replace the `## Local development` and `## Backend tests` sections with:

```markdown
## Local development

1. Copy `.env.example` to `.env` and `docker-compose.override.yml.example`
   to `docker-compose.override.yml`, adjusting secrets as needed.
2. Generate a local master key (wraps each tenant's data encryption key,
   ADR-0010): `mkdir -p secrets && head -c 32 /dev/urandom > secrets/master.key`
3. `docker compose up --build`
4. Backend health check: `curl http://localhost:8000/health`
5. Frontend health check: `curl http://localhost:3000/api/healthz`

## Backend tests

Requires a running Postgres (`docker compose up -d postgres`) and the
local master key file from step 2 above.

```bash
cd backend
pip install -e ".[dev]"
export DATABASE_URL=postgresql+psycopg://chatgpt_proxy:change-me@localhost:5432/chatgpt_proxy
export MASTER_KEY_PATH=../secrets/master.key
export APP_RUNTIME_PASSWORD=change-me-app-runtime
alembic upgrade head
pytest
ruff check .
lint-imports
```
```

- [ ] **Step 7: Generate a local master key and verify the full stack still boots**

Run:
```bash
mkdir -p secrets && head -c 32 /dev/urandom > secrets/master.key
docker compose up --build -d postgres keycloak backend frontend
sleep 5
docker compose logs backend | grep -i alembic
curl -sf http://localhost:8000/health
curl -sf http://localhost:3000/api/healthz
```
Expected: backend logs show `Running upgrade ... -> 0003`; both curls
print `{"status":"ok"}`. (Skip the `ollama` service in this command if a
native Ollama is already running on the host and holding port 11434.)

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml docker-compose.override.yml.example backend/Dockerfile .gitignore README.md .env.example
git commit -m "chore: wire DATABASE_URL, APP_RUNTIME_PASSWORD, and a mounted master-key secret into Docker Compose"
```

---

## Post-Plan State

After this plan: all 8 tables from the design spec exist in Postgres with
RLS enabled and forced on every tenant-scoped table; a
`tenant_scoped_session` helper and mandatory-`tenant_id` repositories
implement both halves of ADR-0011's defense-in-depth; the `TokenVault` can
create, resolve, delete, and expire encrypted token mappings with
tenant+conversation-scoped authorization proven by tests; CI runs the
whole suite against a real Postgres service container; and
`docker compose up --build` still brings up a fully working stack. The
detection/pseudonymization pipeline plan can now call `TokenVault`
directly instead of modeling its own storage, and the auth plan's only
remaining job is supplying a real `tenant_id` to `tenant_scoped_session`
from a validated JWT instead of a test-supplied value.
