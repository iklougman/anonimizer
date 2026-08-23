import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class TokenMapping(Base):
    __tablename__ = "token_mappings"
    # Both parent references are composite (see migration 0004): FK checks bypass
    # RLS, so single-column FKs would accept a conversation, document, or DEK
    # owned by another tenant. Exactly one of conversation_id/document_id is
    # populated, selected by scope_type -- see migration 0009 and
    # docs/adr/0023-token-vault-scope-generalization.md.
    __table_args__ = (
        CheckConstraint(
            "(scope_type = 'conversation' AND conversation_id IS NOT NULL AND document_id IS NULL) "
            "OR (scope_type = 'document' AND document_id IS NOT NULL AND conversation_id IS NULL)",
            name="ck_token_mappings_scope_consistency",
        ),
        # A plain UniqueConstraint over columns that are sometimes NULL would not
        # enforce per-scope uniqueness -- Postgres treats every NULL as distinct.
        # These partial indexes are what actually enforce "one token per scope
        # instance", independently for each scope_type.
        Index(
            "uq_token_mappings_conversation_scope",
            "tenant_id",
            "conversation_id",
            "token",
            unique=True,
            postgresql_where=text("scope_type = 'conversation'"),
        ),
        Index(
            "uq_token_mappings_document_scope",
            "tenant_id",
            "document_id",
            "token",
            unique=True,
            postgresql_where=text("scope_type = 'document'"),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_token_mappings_tenant_conversation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["documents.tenant_id", "documents.id"],
            name="fk_token_mappings_tenant_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "dek_id"],
            ["tenant_keys.tenant_id", "tenant_keys.id"],
            name="fk_token_mappings_tenant_dek",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String, nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    token: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    dek_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
