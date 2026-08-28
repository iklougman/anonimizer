# e2e — Playwright end-to-end tests

Browser-driven tests that exercise the running compose stack (frontend,
backend, Postgres, Keycloak) as a real user would. Chromium only for Phase 1.

Each run provisions its own throwaway tenant, branch, and three users
(`super_admin`, `doctor`, `staff`) via `backend/scripts/provision_e2e_tenant.py`
and tears it down afterwards — see `global-setup.ts` / `global-teardown.ts`.

## Prerequisites

From the repo root, bring up the full stack:

```bash
docker compose up -d --build
```

## Install

```bash
cd e2e
npm install
npx playwright install --with-deps chromium
```

## Run

Wait for the stack to become healthy, then run the suite:

```bash
./scripts/wait-for-health.sh && npm test
```

`npm test` runs `playwright test`, which provisions a fresh e2e tenant via
global setup before any spec runs, and cleans it up via global teardown
afterwards — even if a test fails.

## Viewing results

After a run, view the HTML report:

```bash
npx playwright show-report
```

## Configuration

`playwright.config.ts` loads the repo-root `.env` file. Relevant variables:

- `E2E_BASE_URL` — frontend base URL (default `http://localhost:3000`)
- `KEYCLOAK_ISSUER` — Keycloak realm issuer URL used for ROPC token requests
  (default `http://localhost:8080/realms/chatgpt-proxy-dev`)
- `NEXTAUTH_SECRET` — required by `support/session.ts`'s `injectSession()` to
  encode a valid NextAuth session cookie
