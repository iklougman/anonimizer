import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import App, TenantAppAssignment, TenantAppEntitlement


class AppEntitlementRepository(BaseRepository):
    def list_catalog(self) -> list[App]:
        stmt = sa.select(App).where(App.is_active.is_(True)).order_by(App.name)
        return list(self.session.execute(stmt).scalars().all())

    def get_by_key(self, key: str) -> App | None:
        stmt = sa.select(App).where(App.key == key)
        return self.session.execute(stmt).scalar_one_or_none()

    def get(self, app_id: uuid.UUID) -> App | None:
        return self.session.get(App, app_id)

    def is_entitled(self, tenant_id: uuid.UUID, app_key: str) -> bool:
        stmt = (
            sa.select(sa.func.count())
            .select_from(TenantAppEntitlement)
            .join(App, App.id == TenantAppEntitlement.app_id)
            .where(
                TenantAppEntitlement.tenant_id == tenant_id,
                App.key == app_key,
                TenantAppEntitlement.revoked_at.is_(None),
            )
        )
        return self.session.execute(stmt).scalar_one() > 0

    def get_assignment(
        self, tenant_id: uuid.UUID, app_id: uuid.UUID, branch_id: uuid.UUID | None
    ) -> TenantAppAssignment | None:
        stmt = sa.select(TenantAppAssignment).where(
            TenantAppAssignment.tenant_id == tenant_id,
            TenantAppAssignment.app_id == app_id,
            TenantAppAssignment.branch_id == branch_id if branch_id is not None
            else TenantAppAssignment.branch_id.is_(None),
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_assignments_for_tenant(self, tenant_id: uuid.UUID, app_id: uuid.UUID) -> list[TenantAppAssignment]:
        stmt = sa.select(TenantAppAssignment).where(
            TenantAppAssignment.tenant_id == tenant_id, TenantAppAssignment.app_id == app_id
        )
        return list(self.session.execute(stmt).scalars().all())

    def is_entitled_and_assigned(
        self, tenant_id: uuid.UUID, app_key: str, user_branch_id: uuid.UUID | None
    ) -> bool:
        """True iff the tenant is entitled to `app_key` AND has it enabled --
        either a tenant-wide (branch_id IS NULL) enabled row, or an enabled row
        scoped to this user's own branch. Mirrors the NULL-branch-degenerates-
        to-tenant-wide pattern in app/auth/permissions.py::can_read_conversation.
        """
        app = self.get_by_key(app_key)
        if app is None or not app.is_active:
            return False
        if not self.is_entitled(tenant_id, app_key):
            return False

        stmt = sa.select(sa.func.count()).select_from(TenantAppAssignment).where(
            TenantAppAssignment.tenant_id == tenant_id,
            TenantAppAssignment.app_id == app.id,
            TenantAppAssignment.is_enabled.is_(True),
            sa.or_(
                TenantAppAssignment.branch_id.is_(None),
                TenantAppAssignment.branch_id == user_branch_id,
            )
            if user_branch_id is not None
            else TenantAppAssignment.branch_id.is_(None),
        )
        return self.session.execute(stmt).scalar_one() > 0

    def list_available_for_user(
        self, tenant_id: uuid.UUID, user_branch_id: uuid.UUID | None
    ) -> list[App]:
        """Apps this tenant is entitled to AND has enabled for this user -- either
        a tenant-wide (branch_id IS NULL) enabled row, or one scoped to this
        user's own branch. For the non-admin "what can I use" dashboard view;
        mirrors is_entitled_and_assigned's NULL-branch-degenerates-to-tenant-wide
        check but as one query across every catalog app instead of one at a time.
        """
        branch_clause = (
            sa.or_(
                TenantAppAssignment.branch_id.is_(None),
                TenantAppAssignment.branch_id == user_branch_id,
            )
            if user_branch_id is not None
            else TenantAppAssignment.branch_id.is_(None)
        )
        stmt = (
            sa.select(App)
            .join(TenantAppEntitlement, TenantAppEntitlement.app_id == App.id)
            .join(TenantAppAssignment, TenantAppAssignment.app_id == App.id)
            .where(
                App.is_active.is_(True),
                TenantAppEntitlement.tenant_id == tenant_id,
                TenantAppEntitlement.revoked_at.is_(None),
                TenantAppAssignment.tenant_id == tenant_id,
                TenantAppAssignment.is_enabled.is_(True),
                branch_clause,
            )
            .order_by(App.name)
            .distinct()
        )
        return list(self.session.execute(stmt).scalars().all())

    def upsert_assignment(
        self,
        tenant_id: uuid.UUID,
        app_id: uuid.UUID,
        branch_id: uuid.UUID | None,
        is_enabled: bool,
        assigned_by: uuid.UUID,
    ) -> TenantAppAssignment:
        existing = self.get_assignment(tenant_id, app_id, branch_id)
        if existing is not None:
            existing.is_enabled = is_enabled
            existing.assigned_by = assigned_by
            existing.updated_at = datetime.now(timezone.utc)
            self.session.flush()
            return existing

        assignment = TenantAppAssignment(
            tenant_id=tenant_id,
            app_id=app_id,
            branch_id=branch_id,
            is_enabled=is_enabled,
            assigned_by=assigned_by,
        )
        self.session.add(assignment)
        self.session.flush()
        return assignment
