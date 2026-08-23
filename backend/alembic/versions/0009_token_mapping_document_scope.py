"""generalize token_mappings scope to conversation or document

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-23

Documents (migration 0008) are not conversations, so a token minted while
anonymizing a document needs a scope that isn't a conversation_id. This adds a
`scope_type` discriminator ('conversation' | 'document'), makes
`conversation_id` nullable, adds a nullable `document_id`, and replaces the
single (tenant_id, conversation_id, token) unique constraint with two partial
unique indexes -- see docs/adr/0023-token-vault-scope-generalization.md.

Existing rows are backfilled to scope_type='conversation' before the CHECK
constraint is added, since every row created before this migration is
conversation-scoped by construction.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("token_mappings", sa.Column("scope_type", sa.String(), nullable=True))
    op.execute("UPDATE token_mappings SET scope_type = 'conversation'")
    op.alter_column("token_mappings", "scope_type", nullable=False)

    op.alter_column("token_mappings", "conversation_id", nullable=True)
    op.add_column(
        "token_mappings", sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True)
    )

    op.execute(
        """
        ALTER TABLE token_mappings ADD CONSTRAINT ck_token_mappings_scope_consistency
        CHECK (
            (scope_type = 'conversation' AND conversation_id IS NOT NULL AND document_id IS NULL)
            OR
            (scope_type = 'document' AND document_id IS NOT NULL AND conversation_id IS NULL)
        )
        """
    )

    op.create_foreign_key(
        "fk_token_mappings_tenant_document",
        "token_mappings",
        "documents",
        ["tenant_id", "document_id"],
        ["tenant_id", "id"],
    )

    # Replace the single scope-agnostic unique constraint with two partial unique
    # indexes -- a plain constraint over sometimes-NULL columns would not enforce
    # per-scope uniqueness, since Postgres treats every NULL as distinct.
    op.drop_constraint("uq_token_mappings_scope", "token_mappings", type_="unique")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_token_mappings_conversation_scope
        ON token_mappings (tenant_id, conversation_id, token) WHERE scope_type = 'conversation'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_token_mappings_document_scope
        ON token_mappings (tenant_id, document_id, token) WHERE scope_type = 'document'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_token_mappings_document_scope")
    op.execute("DROP INDEX IF EXISTS uq_token_mappings_conversation_scope")
    op.create_unique_constraint(
        "uq_token_mappings_scope", "token_mappings", ["tenant_id", "conversation_id", "token"]
    )
    op.drop_constraint("fk_token_mappings_tenant_document", "token_mappings", type_="foreignkey")
    op.execute("ALTER TABLE token_mappings DROP CONSTRAINT ck_token_mappings_scope_consistency")
    op.drop_column("token_mappings", "document_id")
    op.alter_column("token_mappings", "conversation_id", nullable=False)
    op.drop_column("token_mappings", "scope_type")
