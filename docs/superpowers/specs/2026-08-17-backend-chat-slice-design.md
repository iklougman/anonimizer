# Backend Chat Slice — Auth, LLM Gateway, Chat API

**Status:** Approved for implementation planning
**Date:** 2026-08-17
**Related:** `docs/superpowers/specs/2026-08-11-privacy-gateway-mvp-design.md`, ADR-0016, ADR-0020, ADR-0021, ADR-0022, `docs/superpowers/plans/2026-08-15-detection-pseudonymization-pipeline.md`

## 1. Purpose & Scope

The privacy pipeline (`sanitize()`/`deanonymize()`) is complete and tested, but
nothing calls it over HTTP: `backend/app/api/` has only a health check, and
`backend/app/auth/`, `backend/app/llm_gateway/`, `backend/app/guardrails/` are
empty scaffold packages. This spec covers the three subsystems needed before a
real chat UI has anything to talk to:

1. **Auth** — JWT validation against a single pre-provisioned Keycloak realm,
   tenant resolution.
2. **LLM Gateway** — the `LLMProvider` abstraction with both MVP adapters
   (Ollama, OpenAI), per ADR-0016 and ADR-0022.
3. **Chat API** — conversation CRUD + the send-message endpoint that chains
   auth → `sanitize()` → LLM Gateway → `deanonymize()`.

**Explicitly out of scope for this slice** (deferred to later work, called out
here so it isn't silently assumed):

- Automated realm-per-tenant provisioning (Keycloak admin API). This slice
  uses one hand-configured dev realm with a couple of seeded test tenants;
  full provisioning is a separate future plan.
- The frontend chat UI itself — a separate spec, once this API exists to
  build against.
- NeMo Guardrails / prompt-injection rails (Phase 2 per the master design
  doc; `guardrails/` stays an empty scaffold).
- `audit_events` writes beyond what's needed for the two server-side failure
  paths below — a full audit/observability pass is separate work.

## 2. Auth (`backend/app/auth/`)

A single Keycloak realm, `chatgpt-proxy-dev`, defined in a committed
`keycloak/realm-export.json` and imported at container start
(`start-dev --import-realm`). The realm has one OIDC client for the frontend
(public, PKCE) and at least two seeded test users belonging to different
tenants, each carrying a custom `tenant_id` claim mapped from a Keycloak user
attribute.

```
backend/app/auth/
  jwt_validator.py    # verify signature via Keycloak JWKS (cached), check exp/iss/aud
  tenant_resolver.py  # extract tenant_id + user_id claims, cross-check against
                       # the tenants/users tables (never trust the claim alone)
  dependencies.py      # FastAPI Depends(get_current_user) -> AuthenticatedUser
```

`get_current_user`:
1. Extracts the bearer JWT, verifies it against Keycloak's JWKS endpoint.
2. Extracts `tenant_id` (custom claim) and `sub` (Keycloak subject).
3. Looks up the tenant and the user (`keycloak_subject == sub`) in Postgres;
   404-equivalent (401) if either doesn't exist — the claim alone is never
   sufficient (ADR-0021).
4. Sets the RLS session variable for `tenant_id` on the request's DB session.
5. Returns `AuthenticatedUser(tenant_id, user_id)`, which every route
   requires via `Depends`.

**Fail-closed:** if the JWKS endpoint is unreachable (Keycloak down), token
validation fails and the request gets `503`, not a fallback/bypass path
(ADR-0020, ADR-0021).

**Local dev:** `README.md` gains a step to import the realm (either via the
Compose command or a documented `docker compose exec keycloak ...` step) and
notes the two seeded test users' credentials for manual testing.

## 3. LLM Gateway (`backend/app/llm_gateway/`)

```
backend/app/llm_gateway/
  provider.py         # LLMProvider protocol: complete(prompt: str) -> str
  ollama_provider.py  # POST http://ollama:11434/api/generate, stream: false
  openai_provider.py  # POST to OpenAI Chat Completions, stream: false
  registry.py          # get_provider() -> configured adapter, from Settings
```

Both adapters are non-streaming at the HTTP-to-provider level — the buffering
decision in §4 means nothing downstream needs partial tokens from the
provider itself, only the client-facing replay is chunked.

`Settings` additions: `llm_provider: Literal["ollama", "openai"] = "ollama"`,
`openai_api_key: str | None = None`, `ollama_base_url: str =
"http://ollama:11434"`, `ollama_model: str`, `openai_model: str`.
`get_provider()` fails at startup (not at first request) if
`llm_provider == "openai"` and `openai_api_key` is unset — fail-closed on
misconfiguration.

This module — not `privacy_gateway/` — is the codebase's chosen network
boundary for LLM calls (ADR-0001, ADR-0013): only pseudonymized text, built
by `privacy_gateway.pipeline.sanitize()`, is ever passed into
`LLMProvider.complete()`.

## 4. Chat API (`backend/app/api/`)

```
GET    /api/conversations                    -> [{id, title, created_at, updated_at}]
POST   /api/conversations                     -> create empty conversation (201)
GET    /api/conversations/{id}/messages       -> full history, deanonymized
DELETE /api/conversations/{id}                -> soft-delete (204)
POST   /api/conversations/{id}/messages       -> send message
```

All routes require `Depends(get_current_user)` and scope every repository
call by `tenant_id` (existing `ConversationRepository`/`MessageRepository`
pattern). Conversation listing/history are scoped additionally by
`user_id` (`list_for_user`, already exists).

**Schema addition** (small Alembic migration, not a design change):
`conversations.title: str | None` (derived from the first sanitized user
message, truncated) and `conversations.deleted_at: datetime | None`
(soft-delete; excluded from `list_for_user`/`get`).

### `POST /api/conversations/{id}/messages` — the core flow

The response only becomes `text/event-stream` once the entire pipeline has
already succeeded. Every failure path is a plain JSON error, never a
half-open stream — "no partial output" (ADR-0014, ADR-0020) holds at the HTTP
layer, not only within the output guard:

1. `sanitize(tenant_id, conversation_id, text)`. On
   `LowConfidenceSpanError`/`HighRiskMessageError`: **`422`**, generic
   fail-closed message ("Sensitive information could not be safely
   processed"), nothing persisted, no LLM call made.
2. Persist the user's message (`role="user"`, `sanitized_content`).
3. `LLMProvider.complete(sanitized_prompt)` — full completion, buffered
   in-process, never exposed to the client raw. On transport error/timeout:
   **`502`**.
4. `deanonymize(tenant_id, conversation_id, completion)`. On
   `LeakageDetectedError`/`UnresolvedTokenError`: **`500`**, logged as an
   `audit_events` row (event type + entity type + token, never the raw
   value, per the existing `audit_events` schema), generic message to the
   client — never echo what leaked.
5. Persist the assistant's message (`sanitized_content` = the pseudonymized
   form, re-derived from the same spans `deanonymize` resolved — storage
   never holds raw text, matching the master design doc §4 principle).
6. Success: respond `text/event-stream`, replaying the validated,
   human-readable text to the client in word-sized `token` events, followed
   by a `done` event carrying `{id, created_at}`.

### History reconstruction

`GET .../messages` calls `pipeline.deanonymize()` per stored message to
rebuild human-readable text on demand (master design doc §4: "reconstructed
on demand by resolving tokens through the output guard"). This reuses the
same fail-closed function used on the return path; a stored message is
already fully tokenized so the leakage scan is expected to pass trivially,
but a failure here (e.g. an expired/deleted token mapping past retention)
surfaces as `500` on that one message rather than corrupting the whole
history response.

## 5. Testing Strategy

- **Auth unit tests**: valid/expired/wrong-issuer/missing-tenant-claim JWTs;
  tenant claim present but tenant/user not found in Postgres; JWKS endpoint
  unreachable → `503`.
- **LLM Gateway unit tests**: each adapter against a mocked HTTP transport
  (no real network in CI); `get_provider()` fails closed at startup when
  `openai_api_key` is missing and `llm_provider="openai"`.
- **Chat API integration tests**: `TestClient` + a deterministic stub
  `LLMProvider` (canned replies), covering all three error paths (422/502/500)
  and the success path's SSE event sequence.
- **New privacy invariant**: a recording stub `LLMProvider` that captures the
  outbound prompt; run the golden corpus through the chat API and assert the
  captured prompt never contains raw corpus PII strings — this is the
  concrete "captured request body" check the master design doc §9 calls for,
  now exercised through the real API instead of only through
  `Pipeline.sanitize()` directly.
- **Manual/opt-in E2E**: real Keycloak login → known input → captured
  Ollama/OpenAI request body contains zero raw identifiers → deanonymized
  response is correct. Documented in `README.md`, requires the full Docker
  Compose stack; not part of the default CI `pytest` run (real LLM calls,
  real OpenAI cost).

## 6. Out of Scope / Follow-ups

- Automated Keycloak realm-per-tenant provisioning.
- The frontend chat UI (next spec).
- Streaming *from* the LLM provider itself (both adapters use
  `stream: false`; only the client-facing replay is chunked).
- Full `audit_events` coverage beyond leakage/rejection events.
- Rate limiting, request size limits, and other production hardening not
  needed for a research/eval deployment.
