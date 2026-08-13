import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class TenantKey(Base):
    __tablename__ = "tenant_keys"
    # `uq_tenant_keys_tenant_id_id` is redundant with the `id` primary key, but a composite
    # foreign key needs a unique constraint covering exactly its referenced columns — it
    # exists so `token_mappings(tenant_id, dek_id)` can reference it (migration 0004).
    __table_args__ = (
        UniqueConstraint("tenant_id", "key_version", name="uq_tenant_keys_tenant_version"),
        UniqueConstraint("tenant_id", "id", name="uq_tenant_keys_tenant_id_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
