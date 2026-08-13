# Data Model & Tenant RLS — Design

**Status:** Draft — pending review

**Depends on:** `docs/superpowers/specs/2026-08-11-privacy-gateway-mvp-design.md`
§4 (data model), ADR-0008 (isolated token vault), ADR-0009 (token scope),
ADR-0010 (encryption), ADR-0011 (tenant isolation), ADR-0019 (retention).

**Follows:** `docs/superpowers/plans/2026-08-11-repo-scaffold-docker-compose.md`,
whose Post-Plan State names this as the next plan.

## 1. Purpose & Scope

Stand up the persistent data model for every table in design-doc §4, with
Postgres Row-Level Security as the database-layer half of tenant isolation
(ADR-0011) and a mandatory-`tenant_id` repository layer as the
application-layer half. Also implement the Token Vault (ADR-0008) and its
`KeyProvider`/envelope-encryption dependency (ADR-0010), since
`token_mappings.encrypted_value` cannot be meaningfully modeled without a
concrete encryption path.

**In scope:** all 8 tables from §4 as SQLAlchemy models + one Alembic
migration; RLS policies (enabled and forced) on every tenant-scoped table;
a tenant-scoped session helper; repositories for `tenants`, `users`,
`conversations`, `messages`; `KeyProvider` + `TokenVault` for
`tenant_keys`/`token_mappings`; the CI schema-migration RLS test from
design-doc §9.

**Out of scope (deferred to later plans):**
- Keycloak/JWT authentication and deriving `tenant_id` from a real request
  (ADR-0021) — this plan's session helper takes `tenant_id` as an explicit
  argument; the auth plan swaps in "from JWT" as the caller.
- Detection/pseudonymization pipeline (design-doc §5) — nothing calls
  `TokenVault.create_mapping` yet outside this plan's own tests.
- `audit_events` and `llm_requests` repositories — schema only; the
  observability and `llm_gateway` plans own writing to them.
- Retention enforcement jobs (ADR-0019) — the `expires_at` columns and
  crypto-shredding *mechanism* (delete a tenant's DEK) exist, but no
  scheduled job runs it yet.
- A real KMS/Vault `KeyProvider` implementation — file-secret only, per
  ADR-0010's explicit deferral.

## 2. Tech Stack

- **ORM:** SQLAlchemy 2.0, sync (`Session`, not `AsyncSession`) — matches
  the existing sync FastAPI endpoints (`app/api/health.py`) and keeps
  pytest fixtures simple.
- **Driver:** `psycopg[binary]` (psycopg3).
- **Migrations:** Alembic, sync `env.py`.
- **New backend deps** (`backend/pyproject.toml`): `sqlalchemy>=2.0,<2.1`,
  `psycopg[binary]>=3.2,<4`, `alembic>=1.13,<2`, `cryptography>=43,<44`.
- **Primary keys:** application-generated `uuid.uuid4()`, Python-side
  default — consistent with the non-sequential-identifier philosophy
  already established for tokens (ADR-0009) and portable without a
  Postgres extension.
- **Settings additions** (`app/config.py`, extending Task 2's `Settings`):
  - `database_url: str` — required, no default (fail-closed: missing DB
    config crashes startup, per ADR-0020).
  - `master_key_path: str` — required, no default. Path to a mounted
    secret file (Docker secret / bind-mounted file) holding the master key
    that wraps every tenant's DEK (ADR-0010). Never a literal key in env.

## 3. Data Model

All 8 tables from design-doc §4, one Alembic migration
(`backend/alembic/versions/0001_initial_schema.py`):

```
tenants(id uuid pk, name text, keycloak_realm text unique,
        retention_days int, created_at timestamptz)

users(id uuid pk, tenant_id uuid fk->tenants, keycloak_subject text,
      email text, role text, created_at timestamptz)
      unique(tenant_id, keycloak_subject)

conversations(id uuid pk, tenant_id uuid fk->tenants, user_id uuid fk->users,
              created_at timestamptz, expires_at timestamptz)

messages(id uuid pk, conversation_id uuid fk->conversations,
         tenant_id uuid fk->tenants, role text, sanitized_content text,
         created_at timestamptz)

token_mappings(id uuid pk, tenant_id uuid fk->tenants,
               conversation_id uuid fk->conversations, token text,
               entity_type text, encrypted_value bytea, dek_id uuid fk->tenant_keys.id,
               created_at timestamptz, expires_at timestamptz, deleted_at timestamptz)
               unique(tenant_id, conversation_id, token)

tenant_keys(id uuid pk, tenant_id uuid fk->tenants, wrapped_dek bytea,
            key_version int, created_at timestamptz)
            unique(tenant_id, key_version)

audit_events(id uuid pk, tenant_id uuid fk->tenants,
             conversation_id uuid fk->conversations, event_type text,
             entity_type text, token text, actor text, timestamp timestamptz)

llm_requests(id uuid pk, tenant_id uuid fk->tenants,
             conversation_id uuid fk->conversations, provider text, model text,
             sanitized_prompt text, sanitized_response text,
             tokens_in int, tokens_out int, cost_usd numeric,
             latency_ms int, created_at timestamptz)
```

Notes:
- Every child table carries `tenant_id` directly (denormalized, matching
  §4 verbatim) rather than requiring a join to `conversations` to know the
  tenant — this is what makes both RLS and the mandatory-first-argument
  repository check possible without a join on every query.
- `token_mappings` unique constraint on `(tenant_id, conversation_id,
  token)` enforces ADR-0009's scoping at the database level, not just in
  application logic.
- `tenant_keys` has a surrogate `id` (not `tenant_id`) as primary key, with
  `unique(tenant_id, key_version)` — this is what lets an old wrapped DEK
  stay retrievable by `dek_id` after a future rotation writes a new row
  with an incremented `key_version`, so historical `token_mappings` rows
  encrypted under an earlier key stay decryptable. Only one row per tenant
  exists in MVP (`key_version = 1`, created at tenant creation); the
  rotation *operation* itself (writing a new row, re-encrypting nothing)
  is out of scope here, but the schema doesn't foreclose it.
- `audit_events` and `llm_requests` deliberately store no raw values —
  enforced by convention at this layer (there is no column for it); a
  privacy-invariant test in this plan asserts sanitized-only content
  doesn't regress once producers exist.

## 4. RLS Enforcement (ADR-0011, database layer)

For every tenant-scoped table **except `tenants` itself**
(`users`, `conversations`, `messages`, `token_mappings`, `tenant_keys`,
`audit_events`, `llm_requests`), the migration runs:

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <table>
  USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid);
```

`FORCE ROW LEVEL SECURITY` is what makes this a real backstop rather than
a no-op: without it, the role that owns the tables (the app's own DB user
in a single-role MVP setup) bypasses RLS by default, which would silently
defeat ADR-0011's "layer 2 catches a layer-1 bug" guarantee for the most
likely real-world case — the application's own connection.

`current_setting('app.current_tenant_id')` is a session-local Postgres
variable, set per logical unit of work by:

```python
# backend/app/db/session.py
@contextmanager
def tenant_scoped_session(tenant_id: uuid.UUID) -> Iterator[Session]:
    with SessionLocal() as session:
        session.execute(text("SET LOCAL app.current_tenant_id = :tid"),
                         {"tid": str(tenant_id)})
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
```

`SET LOCAL` scopes the variable to the current transaction, so it cannot
leak across pooled connections between requests. Every repository method
runs inside a session opened this way; there is no code path that opens a
session without a `tenant_id`. The future auth plan's only change here is
*where* `tenant_id` comes from (JWT claim, re-validated per ADR-0021)
rather than a test/caller-supplied value — the session and repository
code do not change.

`tenants` itself has no RLS: creating a tenant happens before any tenant
context exists, and tenant rows aren't tenant-scoped data — they're the
scoping dimension.

## 5. Repository Layer (ADR-0011, application layer)

`backend/app/db/repositories/base.py` defines the pattern: every public
method's first parameter is `tenant_id: uuid.UUID`, and every query
includes it explicitly in the `WHERE` clause — redundant with RLS by
design (ADR-0011: "neither layer alone is trusted as sufficient"), not
because RLS is assumed absent.

Concrete repositories, each `backend/app/db/repositories/<name>.py`:
- `TenantRepository` — `create(name, keycloak_realm, retention_days)`
  (not tenant-scoped, since it predates the tenant existing); generates a
  random 256-bit DEK, wraps it via `KeyProvider.wrap_dek`, and inserts the
  `tenants` row plus the first `tenant_keys` row (`key_version = 1`) in
  one transaction.
- `UserRepository` — `create(tenant_id, ...)`, `get_by_keycloak_subject(tenant_id, subject)`.
- `ConversationRepository` — `create(tenant_id, user_id)`, `get(tenant_id, id)`, `list(tenant_id, user_id)`.
- `MessageRepository` — `create(tenant_id, conversation_id, role, sanitized_content)`, `list(tenant_id, conversation_id)`.

## 6. Token Vault & KeyProvider (ADR-0008, ADR-0010)

`backend/app/privacy_gateway/token_vault/key_provider.py`:

```python
class KeyProvider(Protocol):
    def wrap_dek(self, raw_dek: bytes) -> bytes: ...
    def unwrap_dek(self, wrapped_dek: bytes) -> bytes: ...

class FileSecretKeyProvider:
    """Wraps/unwraps DEKs (AES-KW) under a master key read once from
    `Settings.master_key_path` at process start. Stateless with respect
    to tenants — it knows nothing about which row a wrapped DEK belongs
    to; that's the repository's job."""
```

`KeyProvider` only wraps/unwraps opaque key bytes — it has no notion of
"the tenant's DEK" or which `tenant_keys` row is active, keeping it a
pure crypto primitive. `TenantRepository.create` generates a random
256-bit DEK, wraps it via `KeyProvider.wrap_dek`, and inserts the first
`tenant_keys` row (`key_version = 1`). `TokenVault` resolves the *active*
key for a tenant by querying `tenant_keys` for the row with the highest
`key_version` for that `tenant_id`, then calls `KeyProvider.unwrap_dek` on
its `wrapped_dek` for the duration of one encrypt/decrypt operation —
never cached beyond that, and never logged (ADR-0010).

`backend/app/privacy_gateway/token_vault/vault.py`:

```python
class TokenVault:
    def create_mapping(self, tenant_id, conversation_id, entity_type,
                        original_value) -> str: ...      # returns token
    def resolve_token(self, tenant_id, conversation_id, token) -> str | None: ...
    def resolve_tokens(self, tenant_id, conversation_id, tokens) -> dict[str, str]: ...
    def delete_mapping(self, tenant_id, conversation_id, token) -> None: ...
    def expire_mapping(self, tenant_id, conversation_id, token) -> None: ...
```

- `create_mapping` generates the token (`[TYPE_XXXXX]`, `secrets.token_hex`
  suffix per ADR-0009), encrypts `original_value` with AES-GCM under the
  tenant's DEK (fresh nonce per encryption, stored alongside ciphertext in
  `encrypted_value`), and writes the row via a tenant-scoped session.
- `resolve_token`/`resolve_tokens` is the authorization-check mechanism
  from design-doc §5: a token is only decrypted if a row exists for
  **this** `(tenant_id, conversation_id, token)` — RLS plus the explicit
  `WHERE` make a cross-tenant or cross-conversation token lookup return no
  row, so the vault has nothing to decrypt. This is the concrete
  enforcement point for the prompt-injection attack class described in
  §5, even though the pipeline that would call it in production doesn't
  exist until a later plan — this plan proves the mechanism in isolation.
- `delete_mapping` hard-deletes; `expire_mapping` sets `deleted_at` (soft),
  per ADR-0019's two-mechanism retention model. Neither is wired to a
  scheduler yet — that's the retention plan's job. Deleting a tenant's DEK
  entirely (crypto-shredding) is a `TenantRepository` operation, not a
  vault operation, and is likewise not scheduled here.
- `privacy_gateway/` still imports nothing network-capable (ADR-0001) —
  `cryptography` and the DB driver are the only new dependencies, both
  local/non-network.

## 7. Testing Strategy

- **CI:** add a `postgres:16-alpine` service container to the `backend`
  job in `.github/workflows/ci.yml` (same image/tag as
  `docker-compose.yml`, so behavior matches local dev), with a
  `POSTGRES_PASSWORD` from a workflow secret/generated value. Before
  `pytest`, run `alembic upgrade head` against it.
- **`tests/privacy_invariants/test_rls.py`** (new): for every
  tenant-scoped table, assert `pg_class.relrowsecurity` and
  `relforcerowsecurity` are both true, and at least one row exists in
  `pg_policies` for it. This is the design-doc §9 "CI includes a
  schema-migration test asserting every tenant-scoped table has an RLS
  policy before merge" requirement, and ADR-0011's explicit CI
  requirement.
- **`tests/privacy_invariants/test_token_vault.py`** (new): resolving a
  token created under tenant A, called with tenant B's `tenant_id`,
  returns `None`/raises — never the decrypted value. Same for a token
  from a different `conversation_id` under the same tenant. Token
  uniqueness within `(tenant_id, conversation_id)` is exercised via the
  DB unique constraint (attempting a collision raises `IntegrityError`).
- **`tests/integration/test_repositories.py`** (new): CRUD round-trip per
  repository against the real Postgres container. One explicit negative
  test: opening a raw session, running a query with **no**
  `SET LOCAL app.current_tenant_id` set at all, against a table with an
  existing row, returns zero rows — proving RLS defaults to deny rather
  than defaulting to allow when the session variable is simply absent
  (distinct from the wrong-tenant case above).
- **Local dev:** developers run migrations/tests against the
  `docker-compose.yml` `postgres` service already established in the
  scaffold plan; no new local tooling required.

## 8. Assumptions Requiring Validation

- `SET LOCAL` reliably resets between pooled connection reuses under
  SQLAlchemy's default `Session`/connection-pool behavior — needs a
  targeted test (covered by the "no session var set" negative test
  above, run repeatedly across pooled connections) rather than taken on
  faith.
- A single Postgres role for the app (no separate migration-only
  superuser role) is acceptable for MVP; `FORCE ROW LEVEL SECURITY`
  is the chosen mitigation for that specific risk rather than a
  privilege-separated role, consistent with ADR-0011's stated MVP
  scope (single Postgres + RLS, not per-tenant databases).
