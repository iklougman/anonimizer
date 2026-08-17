# Backend Chat Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the frontend a real API to talk to — Keycloak-backed auth, an `LLMProvider` abstraction with Ollama and OpenAI adapters, and a chat API that chains auth → `sanitize()` → LLM call → `deanonymize()` — so the privacy pipeline built in the previous plan is reachable over HTTP.

**Architecture:** A single pre-provisioned Keycloak realm validates JWTs and supplies `tenant_id`/`user_id` via a FastAPI dependency chain (`get_current_user` → `get_db_session`, RLS-bound). `LLMProvider` is a small protocol with `OllamaProvider`/`OpenAIProvider` adapters selected by `Settings.llm_provider`. The chat API's send-message endpoint buffers the full pipeline (sanitize → LLM → deanonymize) before ever responding, then replays the validated result to the client as SSE chunks — so "no partial output" holds at the HTTP layer, not only inside the output guard.

**Tech Stack:** `PyJWT` (RS256, JWKS-based validation), `httpx` (LLM provider calls + JWKS fetch), FastAPI `StreamingResponse` (SSE), existing SQLAlchemy 2.0 / repositories / `Pipeline` / `tenant_scoped_session` layer, Keycloak 24 (realm import), pytest against a real Postgres 16 and a stub `LLMProvider` (no real network calls in the default CI run).

## Global Constraints

- **Auth scope for this slice is one pre-provisioned dev realm, not realm-per-tenant automation.** `keycloak/realm-export.json` is imported at container start; two seeded test tenants/users cover both single- and multi-tenant testing. Automated Keycloak-admin-API realm provisioning is out of scope (design spec §1).
- **Both `OllamaProvider` and `OpenAIProvider` ship in this slice** (ADR-0016, ADR-0022) — not Ollama-only. `openai_api_key` is required and validated at `Settings` construction time whenever `llm_provider == "openai"`; missing it is a startup failure, not a first-request failure (ADR-0020).
- **The send-message endpoint is non-streaming from the LLM provider, buffered-then-replayed to the client.** Both provider adapters call their backend with `stream: false`. The HTTP response only becomes `text/event-stream` after `sanitize()` → `LLMProvider.complete()` → `deanonymize()` have already all succeeded; every failure path is a plain JSON error response, never a half-open stream (ADR-0014, ADR-0020, design spec §5).
- **`tenant_id` is never trusted transitively from a JWT claim alone** (ADR-0021). `get_current_user` re-validates the claim against the `tenants`/`users` tables before any tenant-scoped code runs.
- **Fail-closed everywhere** (ADR-0020): Keycloak/JWKS unreachable → `503`, no fallback auth. `sanitize()` raising → `422`, nothing persisted, no LLM call made. LLM provider transport failure → `502`. `deanonymize()` raising (leakage or unresolved token) → `500`, audit-logged, generic message to the client — never echo what leaked.
- **Messages persist only pseudonymized text** (master design doc §4). `messages.sanitized_content` for both roles is always token-bearing text, never the human-readable reconstruction — the assistant role's stored value is literally the raw `LLMProvider.complete()` output (already token-shaped, since the LLM was only ever given pseudonymized input), stored only after `deanonymize()` has proven it leak-free and fully resolvable.
- **Only synthetic/fabricated data is ever sent to `OpenAIProvider`** (ADR-0022) — a project-wide data-handling rule this plan does not enforce in code (there is no way to distinguish synthetic from real input at the API layer); it governs how this slice is *used*, documented in README.
- **ADR-0001 network isolation is unaffected.** `app/auth/` and `app/llm_gateway/` are the codebase's chosen network boundaries for JWKS fetches and LLM calls respectively — both sit outside `app/privacy_gateway/`, so the existing import-linter contract ("Privacy gateway must not be network-capable") needs no change and must still report KEPT after every task.
- **New runtime dependencies:** `httpx` (moved from dev-only to a main dependency), `pyjwt` (RS256 support via the already-present `cryptography` package). No other new dependencies.
- Local dev assumes `docker compose up -d postgres keycloak ollama` is running, `alembic upgrade head` has been run, `secrets/master.key` exists, and (after Task 1) `python scripts/seed_dev_tenants.py` has been run once.

## Resolved Design Ambiguities

1. **Chat API routes depend on `Pipeline`/`LLMProvider` via `Depends(get_pipeline)`/`Depends(get_provider)`, not the free `sanitize()`/`deanonymize()` functions.** Both are already zero-argument, `lru_cache`d factories (`app.privacy_gateway.pipeline.get_pipeline`, `app.llm_gateway.registry.get_provider`) — using them directly as FastAPI dependencies lets integration tests override them with an in-memory-gazetteer `Pipeline` (exactly like Task 12 of the previous plan's `corpus_pipeline` fixture) and a stub `LLMProvider`, so CI never needs the real ORDO/Krankenhausverzeichnis reference data or a live Ollama/OpenAI connection.
2. **Interim commits inside the send-message route.** The user's message is persisted and committed *before* `LLMProvider.complete()` is called, so a provider outage (`502`) doesn't roll back and lose an already-sanitized, already-risk-scored message. The audit-event row on a `deanonymize()` failure is committed on its own before the `500` is raised, for the same reason.
3. **A common `LLMProviderError` base class** in `app.llm_gateway.provider`, with `OllamaProviderError`/`OpenAIProviderError` as subclasses, so the chat API can map "the configured provider failed" to `502` with one `except` clause regardless of which adapter is active.
4. **`get_current_user` is decomposed into `get_jwt_validator` + `get_user_resolver`, both their own zero-arg `Depends`.** This makes `get_current_user` fully unit-testable (fake validator, fake resolver, no network or Postgres) while `resolve_authenticated_user` gets its own integration test against real Postgres.
5. **Two separate Keycloak URLs.** `keycloak_issuer_url` (what the browser sees, checked against the JWT's `iss` claim) and `keycloak_jwks_url` (what the backend container uses to fetch signing keys over the Docker network) are different settings because inside Compose the backend reaches Keycloak via the service name (`http://keycloak:8080/...`) while the browser — and therefore the token's issuer claim — uses `http://localhost:8080/...`.
6. **`conversations.updated_at` is a real column, touched explicitly**, not derived by joining `messages` at read time. Simpler and cheaper for MVP scope than a per-list-request aggregate query; `ConversationRepository.touch()` sets it whenever a message is added to that conversation.
7. **Dev tenant/user seeding needs fixed UUIDs matching the Keycloak realm export.** `TenantRepository.create()` gains an optional `tenant_id: uuid.UUID | None = None` parameter (default `None` preserves today's random-UUID behavior, so no existing caller or test changes) so `scripts/seed_dev_tenants.py` can create tenants whose IDs match the `tenant_id` attribute baked into `keycloak/realm-export.json`'s two seeded users, and whose `keycloak_subject` matches those users' fixed Keycloak `id`s.
8. **The 500 audit-event on a leakage/unresolved-token failure uses `entity_type="UNKNOWN"` and `token=""`.** `OutputGuard.restore()` raises with a human-readable message string, not structured span data (deliberately — see its docstring), so there is nothing more specific to log without changing `output_guard/guard.py`, which is out of scope for this slice.

---

## Task 1: Dependencies, Settings, Keycloak dev realm, dev seeding

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/config.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `keycloak/realm-export.json`
- Create: `backend/app/db/repositories/tenant_repository.py` (modify — add optional `tenant_id` param)
- Create: `backend/scripts/seed_dev_tenants.py`
- Test: `backend/tests/unit/test_config.py` (extend)
- Test: `backend/tests/integration/test_tenant_repository.py` (extend)

**Interfaces:**
- Produces: `Settings.keycloak_issuer_url`, `Settings.keycloak_jwks_url`, `Settings.keycloak_audience`, `Settings.llm_provider`, `Settings.openai_api_key`, `Settings.openai_model`, `Settings.ollama_base_url`, `Settings.ollama_model`; `TenantRepository.create(..., tenant_id: uuid.UUID | None = None)`; `httpx` and `pyjwt` importable at runtime.

- [ ] **Step 1: Add `httpx` and `pyjwt` as runtime dependencies**

Edit `backend/pyproject.toml`. Move `httpx` out of `[project.optional-dependencies].dev` and add it, plus `pyjwt`, to `[project].dependencies`:

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
    "presidio-analyzer>=2.2,<3",
    "spacy>=3.8,<3.9",
    "rdflib>=7.1,<8",
    "openpyxl>=3.1,<4",
    "httpx>=0.27,<0.28",
    "pyjwt>=2.9,<3",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3,<9",
    "pytest-cov>=5.0,<6",
    "ruff>=0.7,<0.8",
    "import-linter>=2.1,<3",
]
```

- [ ] **Step 2: Install the new dependencies**

Run:
```bash
cd backend
pip install -e ".[dev]"
```
Expected: succeeds; `pip list` shows `httpx` and `pyjwt`.

- [ ] **Step 3: Write the failing Settings tests**

Append to `backend/tests/unit/test_config.py`:

```python
def test_settings_auth_and_llm_defaults(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    for var in [
        "KEYCLOAK_ISSUER_URL", "KEYCLOAK_JWKS_URL", "KEYCLOAK_AUDIENCE",
        "LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL",
        "OLLAMA_BASE_URL", "OLLAMA_MODEL",
    ]:
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.keycloak_issuer_url == "http://localhost:8080/realms/chatgpt-proxy-dev"
    assert settings.keycloak_jwks_url == (
        "http://keycloak:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/certs"
    )
    assert settings.keycloak_audience == "chatgpt-proxy-frontend"
    assert settings.llm_provider == "ollama"
    assert settings.openai_api_key is None
    assert settings.openai_model == "gpt-4o-mini"
    assert settings.ollama_base_url == "http://ollama:11434"
    assert settings.ollama_model == "llama3.1"


def test_settings_rejects_openai_provider_without_an_api_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_accepts_openai_provider_with_an_api_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "openai"
    assert settings.openai_api_key == "sk-test"
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd backend && pytest tests/unit/test_config.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'keycloak_issuer_url'`.

- [ ] **Step 5: Add the new settings to `backend/app/config.py`**

Replace the file in full:

```python
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url

# Design spec §2: "PATIENT_NUMBER — configurable numeric ID pattern." The default
# covers the label forms German clinical notes actually use. The optional named group
# `value` marks the part of the match that is the identifier itself, so the token
# replaces only the digits and the surrounding label stays readable for the LLM.
DEFAULT_PATIENT_NUMBER_PATTERN = (
    r"(?:Patientennummer|Patienten-Nr\.|Pat\.-Nr\.|Fallnummer|Fallnr\.)"
    r"\s*:?\s*(?P<value>\d{6,10})"
)


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"]
    log_level: str = "INFO"
    debug: bool = False
    cors_allowed_origins: list[str] = Field(default_factory=list)
    database_url: str
    master_key_path: str
    app_runtime_password: str
    patient_number_pattern: str = DEFAULT_PATIENT_NUMBER_PATTERN
    # Parsing the bundled ORDO OWL + Krankenhausverzeichnis xlsx at startup (design
    # spec §6) costs minutes and gigabytes; the test suite injects small in-memory
    # reference sets instead, so it turns the warm-up off.
    warm_reference_data_on_startup: bool = True

    # Backend-chat-slice design doc §2: two separate Keycloak URLs because inside
    # Docker Compose the backend reaches Keycloak via the service name, while the
    # browser (and therefore the token's `iss` claim) uses localhost.
    keycloak_issuer_url: str = "http://localhost:8080/realms/chatgpt-proxy-dev"
    keycloak_jwks_url: str = (
        "http://keycloak:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/certs"
    )
    keycloak_audience: str = "chatgpt-proxy-frontend"

    # ADR-0016 / ADR-0022: both providers ship in the MVP.
    llm_provider: Literal["ollama", "openai"] = "ollama"
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    @model_validator(mode="after")
    def _require_openai_key_when_selected(self) -> "Settings":
        # ADR-0020: fail closed on misconfiguration at startup, not at first request.
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when LLM_PROVIDER=openai"
            )
        return self

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

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && pytest tests/unit/test_config.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 7: Add an optional fixed `tenant_id` to `TenantRepository.create()`**

Modify `backend/app/db/repositories/tenant_repository.py`'s `create` method signature and body:

```python
    def create(
        self,
        name: str,
        keycloak_realm: str,
        retention_days: int,
        tenant_id: uuid.UUID | None = None,
    ) -> Tenant:
        tenant = Tenant(
            id=tenant_id if tenant_id is not None else uuid.uuid4(),
            name=name,
            keycloak_realm=keycloak_realm,
            retention_days=retention_days,
        )
        self.session.add(tenant)
        self.session.flush()
```

(the rest of the method is unchanged). This preserves existing behavior exactly when `tenant_id` is omitted — every existing caller (Task 12's `new_scope`, the tenant repository tests) passes no `tenant_id` and keeps getting a random UUID.

- [ ] **Step 8: Add a regression test for the new parameter**

Append to `backend/tests/integration/test_tenant_repository.py`:

```python
def test_create_accepts_an_explicit_tenant_id(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))
    fixed_id = uuid.uuid4()

    with SessionLocal() as session:
        repo = TenantRepository(session, key_provider)
        tenant = repo.create(
            name="Fixed Clinic",
            keycloak_realm=f"realm-{uuid.uuid4()}",
            retention_days=30,
            tenant_id=fixed_id,
        )
        session.commit()

    assert tenant.id == fixed_id
```

- [ ] **Step 9: Run the repository tests to verify they pass**

Run: `cd backend && pytest tests/integration/test_tenant_repository.py -v`
Expected: PASS, including the new test and the pre-existing `test_create_tenant_provisions_a_dek`/`test_get_returns_none_for_unknown_tenant` (unchanged behavior).

- [ ] **Step 10: Create the Keycloak dev realm export**

`keycloak/realm-export.json`:

```json
{
  "realm": "chatgpt-proxy-dev",
  "enabled": true,
  "sslRequired": "none",
  "registrationAllowed": false,
  "clients": [
    {
      "clientId": "chatgpt-proxy-frontend",
      "enabled": true,
      "publicClient": true,
      "protocol": "openid-connect",
      "standardFlowEnabled": true,
      "directAccessGrantsEnabled": true,
      "redirectUris": ["http://localhost:3000/*"],
      "webOrigins": ["http://localhost:3000"],
      "protocolMappers": [
        {
          "name": "tenant_id",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-usermodel-attribute-mapper",
          "consentRequired": false,
          "config": {
            "user.attribute": "tenant_id",
            "claim.name": "tenant_id",
            "jsonType.label": "String",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "userinfo.token.claim": "true"
          }
        },
        {
          "name": "audience",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-audience-mapper",
          "consentRequired": false,
          "config": {
            "included.client.audience": "chatgpt-proxy-frontend",
            "id.token.claim": "false",
            "access.token.claim": "true"
          }
        }
      ]
    }
  ],
  "users": [
    {
      "id": "11111111-1111-4111-8111-111111111111",
      "username": "dr.mueller",
      "email": "dr.mueller@clinic-a.example",
      "firstName": "Lukas",
      "lastName": "Mueller",
      "enabled": true,
      "emailVerified": true,
      "requiredActions": [],
      "credentials": [
        {"type": "password", "value": "dev-password", "temporary": false}
      ],
      "attributes": {
        "tenant_id": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]
      }
    },
    {
      "id": "22222222-2222-4222-8222-222222222222",
      "username": "dr.klein",
      "email": "dr.klein@clinic-b.example",
      "firstName": "Anna",
      "lastName": "Klein",
      "enabled": true,
      "emailVerified": true,
      "requiredActions": [],
      "credentials": [
        {"type": "password", "value": "dev-password", "temporary": false}
      ],
      "attributes": {
        "tenant_id": ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]
      }
    }
  ]
}
```

**Discovered during Step 16's verification (not obvious from the schema alone):** Keycloak 24's "Verify Profile" required action is enabled by default and resolves dynamically at login time — even with `requiredActions: []` stored on the user — whenever `firstName`/`lastName` are absent, and the direct-grant (ROPC) flow cannot satisfy it interactively, failing with `invalid_grant: Account is not fully set up`. Both `firstName`/`lastName` above are required for the manual E2E `curl` token flow (Step 14 / Task 10) to work, not optional flavor text.

**Discovered while validating Task 2's `JWTValidator` against a real token from this realm:** without an explicit audience protocol mapper, Keycloak does not put the client's own `clientId` in the access token's `aud` claim at all (confirmed by decoding a real token — no `aud` key present), so `jwt.decode(..., audience=...)` fails every token with `MissingRequiredClaimError: aud`. The `oidc-audience-mapper` above (`included.client.audience: "chatgpt-proxy-frontend"`) is required, not optional — this is the second correction the `audience`/`id.token.claim`/`access.token.claim` config values above already include.

- [ ] **Step 11: Wire the realm import and new env vars into `docker-compose.yml`**

Modify the `keycloak` service:

```yaml
  keycloak:
    image: quay.io/keycloak/keycloak:24.0
    command: start-dev --import-realm
    environment:
      KEYCLOAK_ADMIN: ${KEYCLOAK_ADMIN}
      KEYCLOAK_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD}
    volumes:
      - ./keycloak/realm-export.json:/opt/keycloak/data/import/realm-export.json:ro
    ports:
      - "127.0.0.1:8080:8080"
```

Modify the `backend` service (add the new environment keys and a `keycloak` dependency):

```yaml
  backend:
    build: ./backend
    environment:
      ENVIRONMENT: ${ENVIRONMENT}
      LOG_LEVEL: ${LOG_LEVEL}
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      MASTER_KEY_PATH: /run/secrets/master_key
      APP_RUNTIME_PASSWORD: ${APP_RUNTIME_PASSWORD}
      CORS_ALLOWED_ORIGINS: ${CORS_ALLOWED_ORIGINS:-["http://localhost:3000"]}
      KEYCLOAK_ISSUER_URL: http://localhost:8080/realms/chatgpt-proxy-dev
      KEYCLOAK_JWKS_URL: http://keycloak:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/certs
      KEYCLOAK_AUDIENCE: chatgpt-proxy-frontend
      LLM_PROVIDER: ${LLM_PROVIDER:-ollama}
      OLLAMA_BASE_URL: http://ollama:11434
      OLLAMA_MODEL: ${OLLAMA_MODEL:-llama3.1}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
      OPENAI_MODEL: ${OPENAI_MODEL:-gpt-4o-mini}
    secrets:
      - master_key
    ports:
      - "127.0.0.1:8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      keycloak:
        condition: service_started
```

- [ ] **Step 12: Document the new env vars in `.env.example`**

Append to `.env.example`:

```
# --- Frontend origin allowed to call the backend API ---
CORS_ALLOWED_ORIGINS=["http://localhost:3000"]

# --- LLM Gateway ---
# "ollama" (local, no egress) or "openai" (external egress; only sanitized text
# ever crosses this boundary -- ADR-0013, ADR-0022).
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1
# Required only when LLM_PROVIDER=openai. Only synthetic/fabricated data is ever
# sent to OpenAI in this project (ADR-0022) -- never real or real-derived patient data.
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
```

- [ ] **Step 13: Create the dev tenant/user seed script**

`backend/scripts/seed_dev_tenants.py`:

```python
"""Seed the two dev tenants/users matching keycloak/realm-export.json's fixed
user IDs and tenant_id attributes.

Local dev and the manual E2E test (README) only -- never run against a
production database. Idempotent: re-running skips tenants that already exist.
"""

from __future__ import annotations

import uuid

from app.config import get_settings
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Tenant
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider

DEV_TENANTS = [
    {
        "tenant_id": uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        "name": "Clinic A",
        # tenants.keycloak_realm is UNIQUE (migration 0001); both dev tenants share
        # the one imported Keycloak realm ("chatgpt-proxy-dev", see
        # keycloak/realm-export.json), so this column can't literally hold that
        # shared value for both rows -- it's a per-tenant label within that realm,
        # not the realm name itself, for this single-dev-realm slice (Resolved
        # Design Ambiguity #1: this plan does not implement realm-per-tenant).
        "keycloak_realm": "chatgpt-proxy-dev-clinic-a",
        "keycloak_subject": "11111111-1111-4111-8111-111111111111",
        "email": "dr.mueller@clinic-a.example",
    },
    {
        "tenant_id": uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        "name": "Clinic B",
        "keycloak_realm": "chatgpt-proxy-dev-clinic-b",
        "keycloak_subject": "22222222-2222-4222-8222-222222222222",
        "email": "dr.klein@clinic-b.example",
    },
]


def main() -> int:
    key_provider = FileSecretKeyProvider(get_settings().master_key_path)
    for entry in DEV_TENANTS:
        with SessionLocal() as session:
            if session.get(Tenant, entry["tenant_id"]) is not None:
                print(f"{entry['name']}: already seeded, skipping")
                continue
            TenantRepository(session, key_provider).create(
                name=entry["name"],
                keycloak_realm=entry["keycloak_realm"],
                retention_days=30,
                tenant_id=entry["tenant_id"],
            )
            session.commit()

        with tenant_scoped_session(entry["tenant_id"]) as session:
            UserRepository(session).create(
                entry["tenant_id"],
                keycloak_subject=entry["keycloak_subject"],
                email=entry["email"],
                role="doctor",
            )
        print(f"{entry['name']}: seeded tenant {entry['tenant_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 14: Document the dev auth setup in `README.md`**

In `README.md`, under `## Local development`, add a step after `alembic upgrade head` and before `docker compose up --build` (renumber subsequent steps):

    Seed the two dev tenants/users that match `keycloak/realm-export.json`
    (idempotent, safe to re-run):

    ```bash
    cd backend
    python scripts/seed_dev_tenants.py
    ```

    Keycloak imports `chatgpt-proxy-dev` automatically on container start. Test
    logins: `dr.mueller` / `dev-password` (Clinic A) and `dr.klein` /
    `dev-password` (Clinic B), both against
    `http://localhost:8080/realms/chatgpt-proxy-dev`. To fetch a token without a
    browser (useful for `curl`-testing the API directly):

    ```bash
    curl -s http://localhost:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/token \
      -d grant_type=password -d client_id=chatgpt-proxy-frontend \
      -d username=dr.mueller -d password=dev-password | jq -r .access_token
    ```

- [ ] **Step 15: Run the full backend suite and linters**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; both import-linter contracts KEPT; all tests pass.

- [ ] **Step 16: Bring up the stack and verify the realm import**

Run:
```bash
docker compose up -d postgres keycloak
```
Then, once Keycloak is healthy:
```bash
curl -s http://localhost:8080/realms/chatgpt-proxy-dev/.well-known/openid-configuration | jq .issuer
```
Expected: `"http://localhost:8080/realms/chatgpt-proxy-dev"`. If this 404s, check `docker compose logs keycloak` for an import error and fix `keycloak/realm-export.json` before proceeding — every later task's manual testing depends on this realm existing.

- [ ] **Step 17: Commit**

```bash
git add backend/pyproject.toml backend/app/config.py backend/app/db/repositories/tenant_repository.py \
  backend/scripts/seed_dev_tenants.py backend/tests/unit/test_config.py \
  backend/tests/integration/test_tenant_repository.py docker-compose.yml .env.example README.md \
  keycloak/realm-export.json
git commit -m "feat: add auth/LLM settings, Keycloak dev realm, and dev tenant seeding"
```

---

## Task 2: JWT validator

**Files:**
- Create: `backend/app/auth/jwt_validator.py`
- Test: `backend/tests/unit/test_jwt_validator.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (pure `httpx` + `pyjwt`).
- Produces: `TokenClaims(tenant_id: uuid.UUID, keycloak_subject: str)`; `JWTValidator(jwks_url: str, issuer: str, audience: str, http_client: httpx.Client | None = None)` with `.validate(token: str) -> TokenClaims`; `KeycloakUnreachableError`, `InvalidTokenError`. Consumed by Task 3's `dependencies.py`.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/test_jwt_validator.py`:

```python
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.auth.jwt_validator import InvalidTokenError, JWTValidator, KeycloakUnreachableError

ISSUER = "http://localhost:8080/realms/chatgpt-proxy-dev"
AUDIENCE = "chatgpt-proxy-frontend"
JWKS_URL = "http://keycloak:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/certs"
KID = "test-key-1"


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def jwks_payload(rsa_keypair):
    _, public_key = rsa_keypair
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = KID
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return {"keys": [jwk]}


def _make_validator(jwks_payload) -> JWTValidator:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=jwks_payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return JWTValidator(jwks_url=JWKS_URL, issuer=ISSUER, audience=AUDIENCE, http_client=client)


def _make_token(
    private_key,
    *,
    tenant_id=None,
    sub="doctor-1",
    issuer=ISSUER,
    audience=AUDIENCE,
    expired=False,
    kid=KID,
):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "iat": now,
        "exp": now - timedelta(minutes=5) if expired else now + timedelta(minutes=5),
    }
    if tenant_id is not None:
        claims["tenant_id"] = str(tenant_id)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def test_valid_token_returns_claims(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    tenant_id = uuid.uuid4()
    token = _make_token(private_key, tenant_id=tenant_id)

    claims = validator.validate(token)

    assert claims.tenant_id == tenant_id
    assert claims.keycloak_subject == "doctor-1"


def test_expired_token_is_rejected(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    token = _make_token(private_key, tenant_id=uuid.uuid4(), expired=True)

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_wrong_issuer_is_rejected(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    token = _make_token(private_key, tenant_id=uuid.uuid4(), issuer="http://evil.example/realms/x")

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_wrong_audience_is_rejected(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    token = _make_token(private_key, tenant_id=uuid.uuid4(), audience="some-other-client")

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_missing_tenant_id_claim_is_rejected(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    token = _make_token(private_key, tenant_id=None)

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_unknown_kid_is_rejected(rsa_keypair, jwks_payload):
    private_key, _ = rsa_keypair
    validator = _make_validator(jwks_payload)
    token = _make_token(private_key, tenant_id=uuid.uuid4(), kid="not-in-jwks")

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_signature_from_a_different_key_is_rejected(jwks_payload):
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    validator = _make_validator(jwks_payload)
    token = _make_token(other_private_key, tenant_id=uuid.uuid4())

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_jwks_endpoint_unreachable_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = JWTValidator(jwks_url=JWKS_URL, issuer=ISSUER, audience=AUDIENCE, http_client=client)

    with pytest.raises(KeycloakUnreachableError):
        validator.validate("irrelevant-token")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_jwt_validator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth.jwt_validator'`.

- [ ] **Step 3: Create `backend/app/auth/jwt_validator.py`**

```python
from __future__ import annotations

import time
import uuid

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

# Keycloak rotates signing keys rarely; caching the JWKS response avoids putting a
# network round-trip (and a Keycloak outage) on every single authenticated request.
_JWKS_CACHE_TTL_SECONDS = 300


class KeycloakUnreachableError(Exception):
    """The JWKS endpoint could not be reached. ADR-0020/0021: fail closed, no
    fallback authentication path."""


class InvalidTokenError(Exception):
    """The token's signature, issuer, audience, expiry, or required claims failed
    verification."""


class TokenClaims:
    __slots__ = ("tenant_id", "keycloak_subject")

    def __init__(self, tenant_id: uuid.UUID, keycloak_subject: str) -> None:
        self.tenant_id = tenant_id
        self.keycloak_subject = keycloak_subject


class JWTValidator:
    """Validates a Keycloak-issued JWT against its realm's JWKS (RS256 only)."""

    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._audience = audience
        self._http_client = http_client if http_client is not None else httpx.Client(timeout=5.0)
        self._jwks_cache: dict[str, object] | None = None
        self._jwks_cached_at: float = 0.0

    def validate(self, token: str) -> TokenClaims:
        jwks = self._get_jwks()

        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError(f"malformed token: {exc}") from exc

        key = next((k for k in jwks["keys"] if k.get("kid") == header.get("kid")), None)
        if key is None:
            raise InvalidTokenError(f"no signing key found for kid={header.get('kid')!r}")
        public_key = RSAAlgorithm.from_jwk(key)

        try:
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError(str(exc)) from exc

        tenant_id_claim = claims.get("tenant_id")
        keycloak_subject = claims.get("sub")
        if not tenant_id_claim or not keycloak_subject:
            raise InvalidTokenError("token is missing required tenant_id or sub claim")
        try:
            tenant_id = uuid.UUID(tenant_id_claim)
        except ValueError as exc:
            raise InvalidTokenError(
                f"tenant_id claim is not a valid UUID: {tenant_id_claim!r}"
            ) from exc

        return TokenClaims(tenant_id=tenant_id, keycloak_subject=keycloak_subject)

    def _get_jwks(self) -> dict[str, object]:
        now = time.monotonic()
        if self._jwks_cache is not None and now - self._jwks_cached_at < _JWKS_CACHE_TTL_SECONDS:
            return self._jwks_cache
        try:
            response = self._http_client.get(self._jwks_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KeycloakUnreachableError(
                f"could not fetch JWKS from {self._jwks_url}: {exc}"
            ) from exc
        self._jwks_cache = response.json()
        self._jwks_cached_at = now
        return self._jwks_cache
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_jwt_validator.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth/jwt_validator.py backend/tests/unit/test_jwt_validator.py
git commit -m "feat: add JWT validator against Keycloak JWKS"
```

---

## Task 3: Tenant/user resolution and FastAPI auth dependencies

**Files:**
- Create: `backend/app/auth/tenant_resolver.py`
- Create: `backend/app/auth/dependencies.py`
- Test: `backend/tests/integration/test_tenant_resolver.py`
- Test: `backend/tests/unit/test_auth_dependencies.py`

**Interfaces:**
- Consumes: `TokenClaims`, `JWTValidator`, `KeycloakUnreachableError`, `InvalidTokenError` (Task 2); `TenantRepository`, `UserRepository`, `SessionLocal`, `tenant_scoped_session` (existing).
- Produces: `AuthenticatedUser(tenant_id: uuid.UUID, user_id: uuid.UUID, role: str)`; `resolve_authenticated_user(claims: TokenClaims) -> AuthenticatedUser`; `UnknownTenantError`, `UnknownUserError`; `get_jwt_validator()`, `get_user_resolver()`, `get_current_user(...) -> AuthenticatedUser`, `get_db_session(...) -> Iterator[Session]`. Consumed by every Task 5+ API route.

- [ ] **Step 1: Write the failing tenant-resolver test**

`backend/tests/integration/test_tenant_resolver.py`:

```python
import uuid

import pytest

from app.auth.jwt_validator import TokenClaims
from app.auth.tenant_resolver import UnknownTenantError, UnknownUserError, resolve_authenticated_user
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def _create_tenant_and_user(tmp_path, keycloak_subject="sub-1", role="doctor"):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))

    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Test Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=keycloak_subject, email="doc@example.com", role=role
        )
        user_id = user.id

    return tenant_id, user_id


def test_resolves_a_known_tenant_and_user(tmp_path):
    tenant_id, user_id = _create_tenant_and_user(tmp_path)
    claims = TokenClaims(tenant_id=tenant_id, keycloak_subject="sub-1")

    result = resolve_authenticated_user(claims)

    assert result.tenant_id == tenant_id
    assert result.user_id == user_id
    assert result.role == "doctor"


def test_unknown_tenant_id_is_rejected():
    claims = TokenClaims(tenant_id=uuid.uuid4(), keycloak_subject="sub-1")
    with pytest.raises(UnknownTenantError):
        resolve_authenticated_user(claims)


def test_known_tenant_unknown_subject_is_rejected(tmp_path):
    tenant_id, _ = _create_tenant_and_user(tmp_path)
    claims = TokenClaims(tenant_id=tenant_id, keycloak_subject="someone-else")
    with pytest.raises(UnknownUserError):
        resolve_authenticated_user(claims)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/integration/test_tenant_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth.tenant_resolver'`.

- [ ] **Step 3: Create `backend/app/auth/tenant_resolver.py`**

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.auth.jwt_validator import TokenClaims
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import Tenant


class UnknownTenantError(Exception):
    """The token's tenant_id claim does not match any provisioned tenant."""


class UnknownUserError(Exception):
    """The token's subject does not match any user provisioned for that tenant."""


@dataclass(frozen=True)
class AuthenticatedUser:
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: str


def resolve_authenticated_user(claims: TokenClaims) -> AuthenticatedUser:
    """ADR-0021: the JWT's tenant_id claim is never trusted transitively -- it must
    resolve to a real tenant, and the subject must resolve to a real user of that
    tenant, before any tenant-scoped code runs."""
    with SessionLocal() as session:
        tenant = session.get(Tenant, claims.tenant_id)
    if tenant is None:
        raise UnknownTenantError(f"no tenant provisioned for tenant_id={claims.tenant_id}")

    with tenant_scoped_session(claims.tenant_id) as session:
        user = UserRepository(session).get_by_keycloak_subject(
            claims.tenant_id, claims.keycloak_subject
        )
    if user is None:
        raise UnknownUserError(
            f"no user provisioned for keycloak_subject={claims.keycloak_subject!r} "
            f"in tenant {claims.tenant_id}"
        )

    return AuthenticatedUser(tenant_id=claims.tenant_id, user_id=user.id, role=user.role)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/integration/test_tenant_resolver.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Write the failing FastAPI-dependencies test**

`backend/tests/unit/test_auth_dependencies.py`:

```python
import uuid

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user, get_jwt_validator, get_user_resolver
from app.auth.jwt_validator import InvalidTokenError, KeycloakUnreachableError, TokenClaims
from app.auth.tenant_resolver import AuthenticatedUser, UnknownTenantError


class _FakeValidator:
    def __init__(self, result_or_error):
        self._result_or_error = result_or_error

    def validate(self, token):
        if isinstance(self._result_or_error, Exception):
            raise self._result_or_error
        return self._result_or_error


def _build_app(validator, resolver):
    app = FastAPI()
    app.dependency_overrides[get_jwt_validator] = lambda: validator
    app.dependency_overrides[get_user_resolver] = lambda: resolver

    @app.get("/whoami")
    def whoami(user: AuthenticatedUser = Depends(get_current_user)):
        return {"tenant_id": str(user.tenant_id), "user_id": str(user.user_id)}

    return TestClient(app)


def test_missing_bearer_header_is_rejected():
    client = _build_app(_FakeValidator(Exception("unused")), resolver=lambda claims: None)
    response = client.get("/whoami")
    assert response.status_code == 401


def test_keycloak_unreachable_fails_closed_with_503():
    client = _build_app(
        _FakeValidator(KeycloakUnreachableError("jwks down")), resolver=lambda claims: None
    )
    response = client.get("/whoami", headers={"Authorization": "Bearer x"})
    assert response.status_code == 503


def test_invalid_token_is_rejected_with_401():
    client = _build_app(_FakeValidator(InvalidTokenError("bad sig")), resolver=lambda claims: None)
    response = client.get("/whoami", headers={"Authorization": "Bearer x"})
    assert response.status_code == 401


def test_unknown_tenant_is_rejected_with_401():
    claims = TokenClaims(tenant_id=uuid.uuid4(), keycloak_subject="sub-1")

    def resolver(_claims):
        raise UnknownTenantError("no such tenant")

    client = _build_app(_FakeValidator(claims), resolver=resolver)
    response = client.get("/whoami", headers={"Authorization": "Bearer x"})
    assert response.status_code == 401


def test_valid_token_resolves_to_the_authenticated_user():
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    claims = TokenClaims(tenant_id=tenant_id, keycloak_subject="sub-1")

    def resolver(_claims):
        return AuthenticatedUser(tenant_id=tenant_id, user_id=user_id, role="doctor")

    client = _build_app(_FakeValidator(claims), resolver=resolver)
    response = client.get("/whoami", headers={"Authorization": "Bearer x"})
    assert response.status_code == 200
    assert response.json() == {"tenant_id": str(tenant_id), "user_id": str(user_id)}
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_auth_dependencies.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.auth.dependencies'`.

- [ ] **Step 7: Create `backend/app/auth/dependencies.py`**

```python
from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.jwt_validator import InvalidTokenError, JWTValidator, KeycloakUnreachableError
from app.auth.tenant_resolver import (
    AuthenticatedUser,
    UnknownTenantError,
    UnknownUserError,
    resolve_authenticated_user,
)
from app.config import get_settings
from app.db.session import tenant_scoped_session


@lru_cache(maxsize=1)
def get_jwt_validator() -> JWTValidator:
    settings = get_settings()
    return JWTValidator(
        jwks_url=settings.keycloak_jwks_url,
        issuer=settings.keycloak_issuer_url,
        audience=settings.keycloak_audience,
    )


def get_user_resolver():
    """A thin, override-able indirection so `get_current_user` is unit-testable
    without a real Postgres connection -- `resolve_authenticated_user` itself is
    covered by its own integration test."""
    return resolve_authenticated_user


def get_current_user(
    request: Request,
    validator: JWTValidator = Depends(get_jwt_validator),
    resolve_user=Depends(get_user_resolver),
) -> AuthenticatedUser:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()

    try:
        claims = validator.validate(token)
    except KeycloakUnreachableError as exc:
        raise HTTPException(status_code=503, detail="authentication service unavailable") from exc
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc

    try:
        return resolve_user(claims)
    except (UnknownTenantError, UnknownUserError) as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc


def get_db_session(user: AuthenticatedUser = Depends(get_current_user)) -> Iterator[Session]:
    """Request-scoped, tenant-RLS-bound session for conversation/message repositories.

    Separate from the sessions `TokenVault` opens internally inside `Pipeline.sanitize`/
    `Pipeline.deanonymize`, which manage their own transactions per call.
    """
    with tenant_scoped_session(user.tenant_id) as session:
        yield session
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_auth_dependencies.py -v`
Expected: PASS (5 passed).

- [ ] **Step 9: Run the full backend suite and linters**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; both import-linter contracts KEPT; all tests pass.

- [ ] **Step 10: Commit**

```bash
git add backend/app/auth/tenant_resolver.py backend/app/auth/dependencies.py \
  backend/tests/integration/test_tenant_resolver.py backend/tests/unit/test_auth_dependencies.py
git commit -m "feat: add tenant/user resolution and FastAPI auth dependencies"
```

---

## Task 4: LLM Gateway — `LLMProvider` protocol, Ollama and OpenAI adapters, registry

**Files:**
- Create: `backend/app/llm_gateway/provider.py`
- Create: `backend/app/llm_gateway/ollama_provider.py`
- Create: `backend/app/llm_gateway/openai_provider.py`
- Create: `backend/app/llm_gateway/registry.py`
- Test: `backend/tests/unit/test_ollama_provider.py`
- Test: `backend/tests/unit/test_openai_provider.py`
- Test: `backend/tests/unit/test_llm_registry.py`

**Interfaces:**
- Consumes: `Settings.llm_provider`/`ollama_base_url`/`ollama_model`/`openai_api_key`/`openai_model` (Task 1).
- Produces: `LLMCompletion(text, tokens_in, tokens_out, cost_usd)`, `LLMProvider` protocol (`name: str`, `model: str`, `complete(prompt: str) -> LLMCompletion`), `LLMProviderError` (+ `OllamaProviderError`, `OpenAIProviderError` subclasses), `OllamaProvider`, `OpenAIProvider`, `get_provider() -> LLMProvider`. Consumed by Task 8's chat API.

- [ ] **Step 1: Write the failing provider protocol/dataclass module**

There is no separate test for `provider.py` itself (it has no logic, only types) — it is exercised by Steps 3-8 below. Create it now so the adapter tests can import it:

`backend/app/llm_gateway/provider.py`:

```python
from __future__ import annotations

import decimal
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMCompletion:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: decimal.Decimal


class LLMProviderError(Exception):
    """A provider adapter's request failed or returned an unexpected response
    shape. The chat API maps this to a single HTTP 502 regardless of which
    adapter is configured."""


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, prompt: str) -> LLMCompletion: ...
```

- [ ] **Step 2: Write the failing Ollama provider test**

`backend/tests/unit/test_ollama_provider.py`:

```python
import decimal

import httpx
import pytest

from app.llm_gateway.ollama_provider import OllamaProvider, OllamaProviderError
from app.llm_gateway.provider import LLMProviderError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_complete_returns_the_response_text_and_token_counts():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        body = httpx.Request("POST", request.url).content  # unused, keeps intent clear
        return httpx.Response(
            200,
            json={"response": "Guten Tag.", "prompt_eval_count": 12, "eval_count": 4},
        )

    provider = OllamaProvider(
        base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler)
    )

    completion = provider.complete("Hallo")

    assert completion.text == "Guten Tag."
    assert completion.tokens_in == 12
    assert completion.tokens_out == 4
    assert completion.cost_usd == decimal.Decimal("0")


def test_sends_stream_false_and_the_configured_model():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    provider = OllamaProvider(
        base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler)
    )
    provider.complete("Hallo")

    assert captured["body"] == {"model": "llama3.1", "prompt": "Hallo", "stream": False}


def test_missing_token_counts_default_to_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": "ok"})

    provider = OllamaProvider(
        base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler)
    )
    completion = provider.complete("Hallo")
    assert completion.tokens_in == 0
    assert completion.tokens_out == 0


def test_transport_error_raises_ollama_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OllamaProvider(
        base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler)
    )
    with pytest.raises(OllamaProviderError):
        provider.complete("Hallo")


def test_missing_response_field_raises_ollama_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = OllamaProvider(
        base_url="http://ollama:11434", model="llama3.1", http_client=_client(handler)
    )
    with pytest.raises(OllamaProviderError):
        provider.complete("Hallo")


def test_ollama_provider_error_is_an_llm_provider_error():
    assert issubclass(OllamaProviderError, LLMProviderError)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_ollama_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.llm_gateway.ollama_provider'`.

- [ ] **Step 4: Create `backend/app/llm_gateway/ollama_provider.py`**

```python
from __future__ import annotations

import decimal

import httpx

from app.llm_gateway.provider import LLMCompletion, LLMProviderError


class OllamaProviderError(LLMProviderError):
    """The Ollama HTTP call failed or returned an unexpected shape."""


class OllamaProvider:
    name = "ollama"

    def __init__(
        self, base_url: str, model: str, http_client: httpx.Client | None = None
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client if http_client is not None else httpx.Client(timeout=120.0)

    def complete(self, prompt: str) -> LLMCompletion:
        try:
            response = self._http_client.post(
                f"{self._base_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaProviderError(f"ollama request failed: {exc}") from exc

        body = response.json()
        text = body.get("response")
        if text is None:
            raise OllamaProviderError(f"ollama response missing 'response' field: {body}")

        # Local inference has no metered $ cost; token counts are best-effort (some
        # models omit eval_count/prompt_eval_count), used only for the research
        # benchmark's utility metrics, never for billing.
        return LLMCompletion(
            text=text,
            tokens_in=body.get("prompt_eval_count", 0),
            tokens_out=body.get("eval_count", 0),
            cost_usd=decimal.Decimal("0"),
        )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_ollama_provider.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Write the failing OpenAI provider test**

`backend/tests/unit/test_openai_provider.py`:

```python
import decimal
import json

import httpx
import pytest

from app.llm_gateway.openai_provider import OpenAIProvider, OpenAIProviderError
from app.llm_gateway.provider import LLMProviderError


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_complete_returns_text_tokens_and_a_computed_cost():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Guten Tag."}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
            },
        )

    provider = OpenAIProvider(
        api_key="sk-test", model="gpt-4o-mini", http_client=_client(handler)
    )

    completion = provider.complete("Hallo")

    assert completion.text == "Guten Tag."
    assert completion.tokens_in == 1000
    assert completion.tokens_out == 1000
    # 1000 prompt tokens @ $0.00015/1K + 1000 completion tokens @ $0.0006/1K
    assert completion.cost_usd == decimal.Decimal("0.00015") + decimal.Decimal("0.0006")


def test_sends_the_configured_model_and_prompt_as_a_user_message():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    provider = OpenAIProvider(
        api_key="sk-test", model="gpt-4o-mini", http_client=_client(handler)
    )
    provider.complete("Hallo")

    assert captured["body"] == {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Hallo"}],
    }


def test_unknown_model_costs_zero_rather_than_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
            },
        )

    provider = OpenAIProvider(
        api_key="sk-test", model="some-future-model", http_client=_client(handler)
    )
    completion = provider.complete("Hallo")
    assert completion.cost_usd == decimal.Decimal("0")


def test_transport_error_raises_openai_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini", http_client=_client(handler))
    with pytest.raises(OpenAIProviderError):
        provider.complete("Hallo")


def test_missing_expected_fields_raises_openai_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini", http_client=_client(handler))
    with pytest.raises(OpenAIProviderError):
        provider.complete("Hallo")


def test_openai_provider_error_is_an_llm_provider_error():
    assert issubclass(OpenAIProviderError, LLMProviderError)
```

- [ ] **Step 7: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_openai_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.llm_gateway.openai_provider'`.

- [ ] **Step 8: Create `backend/app/llm_gateway/openai_provider.py`**

```python
from __future__ import annotations

import decimal

import httpx

from app.llm_gateway.provider import LLMCompletion, LLMProviderError

# Per-1K-token USD pricing, for the research benchmark's cost metric (master design
# doc §8) only -- not billing-accurate, and not kept in sync with OpenAI's price
# changes automatically. Unknown models cost 0 rather than raising, so a model
# rename never blocks a chat response over a pricing lookup.
_PRICING_PER_1K_TOKENS: dict[str, tuple[decimal.Decimal, decimal.Decimal]] = {
    "gpt-4o-mini": (decimal.Decimal("0.00015"), decimal.Decimal("0.0006")),
    "gpt-4o": (decimal.Decimal("0.0025"), decimal.Decimal("0.01")),
}


class OpenAIProviderError(LLMProviderError):
    """The OpenAI HTTP call failed or returned an unexpected shape."""


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        http_client: httpx.Client | None = None,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        # The Authorization header is sent per-request (not baked into the client at
        # construction time) so an injected test http_client still gets it -- baking
        # it into a default-constructed client only helps when no http_client is
        # passed in, which defeats the point of the constructor parameter in tests.
        self._api_key = api_key
        self._http_client = http_client if http_client is not None else httpx.Client(timeout=120.0)

    def complete(self, prompt: str) -> LLMCompletion:
        try:
            response = self._http_client.post(
                f"{self._base_url}/chat/completions",
                json={"model": self.model, "messages": [{"role": "user", "content": prompt}]},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OpenAIProviderError(f"openai request failed: {exc}") from exc

        body = response.json()
        try:
            text = body["choices"][0]["message"]["content"]
            tokens_in = body["usage"]["prompt_tokens"]
            tokens_out = body["usage"]["completion_tokens"]
        except (KeyError, IndexError) as exc:
            raise OpenAIProviderError(f"openai response missing expected fields: {body}") from exc

        price_in, price_out = _PRICING_PER_1K_TOKENS.get(
            self.model, (decimal.Decimal("0"), decimal.Decimal("0"))
        )
        cost_usd = (tokens_in * price_in + tokens_out * price_out) / decimal.Decimal("1000")

        return LLMCompletion(text=text, tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost_usd)
```

- [ ] **Step 9: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_openai_provider.py -v`
Expected: PASS (6 passed).

- [ ] **Step 10: Write the failing registry test**

`backend/tests/unit/test_llm_registry.py`:

```python
from app.config import Settings
from app.llm_gateway.ollama_provider import OllamaProvider
from app.llm_gateway.openai_provider import OpenAIProvider
from app.llm_gateway.registry import get_provider


def _base_env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/db")
    monkeypatch.setenv("MASTER_KEY_PATH", "/run/secrets/master_key")
    monkeypatch.setenv("APP_RUNTIME_PASSWORD", "runtime-secret")


def test_defaults_to_ollama_provider(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    get_provider.cache_clear()
    from app.config import get_settings

    get_settings.cache_clear()
    provider = get_provider()
    assert isinstance(provider, OllamaProvider)
    assert provider.model == Settings(_env_file=None).ollama_model


def test_selects_openai_provider_when_configured(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_provider.cache_clear()
    from app.config import get_settings

    get_settings.cache_clear()
    provider = get_provider()
    assert isinstance(provider, OpenAIProvider)
```

- [ ] **Step 11: Run the test to verify it fails**

Run: `cd backend && pytest tests/unit/test_llm_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.llm_gateway.registry'`.

- [ ] **Step 12: Create `backend/app/llm_gateway/registry.py`**

```python
from __future__ import annotations

from functools import lru_cache

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

- [ ] **Step 13: Run the test to verify it passes**

Run: `cd backend && pytest tests/unit/test_llm_registry.py -v`
Expected: PASS (2 passed).

- [ ] **Step 14: Run the full backend suite and linters**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; both import-linter contracts KEPT (in particular, `app.llm_gateway` importing `httpx` is fine — that contract only forbids it inside `app.privacy_gateway`); all tests pass.

- [ ] **Step 15: Commit**

```bash
git add backend/app/llm_gateway/ backend/tests/unit/test_ollama_provider.py \
  backend/tests/unit/test_openai_provider.py backend/tests/unit/test_llm_registry.py
git commit -m "feat: add LLMProvider protocol with Ollama and OpenAI adapters"
```

---

## Task 5: Conversation schema — title, timestamps, soft delete

**Files:**
- Modify: `backend/app/models/conversation.py`
- Modify: `backend/app/db/repositories/conversation_repository.py`
- Create: `backend/alembic/versions/0005_conversation_title_and_lifecycle.py`
- Test: `backend/tests/integration/test_conversation_lifecycle.py`

**Interfaces:**
- Consumes: existing `Conversation` model, `ConversationRepository`, `tenant_scoped_session`.
- Produces: `Conversation.title: str | None`, `Conversation.updated_at: datetime`, `Conversation.deleted_at: datetime | None`; `ConversationRepository.set_title(tenant_id, conversation_id, title)`, `.touch(tenant_id, conversation_id)`, `.soft_delete(tenant_id, conversation_id)`; `.get()`/`.list_for_user()` now exclude soft-deleted rows. Consumed by Task 7/8's chat API.

- [ ] **Step 1: Write the failing migration/repository test**

`backend/tests/integration/test_conversation_lifecycle.py`:

```python
import uuid

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def _create_tenant_and_user(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        )
        user_id = user.id
    return tenant_id, user_id


def test_new_conversation_has_no_title_and_is_not_deleted(tmp_path):
    tenant_id, user_id = _create_tenant_and_user(tmp_path)
    with tenant_scoped_session(tenant_id) as session:
        conversation = ConversationRepository(session).create(tenant_id, user_id)
        assert conversation.title is None
        assert conversation.deleted_at is None
        assert conversation.updated_at is not None


def test_set_title_updates_the_conversation(tmp_path):
    tenant_id, user_id = _create_tenant_and_user(tmp_path)
    with tenant_scoped_session(tenant_id) as session:
        conversation_id = ConversationRepository(session).create(tenant_id, user_id).id

    with tenant_scoped_session(tenant_id) as session:
        ConversationRepository(session).set_title(tenant_id, conversation_id, "Erste Anfrage")

    with tenant_scoped_session(tenant_id) as session:
        conversation = ConversationRepository(session).get(tenant_id, conversation_id)
        assert conversation.title == "Erste Anfrage"


def test_touch_updates_updated_at(tmp_path):
    tenant_id, user_id = _create_tenant_and_user(tmp_path)
    with tenant_scoped_session(tenant_id) as session:
        conversation = ConversationRepository(session).create(tenant_id, user_id)
        conversation_id = conversation.id
        original_updated_at = conversation.updated_at

    with tenant_scoped_session(tenant_id) as session:
        ConversationRepository(session).touch(tenant_id, conversation_id)

    with tenant_scoped_session(tenant_id) as session:
        conversation = ConversationRepository(session).get(tenant_id, conversation_id)
        assert conversation.updated_at >= original_updated_at


def test_soft_deleted_conversation_is_excluded_from_get_and_list(tmp_path):
    tenant_id, user_id = _create_tenant_and_user(tmp_path)
    with tenant_scoped_session(tenant_id) as session:
        conversation_id = ConversationRepository(session).create(tenant_id, user_id).id

    with tenant_scoped_session(tenant_id) as session:
        ConversationRepository(session).soft_delete(tenant_id, conversation_id)

    with tenant_scoped_session(tenant_id) as session:
        repo = ConversationRepository(session)
        assert repo.get(tenant_id, conversation_id) is None
        assert conversation_id not in {c.id for c in repo.list_for_user(tenant_id, user_id)}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/integration/test_conversation_lifecycle.py -v`
Expected: FAIL — `AttributeError` (`Conversation` has no `title`) or a `TypeError` on `ConversationRepository.set_title` not existing.

- [ ] **Step 3: Add the columns to `backend/app/models/conversation.py`**

Replace the file in full:

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class Conversation(Base):
    __tablename__ = "conversations"
    # See migration 0004: FK checks bypass RLS, so child->parent tenant consistency is
    # enforced by composite FKs. `uq_conversations_tenant_id_id` exists so this table can
    # itself be the target of one from messages/token_mappings/audit_events/llm_requests.
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_conversations_tenant_user",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Create the migration**

`backend/alembic/versions/0005_conversation_title_and_lifecycle.py`:

```python
"""add title, updated_at, and deleted_at to conversations

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("title", sa.String(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column("conversations", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "deleted_at")
    op.drop_column("conversations", "updated_at")
    op.drop_column("conversations", "title")
```

- [ ] **Step 5: Run the migration**

Run: `cd backend && alembic upgrade head`
Expected: applies `0005` cleanly.

- [ ] **Step 6: Add the new methods to `backend/app/db/repositories/conversation_repository.py`**

Replace the file in full:

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
            Conversation.deleted_at.is_(None),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_user(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[Conversation]:
        stmt = (
            sa.select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.user_id == user_id,
                Conversation.deleted_at.is_(None),
            )
            .order_by(Conversation.updated_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def set_title(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID, title: str) -> None:
        stmt = (
            sa.update(Conversation)
            .where(Conversation.tenant_id == tenant_id, Conversation.id == conversation_id)
            .values(title=title)
        )
        self.session.execute(stmt)

    def touch(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        stmt = (
            sa.update(Conversation)
            .where(Conversation.tenant_id == tenant_id, Conversation.id == conversation_id)
            .values(updated_at=sa.func.now())
        )
        self.session.execute(stmt)

    def soft_delete(self, tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        stmt = (
            sa.update(Conversation)
            .where(Conversation.tenant_id == tenant_id, Conversation.id == conversation_id)
            .values(deleted_at=sa.func.now())
        )
        self.session.execute(stmt)
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `cd backend && pytest tests/integration/test_conversation_lifecycle.py -v`
Expected: PASS (4 passed).

- [ ] **Step 8: Run the full backend suite and linters**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; both import-linter contracts KEPT; all tests pass, including the pre-existing `test_repositories.py`/Task 12 corpus suite (unaffected — none of them read `title`/`updated_at`/`deleted_at`).

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/conversation.py backend/app/db/repositories/conversation_repository.py \
  backend/alembic/versions/0005_conversation_title_and_lifecycle.py \
  backend/tests/integration/test_conversation_lifecycle.py
git commit -m "feat: add conversation title, updated_at, and soft delete"
```

---

## Task 6: `audit_events` and `llm_requests` repositories

**Files:**
- Create: `backend/app/db/repositories/audit_event_repository.py`
- Create: `backend/app/db/repositories/llm_request_repository.py`
- Test: `backend/tests/integration/test_audit_event_repository.py`
- Test: `backend/tests/integration/test_llm_request_repository.py`

**Interfaces:**
- Consumes: existing `AuditEvent`, `LLMRequest` models; `tenant_scoped_session`.
- Produces: `AuditEventRepository.create(tenant_id, conversation_id, event_type, entity_type, token, actor) -> AuditEvent`; `LLMRequestRepository.create(tenant_id, conversation_id, provider, model, sanitized_prompt, sanitized_response, tokens_in, tokens_out, cost_usd, latency_ms) -> LLMRequest`. Consumed by Task 8's chat API.

- [ ] **Step 1: Write the failing audit-event test**

`backend/tests/integration/test_audit_event_repository.py`:

```python
import uuid

import sqlalchemy as sa

from app.db.repositories.audit_event_repository import AuditEventRepository
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import AuditEvent
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def test_create_persists_an_audit_event_row(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))

    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        )
        conversation_id = ConversationRepository(session).create(tenant_id, user.id).id
        event = AuditEventRepository(session).create(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            event_type="LeakageDetectedError",
            entity_type="UNKNOWN",
            token="",
            actor=str(user.id),
        )
        event_id = event.id

    with tenant_scoped_session(tenant_id) as session:
        stored = session.execute(
            sa.select(AuditEvent).where(AuditEvent.id == event_id)
        ).scalar_one()
        assert stored.event_type == "LeakageDetectedError"
        assert stored.conversation_id == conversation_id
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/integration/test_audit_event_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db.repositories.audit_event_repository'`.

- [ ] **Step 3: Create `backend/app/db/repositories/audit_event_repository.py`**

```python
import uuid
from datetime import datetime, timezone

from app.db.repositories.base import BaseRepository
from app.models import AuditEvent


class AuditEventRepository(BaseRepository):
    def create(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        event_type: str,
        entity_type: str,
        token: str,
        actor: str,
    ) -> AuditEvent:
        event = AuditEvent(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            event_type=event_type,
            entity_type=entity_type,
            token=token,
            actor=actor,
            timestamp=datetime.now(timezone.utc),
        )
        self.session.add(event)
        self.session.flush()
        return event
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/integration/test_audit_event_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing llm-request test**

`backend/tests/integration/test_llm_request_repository.py`:

```python
import decimal
import uuid

import sqlalchemy as sa

from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.llm_request_repository import LLMRequestRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.models import LLMRequest
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def test_create_persists_an_llm_request_row(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))

    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id

    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        )
        conversation_id = ConversationRepository(session).create(tenant_id, user.id).id
        request = LLMRequestRepository(session).create(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            provider="ollama",
            model="llama3.1",
            sanitized_prompt="Hallo PATIENT_1234567890",
            sanitized_response="Guten Tag PATIENT_1234567890",
            tokens_in=12,
            tokens_out=8,
            cost_usd=decimal.Decimal("0"),
            latency_ms=245,
        )
        request_id = request.id

    with tenant_scoped_session(tenant_id) as session:
        stored = session.execute(
            sa.select(LLMRequest).where(LLMRequest.id == request_id)
        ).scalar_one()
        assert stored.provider == "ollama"
        assert stored.tokens_in == 12
        assert stored.cost_usd == decimal.Decimal("0")
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd backend && pytest tests/integration/test_llm_request_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db.repositories.llm_request_repository'`.

- [ ] **Step 7: Create `backend/app/db/repositories/llm_request_repository.py`**

```python
import decimal
import uuid

from app.db.repositories.base import BaseRepository
from app.models import LLMRequest


class LLMRequestRepository(BaseRepository):
    def create(
        self,
        tenant_id: uuid.UUID,
        conversation_id: uuid.UUID,
        provider: str,
        model: str,
        sanitized_prompt: str,
        sanitized_response: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: decimal.Decimal,
        latency_ms: int,
    ) -> LLMRequest:
        request = LLMRequest(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            provider=provider,
            model=model,
            sanitized_prompt=sanitized_prompt,
            sanitized_response=sanitized_response,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
        self.session.add(request)
        self.session.flush()
        return request
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd backend && pytest tests/integration/test_llm_request_repository.py -v`
Expected: PASS.

- [ ] **Step 9: Run the full backend suite and linters**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; both import-linter contracts KEPT; all tests pass.

- [ ] **Step 10: Commit**

```bash
git add backend/app/db/repositories/audit_event_repository.py backend/app/db/repositories/llm_request_repository.py \
  backend/tests/integration/test_audit_event_repository.py backend/tests/integration/test_llm_request_repository.py
git commit -m "feat: add audit_events and llm_requests repositories"
```

---

## Task 7: Chat API — conversation CRUD

**Files:**
- Create: `backend/app/api/schemas.py`
- Create: `backend/app/api/conversations.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_conversations_api.py`

**Interfaces:**
- Consumes: `get_current_user`, `get_db_session` (Task 3); `ConversationRepository`, `MessageRepository` (existing/Task 5); `get_pipeline` (existing `app.privacy_gateway.pipeline`).
- Produces: `GET /api/conversations`, `POST /api/conversations`, `GET /api/conversations/{id}/messages`, `DELETE /api/conversations/{id}`; `ConversationSummary`, `MessageOut` Pydantic models. Consumed by Task 8 (shares the schemas and router prefix) and by the future frontend.

- [ ] **Step 1: Write the failing conversations-API test**

`backend/tests/integration/test_conversations_api.py`:

```python
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.message_repository import MessageRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.main import app
from app.privacy_gateway.pipeline import get_pipeline
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


@pytest.fixture
def scope(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        )
        user_id = user.id
    return tenant_id, user_id


@pytest.fixture
def client(scope):
    tenant_id, user_id = scope
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id, user_id=user_id, role="doctor"
    )
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def test_create_and_list_conversations(client):
    created = client.post("/api/conversations")
    assert created.status_code == 201
    body = created.json()
    assert body["title"] is None

    listed = client.get("/api/conversations")
    assert listed.status_code == 200
    ids = {c["id"] for c in listed.json()}
    assert body["id"] in ids


def test_get_messages_reconstructs_human_readable_text(scope, client):
    tenant_id, user_id = scope
    with tenant_scoped_session(tenant_id) as session:
        conversation_id = ConversationRepository(session).create(tenant_id, user_id).id

    sanitized = get_pipeline().sanitize(tenant_id, conversation_id, "Hallo, hier ist Anna Schmitt.")
    with tenant_scoped_session(tenant_id) as session:
        MessageRepository(session).create(
            tenant_id, conversation_id, role="user", sanitized_content=sanitized
        )

    response = client.get(f"/api/conversations/{conversation_id}/messages")
    assert response.status_code == 200
    messages = response.json()
    assert len(messages) == 1
    assert messages[0]["content"] == "Hallo, hier ist Anna Schmitt."


def test_messages_for_unknown_conversation_is_404(client):
    response = client.get(f"/api/conversations/{uuid.uuid4()}/messages")
    assert response.status_code == 404


def test_delete_soft_deletes_and_hides_the_conversation(client):
    created = client.post("/api/conversations").json()

    deleted = client.delete(f"/api/conversations/{created['id']}")
    assert deleted.status_code == 204

    listed = client.get("/api/conversations")
    assert created["id"] not in {c["id"] for c in listed.json()}


def test_conversations_do_not_cross_users(scope, client):
    tenant_id, _ = scope
    with tenant_scoped_session(tenant_id) as session:
        other_user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-2", email="other@example.com", role="doctor"
        )
        other_conversation_id = ConversationRepository(session).create(tenant_id, other_user.id).id

    response = client.get(f"/api/conversations/{other_conversation_id}/messages")
    assert response.status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/integration/test_conversations_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.schemas'` (or a 404 on every route, since none exist yet).

- [ ] **Step 3: Create `backend/app/api/schemas.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime


class SendMessageIn(BaseModel):
    content: str
```

- [ ] **Step 4: Create `backend/app/api/conversations.py`**

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import ConversationSummary, MessageOut
from app.auth.dependencies import get_current_user, get_db_session
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.message_repository import MessageRepository
from app.privacy_gateway.pipeline import Pipeline, get_pipeline

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _to_summary(conversation) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[ConversationSummary]:
    conversations = ConversationRepository(session).list_for_user(user.tenant_id, user.user_id)
    return [_to_summary(c) for c in conversations]


@router.post("", response_model=ConversationSummary, status_code=201)
def create_conversation(
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ConversationSummary:
    conversation = ConversationRepository(session).create(user.tenant_id, user.user_id)
    return _to_summary(conversation)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def get_messages(
    conversation_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    pipeline: Pipeline = Depends(get_pipeline),
) -> list[MessageOut]:
    conversation = ConversationRepository(session).get(user.tenant_id, conversation_id)
    if conversation is None or conversation.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="conversation not found")

    messages = MessageRepository(session).list_for_conversation(user.tenant_id, conversation_id)
    return [
        MessageOut(
            id=message.id,
            role=message.role,
            content=pipeline.deanonymize(user.tenant_id, conversation_id, message.sanitized_content),
            created_at=message.created_at,
        )
        for message in messages
    ]


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> None:
    repo = ConversationRepository(session)
    conversation = repo.get(user.tenant_id, conversation_id)
    if conversation is None or conversation.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="conversation not found")
    repo.soft_delete(user.tenant_id, conversation_id)
```

- [ ] **Step 5: Register the router and CORS middleware in `backend/app/main.py`**

Replace the file in full:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.conversations import router as conversations_router
from app.api.health import router as health_router
from app.config import get_settings
from app.privacy_gateway.pipeline import get_pipeline

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Design spec §6: the bundled ORDO OWL and Krankenhausverzeichnis xlsx are parsed
    # once, at startup, into in-memory sets — never per request. The spaCy model is
    # loaded here too, so the first real request does not pay for it.
    if settings.warm_reference_data_on_startup:
        get_pipeline()
    yield


app = FastAPI(
    title="Privacy-First Medical LLM Gateway",
    debug=settings.debug,
    lifespan=lifespan,
)

if settings.cors_allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(health_router)
app.include_router(conversations_router)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd backend && pytest tests/integration/test_conversations_api.py -v`
Expected: PASS (5 passed). Note: this test calls the real `get_pipeline()` (via `Pipeline.sanitize`/`deanonymize` in the test body and inside the route), so it needs the real reference data fetched (`python scripts/fetch_reference_data.py`) or `WARM_REFERENCE_DATA_ON_STARTUP=false` plus the reference data present — same prerequisite Task 4 of the previous plan already established for this repo's dev/CI environment.

- [ ] **Step 7: Run the full backend suite and linters**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; both import-linter contracts KEPT (`app.api` importing `app.privacy_gateway` is the allowed direction); all tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/schemas.py backend/app/api/conversations.py backend/app/main.py \
  backend/tests/integration/test_conversations_api.py
git commit -m "feat: add conversation CRUD API endpoints"
```

---

## Task 8: Chat API — send-message endpoint (buffered, SSE-replayed)

**Files:**
- Create: `backend/app/api/chat.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_chat_api.py`

**Interfaces:**
- Consumes: everything from Tasks 3, 4, 5, 6, 7.
- Produces: `POST /api/conversations/{id}/messages` (SSE on success, JSON error on 422/502/500).

- [ ] **Step 1: Write the failing send-message test**

`backend/tests/integration/test_chat_api.py`:

```python
import decimal
import json
import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.llm_gateway.provider import LLMCompletion, LLMProviderError
from app.llm_gateway.registry import get_provider
from app.main import app
from app.models import LLMRequest, Message
from app.privacy_gateway.detectors.base import normalize
from app.privacy_gateway.detectors.custom_recognizers import CustomRecognizers
from app.privacy_gateway.detectors.presidio_detector import PresidioDetector
from app.privacy_gateway.detectors.regex_detector import RegexDetector
from app.privacy_gateway.detectors.stack import DetectorStack
from app.privacy_gateway.output_guard.guard import OutputGuard
from app.privacy_gateway.pipeline import Pipeline, get_pipeline
from app.privacy_gateway.pseudonymization.pseudonymizer import Pseudonymizer
from app.privacy_gateway.risk_scoring.scorer import RiskScorer
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider
from app.privacy_gateway.token_vault.vault import TokenVault


class _StubProvider:
    name = "stub"
    model = "stub-model"

    def __init__(self, response_text: str | None = None, error: Exception | None = None):
        self._response_text = response_text
        self._error = error

    def complete(self, prompt: str) -> LLMCompletion:
        if self._error is not None:
            raise self._error
        return LLMCompletion(
            text=self._response_text, tokens_in=10, tokens_out=10, cost_usd=decimal.Decimal("0")
        )


@pytest.fixture
def scope(tmp_path):
    master_key_path = tmp_path / "master.key"
    master_key_path.write_bytes(bytes(range(32)))
    key_provider = FileSecretKeyProvider(str(master_key_path))
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject="sub-1", email="doc@example.com", role="doctor"
        )
        user_id = user.id
        conversation_id = ConversationRepository(session).create(tenant_id, user_id).id
    return tenant_id, user_id, conversation_id


@pytest.fixture
def authenticated(scope):
    tenant_id, user_id, _ = scope
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id, user_id=user_id, role="doctor"
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _use_provider(provider) -> None:
    app.dependency_overrides[get_provider] = lambda: provider


def _clear_provider_override() -> None:
    app.dependency_overrides.pop(get_provider, None)


def test_send_message_streams_the_validated_response(scope, authenticated):
    tenant_id, user_id, conversation_id = scope
    _use_provider(_StubProvider(response_text="Das klingt nach einer guten Genesung."))
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "Wie geht es dem Patienten?"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [line for line in response.text.split("\n\n") if line.strip()]
    deltas = "".join(
        json.loads(line.split("data: ", 1)[1])["delta"]
        for line in events
        if line.startswith("event: token")
    )
    assert deltas == "Das klingt nach einer guten Genesung."
    assert any(line.startswith("event: done") for line in events)

    with tenant_scoped_session(tenant_id) as session:
        messages = session.execute(
            sa.select(Message).where(Message.conversation_id == conversation_id)
        ).scalars().all()
        assert {m.role for m in messages} == {"user", "assistant"}

        llm_requests = session.execute(
            sa.select(LLMRequest).where(LLMRequest.conversation_id == conversation_id)
        ).scalars().all()
        assert len(llm_requests) == 1
        assert llm_requests[0].provider == "stub"

    _clear_provider_override()


def test_high_risk_message_is_rejected_with_422(scope, authenticated, tmp_path):
    _, _, conversation_id = scope
    # A standalone Pipeline (same construction as get_pipeline(), see pipeline.py),
    # with an injected rare-disease set -- avoids depending on the real reference
    # data containing "Marfan-Syndrom", and proves HighRiskMessageError propagates
    # through the route as a 422 without ever calling the LLM provider.
    key_provider = FileSecretKeyProvider(str(tmp_path / "high-risk-master.key"))
    (tmp_path / "high-risk-master.key").write_bytes(bytes(range(32)))
    vault = TokenVault(key_provider)
    detector_stack = DetectorStack(RegexDetector(), PresidioDetector(), CustomRecognizers(frozenset()))
    test_pipeline = Pipeline(
        detector_stack=detector_stack,
        risk_scorer=RiskScorer(frozenset({normalize("Marfan-Syndrom")})),
        pseudonymizer=Pseudonymizer(vault),
        output_guard=OutputGuard(detector_stack, vault),
    )
    app.dependency_overrides[get_pipeline] = lambda: test_pipeline
    _use_provider(_StubProvider(response_text="unused"))
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": (
                "Der Patient ist 67 Jahre alt, wurde in Heidelberg behandelt und am "
                "12.03.2024 mit Marfan-Syndrom diagnostiziert."
            )
        },
    )

    assert response.status_code == 422

    app.dependency_overrides.pop(get_pipeline, None)
    _clear_provider_override()


def test_provider_failure_is_a_502_and_the_user_message_survives(scope, authenticated):
    tenant_id, _, conversation_id = scope
    _use_provider(_StubProvider(error=LLMProviderError("provider is down")))
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "Hallo."}
    )

    assert response.status_code == 502

    with tenant_scoped_session(tenant_id) as session:
        messages = session.execute(
            sa.select(Message).where(Message.conversation_id == conversation_id)
        ).scalars().all()
        assert len(messages) == 1
        assert messages[0].role == "user"

    _clear_provider_override()


def test_leaked_response_is_a_500_and_is_audit_logged(scope, authenticated):
    tenant_id, _, conversation_id = scope
    _use_provider(_StubProvider(response_text="Der Patient heißt Anna Schmitt."))
    client = TestClient(app)

    response = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "Hallo."}
    )

    assert response.status_code == 500

    from app.models import AuditEvent

    with tenant_scoped_session(tenant_id) as session:
        events = session.execute(
            sa.select(AuditEvent).where(AuditEvent.conversation_id == conversation_id)
        ).scalars().all()
        assert len(events) == 1
        assert events[0].event_type == "LeakageDetectedError"

    _clear_provider_override()


def test_send_message_to_unknown_conversation_is_404(authenticated):
    client = TestClient(app)
    response = client.post(
        f"/api/conversations/{uuid.uuid4()}/messages", json={"content": "Hallo."}
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/integration/test_chat_api.py -v`
Expected: FAIL — every request 404s (`app.api.chat` doesn't exist / route isn't registered).

- [ ] **Step 3: Create `backend/app/api/chat.py`**

```python
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.schemas import SendMessageIn
from app.auth.dependencies import get_current_user, get_db_session
from app.auth.tenant_resolver import AuthenticatedUser
from app.db.repositories.audit_event_repository import AuditEventRepository
from app.db.repositories.conversation_repository import ConversationRepository
from app.db.repositories.llm_request_repository import LLMRequestRepository
from app.db.repositories.message_repository import MessageRepository
from app.llm_gateway.provider import LLMProvider, LLMProviderError
from app.llm_gateway.registry import get_provider
from app.privacy_gateway.output_guard.guard import LeakageDetectedError, UnresolvedTokenError
from app.privacy_gateway.pipeline import Pipeline, get_pipeline
from app.privacy_gateway.risk_scoring.scorer import HighRiskMessageError, LowConfidenceSpanError

router = APIRouter(prefix="/api/conversations", tags=["messages"])

FAIL_CLOSED_MESSAGE = "Sensitive information could not be safely processed."
_CHUNK_WORDS = 3
_TITLE_MAX_LENGTH = 60


def _derive_title(sanitized_prompt: str) -> str:
    stripped = sanitized_prompt.strip()
    if len(stripped) <= _TITLE_MAX_LENGTH:
        return stripped
    return stripped[:_TITLE_MAX_LENGTH].rsplit(" ", 1)[0] + "…"


def _replay_as_sse(text: str, message_id: uuid.UUID, created_at) -> Iterator[str]:
    words = text.split(" ")
    for i in range(0, len(words), _CHUNK_WORDS):
        chunk = " ".join(words[i : i + _CHUNK_WORDS])
        if i + _CHUNK_WORDS < len(words):
            chunk += " "
        yield f"event: token\ndata: {json.dumps({'delta': chunk})}\n\n"
    yield (
        "event: done\n"
        f"data: {json.dumps({'id': str(message_id), 'created_at': created_at.isoformat()})}\n\n"
    )


@router.post("/{conversation_id}/messages")
def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageIn,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    pipeline: Pipeline = Depends(get_pipeline),
    provider: LLMProvider = Depends(get_provider),
) -> StreamingResponse:
    conversation_repo = ConversationRepository(session)
    conversation = conversation_repo.get(user.tenant_id, conversation_id)
    if conversation is None or conversation.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="conversation not found")

    try:
        sanitized_prompt = pipeline.sanitize(user.tenant_id, conversation_id, body.content)
    except (LowConfidenceSpanError, HighRiskMessageError) as exc:
        raise HTTPException(status_code=422, detail=FAIL_CLOSED_MESSAGE) from exc

    # Persist and commit the user's message before calling the LLM provider, so a
    # provider outage doesn't roll back and lose an already-sanitized message.
    message_repo = MessageRepository(session)
    message_repo.create(
        user.tenant_id, conversation_id, role="user", sanitized_content=sanitized_prompt
    )
    if conversation.title is None:
        conversation_repo.set_title(user.tenant_id, conversation_id, _derive_title(sanitized_prompt))
    conversation_repo.touch(user.tenant_id, conversation_id)
    session.commit()

    started_at = time.monotonic()
    try:
        completion = provider.complete(sanitized_prompt)
    except LLMProviderError as exc:
        raise HTTPException(
            status_code=502, detail="the language model provider is unavailable"
        ) from exc
    latency_ms = int((time.monotonic() - started_at) * 1000)

    try:
        human_readable = pipeline.deanonymize(user.tenant_id, conversation_id, completion.text)
    except (LeakageDetectedError, UnresolvedTokenError) as exc:
        AuditEventRepository(session).create(
            tenant_id=user.tenant_id,
            conversation_id=conversation_id,
            event_type=type(exc).__name__,
            entity_type="UNKNOWN",
            token="",
            actor=str(user.user_id),
        )
        session.commit()
        raise HTTPException(
            status_code=500, detail="the response could not be safely returned"
        ) from exc

    # sanitized_content is the raw completion text: already token-shaped (the LLM
    # only ever saw pseudonymized input), never the human-readable reconstruction --
    # messages store only pseudonymized text (master design doc §4).
    assistant_message = message_repo.create(
        user.tenant_id, conversation_id, role="assistant", sanitized_content=completion.text
    )
    LLMRequestRepository(session).create(
        tenant_id=user.tenant_id,
        conversation_id=conversation_id,
        provider=provider.name,
        model=provider.model,
        sanitized_prompt=sanitized_prompt,
        sanitized_response=completion.text,
        tokens_in=completion.tokens_in,
        tokens_out=completion.tokens_out,
        cost_usd=completion.cost_usd,
        latency_ms=latency_ms,
    )
    conversation_repo.touch(user.tenant_id, conversation_id)
    session.commit()

    return StreamingResponse(
        _replay_as_sse(human_readable, assistant_message.id, assistant_message.created_at),
        media_type="text/event-stream",
    )
```

- [ ] **Step 4: Register the chat router in `backend/app/main.py`**

Add the import and `include_router` call:

```python
from app.api.chat import router as chat_router
```

and after `app.include_router(conversations_router)`:

```python
app.include_router(chat_router)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && pytest tests/integration/test_chat_api.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Run the full backend suite and linters**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; both import-linter contracts KEPT; all tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/chat.py backend/app/main.py backend/tests/integration/test_chat_api.py
git commit -m "feat: add the send-message chat API endpoint (buffered pipeline, SSE replay)"
```

---

## Task 9: Golden-corpus privacy invariant through the chat API

**Files:**
- Modify: `backend/tests/privacy_invariants/conftest.py`
- Test: `backend/tests/privacy_invariants/test_chat_api_corpus.py`

**Interfaces:**
- Consumes: `golden_corpus`, `corpus_pipeline`, `corpus_key_provider` fixtures and `new_scope()` (existing, from the previous plan's Task 12); `SANITIZABLE`, `KNOWN_RECALL_GAPS` from `tests.privacy_invariants.test_pipeline_corpus` (existing).
- Produces: `new_scope_with_user()` helper (conftest addition); a new privacy-invariant test asserting the captured outbound LLM prompt never contains raw corpus PII, exercised through the real chat API rather than only through `Pipeline.sanitize()` directly.

- [ ] **Step 1: Add a scope helper that also returns the user id**

Add to `backend/tests/privacy_invariants/conftest.py` (after the existing `new_scope` function):

```python
def new_scope_with_user(key_provider) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Like new_scope(), but also returns the user id -- needed by the chat-API
    corpus test to build an AuthenticatedUser for dependency_overrides."""
    with SessionLocal() as session:
        tenant = TenantRepository(session, key_provider).create(
            name="Clinic", keycloak_realm=f"realm-{uuid.uuid4()}", retention_days=30
        )
        session.commit()
        tenant_id = tenant.id
    with tenant_scoped_session(tenant_id) as session:
        user = UserRepository(session).create(
            tenant_id, keycloak_subject=f"sub-{uuid.uuid4()}", email="doc@example.com", role="doctor"
        )
        user_id = user.id
        conversation_id = ConversationRepository(session).create(tenant_id, user_id).id
    return tenant_id, user_id, conversation_id
```

- [ ] **Step 2: Run the existing privacy-invariant suite to verify nothing broke**

Run: `cd backend && pytest tests/privacy_invariants/ -v`
Expected: PASS (same count as before this change — `new_scope_with_user` is additive).

- [ ] **Step 3: Write the failing chat-API corpus test**

`backend/tests/privacy_invariants/test_chat_api_corpus.py`:

```python
import decimal

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.auth.tenant_resolver import AuthenticatedUser
from app.llm_gateway.provider import LLMCompletion
from app.llm_gateway.registry import get_provider
from app.main import app
from app.privacy_gateway.pipeline import get_pipeline
from tests.privacy_invariants.conftest import new_scope_with_user
from tests.privacy_invariants.test_pipeline_corpus import KNOWN_RECALL_GAPS, SANITIZABLE


class _RecordingProvider:
    name = "recording-stub"
    model = "stub-model"

    def __init__(self) -> None:
        self.captured_prompts: list[str] = []

    def complete(self, prompt: str) -> LLMCompletion:
        self.captured_prompts.append(prompt)
        return LLMCompletion(
            text="Verstanden.", tokens_in=1, tokens_out=1, cost_usd=decimal.Decimal("0")
        )


@pytest.mark.parametrize("note", SANITIZABLE, ids=lambda note: note["id"])
def test_outbound_llm_prompt_never_contains_raw_corpus_pii(
    note, corpus_pipeline, corpus_key_provider
):
    tenant_id, user_id, conversation_id = new_scope_with_user(corpus_key_provider)
    recording_provider = _RecordingProvider()

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        tenant_id=tenant_id, user_id=user_id, role="doctor"
    )
    app.dependency_overrides[get_pipeline] = lambda: corpus_pipeline
    app.dependency_overrides[get_provider] = lambda: recording_provider
    client = TestClient(app)

    try:
        response = client.post(
            f"/api/conversations/{conversation_id}/messages", json={"content": note["text"]}
        )
        assert response.status_code == 200

        leaked = [
            entity["text"]
            for entity in note["entities"]
            for prompt in recording_provider.captured_prompts
            if entity["text"] in prompt and (note["id"], entity["text"]) not in KNOWN_RECALL_GAPS
        ]
        assert not leaked, (
            f"{note['id']}: raw PII reached the LLM provider: {leaked}\n"
            f"captured prompts: {recording_provider.captured_prompts}"
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_pipeline, None)
        app.dependency_overrides.pop(get_provider, None)
```

- [ ] **Step 4: Run the test**

Run: `cd backend && pytest tests/privacy_invariants/test_chat_api_corpus.py -v`
Expected: PASS (21 passed) — the chat API uses the same `Pipeline.sanitize()` call as the direct-pipeline suite, so no new recall gaps are expected; if one appears, follow the triage order documented in the previous plan's Task 12 Step 4 (detector bug → gazetteer/normalization gap → genuine `de_core_news_lg` recall gap added to `KNOWN_RECALL_GAPS` in `test_pipeline_corpus.py`, never edit the corpus note).

- [ ] **Step 5: Run the full backend suite and linters**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; both import-linter contracts KEPT; all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/privacy_invariants/conftest.py backend/tests/privacy_invariants/test_chat_api_corpus.py
git commit -m "test: prove the golden corpus never reaches the LLM provider unsanitized via the chat API"
```

---

## Task 10: Manual E2E documentation and final wrap-up

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-9.
- Produces: a documented, runnable manual E2E check; no new production code.

- [ ] **Step 1: Document the manual E2E flow in `README.md`**

Under `## Backend tests`, append:

    ### Manual end-to-end check

    Not part of the default `pytest` run (it makes a real Ollama call and needs
    the full stack up):

    ```bash
    docker compose up -d
    cd backend && python scripts/seed_dev_tenants.py
    TOKEN=$(curl -s http://localhost:8080/realms/chatgpt-proxy-dev/protocol/openid-connect/token \
      -d grant_type=password -d client_id=chatgpt-proxy-frontend \
      -d username=dr.mueller -d password=dev-password | jq -r .access_token)

    CONVERSATION_ID=$(curl -s -X POST http://localhost:8000/api/conversations \
      -H "Authorization: Bearer $TOKEN" | jq -r .id)

    curl -N -X POST "http://localhost:8000/api/conversations/$CONVERSATION_ID/messages" \
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -d '{"content": "Patientin Anna Schmitt, 45 Jahre, aus Heidelberg."}'
    ```

    Expected: an `event: token` / `event: done` SSE stream. To verify no raw
    identifier crossed the LLM boundary, run
    `docker compose exec ollama ollama list` is not useful for this — instead
    inspect the persisted `llm_requests.sanitized_prompt` row for this
    conversation and confirm it contains `PATIENT_...`/`LOCATION_...`-shaped
    tokens instead of "Anna Schmitt" / "Heidelberg":

    ```bash
    docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
      -c "SELECT sanitized_prompt FROM llm_requests ORDER BY created_at DESC LIMIT 1;"
    ```

    Switch to OpenAI by setting `LLM_PROVIDER=openai` and `OPENAI_API_KEY` in
    `.env`, then `docker compose up -d --build backend` and repeat — this is the
    concrete check for ADR-0022's "the external provider never sees the
    identity mapping" claim against a real network call.

- [ ] **Step 2: Run the full backend suite and linters one last time**

Run:
```bash
cd backend
ruff check .
lint-imports
pytest -v
```
Expected: `ruff` clean; both import-linter contracts KEPT; every test from Tasks 1-9 passes.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the manual end-to-end chat API check"
```

---

## Post-Plan State

After Task 10, `backend/app/` gains:

```
auth/
  jwt_validator.py     # Keycloak JWKS validation
  tenant_resolver.py   # tenant_id claim -> real Tenant/User, never trusted transitively
  dependencies.py       # get_current_user, get_db_session (RLS-bound)
llm_gateway/
  provider.py           # LLMProvider protocol, LLMCompletion, LLMProviderError
  ollama_provider.py
  openai_provider.py
  registry.py
api/
  schemas.py
  conversations.py      # CRUD
  chat.py                # send-message, buffered pipeline + SSE replay
db/repositories/
  audit_event_repository.py
  llm_request_repository.py
```

plus `keycloak/realm-export.json`, `backend/scripts/seed_dev_tenants.py`, and migration `0005`.

**Deliberately not built here:** automated realm-per-tenant provisioning, the
frontend chat UI (next spec — brainstorm separately, using the impeccable
skill for the visual/UX design), NeMo Guardrails, rate limiting/production
hardening, and any legal/compliance sign-off.

**Known follow-ups this plan surfaces:**
- Realm-per-tenant automation via the Keycloak admin API, if a real
  multi-hospital pilot is ever contemplated (mirrors the previous plan's
  cross-message token determinism follow-up in spirit: a real gap, not
  silently assumed away).
- `OutputGuard.restore()` doesn't expose structured leak data, so the audit
  event on a leakage/unresolved-token failure is generic
  (`entity_type="UNKNOWN"`, `token=""`) — richer audit data needs a change to
  `output_guard/guard.py`, out of scope here.
- OpenAI pricing in `openai_provider.py` is a hardcoded table that will drift;
  fine for the research cost metric, not for real billing.
