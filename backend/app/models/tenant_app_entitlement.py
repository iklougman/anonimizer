import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class TenantAppEntitlement(Base):
    """Ops-controlled subscription: "is this tenant subscribed to this app".

    Written only by the internal-only Django ops-admin service (via the
    `app_ops` Postgres role) -- FastAPI/`app_runtime` only ever reads this
    table. Active iff `revoked_at IS NULL`; re-granting after a revoke clears
    that column on the same row rather than inserting a new one.
    """

    __tablename__ = "tenant_app_entitlements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "app_id", name="uq_tenant_app_entitlements_tenant_app"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    app_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("apps.id"), nullable=False)
    granted_by: Mapped[str] = mapped_column(String, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
