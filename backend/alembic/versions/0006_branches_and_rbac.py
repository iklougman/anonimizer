"""add branches, per-tenant role permission overrides, and user role/branch/active columns

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-18

Praxis = tenant stays the isolation boundary (one key set, one RLS scope).
Branches are organizational sub-units *inside* a tenant — a filter/label for
users and reporting, never an RLS dimension. `tenant_role_permissions` holds
sparse per-tenant overrides of the code-level role permission defaults
(app/auth/permissions.py); an empty table means "defaults apply", so adding a
new permission later needs no data backfill.

super_admin is deliberately excluded from `tenant_role_permissions` by CHECK
constraint: it always holds every permission (lock-out impossible by
construction), so a row configuring it would be dead data at best and
misleading at worst.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TENANT_SCOPED_TABLES = ["branches", "tenant_role_permissions"]


def upgrade() -> None:
    op.create_table(
        "branches",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_branches_tenant_name"),
        # Redundant with the `id` primary key, but a composite FK needs a unique
        # constraint covering exactly its referenced columns (migration 0004 pattern) —
        # users(tenant_id, branch_id) references it below.
        sa.UniqueConstraint("tenant_id", "id", name="uq_branches_tenant_id_id"),
    )

    op.create_table(
        "tenant_role_permissions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("permission", sa.String(), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id", "role", "permission", name="uq_role_permissions_tenant_role_permission"
        ),
        sa.CheckConstraint(
            "role IN ('doctor', 'staff')", name="ck_role_permissions_matrix_roles_only"
        ),
    )

    op.add_column(
        "users",
        sa.Column("branch_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    # NULL branch_id makes this FK vacuous — the praxis-single path needs no branch rows.
    op.create_foreign_key(
        "fk_users_tenant_branch",
        "users",
        "branches",
        ["tenant_id", "branch_id"],
        ["tenant_id", "id"],
    )
    # Safe to add directly: constraint-validation scans run as table owner outside RLS
    # (the same Postgres behavior migration 0004's docstring documents for FK checks),
    # and every existing row was seeded 'doctor'.
    op.create_check_constraint(
        "ck_users_role_valid", "users", "role IN ('super_admin', 'doctor', 'staff')"
    )

    # RLS + grants for the new tables: 0003's fail-closed policy form.
    for table in NEW_TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
            """
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_runtime")


def downgrade() -> None:
    for table in reversed(NEW_TENANT_SCOPED_TABLES):
        op.execute(f"REVOKE ALL PRIVILEGES ON {table} FROM app_runtime")
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.drop_constraint("fk_users_tenant_branch", "users", type_="foreignkey")
    op.drop_column("users", "is_active")
    op.drop_column("users", "branch_id")

    op.drop_table("tenant_role_permissions")
    op.drop_table("branches")
