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

AAD hazard: the accompanying `TokenVault` code change (not this migration)
changes the AES-GCM associated data bound to each `token_mappings.encrypted_value`
from `f"{tenant_id}:{conversation_id}:{token}"` to
`f"{tenant_id}:{scope_type}:{scope_id}:{token}"`. This migration does NOT
re-encrypt any existing rows, so any row written before this change was deployed
will fail to decrypt afterward with `cryptography.exceptions.InvalidTag` -- which
is deliberately uncaught and propagates as an unhandled exception (an HTTP 500 on
`GET /api/conversations/{id}/messages`, or an unhandled exception mid-SSE-stream
during a chat response), not a handled rejection. Deploying this change to any
environment with pre-existing `token_mappings` data requires first either (a)
re-encrypting those rows -- unwrap the DEK, decrypt with the legacy 3-field AAD
`f"{tenant_id}:{conversation_id}:{token}"`, re-encrypt with the new 4-field AAD --
or (b) expiring them (`UPDATE token_mappings SET deleted_at = now()`) so they fail
through the handled `UnresolvedTokenError` path instead.

Lock profile: this migration runs a full-table UPDATE, a NOT NULL alteration, a
CHECK constraint addition, an FK addition, and two non-concurrent index builds all
inside one transaction, which blocks writes to `token_mappings` for its duration.
That is an acceptable tradeoff at this project's current (dev/research, "tens of
concurrent users") scale, but a future reader running this against a
larger/production table should revisit it: a batched backfill, `NOT VALID` +
a separate `VALIDATE CONSTRAINT` for the CHECK, and `CREATE INDEX CONCURRENTLY`
run outside the transaction.

Downgrade precondition: `downgrade()` re-applies `conversation_id NOT NULL`, which
will raise if any document-scoped rows (`scope_type='document'`) exist at
downgrade time -- this is intentional fail-loud behavior, not a bug. If you hit
it, delete or re-scope the document-scoped `token_mappings` rows before
downgrading.
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
