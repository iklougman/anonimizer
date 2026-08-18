import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class TenantRolePermission(Base):
    """A sparse per-tenant override of one role permission default.

    Effective permissions are code-level defaults (app/auth/permissions.py)
    plus/minus these rows — an empty table means "defaults apply". super_admin
    is excluded by CHECK constraint: it always holds every permission, so it is
    structurally unconfigurable (no admin can lock the praxis out of admin).
    """

    __tablename__ = "tenant_role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "role", "permission", name="uq_role_permissions_tenant_role_permission"
        ),
        CheckConstraint("role IN ('doctor', 'staff')", name="ck_role_permissions_matrix_roles_only"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    permission: Mapped[str] = mapped_column(String, nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
