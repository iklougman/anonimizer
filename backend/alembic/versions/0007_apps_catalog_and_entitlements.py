"""add app catalog, tenant entitlements/assignments, and the app_ops role

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-22

Two-tier multi-app model:

- `apps` is a global catalog (not tenant-scoped, no RLS -- same pattern as
  `tenants`). Ops adds rows here as new apps are built; this migration seeds
  exactly one, "anonymization" (the existing chat/privacy pipeline).
- `tenant_app_entitlements` is the ops-controlled subscription layer: "is this
  tenant subscribed to this app at all". Written only by the new internal-only
  Django admin (via the `app_ops` role), read-only for `app_runtime`.
- `tenant_app_assignments` is the tenant-admin-controlled enable/assign layer:
  "has this tenant's own admin turned the app on, tenant-wide or for one
  branch". Full CRUD for `app_runtime`; `app_ops`/Django never touches it.

Every existing tenant is backfilled as entitled + tenant-wide-enabled for
"anonymization" in this same migration -- without that backfill, the moment
`require_app_entitlement("anonymization")` is wired into chat.py, every
already-provisioned tenant's chat would immediately 403.

IDs are generated app-side (Python `uuid.uuid4()`), matching every other table
in this codebase -- no `gen_random_uuid()`/pgcrypto dependency is introduced.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.config import get_settings

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_SCOPED_TABLES = ["tenant_app_entitlements", "tenant_app_assignments"]

ANONYMIZATION_APP_ID = uuid.uuid4()


def upgrade() -> None:
    op.create_table(
        "apps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("key", name="uq_apps_key"),
    )

    op.create_table(
        "tenant_app_entitlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("apps.id"), nullable=False),
        # Django `auth_user.username` of the ops staffer who granted this --
        # a disjoint identity namespace from Keycloak subjects, so this is a
        # plain string, not a foreign key into `users`.
        sa.Column("granted_by", sa.String(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # NULL = active. Re-granting after a revoke clears this on the same
        # row rather than inserting a new one (see uq_tenant_app_entitlements).
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "app_id", name="uq_tenant_app_entitlements_tenant_app"),
    )

    op.create_table(
        "tenant_app_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("app_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("apps.id"), nullable=False),
        # NULL branch_id = tenant-wide, mirroring the users.branch_id IS NULL
        # -> praxis-wide convention in app/auth/permissions.py.
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        # Nullable so this migration's own backfill rows (no specific tenant
        # user performed that action) can be written without a real actor.
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "branch_id"], ["branches.tenant_id", "branches.id"], name="fk_tenant_app_assignments_tenant_branch"
        ),
        sa.UniqueConstraint("tenant_id", "app_id", "branch_id", name="uq_tenant_app_assignments_tenant_app_branch"),
    )
    # A plain UNIQUE constraint treats NULLs as distinct in Postgres, so the
    # constraint above does not stop two tenant-wide (branch_id IS NULL) rows
    # for the same (tenant, app) -- this partial index is what actually
    # enforces "at most one tenant-wide row per app".
    op.execute(
        """
        CREATE UNIQUE INDEX uq_tenant_app_assignments_tenant_app_tenant_wide
        ON tenant_app_assignments (tenant_id, app_id) WHERE branch_id IS NULL
        """
    )

    # RLS + grants for the two tenant-scoped tables: 0006's fail-closed form.
    # `apps` is a global catalog (like `tenants`) and gets no RLS.
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
            """
        )

    op.execute("GRANT SELECT ON apps TO app_runtime")
    op.execute("GRANT SELECT ON tenant_app_entitlements TO app_runtime")  # FastAPI never writes this table
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_app_assignments TO app_runtime")

    # --- app_ops role: the internal-only Django admin's DB identity ---------
    #
    # BYPASSRLS is a role-wide attribute (there is no per-table bypass grant in
    # Postgres), which is why this is a brand-new role used by exactly one
    # service whose code only ever defines models for the three tables granted
    # below: `catalog/models.py` in ops_admin/ structurally cannot express a
    # query against `messages`/`conversations`/`token_mappings`/etc, so the
    # RLS bypass's blast radius is bounded by Django's own code, not just the
    # privilege system. Ops staff genuinely need cross-tenant visibility to
    # grant/revoke entitlements for any tenant, and per-request rotation of
    # `app.current_tenant_id` (the alternative to BYPASSRLS) would require
    # looping over every tenant just to render one admin list page.
    escaped_password = get_settings().app_ops_password.replace("'", "''")
    op.execute(
        f"""
        DO $migration_ops_role_body$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_ops') THEN
                CREATE ROLE app_ops LOGIN PASSWORD '{escaped_password}'
                    NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE;
            ELSE
                ALTER ROLE app_ops WITH NOSUPERUSER BYPASSRLS NOCREATEDB NOCREATEROLE
                    PASSWORD '{escaped_password}';
            END IF;
        END
        $migration_ops_role_body$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO app_ops")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON apps, tenant_app_entitlements TO app_ops")
    # Read-only, for the Django admin's tenant picker/label -- app_ops never
    # writes tenants and has no grant on any other tenant-scoped table.
    op.execute("GRANT SELECT ON tenants TO app_ops")
    # Django's own built-in tables (auth_user, sessions, admin log, django's
    # own migration history) live in a separate schema it owns outright, so
    # they can never collide with anything Alembic manages in `public`.
    op.execute("CREATE SCHEMA IF NOT EXISTS ops AUTHORIZATION app_ops")

    # --- Seed the catalog + backfill every existing tenant ------------------
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO apps (id, key, name, description, is_active) "
            "VALUES (:id, :key, :name, :description, true)"
        ),
        {
            "id": ANONYMIZATION_APP_ID,
            "key": "anonymization",
            "name": "Anonymisierung",
            "description": "Die bestehende PII-Sanitisierungs- und De-Anonymisierungs-Chat-Pipeline.",
        },
    )

    tenant_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM tenants"))]
    if tenant_ids:
        bind.execute(
            sa.text(
                "INSERT INTO tenant_app_entitlements (id, tenant_id, app_id, granted_by, granted_at) "
                "VALUES (:id, :tenant_id, :app_id, :granted_by, now())"
            ),
            [
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "app_id": ANONYMIZATION_APP_ID,
                    "granted_by": "system-migration-0007",
                }
                for tenant_id in tenant_ids
            ],
        )
        bind.execute(
            sa.text(
                "INSERT INTO tenant_app_assignments (id, tenant_id, app_id, branch_id, is_enabled, assigned_by) "
                "VALUES (:id, :tenant_id, :app_id, NULL, true, NULL)"
            ),
            [
                {"id": uuid.uuid4(), "tenant_id": tenant_id, "app_id": ANONYMIZATION_APP_ID}
                for tenant_id in tenant_ids
            ],
        )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS ops")
    op.execute("REVOKE ALL PRIVILEGES ON tenants FROM app_ops")
    op.execute("REVOKE ALL PRIVILEGES ON apps, tenant_app_entitlements FROM app_ops")
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_ops")
    op.execute("DROP ROLE IF EXISTS app_ops")

    op.execute("REVOKE ALL PRIVILEGES ON tenant_app_assignments FROM app_runtime")
    op.execute("REVOKE ALL PRIVILEGES ON tenant_app_entitlements FROM app_runtime")
    op.execute("REVOKE ALL PRIVILEGES ON apps FROM app_runtime")

    for table in reversed(TENANT_SCOPED_TABLES):
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP INDEX IF EXISTS uq_tenant_app_assignments_tenant_app_tenant_wide")
    op.drop_table("tenant_app_assignments")
    op.drop_table("tenant_app_entitlements")
    op.drop_table("apps")
