from app.models import Base


def test_all_tables_registered():
    expected = {
        "tenants",
        "users",
        "branches",
        "tenant_role_permissions",
        "conversations",
        "documents",
        "messages",
        "token_mappings",
        "tenant_keys",
        "audit_events",
        "llm_requests",
        "apps",
        "tenant_app_entitlements",
        "tenant_app_assignments",
    }
    assert set(Base.metadata.tables.keys()) == expected


def test_tenant_scoped_tables_have_tenant_id_column():
    tenant_scoped = {
        "users",
        "branches",
        "tenant_role_permissions",
        "conversations",
        "documents",
        "messages",
        "token_mappings",
        "tenant_keys",
        "audit_events",
        "llm_requests",
        "tenant_app_entitlements",
        "tenant_app_assignments",
    }
    for table_name in tenant_scoped:
        table = Base.metadata.tables[table_name]
        assert "tenant_id" in table.columns, f"{table_name} missing tenant_id"


def test_token_mappings_scope_partial_unique_indexes():
    table = Base.metadata.tables["token_mappings"]
    index_names = {index.name for index in table.indexes}
    assert "uq_token_mappings_conversation_scope" in index_names
    assert "uq_token_mappings_document_scope" in index_names


def test_token_mappings_scope_check_constraint():
    table = Base.metadata.tables["token_mappings"]
    check_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_token_mappings_scope_consistency" in check_names
