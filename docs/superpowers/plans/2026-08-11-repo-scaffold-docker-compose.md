# Repo Scaffold, Config & Docker Compose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the full repo skeleton (backend + frontend packages, all
`privacy_gateway/` subpackages per the design spec, tooling, CI) and a
working `docker compose up` that brings up postgres, keycloak, ollama,
backend, and frontend with passing health checks — no business logic yet.

**Architecture:** A FastAPI backend package laid out exactly per
`docs/superpowers/specs/2026-08-11-privacy-gateway-mvp-design.md` §2 (empty
subpackages for `privacy_gateway/`, `llm_gateway/`, `auth/`, etc. scaffolded
now so later plans add code to an already-agreed structure, not invent it
ad hoc), a minimal Next.js frontend, and a Docker Compose stack wiring both
to postgres/keycloak/ollama containers. An import-linter contract enforcing
the `privacy_gateway/` isolation rule is added in this plan, before any code
exists to violate it, so every later plan is checked against it from day one.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings v2, pytest, ruff,
import-linter; Node 20, Next.js 14 (App Router), TypeScript, vitest; Postgres
16, Keycloak 24, Ollama; Docker Compose; GitHub Actions.

## Global Constraints

- Repository layout must match design spec §2 exactly (package names and
  nesting): `backend/app/{main.py,config.py,auth/,api/,privacy_gateway/{detectors/,risk_scoring/,pseudonymization/{strategies/},token_vault/,output_guard/,pipeline.py},guardrails/,llm_gateway/,models/,db/,observability/}`,
  `backend/tests/{unit/,integration/,privacy_invariants/}`, `frontend/`,
  `evaluation/` (evaluation/ is out of scope for this plan — created by the
  benchmark plan later).
- `privacy_gateway/` never imports from `api/` or `llm_gateway/` — only the
  reverse is allowed, and `privacy_gateway/` never imports anything
  network-capable. Enforced with an import-linter rule in CI, not just
  convention (design spec §2).
- Fail-closed everywhere uncertainty exists, applied consistently, not
  decided ad hoc per feature (ADR-0020). For this plan that means: no
  security-relevant setting gets a permissive default — `debug` defaults
  `False`, `cors_allowed_origins` defaults to an empty list, and `environment`
  has no default at all (missing config crashes startup instead of silently
  assuming development mode).
- Docker Compose file provides postgres, keycloak, ollama, backend, frontend
  (design spec §2 repository tree and §10 MVP scope).

---

## File Structure

```
chatgpt-proxy/
├── .env.example
├── .gitignore
├── docker-compose.yml
├── docker-compose.override.yml.example
├── README.md
├── .github/workflows/ci.yml
├── backend/
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── pyproject.toml
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── auth/__init__.py
│   │   ├── api/__init__.py
│   │   ├── api/health.py
│   │   ├── privacy_gateway/__init__.py
│   │   ├── privacy_gateway/detectors/__init__.py
│   │   ├── privacy_gateway/risk_scoring/__init__.py
│   │   ├── privacy_gateway/pseudonymization/__init__.py
│   │   ├── privacy_gateway/pseudonymization/strategies/__init__.py
│   │   ├── privacy_gateway/token_vault/__init__.py
│   │   ├── privacy_gateway/output_guard/__init__.py
│   │   ├── guardrails/__init__.py
│   │   ├── llm_gateway/__init__.py
│   │   ├── models/__init__.py
│   │   ├── db/__init__.py
│   │   └── observability/__init__.py
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       ├── unit/__init__.py
│       ├── unit/test_config.py
│       ├── unit/test_health.py
│       ├── integration/__init__.py
│       └── privacy_invariants/__init__.py
└── frontend/
    ├── Dockerfile
    ├── .dockerignore
    ├── package.json
    ├── tsconfig.json
    ├── next.config.mjs
    ├── vitest.config.ts
    ├── lib/health.ts
    ├── lib/health.test.ts
    └── app/
        ├── layout.tsx
        ├── page.tsx
        └── api/healthz/route.ts
```

- `privacy_gateway/pipeline.py` is **not** created in this plan — it has no
  content until detection/pseudonymization exist (later plans). Only the
  package directories and `__init__.py` files are scaffolded here so the
  import-linter contract has something to check from day one.
- `evaluation/` is not created in this plan; the benchmark plan owns it.

---

## Task 1: Backend project scaffold & tooling

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/auth/__init__.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/privacy_gateway/__init__.py`
- Create: `backend/app/privacy_gateway/detectors/__init__.py`
- Create: `backend/app/privacy_gateway/risk_scoring/__init__.py`
- Create: `backend/app/privacy_gateway/pseudonymization/__init__.py`
- Create: `backend/app/privacy_gateway/pseudonymization/strategies/__init__.py`
- Create: `backend/app/privacy_gateway/token_vault/__init__.py`
- Create: `backend/app/privacy_gateway/output_guard/__init__.py`
- Create: `backend/app/guardrails/__init__.py`
- Create: `backend/app/llm_gateway/__init__.py`
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/db/__init__.py`
- Create: `backend/app/observability/__init__.py`
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/unit/__init__.py`
- Create: `backend/tests/integration/__init__.py`
- Create: `backend/tests/privacy_invariants/__init__.py`

**Interfaces:**
- Produces: an installable `app` package (`pip install -e .` from
  `backend/`) that Task 2 (`app.config`), Task 3 (`app.main`, `app.api.health`)
  and Task 4 (import-linter contract over `app.*`) build on.

- [ ] **Step 1: Create `backend/pyproject.toml`**

```toml
[project]
name = "chatgpt-proxy-backend"
version = "0.1.0"
description = "Privacy-first medical LLM gateway backend"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115,<0.116",
    "uvicorn[standard]>=0.32,<0.33",
    "pydantic>=2.9,<3",
    "pydantic-settings>=2.6,<3",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3,<9",
    "pytest-cov>=5.0,<6",
    "httpx>=0.27,<0.28",
    "ruff>=0.7,<0.8",
    "import-linter>=2.1,<3",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 2: Create all package directories with empty `__init__.py` files**

```bash
cd backend
mkdir -p app/auth app/api \
  app/privacy_gateway/detectors app/privacy_gateway/risk_scoring \
  app/privacy_gateway/pseudonymization/strategies \
  app/privacy_gateway/token_vault app/privacy_gateway/output_guard \
  app/guardrails app/llm_gateway app/models app/db app/observability \
  tests/unit tests/integration tests/privacy_invariants

for d in app app/auth app/api app/privacy_gateway app/privacy_gateway/detectors \
  app/privacy_gateway/risk_scoring app/privacy_gateway/pseudonymization \
  app/privacy_gateway/pseudonymization/strategies app/privacy_gateway/token_vault \
  app/privacy_gateway/output_guard app/guardrails app/llm_gateway app/models \
  app/db app/observability tests tests/unit tests/integration tests/privacy_invariants; do
  touch "$d/__init__.py"
done
```

- [ ] **Step 3: Install and verify the scaffold**

Run: `cd backend && pip install -e ".[dev]" && pytest`
Expected: install succeeds; pytest collects 0 tests and exits 0 (no test
files exist yet).

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml backend/app backend/tests
git commit -m "chore: scaffold backend package structure"
```

---

## Task 2: Config module with fail-closed defaults

**Files:**
- Create: `backend/app/config.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/unit/test_config.py`

**Interfaces:**
- Consumes: `app` package from Task 1.
- Produces: `app.config.Settings` (pydantic-settings model) and
  `app.config.get_settings() -> Settings`, used by Task 3's `app.main`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/conftest.py`:

```python
import os

os.environ.setdefault("ENVIRONMENT", "test")
```

`backend/tests/unit/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_requires_environment_explicitly(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_defaults_are_secure(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    settings = Settings(_env_file=None)
    assert settings.debug is False
    assert settings.cors_allowed_origins == []


def test_settings_rejects_unknown_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/unit/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`.

- [ ] **Step 3: Write `backend/app/config.py`**

```python
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    environment: Literal["development", "test", "production"]
    log_level: str = "INFO"
    debug: bool = False
    cors_allowed_origins: list[str] = Field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/unit/test_config.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/conftest.py backend/tests/unit/test_config.py
git commit -m "feat: add fail-closed settings module"
```

---

## Task 3: FastAPI app entrypoint with `/health`

**Files:**
- Create: `backend/app/api/health.py`
- Modify: `backend/app/main.py` (new file)
- Test: `backend/tests/unit/test_health.py`

**Interfaces:**
- Consumes: `app.config.get_settings` from Task 2.
- Produces: `app.main.app` (FastAPI instance), used by Task 5's Dockerfile
  CMD (`uvicorn app.main:app`) and Task 10's smoke test.

- [ ] **Step 1: Write the failing test**

`backend/tests/unit/test_health.py`:

```python
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/unit/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 3: Write the implementation**

`backend/app/api/health.py`:

```python
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

`backend/app/main.py`:

```python
from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import get_settings

settings = get_settings()

app = FastAPI(title="Privacy-First Medical LLM Gateway", debug=settings.debug)
app.include_router(health_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/unit/test_health.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/health.py backend/app/main.py backend/tests/unit/test_health.py
git commit -m "feat: add FastAPI app with health endpoint"
```

---

## Task 4: Import-linter contract for `privacy_gateway/` isolation

**Files:**
- Modify: `backend/pyproject.toml` (append `[tool.importlinter]` section)

**Interfaces:**
- Consumes: package layout from Task 1.
- Produces: a `lint-imports` CI gate that every later plan touching
  `privacy_gateway/`, `api/`, or `llm_gateway/` must keep passing.

- [ ] **Step 1: Append the contract to `backend/pyproject.toml`**

```toml
[tool.importlinter]
root_package = "app"

[[tool.importlinter.contracts]]
name = "Privacy gateway must not import API or LLM gateway layers"
type = "forbidden"
source_modules = ["app.privacy_gateway"]
forbidden_modules = ["app.api", "app.llm_gateway"]

[[tool.importlinter.contracts]]
name = "Privacy gateway must not be network-capable"
type = "forbidden"
source_modules = ["app.privacy_gateway"]
forbidden_modules = ["httpx", "requests", "aiohttp", "urllib3"]
```

- [ ] **Step 2: Run the linter to verify it passes on the current (empty) tree**

Run: `cd backend && lint-imports`
Expected: `Contracts: 2 kept, 0 broken.` and exit code 0 — `privacy_gateway/`
currently has no code, so both contracts hold trivially; this is the
baseline every later plan must not break.

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml
git commit -m "chore: add import-linter contract for privacy_gateway isolation"
```

---

## Task 5: Backend Dockerfile

**Files:**
- Create: `backend/Dockerfile`
- Create: `backend/.dockerignore`

**Interfaces:**
- Consumes: `app.main:app` from Task 3.
- Produces: `chatgpt-proxy-backend` image, consumed by Task 8's
  `docker-compose.yml` (`backend` service, `build: ./backend`).

- [ ] **Step 1: Create `backend/.dockerignore`**

```
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
*.egg-info/
tests/
```

- [ ] **Step 2: Create `backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Build and smoke-test the image**

Run:
```bash
cd backend
docker build -t chatgpt-proxy-backend .
docker run --rm -e ENVIRONMENT=development -p 8000:8000 -d --name backend-smoke chatgpt-proxy-backend
sleep 2
curl -sf http://localhost:8000/health
docker stop backend-smoke
```
Expected: `curl` prints `{"status":"ok"}`; container stops cleanly.

- [ ] **Step 4: Commit**

```bash
git add backend/Dockerfile backend/.dockerignore
git commit -m "chore: add backend Dockerfile"
```

---

## Task 6: Frontend scaffold (Next.js + TypeScript) with health check

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/next.config.mjs`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/lib/health.ts`
- Test: `frontend/lib/health.test.ts`
- Create: `frontend/app/layout.tsx`
- Create: `frontend/app/page.tsx`
- Create: `frontend/app/api/healthz/route.ts`

**Interfaces:**
- Produces: `getHealthStatus()` from `frontend/lib/health.ts`, consumed by
  the `/api/healthz` route; a buildable Next.js app consumed by Task 7's
  Dockerfile.

- [ ] **Step 1: Create `frontend/package.json`**

```json
{
  "name": "chatgpt-proxy-frontend",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "test": "vitest run"
  },
  "dependencies": {
    "next": "14.2.35",
    "react": "18.3.1",
    "react-dom": "18.3.1"
  },
  "devDependencies": {
    "typescript": "5.6.3",
    "@types/node": "20.16.11",
    "@types/react": "18.3.11",
    "@types/react-dom": "18.3.0",
    "vitest": "2.1.3"
  }
}
```

- [ ] **Step 2: Create `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 3: Create `frontend/next.config.mjs`**

```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {};

export default nextConfig;
```

- [ ] **Step 4: Create `frontend/vitest.config.ts`**

```typescript
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
  },
});
```

- [ ] **Step 5: Write the failing test — `frontend/lib/health.test.ts`**

```typescript
import { describe, expect, it } from "vitest";
import { getHealthStatus } from "./health";

describe("getHealthStatus", () => {
  it("reports ok", () => {
    expect(getHealthStatus()).toEqual({ status: "ok" });
  });
});
```

- [ ] **Step 6: Install dependencies and run the test to verify it fails**

Run: `cd frontend && npm install && npm test`
Expected: FAIL — `Cannot find module './health'`.

- [ ] **Step 7: Write `frontend/lib/health.ts`**

```typescript
export type HealthStatus = {
  status: "ok";
};

export function getHealthStatus(): HealthStatus {
  return { status: "ok" };
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd frontend && npm test`
Expected: PASS (1 test passed).

- [ ] **Step 9: Create the app shell and health route**

`frontend/app/layout.tsx`:

```tsx
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
```

`frontend/app/page.tsx`:

```tsx
export default function HomePage() {
  return (
    <main>
      <h1>Privacy-First Medical LLM Gateway</h1>
      <p>Chat interface ships in a later milestone.</p>
    </main>
  );
}
```

`frontend/app/api/healthz/route.ts`:

```typescript
import { NextResponse } from "next/server";
import { getHealthStatus } from "@/lib/health";

export function GET() {
  return NextResponse.json(getHealthStatus());
}
```

- [ ] **Step 10: Verify the app builds**

Run: `cd frontend && npm run build`
Expected: build completes with no errors.

- [ ] **Step 11: Commit**

```bash
git add frontend/package.json frontend/tsconfig.json frontend/next.config.mjs \
  frontend/vitest.config.ts frontend/lib frontend/app
git commit -m "feat: scaffold Next.js frontend with health check"
```

---

## Task 7: Frontend Dockerfile

**Files:**
- Create: `frontend/Dockerfile`
- Create: `frontend/.dockerignore`

**Interfaces:**
- Consumes: buildable app from Task 6.
- Produces: `chatgpt-proxy-frontend` image, consumed by Task 8's
  `docker-compose.yml` (`frontend` service, `build: ./frontend`).

- [ ] **Step 1: Create `frontend/.dockerignore`**

```
node_modules
.next
```

- [ ] **Step 2: Create `frontend/Dockerfile`**

```dockerfile
FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]
```

- [ ] **Step 3: Build and smoke-test the image**

Run:
```bash
cd frontend
docker build -t chatgpt-proxy-frontend .
docker run --rm -p 3000:3000 -d --name frontend-smoke chatgpt-proxy-frontend
sleep 3
curl -sf http://localhost:3000/api/healthz
docker stop frontend-smoke
```
Expected: `curl` prints `{"status":"ok"}`; container stops cleanly.

- [ ] **Step 4: Commit**

```bash
git add frontend/Dockerfile frontend/.dockerignore
git commit -m "chore: add frontend Dockerfile"
```

---

## Task 8: Docker Compose stack

**Files:**
- Create: `docker-compose.yml`
- Create: `docker-compose.override.yml.example`
- Create: `.env.example`
- Create: `.gitignore`

**Interfaces:**
- Consumes: `chatgpt-proxy-backend` image (Task 5), `chatgpt-proxy-frontend`
  image (Task 7).
- Produces: full local stack, verified end-to-end in Task 10.

- [ ] **Step 1: Create `.env.example`**

```
# --- Backend ---
ENVIRONMENT=development
LOG_LEVEL=INFO

# --- Postgres ---
POSTGRES_USER=chatgpt_proxy
POSTGRES_PASSWORD=change-me
POSTGRES_DB=chatgpt_proxy

# --- Keycloak ---
KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD=change-me
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "5432:5432"
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
      - "8080:8080"

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama

  backend:
    build: ./backend
    environment:
      ENVIRONMENT: ${ENVIRONMENT}
      LOG_LEVEL: ${LOG_LEVEL}
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    depends_on:
      - backend

volumes:
  postgres_data:
  ollama_data:
```

- [ ] **Step 3: Create `docker-compose.override.yml.example`**

```yaml
services:
  backend:
    volumes:
      - ./backend/app:/app/app
      - ./backend/tests:/app/tests
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  frontend:
    volumes:
      - ./frontend:/app
      - /app/node_modules
      - /app/.next
    command: npm run dev
```

- [ ] **Step 4: Create root `.gitignore`**

```
# Python
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
*.egg-info/

# Node
node_modules/
.next/

# Env / local overrides
.env
docker-compose.override.yml

# OS
.DS_Store
```

- [ ] **Step 5: Validate the compose file**

Run: `cp .env.example .env && docker compose config --quiet`
Expected: exits 0 with no output (valid config).

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml docker-compose.override.yml.example .env.example .gitignore
git commit -m "chore: add Docker Compose stack for postgres, keycloak, ollama, backend, frontend"
```

---

## Task 9: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `backend/pyproject.toml` (Tasks 1, 4), `frontend/package.json`
  (Task 6).

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: lint-imports
      - run: pytest --cov=app

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm install
      - run: npm test
      - run: npm run build
```

- [ ] **Step 2: Run the same commands locally to confirm the workflow would pass**

Run:
```bash
cd backend && ruff check . && lint-imports && pytest --cov=app && cd ..
cd frontend && npm test && npm run build && cd ..
```
Expected: all commands exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add backend and frontend CI workflow"
```

---

## Task 10: Full-stack smoke verification & README

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–9.

- [ ] **Step 1: Create `README.md`**

```markdown
# ChatGPT Proxy — Privacy-First Medical LLM Gateway

Research/evaluation platform that pseudonymizes sensitive clinical text
before it reaches an LLM, and deterministically resolves tokens back on the
way out. See
`docs/superpowers/specs/2026-08-11-privacy-gateway-mvp-design.md` for the
full design and `docs/adr/` for architectural decisions. This is a research
platform, not a certified clinical product, and does not by itself
constitute GDPR compliance (see design doc §1).

## Local development

1. Copy `.env.example` to `.env` and `docker-compose.override.yml.example`
   to `docker-compose.override.yml`, adjusting secrets as needed.
2. `docker compose up --build`
3. Backend health check: `curl http://localhost:8000/health`
4. Frontend health check: `curl http://localhost:3000/api/healthz`

## Backend tests

```bash
cd backend
pip install -e ".[dev]"
pytest
ruff check .
lint-imports
```

## Frontend tests

```bash
cd frontend
npm install
npm test
npm run build
```
```

- [ ] **Step 2: Run the full stack and verify both health checks**

Run:
```bash
cp -n .env.example .env
cp -n docker-compose.override.yml.example docker-compose.override.yml
docker compose up --build -d
sleep 5
curl -sf http://localhost:8000/health
curl -sf http://localhost:3000/api/healthz
docker compose down
```
Expected: both curls print `{"status":"ok"}`; `docker compose down` exits 0.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add local development README"
```

---

## Post-Plan State

After this plan: `docker compose up` brings up a working (empty) full
stack with passing health checks on backend and frontend, CI enforces
lint/tests/import-boundaries on every push, and every `privacy_gateway/`
subpackage from the design spec exists as an empty, import-linter-guarded
directory ready for the next plan (data model & tenant RLS) to fill in.
