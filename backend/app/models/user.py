import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    true,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class User(Base):
    __tablename__ = "users"
    # `uq_users_tenant_id_id` is redundant with the `id` primary key, but a composite
    # foreign key needs a unique constraint covering exactly its referenced columns —
    # it exists so `conversations(tenant_id, user_id)` can reference it (migration 0004).
    __table_args__ = (
        UniqueConstraint("tenant_id", "keycloak_subject", name="uq_users_tenant_keycloak_subject"),
        UniqueConstraint("tenant_id", "id", name="uq_users_tenant_id_id"),
        # NULL branch_id makes this FK vacuous — the praxis-single path needs no branches.
        ForeignKeyConstraint(
            ["tenant_id", "branch_id"],
            ["branches.tenant_id", "branches.id"],
            name="fk_users_tenant_branch",
        ),
        CheckConstraint("role IN ('super_admin', 'doctor', 'staff')", name="ck_users_role_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    keycloak_subject: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true(), default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
