from app.models import Base


def test_all_tables_registered():
    expected = {
        "tenants",
        "users",
        "conversations",
        "messages",
        "token_mappings",
        "tenant_keys",
        "audit_events",
        "llm_requests",
    }
    assert set(Base.metadata.tables.keys()) == expected


def test_tenant_scoped_tables_have_tenant_id_column():
    tenant_scoped = {
        "users",
        "conversations",
        "messages",
        "token_mappings",
        "tenant_keys",
        "audit_events",
        "llm_requests",
    }
    for table_name in tenant_scoped:
        table = Base.metadata.tables[table_name]
        assert "tenant_id" in table.columns, f"{table_name} missing tenant_id"


def test_token_mappings_unique_scope_constraint():
    table = Base.metadata.tables["token_mappings"]
    unique_cols = {
        tuple(col.name for col in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("tenant_id", "conversation_id", "token") in unique_cols
