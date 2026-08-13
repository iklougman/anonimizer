import sqlalchemy as sa

TENANT_SCOPED_TABLES = [
    "users",
    "conversations",
    "messages",
    "tenant_keys",
    "token_mappings",
    "audit_events",
    "llm_requests",
]


def test_tenant_scoped_tables_have_rls_enabled_and_forced(db_engine):
    with db_engine.connect() as conn:
        for table in TENANT_SCOPED_TABLES:
            row = conn.execute(
                sa.text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = :table"
                ),
                {"table": table},
            ).one()
            assert row.relrowsecurity is True, f"{table} does not have RLS enabled"
            assert row.relforcerowsecurity is True, f"{table} does not have RLS forced"


def test_tenant_scoped_tables_have_a_policy(db_engine):
    with db_engine.connect() as conn:
        for table in TENANT_SCOPED_TABLES:
            count = conn.execute(
                sa.text("SELECT count(*) FROM pg_policies WHERE tablename = :table"),
                {"table": table},
            ).scalar_one()
            assert count >= 1, f"{table} has no RLS policy"


def test_tenants_table_has_no_rls(db_engine):
    with db_engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT relrowsecurity FROM pg_class WHERE relname = 'tenants'")
        ).one()
        assert row.relrowsecurity is False
