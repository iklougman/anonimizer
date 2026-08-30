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

`gen_random_uuid()` needs no `CREATE EXTENSION pgcrypto` here: it has been a
core built-in of Postgres (not an extension function) since PG13, and this
project runs postgres:16-alpine. 0007's docstring ("no gen_random_uuid()/
pgcrypto dependency is introduced") documents a stylistic choice for that
migration's own Python-driven row-ID inserts, not an unavailability of the
function -- provision_e2e_tenant.py's own entitlement-grant SQL already
calls gen_random_uuid() successfully against this exact database. This
migration's function body runs entirely inside Postgres with no Python-side
UUID to pass in, so generating the id with gen_random_uuid() here is the
correct choice, not a deviation from that convention.

The function is created by whatever role runs this migration (POSTGRES_USER,
a real Postgres superuser under the official postgres image) and is neither
marked BYPASSRLS nor SECURITY INVOKER, but SUPERUSER status itself always
bypasses RLS regardless of a table's FORCE ROW LEVEL SECURITY setting (see
tenant_app_entitlements' FORCE RLS in migration 0007), so its INSERT is not
blocked by the tenant_isolation policy despite the calling app_runtime
session having no (or a different) app.current_tenant_id set.
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
