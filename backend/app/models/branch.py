import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class Branch(Base):
    """An organizational sub-unit (Filiale) inside a praxis tenant.

    Never an isolation boundary: RLS, encryption keys, and billing all stay
    per-tenant. A single-location praxis simply has no rows here.
    """

    __tablename__ = "branches"
    # `uq_branches_tenant_id_id` is redundant with the `id` primary key, but a composite
    # foreign key needs a unique constraint covering exactly its referenced columns —
    # it exists so `users(tenant_id, branch_id)` can reference it (migration 0006).
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_branches_tenant_name"),
        UniqueConstraint("tenant_id", "id", name="uq_branches_tenant_id_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
