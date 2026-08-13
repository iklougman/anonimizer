"""enforce tenant consistency across parent/child rows with composite foreign keys

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-13

Postgres foreign-key checks bypass Row-Level Security unconditionally (documented
behavior: the referential-integrity triggers run as the table owner with RLS off).
So before this migration nothing stopped a write like
`MessageRepository.create(tenant_id=A, conversation_id=<a conversation owned by B>)`
from succeeding — the `WITH CHECK` policy on `messages` only validates
`messages.tenant_id`, and the FK check against `conversations.id` never saw the
tenant dimension at all. Reads still filtered correctly by `tenant_id`, so this was
not a confidentiality leak, but the schema's central invariant (a child row's
`tenant_id` must equal its parent's) had no database-level enforcement.

Replacing each single-column FK with a composite `(tenant_id, <parent_id>)` FK makes
the invariant structural: the referenced row must exist *under the same tenant*.

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Composite unique constraints on the parent tables, required as composite-FK targets.
# (tenant_id, id) is redundant with the existing `id` primary key, but a composite FK
# needs a unique constraint covering exactly its referenced column list.
PARENT_UNIQUE_CONSTRAINTS = [
    ("users", "uq_users_tenant_id_id"),
    ("conversations", "uq_conversations_tenant_id_id"),
    ("tenant_keys", "uq_tenant_keys_tenant_id_id"),
]

# (table, old single-column FK name, old local column, referent table, referent column,
#  new composite FK name)
# Old names verified against the live database's `pg_constraint` before writing this
# migration; they are Postgres's default `<table>_<column>_fkey` form.
COMPOSITE_FOREIGN_KEYS = [
    (
        "conversations",
        "conversations_user_id_fkey",
        "user_id",
        "users",
        "id",
        "fk_conversations_tenant_user",
    ),
    (
        "messages",
        "messages_conversation_id_fkey",
        "conversation_id",
        "conversations",
        "id",
        "fk_messages_tenant_conversation",
    ),
    (
        "token_mappings",
        "token_mappings_conversation_id_fkey",
        "conversation_id",
        "conversations",
        "id",
        "fk_token_mappings_tenant_conversation",
    ),
    (
        "token_mappings",
        "token_mappings_dek_id_fkey",
        "dek_id",
        "tenant_keys",
        "id",
        "fk_token_mappings_tenant_dek",
    ),
    (
        "audit_events",
        "audit_events_conversation_id_fkey",
        "conversation_id",
        "conversations",
        "id",
        "fk_audit_events_tenant_conversation",
    ),
    (
        "llm_requests",
        "llm_requests_conversation_id_fkey",
        "conversation_id",
        "conversations",
        "id",
        "fk_llm_requests_tenant_conversation",
    ),
]


def upgrade() -> None:
    for table, constraint_name in PARENT_UNIQUE_CONSTRAINTS:
        op.create_unique_constraint(constraint_name, table, ["tenant_id", "id"])

    for (
        table,
        old_fk_name,
        local_column,
        referent_table,
        referent_column,
        new_fk_name,
    ) in COMPOSITE_FOREIGN_KEYS:
        op.drop_constraint(old_fk_name, table, type_="foreignkey")
        op.create_foreign_key(
            new_fk_name,
            table,
            referent_table,
            ["tenant_id", local_column],
            ["tenant_id", referent_column],
        )


def downgrade() -> None:
    for (
        table,
        old_fk_name,
        local_column,
        referent_table,
        referent_column,
        new_fk_name,
    ) in reversed(COMPOSITE_FOREIGN_KEYS):
        op.drop_constraint(new_fk_name, table, type_="foreignkey")
        # Recreated under the original auto-generated name so an upgrade/downgrade
        # cycle leaves the schema byte-identical to where it started.
        op.create_foreign_key(
            old_fk_name,
            table,
            referent_table,
            [local_column],
            [referent_column],
        )

    for table, constraint_name in reversed(PARENT_UNIQUE_CONSTRAINTS):
        op.drop_constraint(constraint_name, table, type_="unique")
