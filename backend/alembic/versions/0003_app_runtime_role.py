"""provision restricted app_runtime role; fix RLS policy to fail closed on missing context

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-13

"""
from typing import Sequence, Union

from alembic import op

from app.config import get_settings

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALL_TABLES = [
    "tenants",
    "users",
    "conversations",
    "messages",
    "tenant_keys",
    "token_mappings",
    "audit_events",
    "llm_requests",
]

TENANT_SCOPED_TABLES = [
    "users",
    "conversations",
    "messages",
    "tenant_keys",
    "token_mappings",
    "audit_events",
    "llm_requests",
]


def upgrade() -> None:
    escaped_password = get_settings().app_runtime_password.replace("'", "''")

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_runtime') THEN
                CREATE ROLE app_runtime LOGIN PASSWORD '{escaped_password}'
                    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            ELSE
                ALTER ROLE app_runtime WITH PASSWORD '{escaped_password}';
            END IF;
        END
        $$;
        """
    )

    op.execute("GRANT USAGE ON SCHEMA public TO app_runtime")
    for table in ALL_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_runtime")

    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            """
        )


def downgrade() -> None:
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.current_tenant_id')::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id')::uuid)
            """
        )

    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM app_runtime")
    op.execute("REVOKE USAGE ON SCHEMA public FROM app_runtime")
    op.execute("DROP ROLE IF EXISTS app_runtime")
