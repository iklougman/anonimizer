import uuid

import sqlalchemy as sa

from app.db.repositories.base import BaseRepository
from app.models import Branch, User


class BranchInUseError(Exception):
    """The branch still has users assigned; explicit reassignment is required
    before deletion — silently NULLing users' branch_id would quietly change
    what a `conversations:read:branch` grant lets them see."""


class BranchRepository(BaseRepository):
    def create(self, tenant_id: uuid.UUID, name: str) -> Branch:
        branch = Branch(tenant_id=tenant_id, name=name)
        self.session.add(branch)
        self.session.flush()
        return branch

    def get(self, tenant_id: uuid.UUID, branch_id: uuid.UUID) -> Branch | None:
        stmt = sa.select(Branch).where(Branch.tenant_id == tenant_id, Branch.id == branch_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_for_tenant(self, tenant_id: uuid.UUID) -> list[Branch]:
        stmt = sa.select(Branch).where(Branch.tenant_id == tenant_id).order_by(Branch.name)
        return list(self.session.execute(stmt).scalars().all())

    def rename(self, tenant_id: uuid.UUID, branch_id: uuid.UUID, name: str) -> None:
        stmt = (
            sa.update(Branch)
            .where(Branch.tenant_id == tenant_id, Branch.id == branch_id)
            .values(name=name)
        )
        self.session.execute(stmt)

    def count_users(self, tenant_id: uuid.UUID, branch_id: uuid.UUID) -> int:
        stmt = sa.select(sa.func.count()).where(
            User.tenant_id == tenant_id, User.branch_id == branch_id
        )
        return self.session.execute(stmt).scalar_one()

    def delete(self, tenant_id: uuid.UUID, branch_id: uuid.UUID) -> None:
        if self.count_users(tenant_id, branch_id) > 0:
            raise BranchInUseError(f"branch {branch_id} still has users assigned")
        stmt = sa.delete(Branch).where(Branch.tenant_id == tenant_id, Branch.id == branch_id)
        self.session.execute(stmt)
