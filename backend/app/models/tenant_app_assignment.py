import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class TenantAppAssignment(Base):
    """Tenant-admin-controlled enable/assign layer, one level below entitlement.

    NULL `branch_id` means tenant-wide, mirroring the `users.branch_id IS
    NULL` -> praxis-wide convention in `app/auth/permissions.py`. Full CRUD
    for `app_runtime` (the tenant's own admin manages this via the Next.js
    admin UI); `app_ops`/Django never touches this table.
    """

    __tablename__ = "tenant_app_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "branch_id"],
            ["branches.tenant_id", "branches.id"],
            name="fk_tenant_app_assignments_tenant_branch",
        ),
        UniqueConstraint("tenant_id", "app_id", "branch_id", name="uq_tenant_app_assignments_tenant_app_branch"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    app_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("apps.id"), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
