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

## Frontend tests

```bash
cd frontend
npm install
npm test
npm run build
```
