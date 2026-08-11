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
