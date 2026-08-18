import uuid

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import TenantRolePermission


class RolePermissionRepository(BaseRepository):
    def list_for_role(self, tenant_id: uuid.UUID, role: str) -> list[TenantRolePermission]:
        stmt = sa.select(TenantRolePermission).where(
            TenantRolePermission.tenant_id == tenant_id,
            TenantRolePermission.role == role,
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_for_tenant(self, tenant_id: uuid.UUID) -> list[TenantRolePermission]:
        stmt = sa.select(TenantRolePermission).where(
            TenantRolePermission.tenant_id == tenant_id
        )
        return list(self.session.execute(stmt).scalars().all())

    def replace_for_tenant(
        self, tenant_id: uuid.UUID, rows: list[tuple[str, str, bool]]
    ) -> None:
        """Full-replace PUT semantics: delete every override row for the tenant,
        then insert the given (role, permission, granted) tuples. Runs inside the
        caller's transaction, so a failed insert rolls the delete back too."""
        self.session.execute(
            sa.delete(TenantRolePermission).where(TenantRolePermission.tenant_id == tenant_id)
        )
        for role, permission, granted in rows:
            self.session.add(
                TenantRolePermission(
                    tenant_id=tenant_id, role=role, permission=permission, granted=granted
                )
            )
        self.session.flush()
