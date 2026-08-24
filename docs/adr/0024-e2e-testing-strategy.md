# 0024 — End-to-End Testing Strategy (Phase 1)

**Status:** Accepted

## Context

Nothing in this repo exercised the system the way a real doctor does:
through a browser, through Keycloak's actual login redirect, against a
running backend, with RBAC and admin-provisioning code paths that only ever
run when frontend + backend + Keycloak + Postgres are all up together.
Backend integration tests (`backend/tests/integration/`) already hit real
Postgres via FastAPI's `TestClient`, but bypass auth via
`dependency_overrides` and use a hand-rolled, test-only LLM stub. Frontend
tests are jsdom unit tests with no real navigation or HTTP. CI's `compose`
job only checked that `docker-compose.yml` parses — it never proved the
stack actually starts or works together.

This ADR records the Playwright-based e2e suite covering four critical-path
journeys, wired into CI, plus a first-class stub LLM provider (for
deterministic/fast CI runs) and the ephemeral-tenant provisioning it relies
on. This is **Phase 1 only** — the full RBAC permission matrix,
entitlement-gating edge cases beyond the one journey that needs them, and a
scored corpus-benchmark CI job (ADR-0017's F1/leakage-rate reporting) are
explicitly deferred to a future Phase 2. The infrastructure built here
(Playwright config, ephemeral tenant provisioning, CI wiring) is designed so
Phase 2 extends it without rework.

## Decision

Add a Playwright suite (`e2e/`, sibling to `frontend/`/`backend/`/
`ops_admin/`) covering four journeys:

- **`login.spec.ts`** — drives the real Keycloak-hosted login UI (the one
  journey whose point is proving the redirect flow itself): unauthenticated
  request → redirect to Keycloak's hosted form → fill/submit → redirect back
  → authenticated element renders; and the reverse, an unauthenticated
  request to a protected route redirects to sign-in.
- **`chat-roundtrip.spec.ts`** — uses a session-injection shortcut (doctor
  user): send a message containing a name/city → assert the streamed reply
  renders and contains the raw values back (proves
  pseudonymize → LLM → deanonymize round-tripped correctly through opaque
  tokens) → reload the conversation and confirm history persists and still
  deanonymizes.
- **`rbac-branch-visibility.spec.ts`** — three injected sessions (doctor,
  staff, super_admin): doctor creates a conversation → staff (same branch)
  confirms it's absent from their list (own-only default) → super_admin
  grants `conversations:read:branch` via `/admin/permissions` → staff
  confirms it's now visible and opens it read-only.
- **`admin-branch-and-user-crud.spec.ts`** — injected super_admin session:
  create + rename a branch via `/admin/branches`; create a user assigned to
  that branch and change their role via `/admin/users` (exercises
  `POST /api/admin/users`'s Keycloak-provisioning path).

Supporting infrastructure:

- **`StubProvider`** (`backend/app/llm_gateway/stub_provider.py`) — a new,
  first-class `LLMProvider` implementation (`LLM_PROVIDER=stub`) that echoes
  the sanitized prompt verbatim, word-chunked into `StreamDelta`s. Because
  it's a pure echo, it cannot invent, translate, or drop a token — exactly
  what the chat-roundtrip journey needs to prove the pipeline's token
  round-trip, without any real model call. A `Settings` validator
  (`_forbid_stub_provider_in_production`) makes `LLM_PROVIDER=stub` a hard
  startup crash when `ENVIRONMENT=production`, matching ADR-0020's
  fail-closed policy — enforced in code, not deployment discipline.
- **Ephemeral tenant provisioning** (`backend/scripts/provision_e2e_tenant.py`)
  — each full e2e run creates its own uniquely-suffixed tenant, one branch,
  and three users (super_admin, doctor, staff, all on that one branch) via
  the existing repository layer, grants the `anonymization` entitlement, and
  creates real Keycloak users via `KeycloakAdminClient` (extended with a new
  `set_password()` method so Resource Owner Password Credentials login works
  without hitting the forced-password-update wall). `global-setup.ts` runs
  `create` once per full Playwright run; `global-teardown.ts` always runs
  `cleanup --tenant-id <id>`, deleting Keycloak users best-effort and all DB
  rows in FK-safe order. These ephemeral tenants are never the two fixed dev
  tenants from `seed_dev_tenants.py` — those stay reserved for manual
  testing.
- **CI integration** — a fourth job, `e2e`, added to
  `.github/workflows/ci.yml` (`needs: [backend, frontend]`, so it only runs
  once the fast jobs pass). Brings up `postgres keycloak backend ops_admin
  frontend` — omitting `ollama` (never depended on when
  `LLM_PROVIDER=stub`) and `traefik` (Phase 1 tests the app, not the reverse
  proxy) — with `ENVIRONMENT=test` and `LLM_PROVIDER=stub`, waits for health,
  runs the suite, and uploads the Playwright HTML report (always) and
  `docker compose logs` (on failure) as artifacts.

## Alternatives Considered

- **Cypress or Puppeteer instead of Playwright** — rejected: the RBAC
  journey needs multiple simultaneous role sessions in one test, which
  Playwright's multi-context support handles natively.
- **Driving every journey through the real Keycloak UI** — rejected as the
  default for all four specs: only the login journey needs to prove that
  specific redirect flow. The session-injection shortcut trades UI coverage
  for speed on the other three, a documented tradeoff, not an oversight —
  it does couple three of the four specs to NextAuth's internal JWE cookie
  format (see Consequences).
- **Reusing the two fixed dev tenants from `seed_dev_tenants.py`** —
  rejected: not safe for parallel or repeated runs, and would pollute
  tenants reserved for manual testing.
- **A scored corpus benchmark as part of this plan** — rejected: ADR-0017
  already scopes that separately; conflating it here would block Phase 1's
  smaller, achievable goal behind a much larger one.

## Consequences

CI gets slower on every PR (five containers plus a real browser, versus the
existing three lighter jobs), traded for the first automated proof that
these four journeys work together end-to-end. This is a bounded claim,
matching ADR-0015's framing: it proves these four journeys pass in this
stub-LLM configuration against one hand-provisioned tenant shape — not the
full RBAC matrix, not real-LLM behavior, not scale or concurrency.

Two specific risks are accepted for Phase 1, both because they are cheap to
notice and fix if they bite: an upgrade of `next-auth` past `^4.24.15` that
changes its JWE internals would break `injectSession()` for three of the
four specs (the login spec, using the real UI, would keep working); and all
four specs share one ephemeral tenant per run, so a bug that corrupts shared
state in one spec can produce a confusing failure in a later spec within the
same run (already observed once during implementation — two specs
independently creating conversations for the shared doctor account made a
third spec's exact-count assertion flaky across the full suite; fixed by
loosening that assertion rather than splitting tenants). Per-spec-file
tenants are a reasonable Phase 2 reconsideration if this proves flakier in
practice than this one incident suggested.

## Security Implications

`StubProvider`'s production guard is enforced at settings-validation time
(a startup crash), not deployment discipline — this is the one ADR-0020
fail-closed trigger this suite's own existence adds. The login journey's
negative assertion (unauthenticated request redirects to sign-in)
incidentally exercises a second, pre-existing fail-closed trigger;
comprehensive coverage of all of them is Phase 2.

## Privacy Implications

The echo-based `StubProvider` lets `chat-roundtrip.spec.ts` prove
pseudonymize → LLM → deanonymize round-trips real values through opaque
tokens without ever sending real-looking data to any real external provider
in CI. This is a bounded claim about mechanics, not about a real model's
occasionally-uncooperative behavior — `StubProvider` is a deterministic
echo, not a model, so this suite can never positively exercise
`LeakageDetectedError`/`UnresolvedTokenError`'s fail-closed paths the way a
real model's actual misbehavior would. That coverage remains the existing
golden-corpus suite's job, alongside `OUTPUT_GUARD_ENABLED`'s
manual-inspection path.

## Reversibility

High — the e2e suite, `StubProvider`, and the provisioning script are all
additive and independently removable. Removing the `e2e` CI job or deleting
the `e2e/` directory does not affect any other part of the system;
`StubProvider` is one more branch in an existing provider registry, guarded
against production use by its own validator.
