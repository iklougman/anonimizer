import sqlalchemy as sa

from app.models import Base

# Derived from the ORM metadata rather than hardcoded, so a future migration that
# adds a tenant-scoped table without an RLS policy fails these tests immediately.
# `tenants` is excluded deliberately: it is the scoping dimension, not scoped data (design spec §4).
TENANT_SCOPED_TABLES = sorted(
    table_name
    for table_name, table in Base.metadata.tables.items()
    if "tenant_id" in table.columns and table_name != "tenants"
)


def test_tenant_scoped_table_list_is_non_empty():
    assert TENANT_SCOPED_TABLES, "no tenant-scoped tables were derived from the ORM metadata"


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


def test_tenant_isolation_policy_expression_is_identical_on_every_table(db_engine):
    """Every table's policy was created from the same literal expression in migration 0002/0003.

    Asserting only that *a* policy named `tenant_isolation` exists would not catch a
    future migration installing a different (e.g. weaker, or missing the `nullif`)
    expression on one table. Postgres normalizes and stores the parsed expression in
    `pg_policies.qual`/`with_check`, so identical source expressions normalize to
    identical stored text — any divergence means one table's policy is not the same
    policy as the others'.
    """
    expressions: dict[str, tuple[str, str]] = {}

    with db_engine.connect() as conn:
        for table in TENANT_SCOPED_TABLES:
            row = conn.execute(
                sa.text(
                    "SELECT qual, with_check FROM pg_policies "
                    "WHERE tablename = :table AND policyname = 'tenant_isolation'"
                ),
                {"table": table},
            ).one()

            assert row.qual is not None, f"{table}: tenant_isolation policy has no USING clause"
            assert row.with_check is not None, (
                f"{table}: tenant_isolation policy has no WITH CHECK clause"
            )

            # Both halves of the policy must guard the same thing.
            assert row.qual == row.with_check, (
                f"{table}: USING and WITH CHECK expressions differ "
                f"({row.qual!r} vs {row.with_check!r})"
            )

            # The `nullif(..., '')` wrapper is what makes a stale, empty-string GUC on a
            # reused pooled connection evaluate to NULL instead of raising (design spec §4).
            assert "nullif" in row.qual.lower(), (
                f"{table}: policy expression is missing the nullif() guard: {row.qual!r}"
            )
            assert "app.current_tenant_id" in row.qual, (
                f"{table}: policy expression does not reference app.current_tenant_id: {row.qual!r}"
            )

            expressions[table] = (row.qual, row.with_check)

    distinct = set(expressions.values())
    assert len(distinct) == 1, (
        "tenant_isolation policy expressions diverge across tenant-scoped tables: "
        f"{ {t: e[0] for t, e in expressions.items()} }"
    )


def test_tenants_table_has_no_rls(db_engine):
    with db_engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT relrowsecurity FROM pg_class WHERE relname = 'tenants'")
        ).one()
        assert row.relrowsecurity is False
