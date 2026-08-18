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
3. Install the German spaCy model and fetch the reference datasets. Both are
   build-time inputs (design spec §6) — `app/privacy_gateway/` never downloads
   anything at runtime (ADR-0001):

   ```bash
   cd backend
   python -m spacy download de_core_news_lg
   python scripts/fetch_reference_data.py
   ```

   The fetch downloads the German ORDO OWL release (~51 MB, CC BY 4.0, Orphadata)
   and the Destatis Krankenhausverzeichnis (~2.4 MB, free use with attribution)
   into `backend/app/privacy_gateway/risk_scoring/data/`, which is gitignored. The
   script skips files that are already present, so it is safe to re-run.
4. `docker compose up --build`
5. Seed the two dev tenants/users that match `keycloak/realm-export.json`
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
6. Backend health check: `curl http://localhost:8000/health`
7. Frontend health check: `curl http://localhost:3000/api/healthz`

### Changing `APP_RUNTIME_PASSWORD`

The restricted `app_runtime` Postgres role that the application connects as is
created (and its password set) by migration `0003`. Alembic will not re-run a
migration it has already applied, so **changing `APP_RUNTIME_PASSWORD` in `.env`
and restarting is not enough** — the role keeps its old password and the backend
fails to authenticate.

To roll the password, re-run `0003`'s upgrade logic against the database:

```bash
cd backend
export APP_RUNTIME_PASSWORD=<the new password>
alembic downgrade 0002 && alembic upgrade head
```

`0003`'s upgrade is written to `ALTER ROLE ... PASSWORD` when the role already
exists, so this is safe to repeat. Note that its downgrade drops the
`app_runtime` role, so no application process should be connected while this
runs.

## Backend tests

Requires a running Postgres (`docker compose up -d postgres`) and the
local master key file from step 2 above.

The detection tests need the `de_core_news_lg` model installed (step 3 above).
They do **not** need the reference datasets — they inject small in-memory
rare-disease and hospital sets instead.

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

### Golden-corpus regression

`backend/tests/privacy_invariants/test_pipeline_corpus.py` runs the whole
`evaluation/golden_corpus/` through `sanitize()` and asserts that no annotated
raw PII string survives, that every note round-trips through `deanonymize()`,
and that cross-tenant / cross-conversation token resolution always fails. It
runs in CI on every push (design doc §9).

If a note starts failing, fix the detector — do not edit the note. The only
sanctioned exception is a genuine `de_core_news_lg` recall gap, which goes in
that file's `KNOWN_RECALL_GAPS` set with a comment recording what the model
produced instead. Its counterpart `KNOWN_GUARD_FALSE_POSITIVES` records the
mirror-image case — a note the output guard rejects on a model *precision*
gap — under the same rule.

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
identifier crossed the LLM boundary, inspect the persisted
`llm_requests.sanitized_prompt` row for this conversation and confirm it
contains `PATIENT_...`/`LOCATION_...`-shaped tokens instead of "Anna Schmitt"
/ "Heidelberg":

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT sanitized_prompt FROM llm_requests ORDER BY created_at DESC LIMIT 1;"
```

Switch to OpenAI by setting `LLM_PROVIDER=openai` and `OPENAI_API_KEY` in
`.env`, then `docker compose up -d --build backend` and repeat — this is the
concrete check for ADR-0022's "the external provider never sees the identity
mapping" claim against a real network call.

## Frontend tests

```bash
cd frontend
npm install
npm test
npm run build
```

### Manual end-to-end check

```bash
docker compose up -d --build frontend
open http://localhost:3000
```

Expected: redirected to Keycloak's hosted login page (not a custom form).
Log in as `dr.mueller` / `dev-password`. You should land on the chat shell
with an empty sidebar. Click "+ Neue Anfrage", send a message, and confirm:

- A typing indicator shows while the backend buffers sanitize → LLM →
  deanonymize (no partial/raw text appears before the first `token` event).
- The reply streams in word-by-word once it starts.
- The conversation gets a title (derived from your first message) after
  the first exchange, and appears in the sidebar.
- Refreshing the page keeps you logged in and reloads the conversation
  history correctly (proves the NextAuth session and `GET .../messages`
  round-trip both work).

To see the 422 fail-closed path, send a message combining several
identifying details (age + city + a rare disease name + a date) and
confirm it renders as an inline error with no retry button.
