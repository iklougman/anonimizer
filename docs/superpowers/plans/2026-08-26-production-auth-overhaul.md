# Production-Ready Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Proper access/refresh token handling, real single logout, admin-triggered session revocation, a production-ready admin-invite flow, and a new public "create your practice" self-signup flow, for a multi-tenant, PHI-adjacent medical-practice app.

**Architecture:** NextAuth v4 (JWT strategy) + self-hosted Keycloak stays the identity core; this plan closes real gaps in that existing design (cookie security, refresh-token scope, centralized error handling, single logout) and adds two new, currently-nonexistent flows (public tenant self-signup, working admin-invite emails) on top of it, reusing the app's established provisioning/compensation patterns throughout.

**Tech Stack:** Next.js 15 / NextAuth v4.24.15 / Mantine (frontend), FastAPI / SQLAlchemy / PyJWT / httpx (backend), Keycloak 24.0, Postgres 16 with RLS, Docker Compose + Traefik, Playwright (e2e), Mailpit (dev/CI SMTP catcher).

**Spec:** `/Users/imac/.claude/plans/1-as-user-i-polymorphic-willow.md`

## Global Constraints

- ADR-0021: tenant/user identity is re-validated against Postgres on **every** request — never trust JWT claims transitively. Nothing in this plan changes that.
- ADR-0020 (fail-closed): Keycloak unreachable → no session issued, no fallback auth. Best-effort cleanup calls (Keycloak logout, invite/verification emails) may fail silently — that governs *teardown*, not *issuance*, and is a different rule; never conflate the two.
- `app_runtime` (the FastAPI backend's normal Postgres role) has `SELECT`-only on `tenant_app_entitlements` — confirmed in `backend/alembic/versions/0007_apps_catalog_and_entitlements.py:119`. Never write that table directly from request-serving code; use the new `SECURITY DEFINER` function (Task 4).
- `registrationAllowed` stays `false` at the Keycloak realm level. Self-signup in this plan is entirely our own `/api/signup` endpoint + `VERIFY_EMAIL` required action, not Keycloak's built-in registration form.
- No MFA, no alternate/cloud IdPs, no RBAC/permission-matrix changes beyond what's described here.
- Every new Python file follows this repo's existing patterns: type hints, `from __future__ import annotations` where the codebase already uses it, repository classes taking `tenant_id` as the first argument, no raw PII/secrets logged.
- Every new/changed TypeScript file follows the existing frontend conventions (Mantine for new UI, CSS Modules only where a file is already CSS-Modules-based, German UI copy).

---

### Task 1: `KeycloakAdminClient` extensions — `logout_user`, generalized required-actions email, `create_user` required-actions param

**Files:**
- Modify: `backend/app/keycloak_admin/client.py`
- Modify: `backend/tests/unit/test_keycloak_admin_client.py`

**Interfaces:**
- Produces: `KeycloakAdminClient.logout_user(subject: str) -> None`; `KeycloakAdminClient.create_user(email, first_name, last_name, tenant_id, required_actions: list[str] | None = None) -> str`; `KeycloakAdminClient.send_required_actions_email(subject: str, actions: list[str]) -> None`. `send_invite(subject: str) -> None` becomes a one-line wrapper around the new method — its signature and behavior are unchanged for existing callers.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/unit/test_keycloak_admin_client.py` (follow the existing file's fixture/mocking pattern — it already mocks `httpx.Client` for `create_user`/`set_enabled`/etc.; add these alongside):

```python
def test_logout_user_calls_the_session_revocation_endpoint(client, mock_http):
    client.logout_user("subject-123")
    mock_http.post.assert_called_once_with(
        "https://kc.example/admin/realms/test-realm/users/subject-123/logout",
        headers={"Authorization": "Bearer fake-admin-token"},
    )


def test_logout_user_raises_keycloak_admin_error_on_failure(client, mock_http):
    mock_http.post.side_effect = httpx.HTTPError("boom")
    with pytest.raises(KeycloakAdminError, match="logout_user"):
        client.logout_user("subject-123")


def test_send_required_actions_email_posts_the_given_actions(client, mock_http):
    client.send_required_actions_email("subject-123", ["VERIFY_EMAIL"])
    call = mock_http.put.call_args
    assert call.args[0] == "https://kc.example/admin/realms/test-realm/users/subject-123/execute-actions-email"
    assert call.kwargs["json"] == ["VERIFY_EMAIL"]


def test_send_invite_still_sends_update_password(client, mock_http):
    client.send_invite("subject-123")
    call = mock_http.put.call_args
    assert call.kwargs["json"] == ["UPDATE_PASSWORD"]


def test_create_user_defaults_to_update_password_required_action(client, mock_http):
    client.create_user(email="a@b.com", first_name="A", last_name="B", tenant_id="t1")
    call = mock_http.post.call_args
    assert call.kwargs["json"]["requiredActions"] == ["UPDATE_PASSWORD"]


def test_create_user_accepts_a_different_required_actions_list(client, mock_http):
    client.create_user(
        email="a@b.com", first_name="A", last_name="B", tenant_id="t1",
        required_actions=["VERIFY_EMAIL"],
    )
    call = mock_http.post.call_args
    assert call.kwargs["json"]["requiredActions"] == ["VERIFY_EMAIL"]
```

Adapt the exact fixture names (`client`, `mock_http`) to whatever this test file's existing fixtures are actually called — read the file first and match its conventions exactly rather than inventing new fixture names.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/unit/test_keycloak_admin_client.py -v`
Expected: the 6 new tests FAIL (`AttributeError: 'KeycloakAdminClient' object has no attribute 'logout_user'`, etc.); all pre-existing tests in the file still PASS.

- [ ] **Step 3: Implement**

In `backend/app/keycloak_admin/client.py`, replace the `create_user` method and `send_invite` method, and add `logout_user`:

```python
    def create_user(
        self,
        email: str,
        first_name: str,
        last_name: str,
        tenant_id: str,
        required_actions: list[str] | None = None,
    ) -> str:
        """Creates a Keycloak user carrying the tenant_id attribute the JWT
        validator reads, and returns its subject (Keycloak user id).

        `required_actions` defaults to UPDATE_PASSWORD (the admin-invite
        shape: no password set yet, Keycloak forces one on first login).
        The public self-signup flow passes ["VERIFY_EMAIL"] instead, since
        it sets a real password immediately via set_password()."""
        actions = required_actions if required_actions is not None else ["UPDATE_PASSWORD"]
        try:
            response = self._http_client.post(
                f"{self._base_url}/admin/realms/{self._realm}/users",
                headers=self._headers(),
                json={
                    "username": email,
                    "email": email,
                    "firstName": first_name,
                    "lastName": last_name,
                    "enabled": True,
                    "emailVerified": False,
                    "requiredActions": actions,
                    "attributes": {"tenant_id": [tenant_id]},
                },
            )
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(f"create_user request failed: {exc}") from exc

        if response.status_code == 409:
            raise KeycloakAdminConflictError(f"a Keycloak user for {email!r} already exists")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise KeycloakAdminError(f"create_user failed: {exc}") from exc

        location = response.headers.get("Location", "")
        subject = location.rsplit("/", 1)[-1]
        if not subject:
            raise KeycloakAdminError(f"create_user response had no usable Location header: {location!r}")
        return subject
```

```python
    def send_required_actions_email(self, subject: str, actions: list[str]) -> None:
        try:
            response = self._http_client.put(
                f"{self._base_url}/admin/realms/{self._realm}/users/{subject}/execute-actions-email",
                headers=self._headers(),
                json=actions,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Best-effort: dev/self-hosted Keycloak commonly has no SMTP
            # configured. The caller reports the email as not-sent rather
            # than failing the whole request over this.
            raise KeycloakAdminError(f"send_required_actions_email({subject!r}, {actions}) failed: {exc}") from exc

    def send_invite(self, subject: str) -> None:
        self.send_required_actions_email(subject, ["UPDATE_PASSWORD"])
```

```python
    def logout_user(self, subject: str) -> None:
        """Ends every active Keycloak SSO session for this user (defense in
        depth for account deactivation -- app-DB is_active is what actually
        makes deactivation take effect on the next request; this stops
        *future* refreshes/re-logins, not an already-issued access token
        before its own exp). Best-effort, same shape as set_enabled."""
        try:
            response = self._http_client.post(
                f"{self._base_url}/admin/realms/{self._realm}/users/{subject}/logout",
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KeycloakAdminError(f"logout_user({subject!r}) failed: {exc}") from exc
```

Remove the old `send_invite` body (the one hardcoding `json=["UPDATE_PASSWORD"]` directly) — it's now the one-liner above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/unit/test_keycloak_admin_client.py -v`
Expected: all tests PASS, including the pre-existing ones (nothing else in the file changed behavior).

- [ ] **Step 5: Commit**

```bash
git add backend/app/keycloak_admin/client.py backend/tests/unit/test_keycloak_admin_client.py
git commit -m "feat: add KeycloakAdminClient.logout_user and generalize required-actions email"
```

---

### Task 2: Wire `logout_user` into admin-triggered deactivation

**Files:**
- Modify: `backend/app/api/admin.py`
- Modify: `backend/tests/integration/test_admin_api.py` (or wherever `update_user`'s existing tests live — find the actual file via `grep -rn "def test.*update_user\|PATCH.*api/admin/users" backend/tests/` before writing new tests, and add alongside them)

**Interfaces:**
- Consumes: `KeycloakAdminClient.logout_user` (Task 1).

- [ ] **Step 1: Write the failing test**

Using this repo's existing `FakeKeycloakAdminClient`/dependency-override pattern (the same one `test_provision_e2e_tenant.py` and the admin API's existing tests already use — read one of those tests first to match the exact fixture/override style), add:

```python
def test_deactivating_a_user_calls_logout_user(client_with_fake_keycloak, ...):
    # Create an active non-super_admin user, then PATCH is_active=false.
    response = client_with_fake_keycloak.patch(
        f"/api/admin/users/{user_id}", json={"is_active": False}, headers=auth_headers,
    )
    assert response.status_code == 200
    assert fake_keycloak_client.logout_user_calls == [keycloak_subject]


def test_reactivating_a_user_does_not_call_logout_user(client_with_fake_keycloak, ...):
    response = client_with_fake_keycloak.patch(
        f"/api/admin/users/{user_id}", json={"is_active": True}, headers=auth_headers,
    )
    assert response.status_code == 200
    assert fake_keycloak_client.logout_user_calls == []
```

Adapt to the real fixture names/shapes in the target test file — this is illustrative of the two behaviors to cover (deactivate calls it, reactivate/no-change doesn't), not a literal drop-in. If the fake Keycloak test double used in this file doesn't already track calls, extend it minimally the same way the file's other fake-client methods already do.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest <the target test file> -v -k logout_user`
Expected: FAIL (`logout_user` never called, or `AttributeError` if the fake client needs the tracking attribute added first).

- [ ] **Step 3: Implement**

In `backend/app/api/admin.py`'s `update_user` (confirmed at lines 235-241), extend the existing best-effort block:

```python
    if body.is_active is not None and admin_client is not None:
        try:
            admin_client.set_enabled(target.keycloak_subject, body.is_active)
            if body.is_active is False:
                # Defense in depth, not the primary revocation mechanism --
                # repo.update() above (DB is_active) already makes the next
                # API request fail regardless of Keycloak session state.
                # This just stops the account from silently refreshing or
                # re-logging-in via Keycloak while disabled.
                admin_client.logout_user(target.keycloak_subject)
        except KeycloakAdminError:
            # DB is_active is authoritative (the resolver rejects the next
            # request regardless); Keycloak-side sync is best-effort.
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest <the target test file> -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/admin.py <the target test file>
git commit -m "feat: revoke a user's Keycloak sessions when an admin deactivates them"
```

---

### Task 3: JWKS cache hardening — force refresh on a `kid` miss

**Files:**
- Modify: `backend/app/auth/jwt_validator.py`
- Modify: `backend/tests/unit/test_jwt_validator.py` (find the actual existing test file via `grep -rl "JWTValidator" backend/tests/`)

**Interfaces:**
- No external interface change — `JWTValidator.validate()`'s signature and behavior for already-passing cases are unchanged.

- [ ] **Step 1: Write the failing tests**

Read the existing test file first to match its fixture/mocking style for `_http_client`/JWKS responses, then add:

```python
def test_kid_miss_forces_a_fresh_jwks_fetch_before_failing(validator, mock_http):
    # First fetch returns a JWKS with only "old-kid"; second (forced) fetch
    # returns one with "new-kid" too -- simulating a real key rotation.
    mock_http.get.side_effect = [old_jwks_response, rotated_jwks_response]
    token = make_token(kid="new-kid")  # signed with the key only in the rotated response
    claims = validator.validate(token)
    assert mock_http.get.call_count == 2
    assert claims.tenant_id == EXPECTED_TENANT_ID


def test_kid_still_missing_after_forced_refresh_raises_invalid_token(validator, mock_http):
    mock_http.get.side_effect = [old_jwks_response, old_jwks_response]
    token = make_token(kid="never-existed")
    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_forced_refresh_has_a_cooldown(validator, mock_http):
    mock_http.get.side_effect = [old_jwks_response] * 4
    token = make_token(kid="missing")
    with pytest.raises(InvalidTokenError):
        validator.validate(token)
    with pytest.raises(InvalidTokenError):
        validator.validate(token)  # second call, still within the cooldown window
    # Only ONE forced refresh happened across both calls (2 total fetches:
    # the initial cache-miss fetch, plus one forced refresh) -- the second
    # validate() call's kid-miss did not trigger a second forced fetch.
    assert mock_http.get.call_count == 2
```

Adapt `make_token`/response fixtures to whatever helpers the existing test file already has for constructing a signed test JWT and a JWKS response body.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest backend/tests/unit/test_jwt_validator.py -v -k "kid_miss or forced_refresh"`
Expected: FAIL (no forced-refresh behavior exists yet).

- [ ] **Step 3: Implement**

In `backend/app/auth/jwt_validator.py`, add a cooldown constant and refactor `_get_jwks`/`validate`:

```python
_FORCED_REFRESH_COOLDOWN_SECONDS = 5
```

```python
    def __init__(self, ...) -> None:
        ...
        self._jwks_cache: dict[str, object] | None = None
        self._jwks_cached_at: float = 0.0
        self._last_forced_refresh_at: float = 0.0
```

```python
    def _get_jwks(self, force_refresh: bool = False) -> dict[str, object]:
        now = time.monotonic()
        if (
            not force_refresh
            and self._jwks_cache is not None
            and now - self._jwks_cached_at < _JWKS_CACHE_TTL_SECONDS
        ):
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

In `validate()`, replace the key-lookup block:

```python
        jwks = self._get_jwks()
        key = next((k for k in jwks["keys"] if k.get("kid") == header.get("kid")), None)
        if key is None:
            now = time.monotonic()
            if now - self._last_forced_refresh_at >= _FORCED_REFRESH_COOLDOWN_SECONDS:
                self._last_forced_refresh_at = now
                jwks = self._get_jwks(force_refresh=True)
                key = next((k for k in jwks["keys"] if k.get("kid") == header.get("kid")), None)
        if key is None:
            raise InvalidTokenError(f"no signing key found for kid={header.get('kid')!r}")
```

(This replaces the two-line lookup that previously ran right after `jwks = self._get_jwks()` — read the current `validate()` method first and place this exactly where that lookup was, keeping everything else in the method unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest backend/tests/unit/test_jwt_validator.py -v`
Expected: all PASS, including every pre-existing test in the file.

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth/jwt_validator.py backend/tests/unit/test_jwt_validator.py
git commit -m "fix: force a fresh JWKS fetch on a kid miss so key rotation doesn't reject valid tokens"
```

---

### Task 4: Migration 0010 — `grant_default_entitlement_on_signup` SECURITY DEFINER function

**Files:**
- Create: `backend/alembic/versions/0010_signup_entitlement_grant_function.py`
- Test: `backend/tests/integration/test_signup_entitlement_grant_function.py`

**Interfaces:**
- Produces: a Postgres function `grant_default_entitlement_on_signup(p_tenant_id uuid) RETURNS void`, callable via `SELECT grant_default_entitlement_on_signup(:tenant_id)` from an `app_runtime`-scoped session (Task 6 consumes this).

- [ ] **Step 1: Write the failing integration test**

```python
import uuid

import sqlalchemy as sa

from app.config import get_settings
from app.db.session import SessionLocal
from app.db.repositories.tenant_repository import TenantRepository
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider


def test_grant_default_entitlement_on_signup_inserts_exactly_one_row():
    key_provider = FileSecretKeyProvider(get_settings().master_key_path)
    tenant_id = uuid.uuid4()
    with SessionLocal() as session:
        TenantRepository(session, key_provider).create(
            name="Test Signup Praxis", keycloak_realm=f"test-{tenant_id.hex[:8]}",
            retention_days=30, tenant_id=tenant_id,
        )
        session.commit()

    with SessionLocal() as session:
        session.execute(
            sa.text("SELECT grant_default_entitlement_on_signup(:tid)"), {"tid": str(tenant_id)}
        )
        session.commit()

    # Verify via a direct migration-owner connection (this check, not the
    # function call itself, is allowed to use the elevated credential --
    # it's test-only verification, not request-serving code).
    engine = sa.create_engine(get_settings().database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT a.key FROM tenant_app_entitlements e JOIN apps a ON a.id = e.app_id "
                "WHERE e.tenant_id = :tid AND e.revoked_at IS NULL"
            ),
            {"tid": str(tenant_id)},
        ).fetchall()
    engine.dispose()
    assert [r[0] for r in rows] == ["anonymization"]


def test_grant_default_entitlement_on_signup_is_idempotent_on_reentitlement():
    # Calling it twice for the same tenant must not raise or duplicate the
    # row -- mirrors the ON CONFLICT ... DO UPDATE pattern used elsewhere
    # for this exact table (see provision_e2e_tenant.py's own grant SQL).
    ...  # same setup as above, call the function twice, assert exactly one row


def test_app_runtime_role_can_call_the_function_but_still_cannot_insert_directly():
    # Regression guard for the actual security property this migration
    # exists for: app_runtime has EXECUTE on the function (proven by the
    # two tests above using the normal app-scoped SessionLocal/app_runtime
    # connection) but a direct INSERT into tenant_app_entitlements through
    # that same connection must still fail.
    with SessionLocal() as session:
        with pytest.raises(sa.exc.DBAPIError):
            session.execute(
                sa.text(
                    "INSERT INTO tenant_app_entitlements (id, tenant_id, app_id, granted_by) "
                    "VALUES (gen_random_uuid(), :tid, (SELECT id FROM apps LIMIT 1), 'test')"
                ),
                {"tid": str(uuid.uuid4())},
            )
            session.commit()
```

Add `import pytest` at the top; match this test file's actual DB-fixture/cleanup conventions by reading a neighboring integration test first (e.g. `test_provision_e2e_tenant.py`) rather than assuming these three tests are self-cleaning — add a fixture or explicit teardown deleting the test tenant's rows the same way that file does.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && alembic upgrade head && pytest tests/integration/test_signup_entitlement_grant_function.py -v`
Expected: FAIL (`function grant_default_entitlement_on_signup(uuid) does not exist`).

- [ ] **Step 3: Write the migration**

```python
"""add grant_default_entitlement_on_signup(uuid) SECURITY DEFINER function

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-26

app_runtime has SELECT-only on tenant_app_entitlements (migration 0007:
"FastAPI never writes this table") -- by design, so a compromised or buggy
request handler can't silently grant itself arbitrary app access. The new
public POST /api/signup endpoint (a separate task) needs to grant exactly
one, fixed entitlement ("anonymization") to a brand-new tenant it just
created, from inside a normal app_runtime-scoped request. Rather than
punching a hole in that GRANT (or handing request-serving code the
migration-owner credential, the way the trusted CLI script
provision_e2e_tenant.py does for its own out-of-band use), this migration
adds one narrowly-scoped SECURITY DEFINER function: it takes only a
tenant_id (no caller-supplied app key -- cannot be used to grant an
arbitrary app), is owned by the migration-owner role, and is granted
EXECUTE (not direct table access) to app_runtime. The existing
SELECT-only grant on tenant_app_entitlements itself is untouched.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION grant_default_entitlement_on_signup(p_tenant_id uuid)
        RETURNS void
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public
        AS $$
            INSERT INTO tenant_app_entitlements (id, tenant_id, app_id, granted_by)
            SELECT gen_random_uuid(), p_tenant_id, id, 'self-signup'
            FROM apps WHERE key = 'anonymization'
            ON CONFLICT (tenant_id, app_id) DO UPDATE SET revoked_at = NULL
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION grant_default_entitlement_on_signup(uuid) TO app_runtime")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION grant_default_entitlement_on_signup(uuid) FROM app_runtime")
    op.execute("DROP FUNCTION grant_default_entitlement_on_signup(uuid)")
```

`gen_random_uuid()` requires the `pgcrypto` extension — check `0001_initial_schema.py`/subsequent migrations for whether it's already enabled (the codebase's own convention, per 0007's docstring, is app-side `uuid.uuid4()` generation, "no `gen_random_uuid()`/pgcrypto dependency is introduced" — but `provision_e2e_tenant.py`'s own grant SQL, which this mirrors, *does* use `gen_random_uuid()` for this specific table's `id` column already in production use, so the extension is confirmed already available; verify by grepping for `pgcrypto`/`gen_random_uuid` across `backend/alembic/versions/` before assuming).

- [ ] **Step 4: Run migration and test to verify it passes**

Run: `cd backend && alembic upgrade head && pytest tests/integration/test_signup_entitlement_grant_function.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Run the full backend suite to confirm no regressions**

Run: `cd backend && pytest tests/unit tests/integration tests/privacy_invariants -v && ruff check . && lint-imports`
Expected: all pass, same pass count as before plus the 3 new tests.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0010_signup_entitlement_grant_function.py backend/tests/integration/test_signup_entitlement_grant_function.py
git commit -m "feat: add a SECURITY DEFINER function so app_runtime can grant the signup default entitlement"
```

---

### Task 5: Per-IP rate limiter for public endpoints

**Files:**
- Create: `backend/app/api/rate_limit.py`
- Test: `backend/tests/unit/test_rate_limit.py`
- Modify: `backend/app/config.py`

**Interfaces:**
- Produces: `check_rate_limit(key: str, limit_per_hour: int) -> None` (raises `RateLimitExceededError` — Task 6 catches it and returns 429), and a `Settings.signup_rate_limit_per_hour: int = 5` field.

- [ ] **Step 1: Write the failing tests**

```python
import time

import pytest

from app.api.rate_limit import RateLimitExceededError, check_rate_limit, _reset_for_tests


@pytest.fixture(autouse=True)
def reset_limiter():
    _reset_for_tests()
    yield
    _reset_for_tests()


def test_allows_requests_under_the_limit():
    for _ in range(5):
        check_rate_limit("1.2.3.4", limit_per_hour=5)  # must not raise


def test_blocks_the_request_over_the_limit():
    for _ in range(5):
        check_rate_limit("1.2.3.4", limit_per_hour=5)
    with pytest.raises(RateLimitExceededError):
        check_rate_limit("1.2.3.4", limit_per_hour=5)


def test_limits_are_independent_per_key():
    for _ in range(5):
        check_rate_limit("1.2.3.4", limit_per_hour=5)
    check_rate_limit("5.6.7.8", limit_per_hour=5)  # different IP, must not raise


def test_old_entries_outside_the_window_do_not_count(monkeypatch):
    calls = [3600.0]  # fake clock, starts 1 hour in

    def fake_time():
        return calls[0]

    monkeypatch.setattr("app.api.rate_limit.time.monotonic", fake_time)
    for _ in range(5):
        check_rate_limit("1.2.3.4", limit_per_hour=5)
    calls[0] += 3601  # advance past the 1-hour window
    check_rate_limit("1.2.3.4", limit_per_hour=5)  # must not raise -- old entries expired
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/unit/test_rate_limit.py -v`
Expected: FAIL (`ModuleNotFoundError: app.api.rate_limit`).

- [ ] **Step 3: Implement**

```python
from __future__ import annotations

import threading
import time
from collections import defaultdict

_WINDOW_SECONDS = 3600
_lock = threading.Lock()
_hits: dict[str, list[float]] = defaultdict(list)


class RateLimitExceededError(Exception):
    """The caller has exceeded the allowed number of requests for this key
    within the current window."""


def check_rate_limit(key: str, limit_per_hour: int) -> None:
    """In-memory, per-process sliding-window limiter. Explicit known
    limitation (see ADR-0025): this is per-uvicorn-worker, not shared
    across workers or process restarts -- under UVICORN_WORKERS=4 the
    effective ceiling is up to 4x limit_per_hour. Accepted MVP scope for
    the one public endpoint (signup) this guards; a Postgres/Redis-backed
    limiter is a named follow-up if abuse is observed in practice."""
    now = time.monotonic()
    with _lock:
        recent = [t for t in _hits[key] if now - t < _WINDOW_SECONDS]
        if len(recent) >= limit_per_hour:
            _hits[key] = recent
            raise RateLimitExceededError(f"rate limit exceeded for {key!r}")
        recent.append(now)
        _hits[key] = recent


def _reset_for_tests() -> None:
    with _lock:
        _hits.clear()
```

In `backend/app/config.py`, add near the other tunables (after `patient_number_pattern` or alongside the pool-sizing block — match existing field grouping/comment style):

```python
    # Public, unauthenticated endpoint (signup) abuse guard -- see
    # app/api/rate_limit.py's docstring for the accepted MVP limitation.
    signup_rate_limit_per_hour: int = 5
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/unit/test_rate_limit.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/rate_limit.py backend/tests/unit/test_rate_limit.py backend/app/config.py
git commit -m "feat: add an in-process per-IP rate limiter for public endpoints"
```

---

### Task 6: `POST /api/signup` — public tenant self-signup endpoint

**Files:**
- Create: `backend/app/api/signup.py`
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_signup_api.py`

**Interfaces:**
- Consumes: `KeycloakAdminClient.create_user(..., required_actions=...)`, `.set_password`, `.send_required_actions_email`, `.delete_user` (Task 1); `grant_default_entitlement_on_signup` (Task 4); `check_rate_limit`/`RateLimitExceededError` (Task 5); `TenantRepository.create`, `UserRepository.create`; `get_keycloak_admin_client` (existing).
- Produces: `POST /api/signup` — request `TenantSignupIn`, response `TenantSignupOut` (below), for Task 9's frontend form to call.

- [ ] **Step 1: Add the schemas**

In `backend/app/api/schemas.py`, add alongside `AdminUserCreateIn`/`Out`:

```python
class TenantSignupIn(BaseModel):
    practice_name: str
    first_name: str
    last_name: str
    email: str
    password: str


class TenantSignupOut(BaseModel):
    tenant_id: uuid.UUID
    verification_email_sent: bool
```

- [ ] **Step 2: Write the failing integration tests**

```python
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth.dependencies import get_keycloak_admin_client
from app.db.session import SessionLocal


class FakeKeycloakAdminClientForSignup:
    def __init__(self):
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.sent_actions: list[tuple[str, list[str]]] = []
        self._next_subject = "fake-subject-1"

    def create_user(self, *, email, first_name, last_name, tenant_id, required_actions=None):
        self.created.append({
            "email": email, "tenant_id": tenant_id, "required_actions": required_actions,
        })
        return self._next_subject

    def set_password(self, subject, password, temporary=False):
        pass

    def send_required_actions_email(self, subject, actions):
        self.sent_actions.append((subject, actions))

    def delete_user(self, subject):
        self.deleted.append(subject)


@pytest.fixture
def fake_admin_client():
    return FakeKeycloakAdminClientForSignup()


@pytest.fixture
def client(fake_admin_client):
    app.dependency_overrides[get_keycloak_admin_client] = lambda: fake_admin_client
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_signup_creates_a_tenant_and_super_admin_user(client, fake_admin_client):
    response = client.post("/api/signup", json={
        "practice_name": "Test Praxis GmbH",
        "first_name": "Anna", "last_name": "Schmitt",
        "email": f"signup-{uuid.uuid4().hex[:8]}@example.test",
        "password": "correct-horse-battery-staple",
    })
    assert response.status_code == 201
    body = response.json()
    assert "tenant_id" in body
    assert fake_admin_client.created[0]["required_actions"] == ["VERIFY_EMAIL"]
    assert fake_admin_client.sent_actions[0][1] == ["VERIFY_EMAIL"]

    # The tenant and its super_admin user actually landed in the DB.
    with SessionLocal() as session:
        from sqlalchemy import text
        row = session.execute(
            text("SELECT role FROM users WHERE tenant_id = :tid"), {"tid": body["tenant_id"]},
        ).fetchone()
        assert row[0] == "super_admin"
    # cleanup: reuse provision_e2e_tenant.py's cleanup() for the created tenant


def test_signup_grants_the_default_entitlement(client, fake_admin_client):
    response = client.post("/api/signup", json={...})  # same body shape as above
    tenant_id = response.json()["tenant_id"]
    # assert an anonymization entitlement row exists for tenant_id (same
    # query shape as Task 4's test)


def test_signup_returns_501_when_keycloak_admin_client_unconfigured():
    app.dependency_overrides[get_keycloak_admin_client] = lambda: None
    response = TestClient(app).post("/api/signup", json={...})
    assert response.status_code == 501
    app.dependency_overrides.clear()


def test_signup_reports_verification_email_not_sent_on_keycloak_failure(client, fake_admin_client, monkeypatch):
    def failing_send(subject, actions):
        raise KeycloakAdminError("smtp down")
    fake_admin_client.send_required_actions_email = failing_send
    response = client.post("/api/signup", json={...})
    assert response.status_code == 201
    assert response.json()["verification_email_sent"] is False


def test_signup_compensates_keycloak_user_on_db_failure(client, fake_admin_client, monkeypatch):
    # Force the DB write to fail (e.g. monkeypatch UserRepository.create to
    # raise) and assert fake_admin_client.deleted contains the created subject.
    ...


def test_signup_is_rate_limited_per_ip(client, fake_admin_client):
    for i in range(5):
        r = client.post("/api/signup", json={..., "email": f"signup-{i}@example.test"})
        assert r.status_code == 201
    r = client.post("/api/signup", json={..., "email": "signup-overflow@example.test"})
    assert r.status_code == 429
```

Fill in the `...` bodies with the same practice_name/first_name/last_name/email/password shape as the first test, varying `email` per call where a fresh signup is needed to avoid unique-constraint collisions. Add a proper teardown (module-level fixture calling `provision_e2e_tenant.py`'s `cleanup()` logic, or a direct DB cleanup matching its FK-safe delete order) for every tenant these tests create — do not leave orphaned tenants in the test database, matching the standard already established for this repo's other tenant-creating tests.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && pytest tests/integration/test_signup_api.py -v`
Expected: FAIL (`404 Not Found` — no route yet).

- [ ] **Step 4: Implement the endpoint**

Create `backend/app/api/signup.py`:

```python
from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.schemas import TenantSignupIn, TenantSignupOut
from app.auth.dependencies import get_keycloak_admin_client
from app.api.rate_limit import RateLimitExceededError, check_rate_limit
from app.config import get_settings
from app.db.repositories.tenant_repository import TenantRepository
from app.db.repositories.user_repository import UserRepository
from app.db.session import SessionLocal, tenant_scoped_session
from app.keycloak_admin.client import KeycloakAdminClient, KeycloakAdminConflictError, KeycloakAdminError
from app.privacy_gateway.token_vault.key_provider import FileSecretKeyProvider

router = APIRouter(prefix="/api", tags=["signup"])

_DEFAULT_RETENTION_DAYS = 30


@router.post("/signup", response_model=TenantSignupOut, status_code=201)
def signup(
    body: TenantSignupIn,
    request: Request,
    admin_client: KeycloakAdminClient | None = Depends(get_keycloak_admin_client),
) -> TenantSignupOut:
    try:
        check_rate_limit(request.client.host if request.client else "unknown",
                          get_settings().signup_rate_limit_per_hour)
    except RateLimitExceededError as exc:
        raise HTTPException(status_code=429, detail="too many signup attempts, try again later") from exc

    if admin_client is None:
        raise HTTPException(
            status_code=501,
            detail="self-signup is not available -- Keycloak provisioning is not configured",
        )

    tenant_id = uuid.uuid4()
    key_provider = FileSecretKeyProvider(get_settings().master_key_path)
    with SessionLocal() as session:
        TenantRepository(session, key_provider).create(
            name=body.practice_name,
            keycloak_realm=f"self-signup-{tenant_id.hex[:12]}",
            retention_days=_DEFAULT_RETENTION_DAYS,
            tenant_id=tenant_id,
        )
        session.commit()

    with SessionLocal() as session:
        session.execute(
            sa.text("SELECT grant_default_entitlement_on_signup(:tid)"), {"tid": str(tenant_id)}
        )
        session.commit()

    try:
        subject = admin_client.create_user(
            email=body.email, first_name=body.first_name, last_name=body.last_name,
            tenant_id=str(tenant_id), required_actions=["VERIFY_EMAIL"],
        )
        admin_client.set_password(subject, body.password, temporary=False)
    except KeycloakAdminConflictError as exc:
        raise HTTPException(status_code=409, detail="a Keycloak user with this email already exists") from exc
    except KeycloakAdminError as exc:
        raise HTTPException(status_code=502, detail="identity provider unavailable") from exc

    verification_email_sent = True
    try:
        admin_client.send_required_actions_email(subject, ["VERIFY_EMAIL"])
    except KeycloakAdminError:
        verification_email_sent = False

    try:
        with tenant_scoped_session(tenant_id) as session:
            UserRepository(session).create(
                tenant_id, keycloak_subject=subject, email=body.email,
                role="super_admin", branch_id=None,
            )
    except Exception as exc:
        admin_client.delete_user(subject)
        raise HTTPException(status_code=500, detail="signup failed") from exc

    return TenantSignupOut(tenant_id=tenant_id, verification_email_sent=verification_email_sent)
```

In `backend/app/main.py`, add the import and registration alongside the existing routers:

```python
from app.api.signup import router as signup_router
```
```python
app.include_router(signup_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/integration/test_signup_api.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && pytest tests/unit tests/integration tests/privacy_invariants -v && ruff check . && lint-imports`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/signup.py backend/app/api/schemas.py backend/app/main.py backend/tests/integration/test_signup_api.py
git commit -m "feat: add POST /api/signup for public tenant self-onboarding"
```

---

### Task 7: `frontend/lib/auth.ts` — `offline_access`, error type, back-channel logout

**Files:**
- Modify: `frontend/lib/auth.ts`
- Modify: `frontend/types/next-auth.d.ts`
- Modify: `frontend/lib/auth.test.ts` (existing file — extend it)

**Interfaces:**
- Produces: `authOptions.events.signOut` hook; `AuthTokenError` type.

- [ ] **Step 1: Update the type augmentation**

In `frontend/types/next-auth.d.ts`, replace both `error?: string` fields:

```ts
import "next-auth";
import "next-auth/jwt";

export type AuthTokenError = "RefreshAccessTokenError";

declare module "next-auth" {
  interface Session {
    accessToken: string;
    error?: AuthTokenError;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
    refreshToken?: string;
    expiresAt?: number;
    error?: AuthTokenError;
  }
}
```

- [ ] **Step 2: Write the failing test**

Read `frontend/lib/auth.test.ts` first to match its existing mocking style for `fetch`/`refreshAccessToken`, then add a test asserting `events.signOut` POSTs to the Keycloak logout endpoint with the token's `refreshToken`, and a test asserting it does NOT throw when the fetch itself rejects:

```ts
describe("events.signOut", () => {
  it("posts a back-channel logout request to Keycloak with the refresh token", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);
    await authOptions.events!.signOut!({ token: { refreshToken: "rt-123" } } as any);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/protocol/openid-connect/logout"),
      expect.objectContaining({ method: "POST" })
    );
    const body = fetchMock.mock.calls[0][1].body as URLSearchParams;
    expect(body.get("refresh_token")).toBe("rt-123");
  });

  it("does not throw when the back-channel call fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    await expect(
      authOptions.events!.signOut!({ token: { refreshToken: "rt-123" } } as any)
    ).resolves.not.toThrow();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/auth.test.ts`
Expected: FAIL (`authOptions.events` is `undefined`).

- [ ] **Step 4: Implement**

In `frontend/lib/auth.ts`:

```ts
const KEYCLOAK_TOKEN_URL = `${KEYCLOAK_INTERNAL_URL}/protocol/openid-connect/token`;
const KEYCLOAK_LOGOUT_URL = `${KEYCLOAK_INTERNAL_URL}/protocol/openid-connect/logout`;
```

Change the requested scope:

```ts
      authorization: {
        url: `${KEYCLOAK_ISSUER}/protocol/openid-connect/auth`,
        params: { scope: "openid email profile offline_access" },
      },
```

Add the `events` block to `authOptions` (alongside `providers`/`callbacks`):

```ts
  events: {
    async signOut({ token }) {
      // RP-initiated back-channel logout: ends the Keycloak SSO session so
      // a subsequent sign-in can't silently re-authenticate off a still-live
      // Keycloak cookie. Best-effort -- ADR-0020's fail-closed policy governs
      // auth *issuance*, not this teardown call; a failure here must never
      // block the user's own local sign-out from completing.
      try {
        await fetch(KEYCLOAK_LOGOUT_URL, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            client_id: KEYCLOAK_CLIENT_ID,
            refresh_token: token.refreshToken ?? "",
          }),
        });
      } catch {
        // Swallowed deliberately -- see comment above.
      }
    },
  },
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run lib/auth.test.ts`
Expected: all PASS, including every pre-existing test in the file.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/auth.ts frontend/types/next-auth.d.ts frontend/lib/auth.test.ts
git commit -m "feat: request offline_access scope and end the Keycloak SSO session on sign-out"
```

---

### Task 8: `/login` page, `pages.signIn`, middleware matcher, logout callback URL

**Files:**
- Create: `frontend/app/login/page.tsx`
- Create: `frontend/app/login/page.test.tsx`
- Modify: `frontend/lib/auth.ts`
- Modify: `frontend/middleware.ts`
- Modify: `frontend/middleware.test.ts` (existing file)
- Modify: `frontend/components/layout/AppShellChrome.tsx`

**Interfaces:**
- Produces: `/login` route (public), read by Task 10's `SessionErrorHandler` as its redirect target.

- [ ] **Step 1: Write the failing middleware test**

Read `frontend/middleware.test.ts` first to match its existing style, then add:

```ts
it("excludes /login and /signup from the auth gate", () => {
  expect(config.matcher[0]).not.toMatch(/^\/login/); // sanity: matcher is a negative-lookahead regex string
  // Concretely: a request to /login with no token must NOT be redirected.
});
```

(Match this to however the existing file actually tests the matcher/middleware behavior — some existing tests may invoke `middleware()` directly with a mocked `NextRequest` for `/login`; follow that pattern rather than inspecting the matcher string if that's not how the file already tests it.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run middleware.test.ts`
Expected: FAIL (`/login` still gets redirected — the matcher doesn't exclude it yet).

- [ ] **Step 3: Implement the matcher change**

In `frontend/middleware.ts`, change only the `matcher`:

```ts
export const config = {
  matcher: ["/((?!api/auth|_next/static|_next/image|favicon.ico|login|signup).*)"],
};
```

- [ ] **Step 4: `pages.signIn` in `authOptions`**

In `frontend/lib/auth.ts`, add to the `authOptions` object (alongside `providers`/`callbacks`/`events`):

```ts
  pages: {
    signIn: "/login",
  },
```

- [ ] **Step 5: Write the `/login` page**

`frontend/app/login/page.tsx`:

```tsx
"use client";

import { Button, Container, Paper, Stack, Text, Title } from "@mantine/core";
import { signIn } from "next-auth/react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

export default function LoginPage() {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/dashboard";

  return (
    <Container size="xs" style={{ paddingTop: "15vh" }}>
      <Paper withBorder p="xl" radius="md">
        <Stack gap="lg">
          <Title order={2} ta="center">
            Aigenta
          </Title>
          <Button size="md" onClick={() => signIn("keycloak", { callbackUrl })}>
            Anmelden
          </Button>
          <Text ta="center" size="sm" c="dimmed">
            Neu hier?{" "}
            <Text component={Link} href="/signup" c="blue" inherit>
              Praxis registrieren
            </Text>
          </Text>
        </Stack>
      </Paper>
    </Container>
  );
}
```

- [ ] **Step 6: Write the page test**

`frontend/app/login/page.test.tsx` (match the render/mocking conventions of a neighboring existing page test, e.g. `admin/layout.test.tsx`):

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { signIn } from "next-auth/react";
import LoginPage from "./page";

vi.mock("next-auth/react", () => ({ signIn: vi.fn() }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("callbackUrl=%2Fdashboard"),
}));

it("calls signIn with keycloak and the callback URL on Anmelden", async () => {
  render(<LoginPage />);
  await userEvent.click(screen.getByRole("button", { name: "Anmelden" }));
  expect(signIn).toHaveBeenCalledWith("keycloak", { callbackUrl: "/dashboard" });
});

it("links to /signup", () => {
  render(<LoginPage />);
  expect(screen.getByRole("link", { name: "Praxis registrieren" })).toHaveAttribute("href", "/signup");
});
```

- [ ] **Step 7: Logout callback URL**

In `frontend/components/layout/AppShellChrome.tsx:143`, change:

```tsx
                  <Menu.Item leftSection={<IconLogout size={14} />} color="red" onClick={() => signOut({ callbackUrl: "/login" })}>
```

- [ ] **Step 8: Run all frontend tests**

Run: `cd frontend && npx vitest run`
Expected: all PASS, including the new ones.

- [ ] **Step 9: Commit**

```bash
git add frontend/app/login frontend/lib/auth.ts frontend/middleware.ts frontend/middleware.test.ts frontend/components/layout/AppShellChrome.tsx
git commit -m "feat: add a /login landing page offering sign-in or practice sign-up"
```

---

### Task 9: `/signup` page

**Files:**
- Create: `frontend/app/signup/page.tsx`
- Create: `frontend/app/signup/page.test.tsx`
- Create: `frontend/lib/api/signup.ts`

**Interfaces:**
- Consumes: `POST /api/signup` (Task 6).

- [ ] **Step 1: Write the failing test**

`frontend/app/signup/page.test.tsx` (match `admin/users/page.test.tsx`'s fetch-mocking convention if one exists, else mock `lib/api/signup`'s `createTenantSignup` directly):

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SignupPage from "./page";
import { createTenantSignup } from "@/lib/api/signup";

vi.mock("@/lib/api/signup", () => ({ createTenantSignup: vi.fn() }));

it("submits the form and shows a check-your-email confirmation on success", async () => {
  (createTenantSignup as any).mockResolvedValue({ tenant_id: "t1", verification_email_sent: true });
  render(<SignupPage />);
  await userEvent.type(screen.getByLabelText("Praxisname"), "Test Praxis");
  await userEvent.type(screen.getByLabelText("Vorname"), "Anna");
  await userEvent.type(screen.getByLabelText("Nachname"), "Schmitt");
  await userEvent.type(screen.getByLabelText("E-Mail"), "anna@example.test");
  await userEvent.type(screen.getByLabelText("Passwort"), "correct-horse-battery-staple");
  await userEvent.click(screen.getByRole("button", { name: "Registrieren" }));
  await waitFor(() => expect(screen.getByText(/E-Mail/i)).toBeInTheDocument());
  expect(createTenantSignup).toHaveBeenCalledWith({
    practice_name: "Test Praxis", first_name: "Anna", last_name: "Schmitt",
    email: "anna@example.test", password: "correct-horse-battery-staple",
  });
});

it("shows an error message when signup fails", async () => {
  (createTenantSignup as any).mockRejectedValue(new Error("a Keycloak user with this email already exists"));
  render(<SignupPage />);
  // ... fill form, submit ...
  await waitFor(() => expect(screen.getByText(/fehlgeschlagen|existiert/i)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run app/signup/page.test.tsx`
Expected: FAIL (module doesn't exist).

- [ ] **Step 3: Implement `lib/api/signup.ts`**

Follow the exact pattern of an existing `lib/api/*.ts` file (e.g. `lib/api/admin.ts`'s `AdminApiError`/`parseErrorDetail` for consistent error surfacing):

```ts
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface TenantSignupIn {
  practice_name: string;
  first_name: string;
  last_name: string;
  email: string;
  password: string;
}

export interface TenantSignupOut {
  tenant_id: string;
  verification_email_sent: boolean;
}

export async function createTenantSignup(body: TenantSignupIn): Promise<TenantSignupOut> {
  const response = await fetch(`${API_BASE_URL}/api/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail ?? "Registrierung fehlgeschlagen.");
  }
  return response.json();
}
```

- [ ] **Step 4: Implement the page**

`frontend/app/signup/page.tsx` — a Mantine form with the five fields from the test, `useState` for each field + a `status: "idle" | "submitting" | "success" | "error"` + `errorMessage` state, calling `createTenantSignup` on submit. On success, replace the form with a confirmation message referencing checking email (must contain the substring the test's `/E-Mail/i` matcher looks for) and a link back to `/login`. On failure, show `errorMessage` (must satisfy the test's `/fehlgeschlagen|existiert/i` matcher — i.e. render the caught error's `.message` directly). Match `admin/users/page.tsx`'s loading/disabled-button convention (`disabled={submitting}`, button text swap) for the submit button.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run app/signup/page.test.tsx`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/app/signup frontend/lib/api/signup.ts
git commit -m "feat: add the public practice self-signup page"
```

---

### Task 10: Centralize `session.error` handling

**Files:**
- Create: `frontend/components/SessionErrorHandler.tsx`
- Create: `frontend/components/SessionErrorHandler.test.tsx`
- Modify: `frontend/components/SessionProviderWrapper.tsx`
- Modify: `frontend/components/ConversationSidebar.tsx`
- Modify: `frontend/components/ConversationSidebar.test.tsx` (existing — remove the now-dead assertion if one exists for the old behavior)
- Modify: `frontend/middleware.ts`
- Modify: `frontend/middleware.test.ts`

**Interfaces:**
- Consumes: `/login` (Task 8), `events.signOut` (Task 7).

- [ ] **Step 1: Write the failing component test**

`frontend/components/SessionErrorHandler.test.tsx` (match `ConversationSidebar.test.tsx`'s `useSession` mocking convention):

```tsx
import { render } from "@testing-library/react";
import { signOut, useSession } from "next-auth/react";
import { SessionErrorHandler } from "./SessionErrorHandler";

vi.mock("next-auth/react", () => ({ useSession: vi.fn(), signOut: vi.fn() }));

it("signs out with a /login callback when the session has a refresh error", () => {
  (useSession as any).mockReturnValue({ data: { error: "RefreshAccessTokenError" } });
  render(<SessionErrorHandler />);
  expect(signOut).toHaveBeenCalledWith({ callbackUrl: "/login" });
});

it("does nothing when there is no session error", () => {
  (useSession as any).mockReturnValue({ data: { error: undefined } });
  render(<SessionErrorHandler />);
  expect(signOut).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/SessionErrorHandler.test.tsx`
Expected: FAIL (module doesn't exist).

- [ ] **Step 3: Implement**

`frontend/components/SessionErrorHandler.tsx`:

```tsx
"use client";

import { useEffect } from "react";
import { signOut, useSession } from "next-auth/react";

/** Centralized reaction to a broken session, mounted once at the root
 * (see SessionProviderWrapper) so every route is covered -- previously
 * only ConversationSidebar.tsx reacted to this, leaving every other
 * page free to keep firing API calls with a stale/expired accessToken. */
export function SessionErrorHandler() {
  const { data: session } = useSession();

  useEffect(() => {
    if (session?.error === "RefreshAccessTokenError") {
      signOut({ callbackUrl: "/login" });
    }
  }, [session?.error]);

  return null;
}
```

In `frontend/components/SessionProviderWrapper.tsx`, mount it as a sibling of `{children}`:

```tsx
"use client";

import { SessionProvider } from "next-auth/react";
import type { ReactNode } from "react";
import { SessionErrorHandler } from "@/components/SessionErrorHandler";

export function SessionProviderWrapper({ children }: { children: ReactNode }) {
  return (
    <SessionProvider refetchInterval={60}>
      <SessionErrorHandler />
      {children}
    </SessionProvider>
  );
}
```

Remove the duplicated `useEffect` from `frontend/components/ConversationSidebar.tsx` (lines 32-39 and the now-unused `signIn` import if nothing else in the file uses it — check before removing the import).

In `frontend/middleware.ts`, change the guard:

```ts
  if (!token || token.error === "RefreshAccessTokenError") {
```

- [ ] **Step 4: Update the middleware test**

Add to `frontend/middleware.test.ts`:

```ts
it("redirects when the token has a refresh error, even though a token object exists", async () => {
  // mock getToken to resolve { error: "RefreshAccessTokenError", ...} and
  // assert the same redirect-to-signin behavior as the no-token case
});
```

- [ ] **Step 5: Update/remove any now-stale `ConversationSidebar.test.tsx` assertion**

If that file has a test asserting `signIn("keycloak")` gets called on `session.error`, remove it (the behavior moved) — do not leave a test asserting dead behavior.

- [ ] **Step 6: Run all frontend tests**

Run: `cd frontend && npx vitest run`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/SessionErrorHandler.tsx frontend/components/SessionErrorHandler.test.tsx frontend/components/SessionProviderWrapper.tsx frontend/components/ConversationSidebar.tsx frontend/components/ConversationSidebar.test.tsx frontend/middleware.ts frontend/middleware.test.ts
git commit -m "refactor: centralize session-error handling instead of one component's local effect"
```

---

### Task 11: Cookie security — fix `NEXTAUTH_URL`

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.override.yml.example`

- [ ] **Step 1: Fix the base compose file**

In `docker-compose.yml`, the `frontend` service's `environment` block, change:

```yaml
      NEXTAUTH_URL: https://${PUBLIC_HOST:-localhost}
```

(was `http://localhost:${FRONTEND_PORT:-3000}`).

- [ ] **Step 2: Add the local-dev override**

In `docker-compose.override.yml.example`, under the `frontend` service, add an `environment` block (the file currently has none for `frontend`):

```yaml
  frontend:
    volumes:
      - ./frontend:/app
      - /app/node_modules
      - /app/.next
    command: npm run dev
    environment:
      # Local dev bypasses Traefik entirely and serves plain HTTP directly
      # -- without this override, this service would silently inherit the
      # base file's https:// NEXTAUTH_URL and break session-cookie delivery
      # (NextAuth would only set the Secure-flagged/__Secure- cookie, which
      # a plain-http response can't deliver).
      NEXTAUTH_URL: http://localhost:${FRONTEND_PORT:-3000}
```

- [ ] **Step 3: Verify**

Run: `docker compose config --quiet` (base only) and
`docker compose -f docker-compose.yml -f docker-compose.override.yml.example config --quiet`
(the latter needs `cp docker-compose.override.yml.example docker-compose.override.yml` first, in a scratch copy — don't commit that copy). Confirm the rendered `NEXTAUTH_URL` is `https://localhost` in the base-only render and `http://localhost:3000` in the override-merged render.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml docker-compose.override.yml.example
git commit -m "fix: set NEXTAUTH_URL to https in production so NextAuth applies Secure cookie protections"
```

---

### Task 12: Keycloak client split — dedicated e2e/dev ROPC client

**Files:**
- Modify: `keycloak/realm-export.json`
- Modify: `e2e/support/session.ts`

**Interfaces:**
- Produces: Keycloak client `chatgpt-proxy-e2e-ropc`.

- [ ] **Step 1: Disable ROPC on the production client**

In `keycloak/realm-export.json`, on the `chatgpt-proxy-frontend` client, change `"directAccessGrantsEnabled": true` to `false`.

- [ ] **Step 2: Add the new client**

Add a new entry to the `clients` array, after `chatgpt-proxy-frontend`:

```json
    {
      "clientId": "chatgpt-proxy-e2e-ropc",
      "enabled": true,
      "publicClient": true,
      "protocol": "openid-connect",
      "standardFlowEnabled": false,
      "directAccessGrantsEnabled": true,
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
    },
```

The `audience` mapper's `included.client.audience` must stay `"chatgpt-proxy-frontend"` (not `"chatgpt-proxy-e2e-ropc"`) — the backend validates `aud` against `Settings.keycloak_audience = "chatgpt-proxy-frontend"` regardless of which client authenticated.

- [ ] **Step 3: Update the e2e helper**

In `e2e/support/session.ts`, change the `getRopcTokens` body:

```ts
    body: new URLSearchParams({
      grant_type: "password",
      client_id: process.env.E2E_ROPC_CLIENT_ID ?? "chatgpt-proxy-e2e-ropc",
      username,
      password,
    }),
```

- [ ] **Step 4: Validate the realm JSON and re-verify against a live stack**

Run: `python3 -c "import json; json.load(open('keycloak/realm-export.json'))"` to confirm valid JSON.
Then bring up a stack with this realm (`docker compose up -d --build`, or reuse whatever isolated verification approach was used for the earlier e2e work) and confirm: (a) a ROPC request against `chatgpt-proxy-frontend` now fails (`unauthorized_client` or similar), (b) a ROPC request against `chatgpt-proxy-e2e-ropc` succeeds and the resulting access token's `aud` claim is `chatgpt-proxy-frontend`.

- [ ] **Step 5: Commit**

```bash
git add keycloak/realm-export.json e2e/support/session.ts
git commit -m "security: split ROPC out of the production Keycloak client into a dedicated e2e client"
```

(This task intentionally does NOT re-run the full e2e suite yet — the existing specs are expected to still work against the new client, but the full regression pass happens once, at the end, in Task 18's final verification, after every other change in this plan has landed.)

---

### Task 13: Keycloak SMTP + Mailpit for dev/CI

**Files:**
- Modify: `keycloak/realm-export.json`
- Modify: `docker-compose.override.yml.example`
- Modify: `docker-compose.ci.yml`
- Modify: `.env.example`

- [ ] **Step 1: Add the SMTP env vars**

In `.env.example`, add near the other optional/blank-by-default vars (matching `OPENAI_API_KEY`'s convention):

```bash
# --- Keycloak SMTP (required for invite/verification emails to actually
# send -- blank here by design, a real deployment must set real values) ---
SMTP_HOST=
SMTP_PORT=587
SMTP_FROM=
SMTP_USER=
SMTP_PASSWORD=
SMTP_STARTTLS=true
```

- [ ] **Step 2: Add the `smtpServer` block to the realm**

In `keycloak/realm-export.json`, add a top-level key (alongside `realm`/`enabled`/`registrationAllowed`):

```json
  "smtpServer": {
    "host": "${env.SMTP_HOST}",
    "port": "${env.SMTP_PORT}",
    "from": "${env.SMTP_FROM}",
    "fromDisplayName": "Aigenta",
    "auth": "true",
    "user": "${env.SMTP_USER}",
    "password": "${env.SMTP_PASSWORD}",
    "starttls": "${env.SMTP_STARTTLS}"
  },
```

**Verify this actually works** against the pinned `quay.io/keycloak/keycloak:24.0` image before proceeding — bring up a stack, check the Keycloak container logs / admin console (Realm Settings → Email) for whether the `${env.VAR}` placeholders resolved to real values or were left as literal strings. If unresolved: implement the documented fallback instead — add an `envsubst`-based entrypoint step to the `keycloak` service (a small wrapper script, volume-mounted, replacing `command: start-dev --import-realm` with one that first `envsubst`s a template into the imported file) — this is a bigger change than the direct-substitution path above; only do it if the verification shows it's actually needed.

- [ ] **Step 3: Add Mailpit for local dev**

In `docker-compose.override.yml.example`, add a new service and wire the backend... no — SMTP is consumed by *Keycloak*, not the backend. Add:

```yaml
services:
  mailpit:
    image: axllent/mailpit:latest
    ports:
      - "127.0.0.1:8025:8025"  # web UI
    environment:
      MP_SMTP_AUTH_ACCEPT_ANY: "1"
```

And add to the `keycloak` service's `environment` in the same file (the base `docker-compose.yml`'s `keycloak` service has no `environment` entries for SMTP vars today — check whether `keycloak`'s environment block needs `SMTP_HOST`/etc. added to the BASE file instead, since the realm-import substitution reads container-level env vars, not the compose-file `.env`, at the point Keycloak's own process resolves `${env.VAR}`). Add to `docker-compose.yml`'s `keycloak` service `environment` block:

```yaml
      SMTP_HOST: ${SMTP_HOST:-}
      SMTP_PORT: ${SMTP_PORT:-587}
      SMTP_FROM: ${SMTP_FROM:-}
      SMTP_USER: ${SMTP_USER:-}
      SMTP_PASSWORD: ${SMTP_PASSWORD:-}
      SMTP_STARTTLS: ${SMTP_STARTTLS:-true}
```

Then in `docker-compose.override.yml.example`, override just the host/port for local dev:

```yaml
  keycloak:
    environment:
      SMTP_HOST: mailpit
      SMTP_PORT: 1025
```

- [ ] **Step 4: Add Mailpit to the CI overlay**

In `docker-compose.ci.yml`, add the same `mailpit` service (no host port publish needed there — CI reaches it by service name for the new e2e signup spec, or via a published port if the Playwright process itself needs to poll Mailpit's REST API from the host-side runner, matching how e2e's `provision_e2e_tenant.py` already reaches Postgres/Keycloak — check which one the new `signup.spec.ts` (Task 17) actually needs before finalizing whether to publish `8025`). Wire `SMTP_HOST=mailpit`/`SMTP_PORT=1025` into the `e2e` CI job's `.env` construction step in `.github/workflows/ci.yml`, matching the existing pattern (`echo "SMTP_HOST=mailpit" >> .env`, etc.).

- [ ] **Step 5: Validate**

Run: `docker compose config --quiet` and the override-merged variant. Bring up a local stack, open `http://localhost:8025` (Mailpit's web UI), trigger an admin-invite from `/admin/users`, and confirm a real email actually arrives in Mailpit — this is the concrete proof the whole SMTP chain works, not just that the compose files parse.

- [ ] **Step 6: Commit**

```bash
git add keycloak/realm-export.json docker-compose.yml docker-compose.override.yml.example docker-compose.ci.yml .env.example .github/workflows/ci.yml
git commit -m "feat: configure Keycloak SMTP with Mailpit for dev/CI so invite/verification emails actually send"
```

---

### Task 14: Explicit Keycloak token lifespans

**Files:**
- Modify: `keycloak/realm-export.json`

- [ ] **Step 1: Add the realm-level fields**

Alongside `realm`/`enabled`/`registrationAllowed`/`smtpServer`:

```json
  "accessTokenLifespan": 300,
  "ssoSessionIdleTimeout": 1800,
  "ssoSessionMaxLifespan": 36000,
  "offlineSessionIdleTimeout": 2592000,
  "offlineSessionMaxLifespanEnabled": true,
  "offlineSessionMaxLifespan": 7776000,
```

- [ ] **Step 2: Validate**

`python3 -c "import json; json.load(open('keycloak/realm-export.json'))"`, then bring up a stack and confirm via the Keycloak admin console (Realm Settings → Sessions/Tokens) that these values actually took effect after import.

- [ ] **Step 3: Commit**

```bash
git add keycloak/realm-export.json
git commit -m "feat: set explicit Keycloak token lifespans instead of relying on undocumented defaults"
```

---

### Task 15: ADR updates

**Files:**
- Modify: `docs/adr/0021-authentication-and-tenant-identity.md`
- Create: `docs/adr/0025-auth-production-readiness.md`

- [ ] **Step 1: Amend ADR-0021**

Read the file in full first. Correct the Decision section's "one realm per tenant" wording to describe the actual single-realm-plus-`tenant_id`-claim model. Append a dated `## Amendment (2026-08-26)` section noting the drift was found during this auth-overhaul review and that the working single-realm implementation is being kept, not changed to match the stale text. Reword the Reversibility section's "the realm-per-tenant *model*" reference to match.

- [ ] **Step 2: Write ADR-0025**

Follow the exact template `docs/adr/0020-fail-closed.md`/`0021-authentication-and-tenant-identity.md` use (`Status`/`Context`/`Decision`/`Alternatives Considered`/`Consequences`/`Security Implications`/`Privacy Implications`/`Reversibility`). Content, drawn from this plan's Design sections: `offline_access` adoption and its blast-radius trade-off; the `NEXTAUTH_URL`/cookie fix; the back-channel-vs-redirect logout decision and reasoning; the "session revocation is defense-in-depth, `is_active` is primary" framing; the ROPC client split; the token-lifespan values and their justification; the self-signup abuse-mitigation decision (in-memory rate limiter, documented multi-worker limitation); and the entitlement-grant `SECURITY DEFINER` privilege-boundary decision.

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0021-authentication-and-tenant-identity.md docs/adr/0025-auth-production-readiness.md
git commit -m "docs: amend ADR-0021's stale realm-per-tenant wording and record ADR-0025"
```

---

### Task 16: E2E — update `login.spec.ts` for the `/login` landing page

**Files:**
- Modify: `e2e/tests/login.spec.ts`

- [ ] **Step 1: Update the first test's assertion chain**

Read the current file in full first. The unauthenticated-request flow is now `/dashboard → /login → (click "Anmelden") → Keycloak's hosted form`, not a direct redirect to Keycloak. Update the `waitForURL`/interaction sequence to: navigate to `/dashboard`, assert landing on `/login` (e.g. `page.waitForURL(/\/login/)`), click the "Anmelden" button, then continue with the existing Keycloak-hosted-login assertions unchanged from that point on.

- [ ] **Step 2: Run against a live stack**

Run: `cd e2e && npx playwright test tests/login.spec.ts` against a running stack with all prior tasks' changes applied.
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add e2e/tests/login.spec.ts
git commit -m "test(e2e): update login spec for the new /login landing page"
```

---

### Task 17: E2E — new `signup.spec.ts`

**Files:**
- Create: `e2e/tests/signup.spec.ts`
- Modify: `e2e/support/session.ts` or a new `e2e/support/mailpit.ts` (Mailpit REST helper)

**Interfaces:**
- Consumes: `/signup` (Task 9), `POST /api/signup` (Task 6), Mailpit (Task 13).

- [ ] **Step 1: Add a Mailpit REST helper**

`e2e/support/mailpit.ts`:

```ts
const MAILPIT_API_URL = process.env.MAILPIT_API_URL ?? "http://localhost:8025/api/v1";

export async function findLatestEmailTo(address: string, timeoutMs = 15_000): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const response = await fetch(`${MAILPIT_API_URL}/search?query=to:${encodeURIComponent(address)}`);
    const body = await response.json();
    if (body.messages?.length > 0) {
      const messageResponse = await fetch(`${MAILPIT_API_URL}/message/${body.messages[0].ID}`);
      const message = await messageResponse.json();
      return message.Text ?? message.HTML;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`no email arrived for ${address} within ${timeoutMs}ms`);
}
```

- [ ] **Step 2: Write the spec**

`e2e/tests/signup.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
import { findLatestEmailTo } from "../support/mailpit";

test("a new practice can sign up, verify email, and log in", async ({ page }) => {
  const email = `e2e-signup-${Date.now()}@example.test`;
  const password = "correct-horse-battery-staple";

  await page.goto("/signup");
  await page.getByLabel("Praxisname").fill(`E2E Signup Praxis ${Date.now()}`);
  await page.getByLabel("Vorname").fill("Signup");
  await page.getByLabel("Nachname").fill("Test");
  await page.getByLabel("E-Mail").fill(email);
  await page.getByLabel("Passwort").fill(password);
  await page.getByRole("button", { name: "Registrieren" }).click();
  await expect(page.getByText(/E-Mail/i)).toBeVisible();

  const emailBody = await findLatestEmailTo(email);
  const verifyLinkMatch = emailBody.match(/https?:\/\/\S+/);
  expect(verifyLinkMatch).not.toBeNull();
  await page.goto(verifyLinkMatch![0]);

  await page.goto("/login");
  await page.getByRole("button", { name: "Anmelden" }).click();
  await page.fill("#username", email);
  await page.fill("#password", password);
  await page.click("#kc-login");
  await page.waitForURL(/\/dashboard/);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
});
```

Add teardown: capture the `tenant_id` from the signup response (may need `page.waitForResponse` on `POST /api/signup` to extract it, matching the `waitForResponse` pattern already used in `admin-branch-and-user-crud.spec.ts`) and call `provision_e2e_tenant.py cleanup --tenant-id <id>` in a `test.afterEach`, the same way global teardown does for the main provisioned tenant.

- [ ] **Step 3: Run against a live stack**

Run: `cd e2e && npx playwright test tests/signup.spec.ts`
Expected: PASS. If the verification email never arrives, check Task 13's SMTP wiring first (the most likely failure point), not this spec.

- [ ] **Step 4: Commit**

```bash
git add e2e/tests/signup.spec.ts e2e/support/mailpit.ts
git commit -m "test(e2e): add the practice self-signup and email-verification journey"
```

---

### Task 18: E2E — new `logout.spec.ts`, then full regression + deliberate-break rehearsal

**Files:**
- Create: `e2e/tests/logout.spec.ts`

**Interfaces:**
- Consumes: `injectSession`/`getRopcTokens` (with the new `chatgpt-proxy-e2e-ropc` client, Task 12), the back-channel logout hook (Task 7).

- [ ] **Step 1: Write the spec**

```ts
import { test, expect } from "@playwright/test";
import { loadTenant } from "../support/tenant";
import { getRopcTokens, injectSession } from "../support/session";

test("logout ends the Keycloak SSO session, not just the local cookie", async ({ page, context }) => {
  const tenant = loadTenant();
  const { doctor } = tenant.users;
  await injectSession(context, await getRopcTokens(doctor.email, doctor.password));

  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();

  await page.getByRole("button", { name: /Abmelden/i }).click(); // adjust selector to however AppShellChrome's menu is actually opened/clicked
  await page.waitForURL(/\/login/);

  // The real proof: a silent re-auth attempt (prompt=none) must now fail,
  // since a passing test that only checked the local cookie is gone would
  // still pass even if the back-channel Keycloak logout silently failed.
  const issuer = process.env.KEYCLOAK_ISSUER ?? "http://localhost:8080/realms/chatgpt-proxy-dev";
  const response = await page.request.get(
    `${issuer}/protocol/openid-connect/auth`,
    { params: { client_id: "chatgpt-proxy-frontend", response_type: "code", scope: "openid", redirect_uri: process.env.E2E_BASE_URL ?? "http://localhost:3000", prompt: "none" }, maxRedirects: 0 }
  );
  // A dead SSO session redirects back with error=login_required rather than
  // silently issuing a fresh code.
  const location = response.headers()["location"] ?? "";
  expect(location).toContain("login_required");
});
```

Adjust the actual "open the user menu, then click Abmelden" interaction to match `AppShellChrome.tsx`'s real DOM (it's inside a Mantine `Menu` — the target may need a preceding click to open the menu before the item is clickable; check the component's actual markup during implementation rather than assuming a single click suffices).

- [ ] **Step 2: Run against a live stack**

Run: `cd e2e && npx playwright test tests/logout.spec.ts`
Expected: PASS.

- [ ] **Step 3: Deliberate-break rehearsal for this spec specifically**

Temporarily make the `events.signOut` back-channel call in `frontend/lib/auth.ts` a no-op (comment out the `fetch` call, keep the try/catch structure). Re-run `logout.spec.ts`.
Expected: FAILS now, specifically on the `login_required` assertion (the SSO session is still alive, so the `prompt=none` request would succeed instead of redirecting with an error) — not a timeout, not an unrelated failure. This proves the test actually exercises what it claims to. Revert the no-op change immediately after confirming this.

- [ ] **Step 4: Commit the spec**

```bash
git add e2e/tests/logout.spec.ts
git commit -m "test(e2e): add the single-logout journey, verified against a real deliberate-break rehearsal"
```

- [ ] **Step 5: Full regression pass**

Run the complete suite (all 8 specs: the 4 from the earlier e2e-testing-strategy plan, `login.spec.ts` as updated in Task 16, plus `signup.spec.ts`/`logout.spec.ts`):

```bash
docker compose up -d --build
cd e2e && npm ci && npx playwright install --with-deps chromium && npm test
```

Expected: all 8 (or however many individual `test(...)` cases exist across those files) PASS. This is the combined regression gate for the ROPC client split (Task 12) and the `/login` page insertion (Task 16) together, per the Spec's explicit call-out that these two changes' regression surface should be treated as one pass, not two independent small changes.

- [ ] **Step 6: Backend full suite + lint**

```bash
cd backend && pytest tests/unit tests/integration tests/privacy_invariants -v && ruff check . && lint-imports
```
Expected: all pass.

- [ ] **Step 7: Frontend full suite**

```bash
cd frontend && npx vitest run && npm run build
```
Expected: all pass, build succeeds.

- [ ] **Step 8: `docker compose config --quiet`**

Run for the base file and the override-merged variant (per Task 11's Step 3). Expected: clean.

- [ ] **Step 9: Manual verification**

Deactivate a user with a live browser session open; confirm their *next refresh attempt* fails (distinct from confirming their next API call fails, which was already true before this plan via `is_active`) — open the browser's network tab, wait past the access-token lifespan, and observe the refresh call itself now fail rather than silently succeed.

- [ ] **Step 10: CI verification**

Push the branch, confirm the `e2e` CI job (with the Mailpit service and updated env config from Task 13) passes against a fresh GitHub Actions runner — this is the first real, non-local proof the whole chain works end to end.
