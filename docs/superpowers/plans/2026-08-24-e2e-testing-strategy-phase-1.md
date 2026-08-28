# E2E Testing Strategy — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Playwright-based end-to-end test suite covering four critical
user journeys (Keycloak login, an anonymized chat round-trip, one RBAC
visibility check, one admin CRUD flow), wired into CI, backed by a new
first-class `StubProvider` for deterministic/fast LLM calls and an ephemeral
per-run tenant-provisioning script — the first automated proof that this
system's frontend, backend, Keycloak, and Postgres actually work together.

**Architecture:** A new top-level `e2e/` Playwright package drives the real
running stack (`docker-compose.yml` + `docker-compose.override.yml`) over
HTTP/browser. A new `StubProvider` (implementing the existing `LLMProvider`
protocol) echoes sanitized prompts verbatim so the chat journey can assert
pseudonymize→LLM→deanonymize round-trips real values through opaque tokens.
A new `provision_e2e_tenant.py` script creates a uniquely-named, throwaway
tenant/branch/users (DB rows + real Keycloak identities) per test run,
reusing the exact patterns `seed_dev_tenants.py` and
`tests/conftest.py::grant_app_entitlement` already establish.

**Tech Stack:** Playwright (`@playwright/test`), TypeScript, Python 3.12
(provisioning script, `StubProvider`), FastAPI/SQLAlchemy (existing backend),
GitHub Actions.

**Spec:** This plan implements the approved design recorded at
`/Users/imac/.claude/plans/1-as-user-i-polymorphic-willow.md` (End-to-End
Testing Strategy — Phase 1). That file is this plan's authoritative source
for the binding decisions and design rationale; this document turns it into
executable tasks.

## Global Constraints

- Ephemeral e2e tenants/users are NEVER the two fixed dev tenants from
  `seed_dev_tenants.py` (`dr.mueller`/`dr.klein`/etc.) — those stay reserved
  for manual/human testing.
- `LLM_PROVIDER=stub` must be impossible to reach in `ENVIRONMENT=production`
  by accident — enforced by a startup validator (fail-closed), not a comment
  or README note.
- No `--no-verify`, no skipping tests.
- Every new Python file follows this repo's existing patterns exactly: type
  hints, `from __future__ import annotations` where the codebase already uses
  it, repository classes taking `tenant_id` as the first argument, no raw
  PII/secrets logged.
- Every new TypeScript file follows `e2e/`'s own conventions once
  established in Task 4 — do not invent a second style partway through.
- Chromium only for Phase 1 (no multi-browser matrix).

---

### Task 1: `StubProvider` — a first-class echo LLM provider

**Files:**
- Create: `backend/app/llm_gateway/stub_provider.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/llm_gateway/registry.py`
- Modify: `.env.example`
- Create: `backend/tests/unit/test_stub_provider.py`
- Create: `backend/tests/integration/test_stub_provider_registry.py`

**Interfaces:**
- Produces: `StubProvider` (class, `backend/app/llm_gateway/stub_provider.py`)
  implementing the `LLMProvider` protocol (`name: str`, `model: str`,
  `stream(messages: list[ChatMessage]) -> Iterator[StreamDelta | StreamUsage]`,
  per `backend/app/llm_gateway/provider.py`). `Settings.llm_provider` gains a
  third literal value `"stub"`. Consumed by Task 3 (provisioning script's
  target environment) and the e2e CI job (Task 9).

- [ ] **Step 1: Write the failing unit test**

Create `backend/tests/unit/test_stub_provider.py`:

```python
import decimal

from app.llm_gateway.provider import ChatMessage, StreamDelta, StreamUsage
from app.llm_gateway.stub_provider import StubProvider


def test_name_and_model():
    provider = StubProvider()
    assert provider.name == "stub"
    assert provider.model == "stub-echo-v1"


def test_echoes_the_last_user_message_verbatim():
    provider = StubProvider()
    messages = [
        ChatMessage(role="system", content="be helpful"),
        ChatMessage(role="user", content="Patient PATIENT_A1B2C3D4E5 aufgenommen."),
    ]
    items = list(provider.stream(messages))

    deltas = [item for item in items if isinstance(item, StreamDelta)]
    reassembled = "".join(delta.text for delta in deltas)
    assert reassembled == "Patient PATIENT_A1B2C3D4E5 aufgenommen."


def test_stream_ends_with_exactly_one_usage_item():
    provider = StubProvider()
    messages = [ChatMessage(role="user", content="Hallo Welt")]
    items = list(provider.stream(messages))

    assert isinstance(items[-1], StreamUsage)
    assert sum(1 for item in items if isinstance(item, StreamUsage)) == 1
    usage = items[-1]
    assert usage.tokens_in > 0
    assert usage.tokens_out > 0
    assert usage.cost_usd == decimal.Decimal(0)


def test_yields_multiple_deltas_not_one_giant_chunk():
    """Mirrors real streaming providers' multi-delta shape (word-chunked),
    exercising the same SentenceBuffer assembly path in app/api/chat.py a
    real provider does -- a single-delta stub would silently skip testing
    that assembly logic."""
    provider = StubProvider()
    messages = [ChatMessage(role="user", content="Eins zwei drei vier fünf")]
    deltas = [item for item in provider.stream(messages) if isinstance(item, StreamDelta)]
    assert len(deltas) > 1


def test_ignores_system_and_assistant_messages_uses_last_user_only():
    provider = StubProvider()
    messages = [
        ChatMessage(role="system", content="system prompt text"),
        ChatMessage(role="user", content="first user turn"),
        ChatMessage(role="assistant", content="assistant reply"),
        ChatMessage(role="user", content="second user turn"),
    ]
    items = list(provider.stream(messages))
    reassembled = "".join(item.text for item in items if isinstance(item, StreamDelta))
    assert reassembled == "second user turn"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_stub_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm_gateway.stub_provider'`.

- [ ] **Step 3: Write `StubProvider`**

Create `backend/app/llm_gateway/stub_provider.py`:

```python
from __future__ import annotations

import decimal
from collections.abc import Iterator

from app.llm_gateway.provider import ChatMessage, StreamDelta, StreamUsage


class StubProvider:
    """Deterministic, echo-only LLMProvider for e2e/CI use (never production
    -- see Settings._forbid_stub_provider_in_production).

    Echoes the last user message verbatim, word-chunked so it exercises the
    same multi-delta SSE assembly path (app/api/chat.py's SentenceBuffer) a
    real streaming provider does. Because it is a pure echo, it cannot
    invent, translate, or drop a token -- it automatically satisfies the
    same token-preservation contract TOKEN_PRESERVATION_SYSTEM_PROMPT asks
    real models for, which is exactly what an e2e chat round-trip test needs
    to assert: if the rendered reply (after deanonymize()) still contains the
    original raw values, pseudonymize -> LLM -> deanonymize round-tripped
    correctly through opaque tokens.
    """

    name = "stub"
    model = "stub-echo-v1"

    def stream(self, messages: list[ChatMessage]) -> Iterator[StreamDelta | StreamUsage]:
        last_user_content = next(
            (message.content for message in reversed(messages) if message.role == "user"),
            "",
        )
        words = last_user_content.split(" ")
        for index, word in enumerate(words):
            text = word if index == 0 else f" {word}"
            yield StreamDelta(text=text)
        yield StreamUsage(
            tokens_in=len(last_user_content.split()),
            tokens_out=len(words),
            cost_usd=decimal.Decimal(0),
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_stub_provider.py -v`
Expected: PASS (5/5).

- [ ] **Step 5: Wire `Settings.llm_provider` and add the production guard**

Edit `backend/app/config.py`. Change:

Old:
```python
    llm_provider: Literal["ollama", "openai"] = "ollama"
```

New:
```python
    llm_provider: Literal["ollama", "openai", "stub"] = "ollama"
```

Then, immediately after the existing `_require_openai_key_when_selected`
validator method, add a second validator:

```python
    @model_validator(mode="after")
    def _forbid_stub_provider_in_production(self) -> "Settings":
        # ADR-0020: fail closed at startup, not at first request. The stub
        # provider exists for deterministic/fast e2e and CI runs only -- it
        # must never be reachable by a misconfigured production deployment.
        if self.llm_provider == "stub" and self.environment == "production":
            raise ValueError("LLM_PROVIDER=stub is forbidden when ENVIRONMENT=production")
        return self
```

- [ ] **Step 6: Wire the registry**

Edit `backend/app/llm_gateway/registry.py`:

Old:
```python
from app.config import get_settings
from app.llm_gateway.ollama_provider import OllamaProvider
from app.llm_gateway.openai_provider import OpenAIProvider
from app.llm_gateway.provider import LLMProvider


@lru_cache(maxsize=1)
def get_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "ollama":
        return OllamaProvider(base_url=settings.ollama_base_url, model=settings.ollama_model)
    # Settings' model validator guarantees openai_api_key is set whenever
    # llm_provider == "openai" -- fail-closed at startup, not at first request.
    return OpenAIProvider(api_key=settings.openai_api_key, model=settings.openai_model)
```

New:
```python
from app.config import get_settings
from app.llm_gateway.ollama_provider import OllamaProvider
from app.llm_gateway.openai_provider import OpenAIProvider
from app.llm_gateway.provider import LLMProvider
from app.llm_gateway.stub_provider import StubProvider


@lru_cache(maxsize=1)
def get_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "ollama":
        return OllamaProvider(base_url=settings.ollama_base_url, model=settings.ollama_model)
    if settings.llm_provider == "stub":
        return StubProvider()
    # Settings' model validator guarantees openai_api_key is set whenever
    # llm_provider == "openai" -- fail-closed at startup, not at first request.
    return OpenAIProvider(api_key=settings.openai_api_key, model=settings.openai_model)
```

- [ ] **Step 7: Write the registry-wiring integration test**

Create `backend/tests/integration/test_stub_provider_registry.py`:

```python
import os

import pytest

from app.config import get_settings
from app.llm_gateway.registry import get_provider
from app.llm_gateway.stub_provider import StubProvider


@pytest.fixture
def stub_provider_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    get_settings.cache_clear()
    get_provider.cache_clear()
    yield
    get_settings.cache_clear()
    get_provider.cache_clear()


def test_get_provider_returns_stub_when_configured(stub_provider_env):
    provider = get_provider()
    assert isinstance(provider, StubProvider)


def test_stub_provider_forbidden_in_production(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="LLM_PROVIDER=stub is forbidden"):
            get_settings()
    finally:
        monkeypatch.setenv("ENVIRONMENT", "test")
        get_settings.cache_clear()
```

- [ ] **Step 8: Run the new tests and the full suite**

Run: `cd backend && pytest tests/unit/test_stub_provider.py tests/integration/test_stub_provider_registry.py -v`
Expected: all PASS.

Run: `cd backend && pytest tests/unit tests/integration tests/privacy_invariants -v`
Expected: all PASS (no regression — this task only adds a new branch, never
changes existing `ollama`/`openai` behavior).

- [ ] **Step 9: Document the new option**

Edit `.env.example` — find the `LLM_PROVIDER` documentation comment (near
`OPENAI_API_KEY`) and add a note that `stub` is a third option, e2e/CI-only,
blocked in production by a startup validator. Match the existing comment
style in that file exactly (read the surrounding lines first).

- [ ] **Step 10: Run ruff and lint-imports**

Run: `cd backend && ruff check . && lint-imports`
Expected: both clean.

- [ ] **Step 11: Commit**

```bash
git add backend/app/llm_gateway/stub_provider.py backend/app/config.py \
  backend/app/llm_gateway/registry.py .env.example \
  backend/tests/unit/test_stub_provider.py \
  backend/tests/integration/test_stub_provider_registry.py
git commit -m "feat: add a first-class StubProvider for deterministic e2e/CI LLM calls"
```

---

### Task 2: `KeycloakAdminClient.set_password()`

**Files:**
- Modify: `backend/app/keycloak_admin/client.py`
- Create: `backend/tests/unit/test_keycloak_admin_client.py`

**Interfaces:**
- Produces: `KeycloakAdminClient.set_password(subject: str, password: str,
  temporary: bool = False) -> None`. Consumed by Task 3's provisioning
  script — required because `create_user()` (existing) sets
  `requiredActions: ["UPDATE_PASSWORD"]`, which blocks a Resource Owner
  Password Credentials login until cleared; `temporary=False` on the
  password-reset call is what clears it.

- [ ] **Step 1: Write the failing unit test**

Create `backend/tests/unit/test_keycloak_admin_client.py`. This file does
not exist yet, so also check whether any other test file already covers
`KeycloakAdminClient` — if `backend/tests/unit/` or `backend/tests/integration/`
already has a `test_keycloak_admin_client.py`-equivalent (search
`grep -rl KeycloakAdminClient backend/tests/`), read it first and add these
cases to that existing file instead of creating a duplicate; otherwise
create this new file:

```python
import httpx
import pytest

from app.keycloak_admin.client import KeycloakAdminClient, KeycloakAdminError


class _RecordingTransport(httpx.MockTransport):
    """Wraps httpx.MockTransport to also record every request made, so a
    test can assert on the exact method/URL/body sent."""

    def __init__(self, handler):
        self.requests: list[httpx.Request] = []

        def recording_handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        super().__init__(recording_handler)


def _client_with_transport(transport: httpx.MockTransport) -> KeycloakAdminClient:
    http_client = httpx.Client(transport=transport)
    return KeycloakAdminClient(
        base_url="http://keycloak:8080",
        realm="chatgpt-proxy-dev",
        client_id="chatgpt-proxy-backend-admin",
        client_secret="dev-backend-admin-secret",
        http_client=http_client,
    )


def _token_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "fake-token", "expires_in": 300})


def test_set_password_puts_reset_password_with_temporary_false():
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/protocol/openid-connect/token"):
            return _token_response(request)
        seen["request"] = request
        return httpx.Response(204)

    transport = _RecordingTransport(handler)
    client = _client_with_transport(transport)

    client.set_password("subject-123", "a-real-password", temporary=False)

    request = seen["request"]
    assert request.method == "PUT"
    assert request.url.path == (
        "/admin/realms/chatgpt-proxy-dev/users/subject-123/reset-password"
    )
    import json

    body = json.loads(request.content)
    assert body == {"type": "password", "value": "a-real-password", "temporary": False}


def test_set_password_raises_keycloak_admin_error_on_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/protocol/openid-connect/token"):
            return _token_response(request)
        return httpx.Response(400, json={"error": "bad request"})

    transport = _RecordingTransport(handler)
    client = _client_with_transport(transport)

    with pytest.raises(KeycloakAdminError):
        client.set_password("subject-123", "a-real-password")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_keycloak_admin_client.py -v`
Expected: FAIL — `AttributeError: 'KeycloakAdminClient' object has no attribute 'set_password'`.

- [ ] **Step 3: Implement `set_password`**

Edit `backend/app/keycloak_admin/client.py`. Add this method after the
existing `set_enabled` method (same file, same class), following its exact
error-handling shape:

```python
    def set_password(self, subject: str, password: str, temporary: bool = False) -> None:
        """Sets (resets) a user's password directly, clearing any pending
        UPDATE_PASSWORD required action when temporary=False. create_user()
        always sets that required action; a caller that needs the user to be
        immediately usable via a password grant (e.g. e2e tenant
        provisioning) must call this afterward."""
        try:
            response = self._http_client.put(
                f"{self._base_url}/admin/realms/{self._realm}/users/{subject}/reset-password",
                headers=self._headers(),
                json={"type": "password", "value": password, "temporary": temporary},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(f"set_password({subject!r}) failed: {exc}") from exc
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_keycloak_admin_client.py -v`
Expected: PASS (2/2, or more if merged into an existing file — all green).

- [ ] **Step 5: Run the full backend suite, ruff, lint-imports**

Run: `cd backend && pytest tests/unit tests/integration tests/privacy_invariants -v && ruff check . && lint-imports`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/keycloak_admin/client.py backend/tests/unit/test_keycloak_admin_client.py
git commit -m "feat: add KeycloakAdminClient.set_password for e2e tenant provisioning"
```

(If Step 1 found an existing test file instead of creating a new one, `git add`
that file's actual path instead.)

---

### Task 3: Ephemeral tenant provisioning script

**Files:**
- Create: `backend/scripts/provision_e2e_tenant.py`
- Create: `backend/tests/integration/test_provision_e2e_tenant.py`

**Interfaces:**
- Consumes: `TenantRepository.create(name, keycloak_realm, retention_days,
  tenant_id=None)` (`backend/app/db/repositories/tenant_repository.py`),
  `BranchRepository.create(tenant_id, name)`,
  `UserRepository.create(tenant_id, keycloak_subject, email, role,
  branch_id=None, is_active=True)`, `KeycloakAdminClient.create_user(email,
  first_name, last_name, tenant_id) -> str` and `.set_password(subject,
  password, temporary=False)` (Task 2) and `.delete_user(subject)`.
- Produces: a CLI (`python scripts/provision_e2e_tenant.py create` /
  `... cleanup --tenant-id <uuid>`) printing one JSON object to stdout on
  `create`: `{"tenant_id": str, "branch_id": str, "users": {"super_admin":
  {"user_id": str, "email": str, "password": str, "keycloak_subject": str},
  "doctor": {...}, "staff": {...}}}`. Consumed by Task 4's
  `e2e/global-setup.ts`/`global-teardown.ts`.

- [ ] **Step 1: Write the failing integration test**

Create `backend/tests/integration/test_provision_e2e_tenant.py`. This test
drives the script as a subprocess (matching how a human/CI would actually
invoke it) and inspects the real DB afterward — it does not need to touch
real Keycloak (mock `KeycloakAdminClient` at the module level via
monkeypatch, since a real Keycloak Admin API call in a backend-only test
suite run would be an unnecessary external dependency for this specific
test; the full real-Keycloak path is exercised for real by the actual e2e
suite in Task 5+):

```python
import json
import subprocess
import sys
import uuid

import pytest
import sqlalchemy as sa

from app.config import get_settings
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Branch, Tenant, TenantAppEntitlement, User


@pytest.fixture
def fake_keycloak_admin_client(monkeypatch):
    """Provisioning script tests exercise the real DB path but a fake
    Keycloak admin client -- real Keycloak Admin API calls belong to the
    actual e2e suite (Task 5+), not this backend-only integration test."""
    created_subjects: list[str] = []

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def create_user(self, email, first_name, last_name, tenant_id):
            subject = str(uuid.uuid4())
            created_subjects.append(subject)
            return subject

        def set_password(self, subject, password, temporary=False):
            pass

        def delete_user(self, subject):
            pass

    monkeypatch.setattr(
        "app.scripts_support.provision_e2e_tenant_keycloak_client", _FakeClient
    )
    return created_subjects


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

    with SessionLocal() as session:
        entitlement = session.execute(
            sa.select(TenantAppEntitlement).where(
                TenantAppEntitlement.tenant_id == tenant_id
            )
        ).scalar_one_or_none()
        assert entitlement is not None
        assert entitlement.revoked_at is None

    _run_script("cleanup", "--tenant-id", str(tenant_id), "--fake-keycloak-client")


def test_cleanup_removes_tenant_and_all_its_rows(fake_keycloak_admin_client):
    result = _run_script("create", "--fake-keycloak-client")
    payload = json.loads(result.stdout)
    tenant_id = uuid.UUID(payload["tenant_id"])

    cleanup_result = _run_script("cleanup", "--tenant-id", str(tenant_id), "--fake-keycloak-client")
    assert cleanup_result.returncode == 0, cleanup_result.stderr

    with SessionLocal() as session:
        assert session.get(Tenant, tenant_id) is None


def test_repeated_create_generates_distinct_tenants(fake_keycloak_admin_client):
    result_a = _run_script("create", "--fake-keycloak-client")
    result_b = _run_script("create", "--fake-keycloak-client")
    tenant_a = json.loads(result_a.stdout)["tenant_id"]
    tenant_b = json.loads(result_b.stdout)["tenant_id"]
    assert tenant_a != tenant_b

    _run_script("cleanup", "--tenant-id", tenant_a, "--fake-keycloak-client")
    _run_script("cleanup", "--tenant-id", tenant_b, "--fake-keycloak-client")
```

Note: this test spawns the script as a real subprocess, so the
`monkeypatch.setattr` fixture above (which patches the *importing test
process's* module attribute) cannot actually reach the subprocess. Resolve
this in Step 3 by giving the script its own `--fake-keycloak-client` CLI
flag that swaps in an in-process fake `KeycloakAdminClient`-shaped object
directly (no real Keycloak HTTP calls), rather than relying on monkeypatch
across a process boundary — simplify the fixture above to just return
nothing/be a no-op marker once Step 3 is written; the flag is what actually
matters. Adjust the test fixture to match once the script's real interface
is written (this is expected — the test is written first per TDD, but the
subprocess/monkeypatch mismatch is a known, deliberate simplification to
fix in Step 3, not a bug to chase further at this step).

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/integration/test_provision_e2e_tenant.py -v`
Expected: FAIL — `FileNotFoundError`/non-zero exit, `scripts/provision_e2e_tenant.py`
does not exist yet.

- [ ] **Step 3: Write the provisioning script**

Create `backend/scripts/provision_e2e_tenant.py`:

```python
"""Provision (or clean up) a uniquely-named, throwaway tenant for one e2e
test run: a tenant, one branch, and three users (super_admin, doctor,
staff) all on that branch, entitled+assigned for the "anonymization" app.

Never run against a production database. Each invocation of `create`
generates a fresh, unique tenant -- unlike seed_dev_tenants.py's fixed,
upserted dev tenants, this script exists specifically so parallel/repeated
e2e runs never collide. The two named dev tenants stay reserved for manual
testing; e2e tests must never touch them.

Usage:
    python scripts/provision_e2e_tenant.py create
    python scripts/provision_e2e_tenant.py cleanup --tenant-id <uuid>

`create` prints one JSON object to stdout: tenant_id, branch_id, and a
users object keyed by role, each with user_id/email/password/keycloak_subject.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import uuid

import sqlalchemy as sa

from app.config import get_settings
from app.db.repositories.branch_repository import BranchRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.keycloak_admin.client import KeycloakAdminClient
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider

ROLES = ("super_admin", "doctor", "staff")


class _FakeKeycloakAdminClient:
    """In-process stand-in used only by this script's own test suite
    (--fake-keycloak-client), never by a real e2e run. Avoids depending on a
    real Keycloak Admin API connection for a backend-only integration test."""

    def create_user(self, email: str, first_name: str, last_name: str, tenant_id: str) -> str:
        return str(uuid.uuid4())

    def set_password(self, subject: str, password: str, temporary: bool = False) -> None:
        pass

    def delete_user(self, subject: str) -> None:
        pass


def _build_keycloak_client(use_fake: bool) -> KeycloakAdminClient | _FakeKeycloakAdminClient:
    if use_fake:
        return _FakeKeycloakAdminClient()
    settings = get_settings()
    if not settings.keycloak_admin_client_id or not settings.keycloak_admin_client_secret:
        raise SystemExit(
            "KEYCLOAK_ADMIN_CLIENT_ID/KEYCLOAK_ADMIN_CLIENT_SECRET must be set "
            "to provision real Keycloak users (or pass --fake-keycloak-client "
            "for a DB-only test run)"
        )
    return KeycloakAdminClient(
        base_url=settings.keycloak_admin_base_url,
        realm=settings.keycloak_admin_realm,
        client_id=settings.keycloak_admin_client_id,
        client_secret=settings.keycloak_admin_client_secret,
    )


def _grant_anonymization_entitlement(tenant_id: uuid.UUID) -> None:
    """Mirrors tests/conftest.py::grant_app_entitlement exactly: a direct
    create_engine(database_url) connection (the migration-owner role, not
    app_runtime/app_ops), since TenantRepository.create() only auto-creates
    the tenant-wide *assignment* row, never the *entitlement* row -- the dev
    seed tenants get entitled out-of-band via the ops_admin console; an
    ephemeral, fully-automated tenant has no such manual step."""
    engine = sa.create_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO tenant_app_entitlements (id, tenant_id, app_id, granted_by) "
                "SELECT gen_random_uuid(), :tenant_id, id, 'e2e-provisioning' FROM apps WHERE key = 'anonymization' "
                "ON CONFLICT (tenant_id, app_id) DO UPDATE SET revoked_at = NULL"
            ),
            {"tenant_id": str(tenant_id)},
        )
    engine.dispose()


def create(use_fake_keycloak_client: bool) -> int:
    run_marker = uuid.uuid4().hex[:12]
    tenant_id = uuid.uuid4()
    key_provider = FileSecretKeyProvider(get_settings().master_key_path)
    keycloak_client = _build_keycloak_client(use_fake_keycloak_client)

    with SessionLocal() as session:
        TenantRepository(session, key_provider).create(
            name=f"E2E {run_marker}",
            keycloak_realm=f"e2e-{run_marker}",
            retention_days=1,
            tenant_id=tenant_id,
        )
        session.commit()

    _grant_anonymization_entitlement(tenant_id)

    users_payload: dict[str, dict[str, str]] = {}
    with tenant_scoped_session(tenant_id) as session:
        branch = BranchRepository(session).create(tenant_id, f"E2E Branch {run_marker}")
        branch_id = branch.id

        for role in ROLES:
            email = f"e2e-{role}-{run_marker}@example.test"
            password = secrets.token_urlsafe(16)
            subject = keycloak_client.create_user(
                email=email, first_name="E2E", last_name=role.replace("_", " ").title(),
                tenant_id=str(tenant_id),
            )
            keycloak_client.set_password(subject, password, temporary=False)
            user = UserRepository(session).create(
                tenant_id, keycloak_subject=subject, email=email, role=role, branch_id=branch_id,
            )
            users_payload[role] = {
                "user_id": str(user.id),
                "email": email,
                "password": password,
                "keycloak_subject": subject,
            }

    print(json.dumps({
        "tenant_id": str(tenant_id),
        "branch_id": str(branch_id),
        "users": users_payload,
    }))
    return 0


def cleanup(tenant_id: uuid.UUID, use_fake_keycloak_client: bool) -> int:
    keycloak_client = _build_keycloak_client(use_fake_keycloak_client)

    with tenant_scoped_session(tenant_id) as session:
        users = session.execute(
            sa.text("SELECT keycloak_subject FROM users WHERE tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        ).scalars().all()
        for subject in users:
            keycloak_client.delete_user(subject)

        session.execute(sa.text("DELETE FROM messages WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        session.execute(sa.text("DELETE FROM conversations WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        session.execute(sa.text("DELETE FROM users WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        session.execute(sa.text("DELETE FROM branches WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})
        session.execute(sa.text("DELETE FROM tenant_app_assignments WHERE tenant_id = :tenant_id"), {"tenant_id": str(tenant_id)})

    engine = sa.create_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM tenant_app_entitlements WHERE tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )
        connection.execute(
            sa.text("DELETE FROM tenant_keys WHERE tenant_id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )
        connection.execute(
            sa.text("DELETE FROM tenants WHERE id = :tenant_id"),
            {"tenant_id": str(tenant_id)},
        )
    engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("create").add_argument(
        "--fake-keycloak-client", action="store_true",
        help="Use an in-process fake instead of a real Keycloak Admin API connection (test use only).",
    )
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--tenant-id", required=True)
    cleanup_parser.add_argument("--fake-keycloak-client", action="store_true")

    args = parser.parse_args()
    if args.command == "create":
        return create(use_fake_keycloak_client=args.fake_keycloak_client)
    return cleanup(uuid.UUID(args.tenant_id), use_fake_keycloak_client=args.fake_keycloak_client)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Simplify the test's fixture to match the script's real `--fake-keycloak-client` flag**

Edit `backend/tests/integration/test_provision_e2e_tenant.py` — replace the
`fake_keycloak_admin_client` fixture (which assumed a monkeypatch-reachable
module attribute that a subprocess can't see) with a no-op fixture, since
the script now handles faking internally via the CLI flag already passed in
every `_run_script(...)` call above:

```python
@pytest.fixture
def fake_keycloak_admin_client():
    """The script's own --fake-keycloak-client flag (passed in every
    _run_script call in this file) handles this in-process -- nothing to
    patch here across the subprocess boundary."""
    return None
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && pytest tests/integration/test_provision_e2e_tenant.py -v`
Expected: PASS (3/3). If `tenant_app_entitlements`'s `id` column requires an
explicit app-side `uuid.uuid4()` rather than `gen_random_uuid()` per this
repo's ID-generation convention (Global Constraint), check
`grant_app_entitlement` in `backend/tests/conftest.py` for its exact insert
SQL and match it precisely rather than the `gen_random_uuid()` shown above if
it differs — read that fixture function now to confirm before finalizing
this step.

- [ ] **Step 6: Run the full backend suite, ruff, lint-imports**

Run: `cd backend && pytest tests/unit tests/integration tests/privacy_invariants -v && ruff check . && lint-imports`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/provision_e2e_tenant.py backend/tests/integration/test_provision_e2e_tenant.py
git commit -m "feat: add provision_e2e_tenant.py for ephemeral e2e test tenants"
```

---

### Task 4: `e2e/` package scaffolding

**Files:**
- Create: `e2e/package.json`
- Create: `e2e/tsconfig.json`
- Create: `e2e/playwright.config.ts`
- Create: `e2e/support/session.ts`
- Create: `e2e/support/tenant.ts`
- Create: `e2e/global-setup.ts`
- Create: `e2e/global-teardown.ts`
- Create: `e2e/scripts/wait-for-health.sh`
- Create: `e2e/README.md`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `getRopcTokens(username, password) -> Promise<{accessToken,
  refreshToken, expiresAt}>`, `injectSession(context, tokens) ->
  Promise<void>` (`e2e/support/session.ts`); `loadTenant() ->
  E2ETenant` (`e2e/support/tenant.ts`, typed loader for
  `.e2e-tenant.json`). Consumed by Tasks 5-8's spec files.

- [ ] **Step 1: Create the package manifest**

Create `e2e/package.json`:

```json
{
  "name": "chatgpt-proxy-e2e",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "test": "playwright test"
  },
  "devDependencies": {
    "@playwright/test": "^1.48.0",
    "next-auth": "^4.24.15",
    "dotenv": "^16.4.5",
    "typescript": "^5.6.0"
  }
}
```

- [ ] **Step 2: Create `tsconfig.json`**

Create `e2e/tsconfig.json` (standalone, not inheriting `frontend/tsconfig.json`
— different runtime target, Node test runner not Next.js):

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "moduleResolution": "node",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "types": ["node"]
  },
  "include": ["**/*.ts"],
  "exclude": ["node_modules", "playwright-report", "test-results"]
}
```

- [ ] **Step 3: Install dependencies**

Run: `cd e2e && npm install`
Expected: `node_modules/` created, `package-lock.json` written.

Run: `cd e2e && npx playwright install --with-deps chromium`
Expected: Chromium browser binary installed.

- [ ] **Step 4: Write `playwright.config.ts`**

Create `e2e/playwright.config.ts`:

```typescript
import { defineConfig, devices } from "@playwright/test";
import * as dotenv from "dotenv";
import path from "path";

dotenv.config({ path: path.resolve(__dirname, "..", ".env") });

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  globalSetup: require.resolve("./global-setup.ts"),
  globalTeardown: require.resolve("./global-teardown.ts"),
});
```

- [ ] **Step 5: Write the session helper**

Create `e2e/support/session.ts`:

```typescript
import { encode } from "next-auth/jwt";
import type { BrowserContext } from "@playwright/test";

const KEYCLOAK_ISSUER = process.env.KEYCLOAK_ISSUER ?? "http://localhost:8080/realms/chatgpt-proxy-dev";
const NEXTAUTH_SECRET = process.env.NEXTAUTH_SECRET;

export interface RopcTokens {
  accessToken: string;
  refreshToken: string;
  expiresAt: number; // unix seconds, matching frontend/lib/auth.ts's jwt() callback shape
}

export async function getRopcTokens(username: string, password: string): Promise<RopcTokens> {
  const response = await fetch(`${KEYCLOAK_ISSUER}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "password",
      client_id: "chatgpt-proxy-frontend",
      username,
      password,
    }),
  });
  if (!response.ok) {
    throw new Error(`ROPC token request failed for ${username}: ${response.status} ${await response.text()}`);
  }
  const body = await response.json();
  return {
    accessToken: body.access_token,
    refreshToken: body.refresh_token,
    expiresAt: Math.floor(Date.now() / 1000) + body.expires_in,
  };
}

/**
 * Injects a valid NextAuth session cookie directly, bypassing Keycloak's
 * hosted login UI -- for journeys where the point is what happens *after*
 * login (see e2e/tests/login.spec.ts for the one journey that drives the
 * real UI instead). Builds the same token shape frontend/lib/auth.ts's
 * jwt() callback produces and encodes it with next-auth/jwt's encode(),
 * matching the plain-HTTP (no __Secure- prefix) cookie name middleware.ts
 * reads via getToken().
 */
export async function injectSession(context: BrowserContext, tokens: RopcTokens): Promise<void> {
  if (!NEXTAUTH_SECRET) {
    throw new Error("NEXTAUTH_SECRET must be set for injectSession() to encode a valid session cookie");
  }
  const token = {
    accessToken: tokens.accessToken,
    refreshToken: tokens.refreshToken,
    expiresAt: tokens.expiresAt,
  };
  const encoded = await encode({ token, secret: NEXTAUTH_SECRET });
  const url = new URL(process.env.E2E_BASE_URL ?? "http://localhost:3000");
  await context.addCookies([
    {
      name: "next-auth.session-token",
      value: encoded,
      domain: url.hostname,
      path: "/",
      httpOnly: true,
      secure: false,
      sameSite: "Lax",
    },
  ]);
}
```

- [ ] **Step 6: Write the tenant-fixture loader**

Create `e2e/support/tenant.ts`:

```typescript
import * as fs from "fs";
import * as path from "path";

export interface E2EUser {
  user_id: string;
  email: string;
  password: string;
  keycloak_subject: string;
}

export interface E2ETenant {
  tenant_id: string;
  branch_id: string;
  users: {
    super_admin: E2EUser;
    doctor: E2EUser;
    staff: E2EUser;
  };
}

const TENANT_FILE = path.resolve(__dirname, "..", ".e2e-tenant.json");

export function loadTenant(): E2ETenant {
  if (!fs.existsSync(TENANT_FILE)) {
    throw new Error(
      `${TENANT_FILE} not found -- did globalSetup run? (npm test runs it automatically; ` +
      `if you're debugging a single test file directly, run \`npm test\` once first)`
    );
  }
  return JSON.parse(fs.readFileSync(TENANT_FILE, "utf-8"));
}

export function tenantFilePath(): string {
  return TENANT_FILE;
}
```

- [ ] **Step 7: Write global setup/teardown**

Create `e2e/global-setup.ts`:

```typescript
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as path from "path";

async function globalSetup(): Promise<void> {
  const backendDir = path.resolve(__dirname, "..", "backend");
  const output = execFileSync(
    "python", ["scripts/provision_e2e_tenant.py", "create"],
    { cwd: backendDir, encoding: "utf-8", env: process.env }
  );
  const tenantFile = path.resolve(__dirname, ".e2e-tenant.json");
  fs.writeFileSync(tenantFile, output.trim());
  console.log(`e2e: provisioned tenant, credentials written to ${tenantFile}`);
}

export default globalSetup;
```

Create `e2e/global-teardown.ts`:

```typescript
import { execFileSync } from "child_process";
import * as path from "path";
import { loadTenant, tenantFilePath } from "./support/tenant";
import * as fs from "fs";

async function globalTeardown(): Promise<void> {
  const backendDir = path.resolve(__dirname, "..", "backend");
  let tenant;
  try {
    tenant = loadTenant();
  } catch {
    console.warn("e2e: no tenant file found at teardown, nothing to clean up");
    return;
  }
  execFileSync(
    "python", ["scripts/provision_e2e_tenant.py", "cleanup", "--tenant-id", tenant.tenant_id],
    { cwd: backendDir, encoding: "utf-8", env: process.env }
  );
  const file = tenantFilePath();
  if (fs.existsSync(file)) fs.unlinkSync(file);
  console.log(`e2e: cleaned up tenant ${tenant.tenant_id}`);
}

export default globalTeardown;
```

- [ ] **Step 8: Write the health-wait script**

Create `e2e/scripts/wait-for-health.sh`:

```bash
#!/usr/bin/env bash
# Polls the compose stack's health until every service is ready, or fails
# loudly after a bounded timeout. Shared by CI and local manual e2e runs
# (see e2e/README.md) so both fail the same clear way if the stack never
# comes up, instead of a vague Playwright timeout inside a test.
set -euo pipefail

TIMEOUT_SECONDS="${WAIT_FOR_HEALTH_TIMEOUT:-180}"
INTERVAL_SECONDS=3
elapsed=0

check() {
  curl -sf http://localhost:8000/health > /dev/null 2>&1 \
    && curl -sf http://localhost:3000 > /dev/null 2>&1 \
    && curl -sf "http://localhost:8080/realms/chatgpt-proxy-dev" > /dev/null 2>&1
}

echo "Waiting for backend/frontend/keycloak to become healthy (timeout: ${TIMEOUT_SECONDS}s)..."
until check; do
  if [ "$elapsed" -ge "$TIMEOUT_SECONDS" ]; then
    echo "FAILED: stack did not become healthy within ${TIMEOUT_SECONDS}s" >&2
    docker compose ps >&2
    exit 1
  fi
  sleep "$INTERVAL_SECONDS"
  elapsed=$((elapsed + INTERVAL_SECONDS))
done
echo "Stack is healthy after ${elapsed}s."
```

Run: `chmod +x e2e/scripts/wait-for-health.sh`

- [ ] **Step 9: Write `e2e/README.md`**

Create `e2e/README.md` — a short doc mirroring the root `README.md`'s style,
covering: prerequisites (`docker compose up -d --build` from repo root
first), install (`npm install && npx playwright install --with-deps
chromium`), run (`./scripts/wait-for-health.sh && npm test`), and how to
view the HTML report (`npx playwright show-report`) after a run.

- [ ] **Step 10: Update `.gitignore`**

Edit `.gitignore` (repo root) — add:

```
# e2e (Playwright)
e2e/node_modules/
e2e/test-results/
e2e/playwright-report/
e2e/.e2e-tenant.json

# unrelated leftover debug artifacts
.playwright-mcp/
```

- [ ] **Step 11: Verify the scaffolding compiles and global setup/teardown run in isolation**

With the compose stack up (`docker compose up -d --build` from repo root,
`./e2e/scripts/wait-for-health.sh` passing), run:

Run: `cd e2e && npx tsc --noEmit`
Expected: no type errors.

Run: `cd e2e && node -e "require('ts-node/register'); require('./global-setup').default().then(() => require('./global-teardown').default())"`

(If `ts-node` is not already available, add it as a devDependency in Step 1
instead of inventing an alternate invocation — re-run `npm install` after
adding it.)

Expected: prints `e2e: provisioned tenant, credentials written to
.../.e2e-tenant.json` then `e2e: cleaned up tenant <uuid>`, with no errors —
proves the provisioning script wiring (Task 3) actually works end-to-end
from the TypeScript side before any Playwright spec depends on it.

- [ ] **Step 12: Commit**

```bash
git add e2e/package.json e2e/package-lock.json e2e/tsconfig.json \
  e2e/playwright.config.ts e2e/support/session.ts e2e/support/tenant.ts \
  e2e/global-setup.ts e2e/global-teardown.ts e2e/scripts/wait-for-health.sh \
  e2e/README.md .gitignore
git commit -m "feat: scaffold the e2e/ Playwright package"
```

---

### Task 5: `login.spec.ts`

**Files:**
- Create: `e2e/tests/login.spec.ts`

**Interfaces:**
- Consumes: `loadTenant()` (Task 4).

- [ ] **Step 1: Write the spec**

Create `e2e/tests/login.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";
import { loadTenant } from "../support/tenant";

test.describe("login", () => {
  test("unauthenticated request redirects to Keycloak's hosted login, and a successful login lands on an authenticated page", async ({ page }) => {
    const tenant = loadTenant();
    const doctor = tenant.users.doctor;

    await page.goto("/dashboard");

    // middleware.ts redirects to /api/auth/signin, which NextAuth then
    // forwards to Keycloak's own hosted login form -- assert we land there,
    // not on a custom login UI (there isn't one).
    await page.waitForURL(/\/realms\/chatgpt-proxy-dev\/protocol\/openid-connect\/auth/);

    await page.fill("#username", doctor.email);
    await page.fill("#password", doctor.password);
    await page.click("#kc-login");

    await page.waitForURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  });

  test("an unauthenticated request to a protected route redirects to sign-in", async ({ browser }) => {
    // Fresh, cookie-free context -- do not reuse the logged-in context above.
    const context = await browser.newContext();
    const page = await context.newPage();

    await page.goto("/admin");

    await page.waitForURL(/\/api\/auth\/signin/);
    expect(page.url()).toContain("callbackUrl");

    await context.close();
  });
});
```

- [ ] **Step 2: Run the spec against the live stack**

With the compose stack up and healthy, and `NEXTAUTH_SECRET`/`KEYCLOAK_ISSUER`/
`E2E_BASE_URL` set in the environment (matching `.env`):

Run: `cd e2e && npx playwright test tests/login.spec.ts`
Expected: 2/2 PASS. If the Keycloak login form's field IDs (`#username`,
`#password`, `#kc-login`) don't match (Keycloak theme/version differences),
inspect the actual rendered form (`npx playwright test tests/login.spec.ts
--debug` or check a trace) and correct the selectors — these are Keycloak
24.0's default theme IDs as of this plan's writing, called out as a known
brittleness point in the approved design's Open Risks.

- [ ] **Step 3: Commit**

```bash
git add e2e/tests/login.spec.ts
git commit -m "test(e2e): add the Keycloak login journey spec"
```

---

### Task 6: `chat-roundtrip.spec.ts`

**Files:**
- Create: `e2e/tests/chat-roundtrip.spec.ts`

**Interfaces:**
- Consumes: `loadTenant()`, `getRopcTokens()`, `injectSession()` (Task 4).
- Requires: the compose stack running with `LLM_PROVIDER=stub` (Task 1) —
  document this prerequisite in the spec file's top comment.

- [ ] **Step 1: Write the spec**

Create `e2e/tests/chat-roundtrip.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";
import { loadTenant } from "../support/tenant";
import { getRopcTokens, injectSession } from "../support/session";

// Requires the stack to be running with LLM_PROVIDER=stub (see
// backend/app/llm_gateway/stub_provider.py) -- the stub echoes the
// sanitized prompt verbatim, which is what makes this test's round-trip
// assertion meaningful: if the raw values below survive to the rendered
// reply, pseudonymize -> LLM -> deanonymize worked correctly.
const PATIENT_NAME = "Anna Schmitt";
const CITY = "Heidelberg";
const MESSAGE = `Patientin ${PATIENT_NAME}, 45 Jahre, aus ${CITY}.`;

test("sending a message round-trips real values through the anonymization pipeline", async ({ page, context }) => {
  const tenant = loadTenant();
  const doctor = tenant.users.doctor;

  const tokens = await getRopcTokens(doctor.email, doctor.password);
  await injectSession(context, tokens);

  await page.goto("/apps/anonymization");
  await page.getByRole("button", { name: "Neue Anfrage" }).click();
  await page.waitForURL(/\/apps\/anonymization\/c\//);

  await page.getByPlaceholder("Nachricht eingeben…").fill(MESSAGE);
  await page.getByRole("button", { name: "Senden" }).click();

  // The reply streams in; wait for the final rendered text to contain the
  // raw values back (proves the full round trip, not just that something
  // rendered).
  await expect(page.getByText(PATIENT_NAME)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(CITY)).toBeVisible();

  const conversationUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(conversationUrl);
  await expect(page.getByText(PATIENT_NAME)).toBeVisible();
  await expect(page.getByText(CITY)).toBeVisible();
});
```

- [ ] **Step 2: Run the spec**

Run: `cd e2e && npx playwright test tests/chat-roundtrip.spec.ts`
Expected: 1/1 PASS.

If it fails on the "Neue Anfrage" button not being visible: `canCreate` in
`ConversationSidebar.tsx` gates it on `me?.permissions.includes("conversations:create")`
— confirm the ephemeral doctor user actually has that permission (it's in
`DEFAULT_PERMISSIONS[ROLE_DOCTOR]` per `backend/app/auth/permissions.py`, so
it should be present with no extra grant needed; if this fails, check
`GET /api/me`'s response for the injected session directly first before
assuming the UI is wrong).

- [ ] **Step 3: Commit**

```bash
git add e2e/tests/chat-roundtrip.spec.ts
git commit -m "test(e2e): add the anonymized chat round-trip journey spec"
```

---

### Task 7: `rbac-branch-visibility.spec.ts`

**Files:**
- Create: `e2e/tests/rbac-branch-visibility.spec.ts`

**Interfaces:**
- Consumes: `loadTenant()`, `getRopcTokens()`, `injectSession()` (Task 4).

- [ ] **Step 1: Write the spec**

Create `e2e/tests/rbac-branch-visibility.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";
import { loadTenant } from "../support/tenant";
import { getRopcTokens, injectSession } from "../support/session";

test("a doctor's conversation is invisible to same-branch staff until conversations:read:branch is granted", async ({ browser }) => {
  const tenant = loadTenant();
  const { doctor, staff, super_admin } = tenant.users;

  // 1. Doctor creates a conversation.
  const doctorContext = await browser.newContext();
  const doctorPage = await doctorContext.newPage();
  await injectSession(doctorContext, await getRopcTokens(doctor.email, doctor.password));
  await doctorPage.goto("/apps/anonymization");
  await doctorPage.getByRole("button", { name: "Neue Anfrage" }).click();
  await doctorPage.waitForURL(/\/apps\/anonymization\/c\//);
  const conversationId = doctorPage.url().split("/").pop();
  await doctorContext.close();

  // 2. Staff (same branch, per provision_e2e_tenant.py) cannot see it yet --
  // own-only visibility is the default.
  const staffContext = await browser.newContext();
  const staffPage = await staffContext.newPage();
  await injectSession(staffContext, await getRopcTokens(staff.email, staff.password));
  await staffPage.goto("/apps/anonymization");
  await expect(staffPage.locator(`[href*="${conversationId}"], text=Neue Anfrage`).first()).toBeVisible();
  const beforeGrantConversations = await staffPage.locator('[role="button"]').allTextContents();
  expect(beforeGrantConversations.join(" ")).not.toContain(doctor.email.split("@")[0]);
  await staffContext.close();

  // 3. super_admin grants staff conversations:read:branch.
  const adminContext = await browser.newContext();
  const adminPage = await adminContext.newPage();
  await injectSession(adminContext, await getRopcTokens(super_admin.email, super_admin.password));
  await adminPage.goto("/admin/permissions");
  await expect(adminPage.getByRole("heading", { name: "Berechtigungen" })).toBeVisible();

  const staffColumnIndex = 2; // table columns: [permission label, Arzt, Personal] -- "Personal" is staff
  const branchRow = adminPage.locator("tr", { hasText: "Sichtbarkeit Filiale" });
  await branchRow.locator("input[type=checkbox]").nth(1).check(); // Personal column checkbox
  await adminPage.getByRole("button", { name: "Speichern" }).click();
  await expect(adminPage.getByText("Gespeichert.")).toBeVisible();
  await adminContext.close();

  // 4. Staff now sees the doctor's conversation.
  const staffContext2 = await browser.newContext();
  const staffPage2 = await staffContext2.newPage();
  await injectSession(staffContext2, await getRopcTokens(staff.email, staff.password));
  await staffPage2.goto("/apps/anonymization");
  await expect(staffPage2.getByText(doctor.email.split("@")[0])).toBeVisible();
  await staffPage2.goto(`/apps/anonymization/c/${conversationId}`);
  await expect(staffPage2.getByText(/Schreibgeschützt/)).toBeVisible();
  await staffContext2.close();
});
```

Note: the `nth(1)` checkbox-column indexing above is a placeholder for
"whichever column is the Personal/staff column" — before finalizing, run
this spec with `--debug` once and confirm the actual column order rendered
(`AdminPermissionsPage`'s `data.roles` ordering, from `GET
/api/admin/permissions`'s response) matches Doctor-then-Staff as assumed; if
the API returns a different order, replace the `nth(1)` index with a
locator scoped by the column header text instead (e.g. find the `<td>`
under the `<th>` containing "Personal") for robustness against ordering
changes.

- [ ] **Step 2: Run the spec, fix the checkbox-column indexing per the note above if needed**

Run: `cd e2e && npx playwright test tests/rbac-branch-visibility.spec.ts --debug`
Expected: walk through once manually to confirm selectors, then run headless:

Run: `cd e2e && npx playwright test tests/rbac-branch-visibility.spec.ts`
Expected: 1/1 PASS.

- [ ] **Step 3: Commit**

```bash
git add e2e/tests/rbac-branch-visibility.spec.ts
git commit -m "test(e2e): add the RBAC branch-visibility journey spec"
```

---

### Task 8: `admin-branch-and-user-crud.spec.ts`

**Files:**
- Create: `e2e/tests/admin-branch-and-user-crud.spec.ts`

**Interfaces:**
- Consumes: `loadTenant()`, `getRopcTokens()`, `injectSession()` (Task 4).

- [ ] **Step 1: Write the spec**

Create `e2e/tests/admin-branch-and-user-crud.spec.ts`:

```typescript
import { test, expect } from "@playwright/test";
import { loadTenant } from "../support/tenant";
import { getRopcTokens, injectSession } from "../support/session";

test("super_admin can create/edit a branch and create/edit a user", async ({ page, context }) => {
  const tenant = loadTenant();
  const { super_admin } = tenant.users;
  await injectSession(context, await getRopcTokens(super_admin.email, super_admin.password));

  const branchName = `E2E CRUD Branch ${Date.now()}`;
  const renamedBranchName = `${branchName} (renamed)`;

  await page.goto("/admin/branches");
  await page.getByPlaceholder("Name der Filiale").fill(branchName);
  await page.getByRole("button", { name: "Anlegen" }).click();
  await expect(page.getByDisplayValue(branchName)).toBeVisible();

  const renameInput = page.getByDisplayValue(branchName);
  await renameInput.fill(renamedBranchName);
  await renameInput.blur();
  await expect(page.getByDisplayValue(renamedBranchName)).toBeVisible();

  const userEmail = `e2e-crud-user-${Date.now()}@example.test`;
  await page.goto("/admin/users");
  await page.getByLabel("E-Mail").fill(userEmail);
  await page.getByLabel("Vorname").fill("CRUD");
  await page.getByLabel("Nachname").fill("Test");
  await page.locator("form").getByLabel("Rolle").selectOption("staff");
  await page.locator("form").getByLabel("Filiale").selectOption({ label: renamedBranchName });
  await page.getByRole("button", { name: "Benutzer anlegen" }).click();

  await expect(page.getByText(userEmail)).toBeVisible();

  const userRow = page.locator("tr", { hasText: userEmail });
  await userRow.locator("select").first().selectOption("doctor");
  await expect(userRow.locator("select").first()).toHaveValue("doctor");
});
```

- [ ] **Step 2: Run the spec**

Run: `cd e2e && npx playwright test tests/admin-branch-and-user-crud.spec.ts`
Expected: 1/1 PASS. If `getByLabel("Rolle")`/`getByLabel("Filiale")` matches
more than one element (the table's per-row role/branch `<select>`s also
have no explicit label, but check whether Playwright's accessible-name
computation associates the table's bare `<select>` elements with anything —
if it does and causes ambiguity, the `page.locator("form")` scoping already
in the spec above should disambiguate to just the create-form's labeled
selects; verify this holds and adjust the locator scoping if not).

- [ ] **Step 3: Commit**

```bash
git add e2e/tests/admin-branch-and-user-crud.spec.ts
git commit -m "test(e2e): add the admin branch/user CRUD journey spec"
```

---

### Task 9: CI integration

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: everything from Tasks 1-8.

- [ ] **Step 1: Add the `e2e` job**

Edit `.github/workflows/ci.yml` — add a fourth job after `compose`:

```yaml
  e2e:
    runs-on: ubuntu-latest
    needs: [backend, frontend]
    steps:
      - uses: actions/checkout@v4
      - run: cp .env.example .env
      - name: Configure for CI (stub LLM provider, test environment)
        run: |
          sed -i 's/^ENVIRONMENT=.*/ENVIRONMENT=test/' .env
          echo "LLM_PROVIDER=stub" >> .env
      - run: mkdir -p secrets && head -c 32 /dev/urandom > secrets/master.key
      - name: Bring up the stack (omitting ollama and traefik)
        run: docker compose -f docker-compose.yml -f docker-compose.override.yml up -d --build postgres keycloak backend ops_admin frontend
      - name: Wait for stack health
        run: ./e2e/scripts/wait-for-health.sh
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e backend/.[dev]
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm ci
        working-directory: e2e
      - run: npx playwright install --with-deps chromium
        working-directory: e2e
      - run: npm test
        working-directory: e2e
        env:
          DATABASE_URL: postgresql+psycopg://chatgpt_proxy:change-me@localhost:5432/chatgpt_proxy
          MASTER_KEY_PATH: ${{ github.workspace }}/secrets/master.key
          KEYCLOAK_ISSUER: http://localhost:8080/realms/chatgpt-proxy-dev
          E2E_BASE_URL: http://localhost:3000
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: e2e/playwright-report/
      - if: failure()
        run: docker compose logs > compose-logs.txt
      - if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: compose-logs
          path: compose-logs.txt
      - if: always()
        run: docker compose down -v
```

Note: `NEXTAUTH_SECRET` for the `npm test` step must come from `.env.example`'s
existing value (read it — if `.env.example` has a placeholder like
`change-me-generate-a-real-secret` for `NEXTAUTH_SECRET`, that placeholder
value itself is fine for CI, since `injectSession()` just needs *a* secret
that matches whatever the running `frontend` container was started with —
confirm both sides read the same `.env` file, which they do here since both
`docker compose up` and the `npm test` step run from the same checked-out
`.env`). Do not hardcode a different value in the workflow — source it from
`.env` (e.g. `source .env && echo "NEXTAUTH_SECRET=$NEXTAUTH_SECRET" >>
"$GITHUB_ENV"` as an extra step before `npm test`, or read it inline) rather
than duplicating it, so a future `.env.example` change can't silently
desync the two.

- [ ] **Step 2: Verify the workflow YAML is valid**

Run: `docker compose config --quiet` (still passes — this job doesn't touch
the base compose files' syntax) and, if a YAML linter is available locally,
lint `.github/workflows/ci.yml`; otherwise visually re-read the diff for
indentation correctness (GitHub Actions YAML is whitespace-sensitive).

- [ ] **Step 3: Push a branch and confirm the job actually runs in GitHub Actions**

This step requires pushing to the remote — confirm with the user before
doing so if this plan is being executed autonomously; per this repo's own
git safety norms, do not push without that confirmation even though CI
changes are the whole point of this task.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add the e2e job running the Playwright suite against the full stack"
```

---

### Task 10: ADR-0024 and the deliberate-break rehearsal

**Files:**
- Create: `docs/adr/0024-e2e-testing-strategy.md`

**Interfaces:**
- Consumes: nothing new (documents Tasks 1-9).

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0024-e2e-testing-strategy.md` using the full content already
drafted and approved in `/Users/imac/.claude/plans/1-as-user-i-polymorphic-willow.md`'s
"§6. New ADR" section — copy that content verbatim into the standard template
(`# 0024 — Title`, `**Status:**`, `## Context`, `## Decision`, `## Alternatives
Considered`, `## Consequences`, `## Security Implications`, `## Privacy
Implications`, `## Reversibility`), matching the exact section wording already
approved there.

- [ ] **Step 2: Run the deliberate-break rehearsal**

This is a manual, one-time verification (not something to automate) that
the suite actually fails correctly when the system is broken — do each of
the four in turn, on a throwaway local checkout, reverting after each:

1. Temporarily edit `backend/app/privacy_gateway/pipeline.py`'s
   `deanonymize` to `return llm_output` unconditionally (skip real
   resolution) → run `chat-roundtrip.spec.ts` → confirm it FAILS on the
   "reply contains raw values" assertion, not a timeout. Revert.
2. Temporarily edit `backend/app/auth/permissions.py`'s `can_read_conversation`
   to always `return owner.id == user.user_id` (ignore scope entirely) → run
   `rbac-branch-visibility.spec.ts` → confirm it FAILS on the "staff sees it
   after grant" assertion. Revert.
3. Temporarily set a wrong value for `NEXTAUTH_SECRET` in the environment
   used only by `npm test` (not the running frontend container) → run
   `chat-roundtrip.spec.ts` → confirm it FAILS with an auth/redirect error
   (not a silent unauthenticated pass-through). Revert.
4. `docker compose stop backend` → run `npm test` from `e2e/` → confirm
   `wait-for-health.sh` fails fast with its clear message, not a vague
   Playwright timeout inside a test file. `docker compose start backend`.

Record the outcome of all four in the commit message for this task (a
one-line summary per scenario is sufficient) as the evidence this
verification actually happened.

- [ ] **Step 3: Run the full local verification per the approved plan's Verification section**

Run: `docker compose up -d --build` (repo root), then
`cd e2e && npm ci && npx playwright install --with-deps chromium && npm test`
Expected: all four specs (8 tests total across Tasks 5-8) PASS against the
real local stack.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0024-e2e-testing-strategy.md
git commit -m "docs: record the e2e testing strategy as ADR-0024"
```

---

## What this plan does not cover

The full RBAC permission matrix beyond branch-read visibility, entitlement-
gating edge cases beyond the chat journey's needs, a scored corpus-benchmark
CI job (ADR-0017), Documents-app e2e coverage (no frontend route exists
yet), multi-browser/mobile testing, and visual regression testing are all
Phase 2 — a separate future plan, per
`docs/superpowers/specs`-equivalent scoping already recorded in the approved
design at `/Users/imac/.claude/plans/1-as-user-i-polymorphic-willow.md`.
