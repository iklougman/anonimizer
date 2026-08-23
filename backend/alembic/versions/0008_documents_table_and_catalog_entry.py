"""add documents table and the documents app catalog entry

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-23

Adds the `documents` table (Phase 1 of the Documents app -- upload, OCR,
markdown conversion, text anonymization) and seeds its app-catalog row.

Deliberately does NOT backfill tenant_app_entitlements/tenant_app_assignments
for existing tenants, unlike migration 0007's anonymization backfill: this is
a new, separately-sold entitlement, not a base capability every tenant already
has -- ops grants it per tenant explicitly via the Django admin. Do not "fix"
this by copying 0007's backfill loop.
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOCUMENTS_APP_ID = uuid.uuid4()


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("document_type", sa.String(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("raw_storage_path", sa.String(), nullable=False),
        sa.Column("structured_blocks", postgresql.JSONB(), nullable=True),
        sa.Column("sanitized_markdown", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "id", name="uq_documents_tenant_id_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["users.tenant_id", "users.id"], name="fk_documents_tenant_user"
        ),
    )

    op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON documents
        USING (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON documents TO app_runtime")

    # Seed the catalog row only -- no entitlement/assignment backfill (see module
    # docstring). Ops grants tenant_app_entitlements per tenant explicitly; a
    # tenant's own admin then enables tenant_app_assignments the same way any
    # other app is turned on today (frontend/app/(shell)/admin/apps).
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO apps (id, key, name, description, is_active) "
            "VALUES (:id, :key, :name, :description, true)"
        ),
        {
            "id": DOCUMENTS_APP_ID,
            "key": "documents",
            "name": "Dokumente",
            "description": "Dokumenten-Upload, OCR und Anonymisierung von PDFs und Bildern.",
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM tenant_app_assignments WHERE app_id = (SELECT id FROM apps WHERE key = 'documents')"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM tenant_app_entitlements WHERE app_id = (SELECT id FROM apps WHERE key = 'documents')"
        )
    )
    bind.execute(sa.text("DELETE FROM apps WHERE key = 'documents'"))

    op.execute("REVOKE ALL PRIVILEGES ON documents FROM app_runtime")
    op.execute("DROP POLICY tenant_isolation ON documents")
    op.execute("ALTER TABLE documents NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents DISABLE ROW LEVEL SECURITY")
    op.drop_table("documents")
